"""Tools do PNCP — Portal Nacional de Contratações Públicas.

O PNCP é a fonte oficial Lei 14.133/2021 de divulgação obrigatória.
Diferente do Dados Abertos (governo federal SISG), o PNCP cobre **todos**
os entes da federação — federal, estadual e municipal.

Endpoints cobertos:
- /v1/contratacoes/publicacao              (publicação de editais/avisos)
- /v1/contratacoes/proposta                (com prazo de proposta aberto)
- /v1/contratacoes/atualizacao             (alterações em janela)
- /v1/orgaos/{cnpj}/compras/{ano}/{seq}    (contratação singular)
- /v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens
- /v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{n}/resultados
- /v1/contratos                            (contratos publicados)
- /v1/orgaos/{cnpj}/contratos/{ano}/{seq}  (contrato singular)

Cache TTL curto (15 min). Inclui também tool local `modalidades` (cheat-sheet).
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.config import get_settings
from compras_mcp.errors import (
    ComprasHTTPError,
    ComprasNotFoundError,
    ComprasServerError,
)
from compras_mcp.esfera import (
    ESFERA_VALORES,
    aplicar_filtro_esfera_no_envelope,
)
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    ListarPaginadoInput,
    PNCPListarContratacoesInput,
    PNCPListarPropostasAbertasInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_pncp,
    make_pncp,
    with_latency,
)


_pncp_cache = cache_from_env("PNCP", default_ttl=900, default_max_size=300)


_DESC_ESFERA = (
    "Filtro opcional de esfera federativa (`federal`, `estadual`, `municipal` "
    "ou `distrital`). Aplicado client-side sobre a página retornada — útil "
    "para recortar a lista, mas note que `_total_registros` continua "
    "refletindo o total **sem** filtro de esfera."
)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _so_digitos(s: str | None) -> str | None:
    if s is None:
        return None
    return "".join(c for c in s if c.isdigit())


def _resposta_pncp_singular_404(
    endpoint_path: str, codigo_consultado: dict[str, Any], status: int = 404
) -> dict[str, Any]:
    """Payload graceful para tools de consulta singular PNCP quando upstream
    retorna 4xx (registro inexistente / parâmetros inválidos).

    Mesmo padrão da v0.2.8 aplicado a `compras_uasg_*` e v0.2.9 a
    `compras_pncp_orgao_unidades`: em vez de propagar exception crua,
    devolve payload informativo + sugestões.

    Distingue 400 (request malformado) de 404 (recurso inexistente) na
    mensagem — achado bateria A v0.3.5 reportava confusão de linguagem
    quando status era 400 mas diagnóstico falava em "registro não
    encontrado".
    """
    if status == 400:
        diagnostico = (
            "Requisição malformada (HTTP 400). O upstream rejeitou os "
            "parâmetros — não é caso de 'registro não encontrado'. Causas "
            "comuns: "
            "(1) parâmetro com formato errado (ex.: ano não-numérico, "
            "CNPJ com pontuação onde upstream espera só dígitos, "
            "sequencial < 1); "
            "(2) tipo errado: passar `idCompra` em endpoint que espera "
            "`(cnpj, ano, sequencial)`; "
            "(3) combinação de filtros mutuamente exclusivos."
        )
        alternativas = [
            "Confira o esquema do endpoint chamado abaixo.",
            "Para descobrir sequenciais e CNPJs válidos: "
            "`compras_pncp_contratacoes_publicacao(data_inicial, data_final, "
            "codigo_modalidade)` com janela curta.",
        ]
    elif status == 404:
        diagnostico = (
            "Recurso não encontrado (HTTP 404). A combinação solicitada "
            "não existe no PNCP. Causas comuns: "
            "(1) combinação CNPJ+ano+sequencial inexistente, "
            "(2) órgão não publica no PNCP (só federal SISG), "
            "(3) sequencial fora do range de quem aquele CNPJ publicou."
        )
        alternativas = [
            "Liste antes para descobrir sequenciais válidos: "
            "`compras_pncp_contratacoes_publicacao(data_inicial, data_final, "
            "codigo_modalidade)` com janela curta + filtro por `cnpj_orgao`.",
            "Para contratos federais SISG: `compras_contratos_listar(codigo_orgao, "
            "data_vigencia_inicial_min, data_vigencia_inicial_max)`.",
        ]
    else:
        diagnostico = (
            f"Upstream PNCP respondeu HTTP {status}. Erro não-categorizado: "
            "verifique os parâmetros e tente novamente."
        )
        alternativas = []

    return {
        "encontrado": False,
        "codigo_consultado": codigo_consultado,
        "_erro_upstream": {
            "endpoint": f"pncp{endpoint_path}",
            "status": status,
            "diagnostico": diagnostico,
            "alternativas": alternativas,
        },
        "_cache_hit": False,
    }


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratacoes_publicacao(
    data_inicial: Annotated[
        date, Field(description=desc(PNCPListarContratacoesInput, "data_inicial"))
    ],
    data_final: Annotated[
        date, Field(description=desc(PNCPListarContratacoesInput, "data_final"))
    ],
    codigo_modalidade: Annotated[
        int,
        Field(description=desc(PNCPListarContratacoesInput, "codigo_modalidade")),
    ],
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(PNCPListarContratacoesInput, "uf")),
    ] = None,
    codigo_municipio_ibge: Annotated[
        int | None,
        Field(
            default=None,
            description=desc(PNCPListarContratacoesInput, "codigo_municipio_ibge"),
        ),
    ] = None,
    cnpj_orgao: Annotated[
        str | None,
        Field(
            default=None, description=desc(PNCPListarContratacoesInput, "cnpj_orgao")
        ),
    ] = None,
    esfera: Annotated[
        str | None,
        Field(default=None, description=_DESC_ESFERA),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PNCPListarContratacoesInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarContratacoesInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações publicadas no PNCP no período.

    Endpoint `/v1/contratacoes/publicacao`. Cobre todos os entes da
    federação. Modalidades comuns: 6=Pregão Eletrônico, 8=Dispensa,
    9=Inexigibilidade, 4=Concorrência Eletrônica.

    O filtro `esfera` (federal/estadual/municipal/distrital) é aplicado
    client-side sobre a página retornada. Janela máxima por consulta: ~30
    dias. Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj_orgao)
    if esfera and esfera.lower() not in ESFERA_VALORES:
        raise ValueError(
            f"esfera inválida: {esfera!r}. Use {', '.join(ESFERA_VALORES)}."
        )
    key = _ck(
        "pncp_ct_pub",
        data_inicial,
        data_final,
        codigo_modalidade,
        uf,
        codigo_municipio_ibge,
        cnpj_clean,
        esfera,
        pagina,
        tamanho_pagina,
    )
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataInicial": format_date(data_inicial, "pncp"),
        "dataFinal": format_date(data_final, "pncp"),
        "codigoModalidadeContratacao": codigo_modalidade,
    }
    if uf:
        filtros["uf"] = uf.upper()
    if codigo_municipio_ibge is not None:
        filtros["codigoMunicipioIbge"] = codigo_municipio_ibge
    if cnpj_clean:
        filtros["cnpj"] = cnpj_clean

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/contratacoes/publicacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload = aplicar_filtro_esfera_no_envelope(payload, esfera)
    payload["_cache_hit"] = False
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratacoes_proposta(
    data_final: Annotated[
        date,
        Field(description=desc(PNCPListarPropostasAbertasInput, "data_final")),
    ],
    codigo_modalidade: Annotated[
        int,
        Field(
            description=desc(PNCPListarPropostasAbertasInput, "codigo_modalidade")
        ),
    ],
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(PNCPListarPropostasAbertasInput, "uf")),
    ] = None,
    esfera: Annotated[
        str | None,
        Field(default=None, description=_DESC_ESFERA),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PNCPListarPropostasAbertasInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int,
        Field(
            description=desc(PNCPListarPropostasAbertasInput, "tamanho_pagina")
        ),
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações com prazo de proposta aberto no PNCP.

    Endpoint `/v1/contratacoes/proposta`. Útil para mapear oportunidades
    abertas para fornecedores ou para identificar contratações em curso
    em órgãos similares. Filtro `esfera` opcional client-side.

    Cache 15 min.
    """
    started = time.perf_counter()
    if esfera and esfera.lower() not in ESFERA_VALORES:
        raise ValueError(
            f"esfera inválida: {esfera!r}. Use {', '.join(ESFERA_VALORES)}."
        )
    key = _ck(
        "pncp_proposta",
        data_final,
        codigo_modalidade,
        uf,
        esfera,
        pagina,
        tamanho_pagina,
    )
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataFinal": format_date(data_final, "pncp"),
        "codigoModalidadeContratacao": codigo_modalidade,
    }
    if uf:
        filtros["uf"] = uf.upper()

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/contratacoes/proposta",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload = aplicar_filtro_esfera_no_envelope(payload, esfera)
    payload["_cache_hit"] = False
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratacoes_atualizacao(
    data_inicial: Annotated[
        date, Field(description="Data inicial de atualização (YYYY-MM-DD).")
    ],
    data_final: Annotated[
        date, Field(description="Data final de atualização (YYYY-MM-DD).")
    ],
    codigo_modalidade: Annotated[
        int,
        Field(description=desc(PNCPListarContratacoesInput, "codigo_modalidade")),
    ],
    esfera: Annotated[
        str | None,
        Field(default=None, description=_DESC_ESFERA),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PNCPListarContratacoesInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarContratacoesInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações alteradas no período (PNCP).

    Endpoint `/v1/contratacoes/atualizacao`. Útil para monitoramento:
    descobrir editais que sofreram retificações/republicações. Aceita
    filtro `esfera` client-side.

    Cache 15 min.
    """
    started = time.perf_counter()
    if esfera and esfera.lower() not in ESFERA_VALORES:
        raise ValueError(
            f"esfera inválida: {esfera!r}. Use {', '.join(ESFERA_VALORES)}."
        )
    key = _ck(
        "pncp_ct_atu",
        data_inicial,
        data_final,
        codigo_modalidade,
        esfera,
        pagina,
        tamanho_pagina,
    )
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/contratacoes/atualizacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            dataInicial=format_date(data_inicial, "pncp"),
            dataFinal=format_date(data_final, "pncp"),
            codigoModalidadeContratacao=codigo_modalidade,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload = aplicar_filtro_esfera_no_envelope(payload, esfera)
    payload["_cache_hit"] = False
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratacao_por_orgao(
    cnpj: Annotated[
        str,
        Field(
            description="CNPJ do órgão (14 dígitos, com ou sem pontuação).",
            min_length=11,
            max_length=20,
        ),
    ],
    ano: Annotated[int, Field(description="Ano da contratação (4 dígitos).")],
    sequencial: Annotated[
        int,
        Field(description="Sequencial da contratação dentro do órgão e ano."),
    ],
) -> dict[str, Any]:
    """Consulta uma contratação específica pelo CNPJ + ano + sequencial.

    Endpoint `/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}`. Devolve
    cabeçalho completo da contratação.

    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj)
    key = _ck("pncp_ct_orgao", cnpj_clean, ano, sequencial)
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_pncp(get_settings()) as client:
            resp = await client.get_resource(
                f"/v1/orgaos/{cnpj_clean}/compras/{ano}/{sequencial}"
            )
    except (ComprasNotFoundError, ComprasHTTPError, ComprasServerError) as e:
        status = 404 if isinstance(e, ComprasNotFoundError) else 400
        return with_latency(
            _resposta_pncp_singular_404(
                f"/v1/orgaos/{cnpj_clean}/compras/{ano}/{sequencial}",
                {"cnpj": cnpj_clean, "ano": ano, "sequencial": sequencial},
                status=status,
            ),
            started,
        )

    payload: dict[str, Any] = {
        "encontrada": bool(resp),
        "cnpj_consultado": cnpj_clean,
        "ano_consultado": ano,
        "sequencial_consultado": sequencial,
        "contratacao": resp if resp else None,
        "_cache_hit": False,
    }
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratacao_itens(
    cnpj: Annotated[
        str,
        Field(description="CNPJ do órgão.", min_length=11, max_length=20),
    ],
    ano: Annotated[int, Field(description="Ano da contratação.")],
    sequencial: Annotated[int, Field(description="Sequencial da contratação.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de uma contratação no PNCP.

    Endpoint `/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens`.
    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj)
    key = _ck("pncp_itens", cnpj_clean, ano, sequencial, pagina, tamanho_pagina)
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_pncp(get_settings()) as client:
            resp = await client.list_resource(
                f"/v1/orgaos/{cnpj_clean}/compras/{ano}/{sequencial}/itens",
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
            )
    except (ComprasNotFoundError, ComprasHTTPError, ComprasServerError) as e:
        status = 404 if isinstance(e, ComprasNotFoundError) else 400
        return with_latency(
            _resposta_pncp_singular_404(
                f"/v1/orgaos/{cnpj_clean}/compras/{ano}/{sequencial}/itens",
                {"cnpj": cnpj_clean, "ano": ano, "sequencial": sequencial},
                status=status,
            ),
            started,
        )

    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratacao_item_resultados(
    cnpj: Annotated[
        str,
        Field(description="CNPJ do órgão.", min_length=11, max_length=20),
    ],
    ano: Annotated[int, Field(description="Ano da contratação.")],
    sequencial: Annotated[int, Field(description="Sequencial.")],
    numero_item: Annotated[int, Field(description="Número do item dentro da contratação.")],
) -> dict[str, Any]:
    """Lista resultados (vencedores) de um item específico de contratação no PNCP.

    Endpoint `/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens/{n}/resultados`.
    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj)
    key = _ck("pncp_item_result", cnpj_clean, ano, sequencial, numero_item)
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_pncp(get_settings()) as client:
            resp = await client.get_resource(
                f"/v1/orgaos/{cnpj_clean}/compras/{ano}/{sequencial}"
                f"/itens/{numero_item}/resultados"
            )
    except (ComprasNotFoundError, ComprasHTTPError, ComprasServerError) as e:
        status = 404 if isinstance(e, ComprasNotFoundError) else 400
        return with_latency(
            _resposta_pncp_singular_404(
                f"/v1/orgaos/{cnpj_clean}/compras/{ano}/{sequencial}"
                f"/itens/{numero_item}/resultados",
                {
                    "cnpj": cnpj_clean,
                    "ano": ano,
                    "sequencial": sequencial,
                    "numero_item": numero_item,
                },
                status=status,
            ),
            started,
        )

    if isinstance(resp, list):
        payload: dict[str, Any] = {
            "resultado": resp,
            "_total_registros": len(resp),
            "_cache_hit": False,
        }
    else:
        payload = {
            "resultado": resp.get("data") if isinstance(resp, dict) else [],
            "_cache_hit": False,
        }
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contratos_listar(
    data_inicial: Annotated[
        date, Field(description="Data inicial de publicação do contrato (YYYY-MM-DD).")
    ],
    data_final: Annotated[
        date, Field(description="Data final (YYYY-MM-DD).")
    ],
    cnpj_orgao: Annotated[
        str | None,
        Field(default=None, description="CNPJ do órgão (14 dígitos)."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratos publicados no PNCP no período.

    Endpoint `/v1/contratos`. Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj_orgao)
    key = _ck(
        "pncp_contratos",
        data_inicial,
        data_final,
        cnpj_clean,
        pagina,
        tamanho_pagina,
    )
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataInicial": format_date(data_inicial, "pncp"),
        "dataFinal": format_date(data_final, "pncp"),
    }
    if cnpj_clean:
        filtros["cnpjOrgao"] = cnpj_clean

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/contratos",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_contrato_por_orgao(
    cnpj: Annotated[
        str,
        Field(description="CNPJ do órgão.", min_length=11, max_length=20),
    ],
    ano: Annotated[int, Field(description="Ano do contrato.")],
    sequencial: Annotated[int, Field(description="Sequencial do contrato.")],
) -> dict[str, Any]:
    """Consulta um contrato específico no PNCP.

    Endpoint `/v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}`. Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj)
    key = _ck("pncp_contr_orgao", cnpj_clean, ano, sequencial)
    cached = await _pncp_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_pncp(get_settings()) as client:
            resp = await client.get_resource(
                f"/v1/orgaos/{cnpj_clean}/contratos/{ano}/{sequencial}"
            )
    except (ComprasNotFoundError, ComprasHTTPError, ComprasServerError) as e:
        status = 404 if isinstance(e, ComprasNotFoundError) else 400
        return with_latency(
            _resposta_pncp_singular_404(
                f"/v1/orgaos/{cnpj_clean}/contratos/{ano}/{sequencial}",
                {"cnpj": cnpj_clean, "ano": ano, "sequencial": sequencial},
                status=status,
            ),
            started,
        )

    payload: dict[str, Any] = {
        "encontrado": bool(resp),
        "cnpj_consultado": cnpj_clean,
        "ano_consultado": ano,
        "sequencial_consultado": sequencial,
        "contrato": resp if resp else None,
        "_cache_hit": False,
    }
    await _pncp_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_modalidades() -> dict[str, Any]:
    """Cheat sheet local: códigos de modalidade de contratação do PNCP.

    Tool local (não chama upstream). Fonte: tabela oficial PNCP (Lei 14.133).

    **ATENÇÃO — duas tabelas em circulação no ecossistema Compras**:
    - `codigo` aqui (PNCP) é o usado em TODAS as tools `compras_pncp_*` e
      em `modalidadeIdPncp` no payload de retorno.
    - O Dados Abertos / SIASG usa uma enumeração diferente em
      `compras_contratacoes_14133_listar(codigo_modalidade_dados_abertos)`:
      campo `equivalente_dados_abertos` abaixo, ou None se a modalidade
      não estiver disponível naquele endpoint.
    """
    started = time.perf_counter()
    # Fonte única em `compras_mcp.dominio` — evita drift entre esta tool e
    # o resource `compras://referencia/modalidades-pncp`.
    from compras_mcp.dominio import MODALIDADES_PNCP

    payload = {
        "resultado": MODALIDADES_PNCP,
        "_total_registros": len(MODALIDADES_PNCP),
        "fonte": "Tabela oficial PNCP (Lei 14.133/2021)",
        "aviso": (
            "Para `compras_contratacoes_14133_listar` (Dados Abertos), use o "
            "valor de `equivalente_dados_abertos`. Para tools `compras_pncp_*`, "
            "use o `codigo`. Modalidades sem equivalente_dados_abertos só são "
            "consultáveis via PNCP."
        ),
        "_cache_hit": False,
    }
    return with_latency(payload, started)
