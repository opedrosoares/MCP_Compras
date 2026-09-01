"""Tools de Atas de Registro de Preço (ARP).

ARPs são instrumentos de planejamento que permitem aquisições futuras com
preços registrados. Para o analista de outro órgão, a relevância principal
é a **adesão** (carona): aderir a uma ata vigente de outro órgão para
contratar sem licitar novamente.

Endpoints cobertos (parâmetros conforme `/v3/api-docs` do upstream):
- /modulo-arp/1_consultarARP              — exige dataVigenciaInicialMin/Max (janela ≤365 dias)
- /modulo-arp/1.1_consultarARP_Id         — exige numeroControlePncpAta
- /modulo-arp/1.2_consultarARP_FimVigencia — exige dataVigenciaFinalMin/Max
- /modulo-arp/2_consultarARPItem          — exige dataVigenciaInicialMin/Max + filtros
- /modulo-arp/3_consultarUnidadesItem     — exige numeroAta + unidadeGerenciadora + numeroItem
- /modulo-arp/4_consultarEmpenhosSaldoItem — exige numeroAta + unidadeGerenciadora
- /modulo-arp/5_consultarAdesoesItem      — exige numeroAta + unidadeGerenciadora + numeroItem
- /v1/atas                                — PNCP (cobre estados/municípios)

Cache TTL curto (15 min) — saldo e adesões mudam ao longo do dia.

Atenção: `numeroAta` é o número simples (ex.: "00001/2024"), distinto de
`numeroControlePncpAta` (identificador PNCP completo). Os endpoints de
sub-recursos (3, 4, 5) usam **`numeroAta` + `unidadeGerenciadora`** como
chave composta.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.config import get_settings
from compras_mcp.errors import (
    ComprasHTTPError,
    ComprasServerError,
    ComprasTimeoutError,
)
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    ListarPaginadoInput,
    PNCPListarAtasInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    envelope_pncp,
    make_dados_abertos,
    make_pncp,
    with_latency,
)


_atas_cache = cache_from_env("ATAS", default_ttl=900, default_max_size=300)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


# ============================================================================
# ARP — Dados Abertos
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_listar(
    data_vigencia_inicial_min: Annotated[
        date,
        Field(
            description=(
                "Data MÍNIMA do início da vigência da ata (YYYY-MM-DD). Obrigatório. "
                "A janela entre min e max deve ser de no máximo 365 dias."
            )
        ),
    ],
    data_vigencia_inicial_max: Annotated[
        date,
        Field(
            description=(
                "Data MÁXIMA do início da vigência da ata (YYYY-MM-DD). Obrigatório. "
                "Janela max ≤ 365 dias a partir de data_vigencia_inicial_min."
            )
        ),
    ],
    codigo_unidade_gerenciadora: Annotated[
        int | None,
        Field(
            default=None,
            description="Filtra ARPs pela UASG gerenciadora (5-6 dígitos).",
        ),
    ] = None,
    codigo_modalidade_compra: Annotated[
        int | None,
        Field(
            default=None,
            description="Filtra por modalidade da compra que originou a ata.",
        ),
    ] = None,
    numero_ata_registro_preco: Annotated[
        str | None,
        Field(
            default=None,
            description="Filtra por número da ata (ex.: '00001/2024').",
        ),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista Atas de Registro de Preço (ARPs) por janela de início de vigência.

    Endpoint Dados Abertos `/modulo-arp/1_consultarARP`. O upstream exige
    janela `dataVigenciaInicialMin/Max` (≤ 365 dias). Para listar atas
    próximas do vencimento, use `compras_arp_por_fim_vigencia`.

    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck(
        "arp_listar",
        data_vigencia_inicial_min,
        data_vigencia_inicial_max,
        codigo_unidade_gerenciadora,
        codigo_modalidade_compra,
        numero_ata_registro_preco,
        pagina,
        tamanho_pagina,
    )
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataVigenciaInicialMin": format_date(data_vigencia_inicial_min, "dados_abertos"),
        "dataVigenciaInicialMax": format_date(data_vigencia_inicial_max, "dados_abertos"),
    }
    if codigo_unidade_gerenciadora is not None:
        filtros["codigoUnidadeGerenciadora"] = codigo_unidade_gerenciadora
    if codigo_modalidade_compra is not None:
        filtros["codigoModalidadeCompra"] = codigo_modalidade_compra
    if numero_ata_registro_preco:
        filtros["numeroAtaRegistroPreco"] = numero_ata_registro_preco

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-arp/1_consultarARP",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


# Regex do formato de ID de ATA. Compras SRP multi-fornecedor produzem
# múltiplas atas dentro da mesma compra — o sufixo `-NNNNNN` numera a ata.
#
# - ID de **compra**: `cnpj14-1-sequencial/ano`           (ex.: 00394452000103-1-004729/2024)
# - ID de **ata** : `cnpj14-1-sequencial/ano-NNNNNN`     (ex.: ...004729/2024-000006)
#
# A tool `compras_arp_consultar` exige o formato de **ata**. Quando o ID
# vier sem o sufixo, devolvemos diagnóstico explícito antes de bater no
# upstream — caminho que retornava `encontrada: false` silencioso na
# bateria A v0.3.5.
_RX_ID_ATA = re.compile(r"^\d{14}-\d+-\d+/\d{4}-\d{6}$")
_RX_ID_COMPRA = re.compile(r"^\d{14}-\d+-\d+/\d{4}$")


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_consultar(
    numero_controle_pncp_ata: Annotated[
        str,
        Field(
            description=(
                "Identificador PNCP da **ata** (formato "
                "`cnpj14-1-sequencial/ano-NNNNNN`, onde NNNNNN numera a ata "
                "dentro da compra — compras SRP multi-fornecedor geram "
                "várias atas). Exemplo: `00394452000103-1-004729/2024-000006`. "
                "Retornado em `compras_arp_por_fim_vigencia` no campo "
                "`numeroControlePncpAta`. NÃO confundir com "
                "`numeroControlePncpCompra` (formato sem o sufixo)."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Consulta uma ARP específica pelo identificador PNCP.

    Endpoint Dados Abertos `/modulo-arp/1.1_consultarARP_Id`. Devolve o
    cabeçalho completo da ata (vigência, modalidade, gerenciadora, valores).

    Quando o `numero_controle_pncp_ata` vem no formato de **compra** (sem
    o sufixo `-NNNNNN` que numera a ata), a tool detecta e devolve
    diagnóstico explícito em vez de propagar `encontrada=false` silencioso.

    Cache 15 min.
    """
    started = time.perf_counter()

    numero = (numero_controle_pncp_ata or "").strip()
    if not _RX_ID_ATA.fullmatch(numero):
        diagnostico: dict[str, Any] = {
            "encontrada": False,
            "numero_consultado": numero,
            "_erro_upstream": {
                "tipo": "formato_id_invalido",
                "diagnostico": (
                    "Formato esperado: `cnpj14-1-sequencial/ano-NNNNNN` "
                    "(ID de ATA). Compras SRP multi-fornecedor produzem "
                    "várias atas dentro da mesma compra; o sufixo "
                    "`-NNNNNN` numera a ata específica."
                ),
                "id_recebido": numero,
            },
            "_cache_hit": False,
        }
        if _RX_ID_COMPRA.fullmatch(numero):
            diagnostico["_erro_upstream"]["diagnostico_especifico"] = (
                "Você passou um ID de COMPRA (sem o sufixo `-NNNNNN`). "
                "Para listar todas as atas dessa compra, chame "
                "`compras_arp_listar` e pegue os `numeroControlePncpAta` "
                "(que incluem o sufixo). Para consultar uma compra "
                "diretamente, use `compras_pncp_contratacao_por_orgao` "
                "extraindo CNPJ, ano e sequencial deste ID."
            )
        return with_latency(diagnostico, started)

    key = _ck("arp_consultar", numero)
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-arp/1.1_consultarARP_Id",
            pagina=1,
            tamanho_pagina=10,  # clamp mínimo do upstream
            numeroControlePncpAta=numero,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrada": bool(resultados),
        "numero_consultado": numero,
        "ata": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    if not resultados:
        # Formato bate, upstream não achou — caminho válido (ata não
        # existe), mas diagnóstico orientativo.
        payload["_diagnostico"] = (
            "Formato do ID está correto, mas o upstream não encontrou ata "
            "com esse número. Causas comuns: (1) ata excluída/anulada; "
            "(2) número de ata dentro da compra (sufixo) inexistente — "
            "valide com `compras_arp_por_fim_vigencia` ou "
            "`compras_arp_listar`; (3) a ata é muito recente e ainda não "
            "está indexada pelo Dados Abertos."
        )
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_por_fim_vigencia(
    data_vigencia_final_min: Annotated[
        date,
        Field(
            description=(
                "Data MÍNIMA de fim de vigência (YYYY-MM-DD). Obrigatório. "
                "Janela max ≤ 365 dias."
            )
        ),
    ],
    data_vigencia_final_max: Annotated[
        date,
        Field(
            description=(
                "Data MÁXIMA de fim de vigência (YYYY-MM-DD). Obrigatório."
            )
        ),
    ],
    codigo_unidade_gerenciadora: Annotated[
        int | None,
        Field(default=None, description="UASG gerenciadora (opcional)."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista ARPs cuja vigência termina dentro do intervalo informado.

    Endpoint Dados Abertos `/modulo-arp/1.2_consultarARP_FimVigencia`.
    Permite ao gestor identificar atas próximas do vencimento.

    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck(
        "arp_fim_vig",
        data_vigencia_final_min,
        data_vigencia_final_max,
        codigo_unidade_gerenciadora,
        pagina,
        tamanho_pagina,
    )
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataVigenciaFinalMin": format_date(data_vigencia_final_min, "dados_abertos"),
        "dataVigenciaFinalMax": format_date(data_vigencia_final_max, "dados_abertos"),
    }
    if codigo_unidade_gerenciadora is not None:
        filtros["codigoUnidadeGerenciadora"] = codigo_unidade_gerenciadora

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-arp/1.2_consultarARP_FimVigencia",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_buscar_por_objeto(
    palavra_chave: Annotated[
        str,
        Field(
            description=(
                "Termo a procurar no campo `objetoCompra` das ARPs "
                "(case-insensitive, com normalização básica de acentos). "
                "Exemplos: 'notebook', 'uniformes', 'limpeza'."
            ),
            min_length=3,
        ),
    ],
    data_vigencia_final_min: Annotated[
        date,
        Field(
            description=(
                "Limite mínimo do fim de vigência (YYYY-MM-DD). "
                "Tipicamente hoje para 'apenas vigentes'."
            )
        ),
    ],
    data_vigencia_final_max: Annotated[
        date,
        Field(
            description="Limite máximo do fim de vigência (YYYY-MM-DD)."
        ),
    ],
    max_paginas_varridas: Annotated[
        int,
        Field(
            default=10,
            ge=1,
            le=50,
            description=(
                "Quantas páginas do upstream serão varridas para encontrar "
                "matches (proteção de latência). Default 10 × 500 itens = "
                "até 5.000 ARPs examinadas. Cap em 50."
            ),
        ),
    ] = 10,
    max_resultados: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            le=100,
            description="Quantos matches no máximo retornar (curto-circuita a varredura).",
        ),
    ] = 20,
) -> dict[str, Any]:
    """Busca ARPs vigentes cujo `objeto` contém uma palavra-chave.

    Resolve a limitação do endpoint `/modulo-arp/1.2_consultarARP_FimVigencia`,
    que não aceita filtro por texto: pagina internamente até `max_paginas_varridas`
    e filtra client-side por presença de `palavra_chave` (case-insensitive,
    com normalização de acentos). Curto-circuita quando atinge `max_resultados`.

    O servidor faz o trabalho que antes era pedido ao LLM — sem isso, o
    roteiro `oportunidades_carona_arp` esbarrava em 169k ARPs vigentes e
    339 páginas. Achado da bateria A v0.3.5.

    **Limitação conhecida**: o schema upstream de ARP **não traz UF** no
    item — só `nomeOrgao` e `nomeUnidadeGerenciadora`. Para filtrar por
    UF, cruze os matches com `compras_uasg_consultar` usando
    `codigoUnidadeGerenciadora` e compare `unidade.uf`. Não tentamos esse
    cruzamento aqui para manter a tool barata e previsível.

    Output:
        {
          "resultado": [<ARPs que casaram>],
          "total_examinadas": int,
          "matches": int,
          "paginas_varridas": int,
          "curto_circuitou": bool,
          "_filtro_objeto": {...}
        }

    Cache 15 min por (palavra_chave + janela + caps).
    """
    started = time.perf_counter()

    import unicodedata

    def _norm(s: str) -> str:
        s = s or ""
        nfd = unicodedata.normalize("NFD", s)
        return "".join(c for c in nfd if not unicodedata.combining(c)).lower().strip()

    alvo = _norm(palavra_chave)

    key = _ck(
        "arp_busca_obj",
        alvo,
        data_vigencia_final_min,
        data_vigencia_final_max,
        max_paginas_varridas,
        max_resultados,
    )
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    matches: list[dict[str, Any]] = []
    total_examinadas = 0
    pagina = 1
    paginas_processadas = 0  # incrementado APÓS processar cada página
    curto_circuitou = False

    async with make_dados_abertos(get_settings()) as client:
        while pagina <= max_paginas_varridas:
            resp = await client.list_resource(
                "/modulo-arp/1.2_consultarARP_FimVigencia",
                pagina=pagina,
                tamanho_pagina=500,
                dataVigenciaFinalMin=format_date(
                    data_vigencia_final_min, "dados_abertos"
                ),
                dataVigenciaFinalMax=format_date(
                    data_vigencia_final_max, "dados_abertos"
                ),
            )
            items = resp.get("resultado") or []
            if not items:
                break
            paginas_processadas += 1
            total_examinadas += len(items)
            for it in items:
                # Schema real upstream: `objeto`. Fallback para `objetoCompra`
                # se a CGU mudar (defensivo).
                obj = _norm(it.get("objeto") or it.get("objetoCompra") or "")
                if alvo not in obj:
                    continue
                matches.append(it)
                if len(matches) >= max_resultados:
                    curto_circuitou = True
                    break
            if curto_circuitou:
                break
            total_paginas_upstream = int(resp.get("totalPaginas") or 0)
            if pagina >= total_paginas_upstream:
                break
            pagina += 1

    payload: dict[str, Any] = {
        "resultado": matches,
        "total_examinadas": total_examinadas,
        "matches": len(matches),
        "paginas_varridas": paginas_processadas,
        "curto_circuitou": curto_circuitou,
        "_filtro_objeto": {
            "palavra_chave": palavra_chave,
            "campo_pesquisado": "objeto",
            "normalizacao": "lowercase + sem acentos + strip",
            "aviso_uf": (
                "Filtro por UF não suportado nesta tool — o schema upstream "
                "do endpoint /modulo-arp/1.2_consultarARP_FimVigencia não "
                "traz UF no item. Para refinar por UF, cruze os matches com "
                "`compras_uasg_consultar(codigo_uasg=...)` usando o "
                "`codigoUnidadeGerenciadora` de cada match."
            ),
            "aviso_varredura": (
                "Universo de ARPs vigentes tipicamente passa de 100k itens. "
                "Se matches=0, aumente `max_paginas_varridas` (até 50) ou "
                "considere termos alternativos. A varredura é client-side "
                "porque o upstream não aceita filtro textual."
            ),
        },
        "_cache_hit": False,
    }
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_itens_listar(
    data_vigencia_inicial_min: Annotated[
        date,
        Field(description="Data mínima de início de vigência (YYYY-MM-DD)."),
    ],
    data_vigencia_inicial_max: Annotated[
        date,
        Field(description="Data máxima de início de vigência (YYYY-MM-DD)."),
    ],
    codigo_item: Annotated[
        int | None,
        Field(default=None, description="Filtra por código CATMAT ou CATSER."),
    ] = None,
    tipo_item: Annotated[
        str | None,
        Field(
            default=None,
            description="Tipo do item: 'M' (material) ou 'S' (serviço).",
        ),
    ] = None,
    ni_fornecedor: Annotated[
        str | None,
        Field(default=None, description="CPF/CNPJ do fornecedor (apenas dígitos)."),
    ] = None,
    codigo_unidade_gerenciadora: Annotated[
        int | None,
        Field(default=None, description="UASG gerenciadora (opcional)."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de ARPs na janela de vigência informada.

    Endpoint Dados Abertos `/modulo-arp/2_consultarARPItem`. O upstream
    exige `dataVigenciaInicialMin/Max` (janela ≤365 dias). Use filtros
    opcionais para localizar atas com um item específico.

    Cache 15 min.
    """
    started = time.perf_counter()
    ni_clean = "".join(c for c in ni_fornecedor if c.isdigit()) if ni_fornecedor else None
    key = _ck(
        "arp_itens",
        data_vigencia_inicial_min,
        data_vigencia_inicial_max,
        codigo_item,
        tipo_item,
        ni_clean,
        codigo_unidade_gerenciadora,
        pagina,
        tamanho_pagina,
    )
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataVigenciaInicialMin": format_date(data_vigencia_inicial_min, "dados_abertos"),
        "dataVigenciaInicialMax": format_date(data_vigencia_inicial_max, "dados_abertos"),
    }
    if codigo_item is not None:
        filtros["codigoItem"] = codigo_item
    if tipo_item:
        filtros["tipoItem"] = tipo_item.upper()
    if ni_clean:
        filtros["niFornecedor"] = ni_clean
    if codigo_unidade_gerenciadora is not None:
        filtros["codigoUnidadeGerenciadora"] = codigo_unidade_gerenciadora

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-arp/2_consultarARPItem",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_unidades_item(
    numero_ata: Annotated[
        str,
        Field(
            description=(
                "Número simples da ata (ex.: '00001/2024'). Distinto do "
                "`numeroControlePncpAta` — use o campo retornado em "
                "`compras_arp_listar` ou `compras_arp_itens_listar`."
            )
        ),
    ],
    unidade_gerenciadora: Annotated[
        int,
        Field(description="Código da UASG gerenciadora da ata."),
    ],
    numero_item: Annotated[
        int,
        Field(description="Número do item dentro da ata."),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista UGs participantes (potenciais caronas) de um item da ARP.

    Endpoint Dados Abertos `/modulo-arp/3_consultarUnidadesItem`. Determina
    quais unidades podem usar a ata como carona (adesão).
    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck("arp_unidades", numero_ata, unidade_gerenciadora, numero_item, pagina, tamanho_pagina)
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-arp/3_consultarUnidadesItem",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            numeroAta=numero_ata,
            unidadeGerenciadora=unidade_gerenciadora,
            numeroItem=numero_item,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_saldo_item(
    numero_ata: Annotated[
        str,
        Field(description="Número simples da ata (ex.: '00001/2024')."),
    ],
    unidade_gerenciadora: Annotated[
        int,
        Field(description="Código da UASG gerenciadora da ata."),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Devolve o **saldo** (quantidade ainda disponível) por item da ARP.

    Endpoint Dados Abertos `/modulo-arp/4_consultarEmpenhosSaldoItem`.
    **Crítico para adesão**: a ata pode estar vigente mas com saldo
    zerado. Sem saldo, não há como aderir.

    **Estrutura do payload**: o upstream retorna **1 linha por
    (numeroItem, unidade, tipo)** — onde `tipo` pode ser `GERENCIADORA`,
    `PARTICIPANTE` etc. O mesmo `numeroItem` aparece várias vezes quando
    há múltiplas unidades alocadas (carona ou rateio). **Não é
    duplicação** — são alocações distintas dentro da mesma ata.

    Para evitar confusão (achado bateria A v0.3.5), além do `resultado`
    cru, anexamos `resumo_por_item`: dicionário agregando por
    `numeroItem` com soma das quantidades registradas/empenhadas e
    saldo total — pronto para decisão de adesão.

    Cache 15 min (saldo muda ao longo do dia).
    """
    started = time.perf_counter()
    key = _ck("arp_saldo", numero_ata, unidade_gerenciadora, pagina, tamanho_pagina)
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    # Achado bateria A v0.3.11: o timeout do upstream estava vazando como
    # exception não-estruturada (regressão da promessa v0.3.9). A v0.3.9
    # configurou max_retries=0 + timeout=20s no client mas não capturou a
    # exception aqui. Agora envolvemos em try/except e devolvemos
    # `_erro_upstream` com diagnóstico — mesmo padrão das tools de sanção.
    try:
        async with make_dados_abertos(get_settings()) as client:
            resp = await client.list_resource(
                "/modulo-arp/4_consultarEmpenhosSaldoItem",
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
                max_retries=0,
                timeout=20.0,
                numeroAta=numero_ata,
                unidadeGerenciadora=unidade_gerenciadora,
            )
    except (ComprasTimeoutError, ComprasServerError, ComprasHTTPError) as exc:
        return with_latency(
            {
                "resultado": [],
                "_total_registros": 0,
                "_pagina_atual": pagina,
                "resumo_por_item": [],
                "_cache_hit": False,
                "_erro_upstream": {
                    "tipo": (
                        "timeout"
                        if isinstance(exc, ComprasTimeoutError)
                        else "server_error"
                        if isinstance(exc, ComprasServerError)
                        else "http_error"
                    ),
                    "endpoint": "dados_abertos/modulo-arp/4_consultarEmpenhosSaldoItem",
                    "mensagem": str(exc)[:300],
                    "diagnostico": (
                        "Upstream Dados Abertos /modulo-arp/4 não respondeu em "
                        "20s. Causas comuns: (1) ata sem rateio populado — o "
                        "endpoint demora muito para responder lista vazia; "
                        "(2) UASG gerenciadora errada — confirme com "
                        "`compras_arp_consultar` que o "
                        "`codigoUnidadeGerenciadora` do cabeçalho bate; "
                        "(3) instabilidade upstream — retentar em alguns minutos."
                    ),
                    "filtros_tentados": {
                        "numero_ata": numero_ata,
                        "unidade_gerenciadora": unidade_gerenciadora,
                        "pagina": pagina,
                        "tamanho_pagina": tamanho_pagina,
                    },
                },
            },
            started,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False

    # Agrega por numeroItem somando rateios entre unidades/tipos. Achado
    # bateria A v0.3.5: agente sem essa agregação interpretava linhas com
    # mesmo numeroItem como duplicação.
    items_raw = payload.get("resultado") or []
    resumo: dict[str, dict[str, Any]] = {}
    for r in items_raw:
        ni = r.get("numeroItem")
        if ni is None:
            continue
        ni_key = str(ni)
        if ni_key not in resumo:
            resumo[ni_key] = {
                "numeroItem": ni_key,
                "rateios": 0,
                "unidades": [],
                "quantidade_registrada_total": 0.0,
                "quantidade_empenhada_total": 0.0,
                "saldo_empenho_total": 0.0,
            }
        agg = resumo[ni_key]
        agg["rateios"] += 1
        unidade_label = r.get("unidade") or ""
        tipo = r.get("tipo") or ""
        agg["unidades"].append(f"{unidade_label} [{tipo}]" if tipo else unidade_label)
        for campo_origem, campo_destino in (
            ("quantidadeRegistrada", "quantidade_registrada_total"),
            ("quantidadeEmpenhada", "quantidade_empenhada_total"),
            ("saldoEmpenho", "saldo_empenho_total"),
        ):
            v = r.get(campo_origem)
            if isinstance(v, (int, float)):
                agg[campo_destino] += float(v)

    for agg in resumo.values():
        for k in (
            "quantidade_registrada_total",
            "quantidade_empenhada_total",
            "saldo_empenho_total",
        ):
            agg[k] = round(agg[k], 4)

    payload["resumo_por_item"] = list(resumo.values())
    payload["_aviso_estrutura"] = (
        "O `resultado` traz linhas por (numeroItem, unidade, tipo) — "
        "quando o mesmo numeroItem aparece N vezes, são N alocações "
        "distintas (gerenciadora + participantes). Use `resumo_por_item` "
        "para o saldo agregado por item."
    ) if any(r.get("rateios", 0) > 1 for r in resumo.values()) else None

    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_arp_adesoes_item(
    numero_ata: Annotated[
        str,
        Field(description="Número simples da ata (ex.: '00001/2024')."),
    ],
    unidade_gerenciadora: Annotated[
        int,
        Field(description="Código da UASG gerenciadora da ata."),
    ],
    numero_item: Annotated[
        int,
        Field(description="Número do item dentro da ata."),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista adesões (caronas) já realizadas a uma ARP.

    Endpoint Dados Abertos `/modulo-arp/5_consultarAdesoesItem`. Mostra
    quem aderiu e com que quantidade — indica nível de demanda e quanto
    ainda resta no limite legal de adesões.

    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck("arp_adesoes", numero_ata, unidade_gerenciadora, numero_item, pagina, tamanho_pagina)
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-arp/5_consultarAdesoesItem",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            numeroAta=numero_ata,
            unidadeGerenciadora=unidade_gerenciadora,
            numeroItem=numero_item,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


# ============================================================================
# Atas — PNCP (federa estados e municípios)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_atas_listar(
    data_inicial: Annotated[
        date, Field(description=desc(PNCPListarAtasInput, "data_inicial"))
    ],
    data_final: Annotated[
        date, Field(description=desc(PNCPListarAtasInput, "data_final"))
    ],
    cnpj_orgao: Annotated[
        str | None,
        Field(default=None, description=desc(PNCPListarAtasInput, "cnpj_orgao")),
    ] = None,
    pagina: Annotated[int, Field(description=desc(PNCPListarAtasInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarAtasInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista atas registradas no PNCP no período (federal + estadual + municipal).

    Endpoint PNCP `/v1/atas`. Permite encontrar atas de qualquer ente da
    federação — mais amplo que Dados Abertos (só federal SISG).

    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = (
        "".join(c for c in cnpj_orgao if c.isdigit()) if cnpj_orgao else None
    )
    key = _ck(
        "pncp_atas",
        data_inicial,
        data_final,
        cnpj_clean,
        pagina,
        tamanho_pagina,
    )
    cached = await _atas_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataInicial": format_date(data_inicial, "pncp"),
        "dataFinal": format_date(data_final, "pncp"),
    }
    if cnpj_clean:
        filtros["cnpj"] = cnpj_clean

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/atas",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _atas_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
