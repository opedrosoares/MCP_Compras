"""Tools de contratações: Lei 14.133/2021 + regimes legados (Lei 8.666 e RDC).

Endpoints cobertos (Dados Abertos):
- /modulo-contratacoes/1_consultarContratacoes_PNCP_14133       (+ 1.1 por id)
- /modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133  (+ 2.1 por id)
- /modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133 (+ 3.1)
- /modulo-legado/1_consultarLicitacao                            (+ 1.1 por id)
- /modulo-legado/2_consultarItemLicitacao                        (+ 2.1 por id)
- /modulo-legado/3_consultarPregoes                              (+ 3.1 por id)
- /modulo-legado/5_consultarComprasSemLicitacao                  (dispensa/inexigibilidade)
- /modulo-legado/7_consultarRdc                                  (RDC)

Cache TTL 15 min — contratações são publicadas continuamente, mas a janela
de filtro por data já estabiliza a maioria das consultas.
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
from compras_mcp.mcp_instance import mcp
from compras_mcp.schemas import (
    ConsultarContratacao14133Input,
    ListarContratacoes14133Input,
    ListarPaginadoInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    make_dados_abertos,
    with_latency,
)


_contratacoes_cache = cache_from_env(
    "CONTRATACOES", default_ttl=900, default_max_size=300
)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


# ============================================================================
# Lei 14.133/2021 (PNCP-aderente via Dados Abertos)
# ============================================================================


@mcp.tool
async def compras_contratacoes_14133_listar(
    data_inicial_publicacao: Annotated[
        date | None,
        Field(
            default=None,
            description=desc(ListarContratacoes14133Input, "data_inicial_publicacao"),
        ),
    ] = None,
    data_final_publicacao: Annotated[
        date | None,
        Field(
            default=None,
            description=desc(ListarContratacoes14133Input, "data_final_publicacao"),
        ),
    ] = None,
    codigo_uasg: Annotated[
        int | None,
        Field(default=None, description=desc(ListarContratacoes14133Input, "codigo_uasg")),
    ] = None,
    cnpj_orgao: Annotated[
        str | None,
        Field(default=None, description=desc(ListarContratacoes14133Input, "cnpj_orgao")),
    ] = None,
    codigo_modalidade_dados_abertos: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Código de modalidade na tabela do **Dados Abertos / SIASG** "
                "(NÃO é o cheat sheet do PNCP). Equivalências confirmadas em "
                "2026-05 por sweep empírico do endpoint:\n"
                "  3 = Concorrência Eletrônica (PNCP=4)\n"
                "  5 = Pregão Eletrônico (PNCP=6)\n"
                "  6 = Dispensa (PNCP=8)\n"
                "  7 = Inexigibilidade (PNCP=9)\n"
                "Demais códigos (1,2,4,8-13) retornam vazio neste endpoint. "
                "Para consultar usando o cheat sheet PNCP nativo, use "
                "`compras_pncp_contratacoes_publicacao`."
            ),
        ),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ListarContratacoes14133Input, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarContratacoes14133Input, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações da Lei 14.133 publicadas no PNCP (via Dados Abertos).

    Endpoint `/modulo-contratacoes/1_consultarContratacoes_PNCP_14133`.
    Cobre pregões eletrônicos, dispensas, inexigibilidades e demais
    modalidades da Nova Lei de Licitações no governo federal.

    **Atenção semântica**: o filtro `codigo_modalidade_dados_abertos` usa a
    tabela de modalidade do SIASG/Dados Abertos, NÃO o cheat sheet PNCP de
    `compras_pncp_modalidades`. Os payloads retornam ambos os campos
    (`codigoModalidade` do Dados Abertos e `modalidadeIdPncp` do PNCP) — use
    `modalidadeNome` para o nome amigável.

    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_limpo = (
        "".join(c for c in cnpj_orgao if c.isdigit()) if cnpj_orgao else None
    )
    key = _ck(
        "ct14133_listar",
        data_inicial_publicacao,
        data_final_publicacao,
        codigo_uasg,
        cnpj_limpo,
        codigo_modalidade_dados_abertos,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {}
    if data_inicial_publicacao is not None:
        filtros["dataPublicacaoPncpInicial"] = format_date(
            data_inicial_publicacao, "dados_abertos"
        )
    if data_final_publicacao is not None:
        filtros["dataPublicacaoPncpFinal"] = format_date(
            data_final_publicacao, "dados_abertos"
        )
    if codigo_uasg is not None:
        filtros["codigoUasg"] = codigo_uasg
    if cnpj_limpo:
        filtros["cnpjOrgao"] = cnpj_limpo
    if codigo_modalidade_dados_abertos is not None:
        filtros["codigoModalidade"] = codigo_modalidade_dados_abertos

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/1_consultarContratacoes_PNCP_14133",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_contratacoes_14133_consultar(
    id_contratacao: Annotated[
        int,
        Field(description=desc(ConsultarContratacao14133Input, "id_contratacao")),
    ],
) -> dict[str, Any]:
    """Consulta uma contratação 14.133 pelo id interno.

    Endpoint `/modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id`.
    Devolve detalhes completos: objeto, valor estimado, modalidade,
    instrumento convocatório, status no PNCP.

    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck("ct14133_consultar", id_contratacao)
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id",
            pagina=1,
            tamanho_pagina=1,
            tipo="C",
            codigo=id_contratacao,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": id_contratacao,
        "contratacao": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_contratacoes_14133_itens_listar(
    data_inicial_inclusao: Annotated[
        date,
        Field(description="Data inicial de inclusão dos itens no PNCP (YYYY-MM-DD)."),
    ],
    data_final_inclusao: Annotated[
        date,
        Field(description="Data final de inclusão dos itens (YYYY-MM-DD)."),
    ],
    pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de contratações 14.133 incluídos no período.

    Endpoint `/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133`.
    Útil para descobrir o que foi licitado em uma janela específica.
    """
    started = time.perf_counter()
    key = _ck(
        "ct14133_itens",
        data_inicial_inclusao,
        data_final_inclusao,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            dataInclusaoPncpInicial=format_date(data_inicial_inclusao, "dados_abertos"),
            dataInclusaoPncpFinal=format_date(data_final_inclusao, "dados_abertos"),
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_contratacoes_14133_itens_por_contratacao(
    id_contratacao: Annotated[
        int,
        Field(description=desc(ConsultarContratacao14133Input, "id_contratacao")),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de uma contratação 14.133 específica.

    Endpoint `/modulo-contratacoes/2.1_consultarItensContratacoes_PNCP_14133_Id`.
    """
    started = time.perf_counter()
    key = _ck("ct14133_itens_por_ct", id_contratacao, pagina, tamanho_pagina)
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/2.1_consultarItensContratacoes_PNCP_14133_Id",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            tipo="C",
            codigo=id_contratacao,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_contratacoes_14133_resultados_listar(
    data_inicial_resultado: Annotated[
        date,
        Field(description="Data inicial do resultado/homologação (YYYY-MM-DD)."),
    ],
    data_final_resultado: Annotated[
        date,
        Field(description="Data final do resultado/homologação (YYYY-MM-DD)."),
    ],
    pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista resultados (homologações) de itens 14.133 no período.

    Endpoint `/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133`.
    Devolve fornecedor vencedor, valor adjudicado e quantitativo homologado —
    fonte primária de preço praticado para o ETP.
    """
    started = time.perf_counter()
    key = _ck(
        "ct14133_resultados",
        data_inicial_resultado,
        data_final_resultado,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            dataResultadoPncpInicial=format_date(data_inicial_resultado, "dados_abertos"),
            dataResultadoPncpFinal=format_date(data_final_resultado, "dados_abertos"),
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_contratacoes_14133_resultados_por_contratacao(
    id_contratacao: Annotated[
        int,
        Field(description=desc(ConsultarContratacao14133Input, "id_contratacao")),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista resultados de uma contratação 14.133 específica."""
    started = time.perf_counter()
    key = _ck("ct14133_res_por_ct", id_contratacao, pagina, tamanho_pagina)
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/3.1_consultarResultadoItensContratacoes_PNCP_14133_Id",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            tipo="C",
            codigo=id_contratacao,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


# ============================================================================
# Regime Legado — Lei 8.666 e RDC
# ============================================================================


@mcp.tool
async def compras_legado_licitacoes_listar(
    data_publicacao_inicial: Annotated[
        date,
        Field(description="Data inicial de publicação (YYYY-MM-DD). Obrigatório no upstream."),
    ],
    data_publicacao_final: Annotated[
        date,
        Field(description="Data final de publicação (YYYY-MM-DD). Obrigatório no upstream."),
    ],
    modalidade: Annotated[
        int | None,
        Field(
            default=None,
            description="Código de modalidade SIASG (opcional).",
        ),
    ] = None,
    numero_aviso: Annotated[
        int | None, Field(default=None, description="Número do aviso (opcional).")
    ] = None,
    pertence14133: Annotated[
        bool | None,
        Field(default=None, description="Filtrar somente processos vinculados à Lei 14.133."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista licitações do regime legado (Lei 8.666/93).

    Endpoint `/modulo-legado/1_consultarLicitacao`. **Bug upstream
    confirmado**: o filtro `uasg`, embora documentado no swagger oficial,
    retorna HTTP 400 ("Erro ao efetuar a consulta") porque o atributo não
    existe no modelo Hibernate da view (`TbVwLicitacao`). Por isso este
    parâmetro foi removido da assinatura.

    Workaround se você precisar filtrar por UASG: liste sem filtro, depois
    filtre client-side pelo campo `uasg` do resultado.
    """
    started = time.perf_counter()
    key = _ck(
        "legado_lic",
        data_publicacao_inicial,
        data_publicacao_final,
        modalidade,
        numero_aviso,
        pertence14133,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "data_publicacao_inicial": format_date(data_publicacao_inicial, "dados_abertos"),
        "data_publicacao_final": format_date(data_publicacao_final, "dados_abertos"),
    }
    if modalidade is not None:
        filtros["modalidade"] = modalidade
    if numero_aviso is not None:
        filtros["numero_aviso"] = numero_aviso
    if pertence14133 is not None:
        filtros["pertence14133"] = str(pertence14133).lower()

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/1_consultarLicitacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_legado_licitacao_consultar(
    id_compra: Annotated[
        str,
        Field(description="ID da compra no SIASG (string, retornado em `compras_legado_licitacoes_listar`)."),
    ],
) -> dict[str, Any]:
    """Consulta uma licitação legado pelo id_compra.

    Endpoint `/modulo-legado/1.1_consultarLicitacao_Id`. Upstream exige
    `id_compra` (string), não um `id` numérico.
    """
    started = time.perf_counter()
    key = _ck("legado_lic_consultar", id_compra)
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/1.1_consultarLicitacao_Id",
            pagina=1,
            tamanho_pagina=10,
            id_compra=id_compra,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": id_compra,
        "licitacao": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_legado_itens_licitacao_listar(
    modalidade: Annotated[
        int,
        Field(description="Código de modalidade SIASG (obrigatório). Ex.: 5=Pregão, 6=Dispensa."),
    ],
    uasg: Annotated[
        int | None, Field(default=None, description="Código UASG (opcional).")
    ] = None,
    numero_aviso: Annotated[
        int | None, Field(default=None, description="Número do aviso (opcional).")
    ] = None,
    codigo_item_material: Annotated[
        int | None, Field(default=None, description="Código CATMAT (opcional).")
    ] = None,
    codigo_item_servico: Annotated[
        int | None, Field(default=None, description="Código CATSER (opcional).")
    ] = None,
    cnpj_fornecedor: Annotated[
        str | None, Field(default=None, description="CNPJ do fornecedor (opcional).")
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de licitações legado (`/modulo-legado/2_consultarItemLicitacao`).

    Upstream exige `modalidade` obrigatório. Filtros opcionais: `uasg`,
    `numero_aviso`, `codigo_item_material/servico`, `cnpj_fornecedor`.
    """
    started = time.perf_counter()
    cnpj_clean = (
        "".join(c for c in cnpj_fornecedor if c.isdigit()) if cnpj_fornecedor else None
    )
    filtros: dict[str, Any] = {"modalidade": modalidade}
    if uasg is not None:
        filtros["uasg"] = uasg
    if numero_aviso is not None:
        filtros["numero_aviso"] = numero_aviso
    if codigo_item_material is not None:
        filtros["codigo_item_material"] = codigo_item_material
    if codigo_item_servico is not None:
        filtros["codigo_item_servico"] = codigo_item_servico
    if cnpj_clean:
        filtros["cnpj_fornecedor"] = cnpj_clean

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/2_consultarItemLicitacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    return with_latency(payload, started)


@mcp.tool
async def compras_legado_pregoes_listar(
    dt_data_edital_inicial: Annotated[
        date,
        Field(description="Data inicial do edital (YYYY-MM-DD). Obrigatório."),
    ],
    dt_data_edital_final: Annotated[
        date,
        Field(description="Data final do edital (YYYY-MM-DD). Obrigatório."),
    ],
    numero: Annotated[
        int | None, Field(default=None, description="Número do pregão (opcional).")
    ] = None,
    ds_tipo_pregao_compra: Annotated[
        str | None,
        Field(default=None, description="Tipo do pregão de compra (string upstream)."),
    ] = None,
    pertence14133: Annotated[
        bool | None,
        Field(default=None, description="Filtrar pregões vinculados à Lei 14.133."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista pregões eletrônicos do regime legado.

    Endpoint `/modulo-legado/3_consultarPregoes`. **Bug upstream
    confirmado**: os filtros `co_uasg` e `co_orgao`, embora documentados
    no swagger, retornam HTTP 400 com erro Hibernate
    `Could not resolve attribute 'TbVwPregaoId.coUasg'` porque os atributos
    não existem no modelo da view. Por isso ambos foram removidos da
    assinatura.

    Workaround para filtrar por UASG: chame sem filtro e filtre client-side
    pelos campos `coUasg`/`coOrgao` do resultado.
    """
    started = time.perf_counter()
    key = _ck(
        "legado_preg",
        dt_data_edital_inicial,
        dt_data_edital_final,
        numero,
        ds_tipo_pregao_compra,
        pertence14133,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dt_data_edital_inicial": format_date(dt_data_edital_inicial, "dados_abertos"),
        "dt_data_edital_final": format_date(dt_data_edital_final, "dados_abertos"),
    }
    if numero is not None:
        filtros["numero"] = numero
    if ds_tipo_pregao_compra:
        filtros["ds_tipo_pregao_compra"] = ds_tipo_pregao_compra
    if pertence14133 is not None:
        filtros["pertence14133"] = str(pertence14133).lower()

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/3_consultarPregoes",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_legado_compras_sem_licitacao(
    dt_ano_aviso: Annotated[
        int,
        Field(description="Ano do aviso (ex.: 2024). Obrigatório no upstream."),
    ],
    co_uasg: Annotated[
        int | None, Field(default=None, description="Código UASG (opcional).")
    ] = None,
    co_orgao: Annotated[
        int | None, Field(default=None, description="Código do órgão (opcional).")
    ] = None,
    co_orgao_superior: Annotated[
        int | None, Field(default=None, description="Código do órgão superior (opcional).")
    ] = None,
    nu_aviso_licitacao: Annotated[
        int | None, Field(default=None, description="Número do aviso de licitação.")
    ] = None,
    co_modalidade_licitacao: Annotated[
        int | None, Field(default=None, description="Código da modalidade SIASG.")
    ] = None,
    pertence14133: Annotated[
        bool | None,
        Field(default=None, description="Vincula à Lei 14.133."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista compras sem licitação (dispensa/inexigibilidade) do regime legado.

    Endpoint `/modulo-legado/5_consultarComprasSemLicitacao`. **Upstream
    exige `dt_ano_aviso`** (ano inteiro, ex.: 2024) — não janela de datas.
    """
    started = time.perf_counter()
    filtros: dict[str, Any] = {"dt_ano_aviso": dt_ano_aviso}
    if co_uasg is not None:
        filtros["co_uasg"] = co_uasg
    if co_orgao is not None:
        filtros["co_orgao"] = co_orgao
    if co_orgao_superior is not None:
        filtros["co_orgao_superior"] = co_orgao_superior
    if nu_aviso_licitacao is not None:
        filtros["nu_aviso_licitacao"] = nu_aviso_licitacao
    if co_modalidade_licitacao is not None:
        filtros["co_modalidade_licitacao"] = co_modalidade_licitacao
    if pertence14133 is not None:
        filtros["pertence14133"] = str(pertence14133).lower()

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/5_consultarComprasSemLicitacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    return with_latency(payload, started)


@mcp.tool
async def compras_legado_rdc_listar(
    data_publicacao_min: Annotated[
        date,
        Field(description="Data MÍNIMA de publicação (YYYY-MM-DD). Obrigatório."),
    ],
    data_publicacao_max: Annotated[
        date,
        Field(description="Data MÁXIMA de publicação (YYYY-MM-DD). Obrigatório."),
    ],
    uasg: Annotated[
        int | None, Field(default=None, description="Código UASG (opcional).")
    ] = None,
    orgao: Annotated[
        int | None, Field(default=None, description="Código do órgão (opcional).")
    ] = None,
    uf_uasg: Annotated[
        str | None, Field(default=None, description="UF da UASG (sigla, ex.: 'DF').")
    ] = None,
    modalidade: Annotated[
        int | None, Field(default=None, description="Código de modalidade.")
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações pelo RDC (Regime Diferenciado de Contratações).

    Endpoint `/modulo-legado/7_consultarRdc`. **Upstream usa
    `data_publicacao_min/max`** (note `min`/`max`, não `inicial`/`final`).
    RDC foi usado principalmente para obras dos megaeventos e da Copa —
    relevância residual hoje.
    """
    started = time.perf_counter()
    filtros: dict[str, Any] = {
        "data_publicacao_min": format_date(data_publicacao_min, "dados_abertos"),
        "data_publicacao_max": format_date(data_publicacao_max, "dados_abertos"),
    }
    if uasg is not None:
        filtros["uasg"] = uasg
    if orgao is not None:
        filtros["orgao"] = orgao
    if uf_uasg:
        filtros["uf_uasg"] = uf_uasg.upper()
    if modalidade is not None:
        filtros["modalidade"] = modalidade

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/7_consultarRdc",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    return with_latency(payload, started)
