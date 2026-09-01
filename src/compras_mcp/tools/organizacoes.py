"""Tools de organizações: UASG, Órgãos (Dados Abertos) e unidades PNCP.

Endpoints cobertos:
- /modulo-uasg/1_consultarUasg          (Dados Abertos)
- /modulo-uasg/2_consultarOrgao         (Dados Abertos)
- /v1/orgaos/{cnpj}/unidades            (PNCP)

Cache TTL longo (24h) — UASGs e órgãos mudam raramente.
"""

from __future__ import annotations

import asyncio
import json
import time
import unicodedata
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.config import get_settings
from compras_mcp.errors import ComprasNotFoundError
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    ConsultarUasgInput,
    ListarOrgaosInput,
    ListarPaginadoInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    make_dados_abertos,
    make_pncp,
    with_latency,
)


_orgaos_cache = cache_from_env("ORGAOS", default_ttl=86400, default_max_size=500)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


# A rota de UASG não tem filtro textual e devolve páginas fixas de 500.
# 60 páginas = 30 mil UASGs, folga sobre as ~22 mil ativas de hoje.
_MAX_PAGINAS_VARREDURA = 60
_CONCORRENCIA_VARREDURA = 8


def _normalizar(texto: str) -> str:
    """Minúsculas sem acento — 'Aquaviários' casa com 'aquaviarios'."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower().strip()


async def _varrer_uasgs() -> tuple[list[dict[str, Any]], int, bool]:
    """Baixa o universo de UASGs ativas para permitir busca textual local.

    Devolve `(uasgs, paginas_varridas, truncado)`. O resultado fica em
    cache 24h sob chave própria: a varredura completa custa ~8s e as UASGs
    mudam raramente, então só a primeira busca do dia paga esse preço.
    """
    cache_key = _ck("uasg_universo")
    cached = await _orgaos_cache.get(cache_key)
    if cached is not None:
        return (cached["uasgs"], cached["paginas"], cached["truncado"])

    async with make_dados_abertos(get_settings()) as client:

        async def _pagina(n: int) -> list[dict[str, Any]]:
            resp = await client.list_resource(
                "/modulo-uasg/1_consultarUasg",
                pagina=n,
                tamanho_pagina=500,
                statusUasg="true",
            )
            return resp.get("resultado") or []

        primeira = await client.list_resource(
            "/modulo-uasg/1_consultarUasg",
            pagina=1,
            tamanho_pagina=500,
            statusUasg="true",
        )
        uasgs: list[dict[str, Any]] = list(primeira.get("resultado") or [])
        total_paginas = int(primeira.get("totalPaginas") or 1)
        truncado = total_paginas > _MAX_PAGINAS_VARREDURA
        ultima = min(total_paginas, _MAX_PAGINAS_VARREDURA)

        semaforo = asyncio.Semaphore(_CONCORRENCIA_VARREDURA)

        async def _com_limite(n: int) -> list[dict[str, Any]]:
            async with semaforo:
                return await _pagina(n)

        if ultima > 1:
            lotes = await asyncio.gather(
                *(_com_limite(n) for n in range(2, ultima + 1))
            )
            for lote in lotes:
                uasgs.extend(lote)

    await _orgaos_cache.set(
        cache_key,
        json.loads(
            json.dumps(
                {"uasgs": uasgs, "paginas": ultima, "truncado": truncado},
                default=str,
            )
        ),
    )
    return (uasgs, ultima, truncado)


def _resposta_uasg_404(endpoint_path: str) -> dict[str, Any]:
    """Payload educativo quando `/modulo-uasg/*` retorna 404.

    **Correção de diagnóstico em 2026-08-05.** Até a v0.3.12 este helper
    afirmava "bug do servidor SEGES — sem fix possível pelo MCP". Era
    falso. O 404 vinha de **nós**: as tools não enviavam o parâmetro
    obrigatório `statusUasg` / `statusOrgao`, e esta API responde 404 (não
    400) a parâmetro obrigatório ausente. Com o parâmetro, a rota devolve
    200 e ~22 mil UASGs. Mesma classe de defeito da rota
    `/modulo-pesquisa-preco/1_consultarMaterial` — ver CHANGELOG 0.3.13.

    O envelope continua existindo como rede de proteção: se a assinatura
    mudar de novo, as tools degradam com diagnóstico em vez de estourar
    `ComprasNotFoundError`.
    """
    return {
        "resultado": [],
        "_total_registros": 0,
        "_cache_hit": False,
        "_erro_upstream": {
            "endpoint": f"dados_abertos{endpoint_path}",
            "status": 404,
            "diagnostico": (
                "Rota respondeu 404. Nesta API isso indica parâmetro "
                "obrigatório ausente ou assinatura de query alterada — não "
                "'recurso inexistente'. A causa conhecida (falta de "
                "`statusUasg`/`statusOrgao`) foi corrigida na v0.3.13; um "
                "404 aqui sugere nova mudança de contrato upstream."
            ),
            "verificar_com": "python scripts/probe_upstream.py --modulo organizacoes",
            "alternativas": [
                "Para listar UGs federais: GET https://contratos.comprasnet.gov.br/api/contrato/unidades "
                "(via cliente HTTP genérico — retorna só códigos)",
                "Para listar UGs com contratos vigentes: `compras_contrato_comprasnet_por_uasg(uasg)`",
                "Para contratações de um órgão: `compras_contratacoes_14133_listar(codigo_orgao=...)`",
            ],
        },
    }


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_uasg_listar(
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
    codigo_orgao: Annotated[
        int | None,
        Field(
            default=None,
            description="Filtra UASGs subordinadas a este código de órgão.",
        ),
    ] = None,
    ativo: Annotated[
        bool | None,
        Field(
            default=None,
            description="True para apenas UASGs ativas, False para inativas, None para ambas.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Lista UASGs (Unidades Administrativas de Serviços Gerais) do governo.

    **✅ Restaurada em 2026-08-05.** Da v0.2.x até a v0.3.12 esta tool
    devolvia "endpoint indisponível" e a documentação atribuía o 404 a um
    bug de roteamento da SEGES. O diagnóstico estava errado: faltava o
    parâmetro obrigatório `statusUasg`, e esta API responde **404** (não
    400) quando um obrigatório não vem. Enviando o parâmetro, a rota
    devolve 200 com ~22 mil UASGs ativas.

    O filtro `ativo` alimenta `statusUasg`; quando não informado, a tool
    assume `True` (ativas), que é o caso de uso dominante.

    **Paginação**: o upstream ignora `tamanho_pagina` nesta rota e devolve
    páginas fixas de 500 registros — `_total_paginas` reflete a paginação
    real do servidor, não o tamanho pedido.

    Cache 24h.
    """
    started = time.perf_counter()
    key = _ck("uasg_listar", pagina, tamanho_pagina, codigo_orgao, ativo)
    cached = await _orgaos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {}
    if codigo_orgao is not None:
        filtros["codigoOrgao"] = codigo_orgao
    # `statusUasg` é obrigatório no contrato: omitir devolve 404. O nome
    # antigo (`ativo`) não existe upstream e era silenciosamente ignorado.
    filtros["statusUasg"] = "false" if ativo is False else "true"

    try:
        async with make_dados_abertos(get_settings()) as client:
            resp = await client.list_resource(
                "/modulo-uasg/1_consultarUasg",
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
                **filtros,
            )
    except ComprasNotFoundError:
        return with_latency(_resposta_uasg_404("/modulo-uasg/1_consultarUasg"), started)
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _orgaos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_uasg_consultar(
    codigo_uasg: Annotated[
        int, Field(description=desc(ConsultarUasgInput, "codigo_uasg"))
    ],
) -> dict[str, Any]:
    """Consulta uma UASG específica pelo código.

    Devolve nome, sigla, CNPJ vinculado, órgão superior e endereço.
    Útil para resolver `codigo_uasg` antes de consultas filtradas.

    **✅ Restaurada em 2026-08-05** — ver `compras_uasg_listar` para o
    diagnóstico do 404 que afetava toda a família `/modulo-uasg/*`.

    Busca primeiro entre as ativas; se não achar, repete entre as inativas
    (o upstream exige `statusUasg` e não aceita "ambas"), devolvendo
    `ativa: false` para UASGs extintas.

    Cache 24h.
    """
    started = time.perf_counter()
    key = _ck("uasg_consultar", codigo_uasg)
    cached = await _orgaos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    resultados: list[dict[str, Any]] = []
    ativa: bool | None = None
    try:
        async with make_dados_abertos(get_settings()) as client:
            for status in ("true", "false"):
                resp = await client.list_resource(
                    "/modulo-uasg/1_consultarUasg",
                    pagina=1,
                    tamanho_pagina=10,
                    codigoUasg=codigo_uasg,
                    statusUasg=status,
                )
                resultados = resp.get("resultado") or []
                if resultados:
                    ativa = status == "true"
                    break
    except ComprasNotFoundError:
        return with_latency(_resposta_uasg_404("/modulo-uasg/1_consultarUasg"), started)
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": codigo_uasg,
        "ativa": ativa,
        "uasg": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _orgaos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_orgao_listar(
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
    nome: Annotated[
        str | None,
        Field(default=None, description=desc(ListarOrgaosInput, "nome")),
    ] = None,
    esfera: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Esfera administrativa: 'F' (federal), 'E' (estadual), 'M' (municipal). "
                "Dados Abertos cobre majoritariamente federal."
            ),
        ),
    ] = None,
    poder: Annotated[
        str | None,
        Field(
            default=None,
            description="Poder: 'E' (Executivo), 'L' (Legislativo), 'J' (Judiciário).",
        ),
    ] = None,
) -> dict[str, Any]:
    """Lista órgãos cadastrados no Compras.gov.br.

    Endpoint Dados Abertos `/modulo-uasg/2_consultarOrgao`. Inclui órgãos
    do SISG (Sistema de Serviços Gerais), com código numérico, nome,
    esfera, poder e CNPJ.

    **✅ Restaurada em 2026-08-05**: faltava o parâmetro obrigatório
    `statusOrgao` — mesma causa do 404 em `compras_uasg_listar`.

    **⚠️ Filtros textuais não funcionam**: `nome`, `esfera` e `poder` não
    constam do contrato desta rota e são ignorados pelo upstream (a
    resposta vem igual, com todos os ~11,8 mil órgãos ativos). Para
    localizar um órgão específico use `codigo_orgao` em
    `compras_orgao_consultar`. Verificado em 2026-08-05.

    Cache 24h.
    """
    started = time.perf_counter()
    key = _ck("orgao_listar", pagina, tamanho_pagina, nome, esfera, poder)
    cached = await _orgaos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    # `statusOrgao` é obrigatório no contrato: omitir devolve 404.
    filtros: dict[str, Any] = {"statusOrgao": "true"}
    if nome:
        filtros["nome"] = nome
    if esfera:
        filtros["esferaAdministrativa"] = esfera.upper()
    if poder:
        filtros["poder"] = poder.upper()

    try:
        async with make_dados_abertos(get_settings()) as client:
            resp = await client.list_resource(
                "/modulo-uasg/2_consultarOrgao",
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
                **filtros,
            )
    except ComprasNotFoundError:
        return with_latency(_resposta_uasg_404("/modulo-uasg/2_consultarOrgao"), started)
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _orgaos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_orgao_consultar(
    codigo_orgao: Annotated[
        int,
        Field(description="Código numérico do órgão (4-6 dígitos)."),
    ],
) -> dict[str, Any]:
    """Consulta um órgão específico pelo código.

    Devolve nome, sigla, CNPJ, esfera, poder e quantitativos.
    Cache 24h.
    """
    started = time.perf_counter()
    key = _ck("orgao_consultar", codigo_orgao)
    cached = await _orgaos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_dados_abertos(get_settings()) as client:
            resp = await client.list_resource(
                "/modulo-uasg/2_consultarOrgao",
                pagina=1,
                tamanho_pagina=10,
                codigoOrgao=codigo_orgao,
                statusOrgao="true",
            )
    except ComprasNotFoundError:
        return with_latency(_resposta_uasg_404("/modulo-uasg/2_consultarOrgao"), started)
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": codigo_orgao,
        "orgao": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _orgaos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_orgao_unidades(
    cnpj: Annotated[
        str,
        Field(
            description=(
                "CNPJ do órgão (14 dígitos, com ou sem pontuação). "
                "Exemplo: 00394460000141 (Presidência da República)."
            ),
            min_length=11,
            max_length=20,
        ),
    ],
) -> dict[str, Any]:
    """Lista unidades administrativas de um órgão no PNCP.

    Endpoint PNCP `/v1/orgaos/{cnpj}/unidades`. Útil para descobrir códigos
    de unidade antes de filtrar contratações/contratos do órgão.

    Cobre estados e municípios (não só federal). Cache 24h.

    **Tratamento de 404**: nem todo CNPJ está indexado no PNCP. Em vez de
    levantar exception, esta tool retorna `_erro_upstream` informativo
    com lista de alternativas (mesmo padrão das tools `compras_uasg_*` /
    `compras_orgao_*` quando o `/modulo-uasg/*` retorna 404).
    """
    started = time.perf_counter()
    cnpj_clean = "".join(c for c in cnpj if c.isdigit())
    key = _ck("pncp_unidades", cnpj_clean)
    cached = await _orgaos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_pncp(get_settings()) as client:
            resp = await client.get_resource(f"/v1/orgaos/{cnpj_clean}/unidades")
    except ComprasNotFoundError:
        payload = {
            "resultado": [],
            "_total_registros": 0,
            "_cache_hit": False,
            "_erro_upstream": {
                "endpoint": f"pncp/v1/orgaos/{cnpj_clean}/unidades",
                "status": 404,
                "diagnostico": (
                    f"CNPJ {cnpj_clean} não tem unidades indexadas no PNCP. "
                    "Pode ser: (1) o CNPJ raiz do órgão não publica diretamente "
                    "no PNCP (publicações vêm das subunidades com CNPJs próprios), "
                    "(2) órgão é só federal SISG e não usa PNCP, ou (3) CNPJ "
                    "incorreto."
                ),
                "alternativas": [
                    "Use `compras_pncp_contratacoes_publicacao(cnpj=...)` com "
                    "uma janela curta para descobrir quais CNPJs publicam atos "
                    "vinculados a este órgão.",
                    "Para UGs federais SISG: `compras_contrato_comprasnet_por_uasg(uasg=...)` "
                    "ou GET https://contratos.comprasnet.gov.br/api/contrato/unidades",
                    "Confirme o CNPJ correto: alguns órgãos têm CNPJs distintos "
                    "para administração central vs. unidades operacionais.",
                ],
            },
        }
        return with_latency(payload, started)

    if isinstance(resp, list):
        payload: dict[str, Any] = {
            "resultado": resp,
            "_total_registros": len(resp),
            "_cache_hit": False,
        }
    else:
        payload = {
            "resultado": resp.get("data") or [],
            "_total_registros": int(resp.get("totalRegistros") or 0),
            "_cache_hit": False,
        }
    await _orgaos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_uasg_buscar(
    termo: Annotated[
        str,
        Field(
            min_length=2,
            max_length=100,
            description=(
                "Trecho do nome da UASG (match literal, ignora acento e "
                "caixa). Ex.: 'aquaviarios', 'tribunal regional', 'exercito'. "
                "Siglas raramente funcionam — os nomes vêm por extenso no "
                "cadastro ('AGÊNCIA NACIONAL DE TRANSPORTES AQUAVIÁRIOS', "
                "não 'ANTAQ')."
            ),
        ),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Busca UASGs por trecho do nome (match parcial, ignora acento e caixa).

    **✅ Restaurada em 2026-08-05, com busca local.** Duas correções:

    1. A rota exige `statusUasg`; sem ele devolvia 404 (mesma causa de
       `compras_uasg_listar`).
    2. O parâmetro `nome` **não existe** no contrato da rota e era
       ignorado pelo upstream — enviá-lo devolvia o universo inteiro
       (~22 mil UASGs) como se fossem resultados de busca. Corrigir só o
       item 1 teria trocado um erro visível (404) por um erro silencioso,
       que é pior: o analista receberia "TCU - SECRETARIA DE INFORMATICA"
       como 1º resultado de qualquer termo.

    Como não há filtro textual upstream, a busca é feita **localmente**:
    a tool varre as páginas da rota (500 registros cada, ~8s no universo
    completo), filtra por `termo` e pagina o resultado filtrado. O varrido
    fica em cache por 24h, então só a primeira busca do dia paga o custo.

    O payload informa `_busca_local`, `_paginas_varridas` e
    `_universo_varrido` — se a varredura for truncada, isso fica explícito
    em vez de virar silêncio.

    Cache 24h.
    """
    started = time.perf_counter()
    key = _ck("uasg_buscar", _normalizar(termo), pagina, tamanho_pagina)
    cached = await _orgaos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        universo, paginas_varridas, truncado = await _varrer_uasgs()
    except ComprasNotFoundError:
        return with_latency(_resposta_uasg_404("/modulo-uasg/1_consultarUasg"), started)

    alvo = _normalizar(termo)
    achados = [
        u for u in universo if alvo in _normalizar(str(u.get("nomeUasg") or ""))
    ]

    inicio = (pagina - 1) * tamanho_pagina
    recorte = achados[inicio : inicio + tamanho_pagina]
    total_paginas = max(1, -(-len(achados) // tamanho_pagina))
    payload: dict[str, Any] = {
        "resultado": recorte,
        "_pagina_atual": pagina,
        "_total_paginas": total_paginas,
        "_total_registros": len(achados),
        "_proxima_pagina": pagina + 1 if pagina < total_paginas else None,
        "_busca_local": True,
        "_paginas_varridas": paginas_varridas,
        "_universo_varrido": len(universo),
        "_cache_hit": False,
    }
    if truncado:
        payload["_aviso_varredura_truncada"] = (
            f"A varredura parou em {_MAX_PAGINAS_VARREDURA} páginas "
            f"({len(universo)} UASGs). Pode haver UASGs correspondentes fora "
            "desse recorte — refine o termo ou use `compras_uasg_consultar` "
            "com o código."
        )
    if not achados:
        payload["_aviso_sem_resultado"] = (
            f"Nenhuma das {len(universo)} UASGs varridas contém '{termo}' no "
            "nome. O filtro é local e literal (sem sinônimos): tente um "
            "trecho menor, ex. 'ANTAQ' em vez de 'Agência Nacional de "
            "Transportes Aquaviários'."
        )
    await _orgaos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
