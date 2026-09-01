"""Tools de planejamento de contratações (PGC/PCA).

PGC = Plano de Gestão de Contratações (Dados Abertos, governo federal SISG)
PCA = Plano Anual de Contratações (PNCP, abrange todos os entes da Lei 14.133)

Endpoints cobertos:
- /modulo-pgc/{1,2,3}_consultarPgc*       (Dados Abertos)
- /modulo-pgc/{1.1,2.1,3.1}_consultar*_CSV (Dados Abertos — variantes CSV)
- /v1/pca/                                 (PNCP)
- /v1/pca/atualizacao                      (PNCP)

Uso para o analista: ver o que outros órgãos planejaram comprar do mesmo
item ajuda no dimensionamento, na referência de preço estimado e na busca
por intenções de compra (IRP).

Cache TTL médio (1h) — PCA/PGC mudam ao longo do ano, mas não a cada minuto.
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.config import get_settings
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    ListarPaginadoInput,
    ListarPGCInput,
    PNCPListarPCAInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    envelope_pncp,
    make_dados_abertos,
    make_pncp,
    with_latency,
)


_pca_cache = cache_from_env("PCA", default_ttl=3600, default_max_size=300)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


# ============================================================================
# PGC — Dados Abertos (governo federal SISG)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pgc_listar(
    ano: Annotated[int, Field(description=desc(ListarPGCInput, "ano"))],
    codigo_orgao: Annotated[
        int | None, Field(default=None, description=desc(ListarPGCInput, "codigo_orgao"))
    ] = None,
    codigo_uasg: Annotated[
        int | None, Field(default=None, description=desc(ListarPGCInput, "codigo_uasg"))
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPGCInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPGCInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de PGC (Plano de Gestão de Contratações) do governo federal.

    Endpoint Dados Abertos `/modulo-pgc/1_consultarPgcDetalhe`. Cada linha
    representa um item planejado: descrição, quantidade, valor unitário
    estimado, mês previsto de início e categoria de item.

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("pgc_listar", ano, codigo_orgao, codigo_uasg, pagina, tamanho_pagina)
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {"anoPcaProjetoCompra": ano}
    if codigo_orgao is not None:
        filtros["orgao"] = codigo_orgao
    if codigo_uasg is not None:
        filtros["codigoUasg"] = codigo_uasg

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-pgc/1_consultarPgcDetalhe",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pgc_por_catalogo(
    ano: Annotated[int, Field(description="Ano do PCA/PGC.")],
    tipo: Annotated[
        Literal["M", "S"],
        Field(description="'M' para CATMAT (material) ou 'S' para CATSER (serviço)."),
    ],
    codigo_item: Annotated[
        int,
        Field(
            description=(
                "Código do item no catálogo (CATMAT se tipo='M', CATSER se tipo='S')."
            ),
        ),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPGCInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPGCInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista todos os PGCs que incluem determinado item de catálogo (CATMAT/CATSER).

    Endpoint Dados Abertos `/modulo-pgc/2_consultarPgcDetalheCatalogo`.
    Útil para responder: "Quais órgãos planejaram comprar esse item este ano?
    Em que quantidade?". Insumo para ETP e benchmarking de quantitativos.

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("pgc_catalogo", ano, tipo, codigo_item, pagina, tamanho_pagina)
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-pgc/2_consultarPgcDetalheCatalogo",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            anoPcaProjetoCompra=ano,
            tipo=tipo,
            codigo=codigo_item,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pgc_agregacao(
    ano: Annotated[int, Field(description="Ano do PGC.")],
    codigo_orgao: Annotated[
        int,
        Field(
            description=(
                "Código do órgão (obrigatório nesta consulta — é a chave da agregação)."
            ),
        ),
    ],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Resumo agregado do PGC de um órgão num ano (totais por categoria).

    Endpoint Dados Abertos `/modulo-pgc/3_consultarPgcAgregacao`. Retorna
    contagens e valores totais por categoria/grupo, útil para diagnóstico
    rápido do volume planejado pelo órgão.

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("pgc_agregacao", ano, codigo_orgao, pagina, tamanho_pagina)
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-pgc/3_consultarPgcAgregacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            ano=ano,
            orgao=codigo_orgao,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pgc_listar_csv(
    ano: Annotated[int, Field(description=desc(ListarPGCInput, "ano"))],
    codigo_orgao: Annotated[
        int | None, Field(default=None, description=desc(ListarPGCInput, "codigo_orgao"))
    ] = None,
    codigo_uasg: Annotated[
        int | None, Field(default=None, description=desc(ListarPGCInput, "codigo_uasg"))
    ] = None,
) -> dict[str, Any]:
    """Versão CSV de `compras_pgc_listar` (mesmo dataset, formato planilha).

    Endpoint `/modulo-pgc/1.1_consultarPgcDetalhe_CSV`. Útil para colar no
    ETP ou planilhar localmente. Retorna o CSV no campo `csv` da resposta.
    """
    started = time.perf_counter()
    filtros: dict[str, Any] = {"anoPcaProjetoCompra": ano}
    if codigo_orgao is not None:
        filtros["orgao"] = codigo_orgao
    if codigo_uasg is not None:
        filtros["codigoUasg"] = codigo_uasg

    async with make_dados_abertos(get_settings()) as client:
        client_ = await client._ensure_client()
        resp = await client_.get(
            "/modulo-pgc/1.1_consultarPgcDetalhe_CSV", params=filtros
        )
        resp.raise_for_status()
        csv_text = resp.text
    payload = {
        "csv": csv_text,
        "linhas": csv_text.count("\n"),
        "bytes": len(csv_text.encode("utf-8")),
    }
    return with_latency(payload, started)


# ============================================================================
# PCA — PNCP (Lei 14.133, abrange estados e municípios)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_pca_listar(
    ano: Annotated[int, Field(description=desc(PNCPListarPCAInput, "ano"))],
    codigo_classificacao_superior: Annotated[
        int,
        Field(
            description=(
                "Código da classificação superior do item no catálogo. "
                "Obrigatório no endpoint PNCP. Para CATMAT use o código do grupo; "
                "para CATSER use o código da seção. Veja "
                "`compras_catmat_listar_grupos` ou `compras_catser_listar_secoes`."
            ),
        ),
    ],
    cnpj_orgao: Annotated[
        str | None,
        Field(default=None, description=desc(PNCPListarPCAInput, "cnpj_orgao")),
    ] = None,
    pagina: Annotated[int, Field(description=desc(PNCPListarPCAInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarPCAInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista PCAs (Planos Anuais de Contratações) no PNCP.

    Endpoint PNCP `/v1/pca/`. Diferente do PGC, o PCA da Lei 14.133 cobre
    federais + estaduais + municipais. Filtra por categoria do item
    (`codigo_classificacao_superior` é obrigatório no upstream).

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck(
        "pncp_pca",
        ano,
        codigo_classificacao_superior,
        cnpj_orgao,
        pagina,
        tamanho_pagina,
    )
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "anoPca": ano,
        "codigoClassificacaoSuperior": codigo_classificacao_superior,
    }
    if cnpj_orgao:
        filtros["cnpj"] = "".join(c for c in cnpj_orgao if c.isdigit())

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/pca/",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_pca_atualizacao(
    data_inicial: Annotated[
        date,
        Field(description="Data inicial do período de atualização (YYYY-MM-DD)."),
    ],
    data_final: Annotated[
        date,
        Field(description="Data final do período (YYYY-MM-DD). Janela máxima ~30 dias."),
    ],
    pagina: Annotated[int, Field(description=desc(PNCPListarPCAInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarPCAInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista PCAs atualizados num período (PNCP).

    Endpoint PNCP `/v1/pca/atualizacao`. Útil para monitoramento: descobrir
    quais órgãos revisaram seu PCA recentemente.

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("pncp_pca_atual", data_inicial, data_final, pagina, tamanho_pagina)
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/pca/atualizacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            dataInicio=format_date(data_inicial, "pncp"),
            dataFim=format_date(data_final, "pncp"),
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_pca_por_usuario(
    ano: Annotated[int, Field(description="Ano do PCA.")],
    id_usuario: Annotated[
        int,
        Field(
            description=(
                "ID interno de usuário/sistema integrador do PNCP. "
                "Obtido na documentação interna do órgão; raramente usado por analistas."
            ),
        ),
    ],
    pagina: Annotated[int, Field(description=desc(PNCPListarPCAInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarPCAInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista PCAs vinculados a um usuário/sistema integrador específico.

    Endpoint PNCP `/v1/pca/usuario`. Uso menos comum — geralmente o
    analista prefere `compras_pncp_pca_listar` com `cnpj_orgao`.

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("pncp_pca_user", ano, id_usuario, pagina, tamanho_pagina)
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/pca/usuario",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            anoPca=ano,
            idUsuario=id_usuario,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pncp_pca_por_classificacao_superior(
    ano: Annotated[int, Field(description="Ano do PCA.")],
    codigo_classificacao_superior: Annotated[
        int,
        Field(
            description=(
                "Código de classificação superior do item (categoria pai). "
                "Veja a tabela de classificação no manual do PNCP."
            ),
        ),
    ],
    pagina: Annotated[int, Field(description=desc(PNCPListarPCAInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PNCPListarPCAInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de PCA filtrados por categoria superior do item.

    Endpoint PNCP `/v1/pca/` com `codigoClassificacaoSuperior`. Permite
    agregar planejamentos por categoria (ex.: todos os itens de TI
    planejados para o ano).

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck(
        "pncp_pca_cls",
        ano,
        codigo_classificacao_superior,
        pagina,
        tamanho_pagina,
    )
    cached = await _pca_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_pncp(get_settings()) as client:
        resp = await client.list_resource(
            "/v1/pca/",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            anoPca=ano,
            codigoClassificacaoSuperior=codigo_classificacao_superior,
        )
    payload = envelope_pncp(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _pca_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
