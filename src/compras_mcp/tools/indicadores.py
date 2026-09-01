"""Tools de indicadores de saúde da API Dados Abertos.

Endpoints cobertos:
- /modulo-indicadores/1_consultarIndicadoresConsolidados (Dados Abertos)
- /modulo-indicadores/2_consultarIndicadoresPorPeriodo   (Dados Abertos)

**Atenção**: na prática estes endpoints devolvem **métricas operacionais
da própria API** (total de requisições, taxa de sucesso, latência média,
volume de download, número de serviços disponíveis) — não indicadores de
compras públicas. Útil para monitorar disponibilidade do upstream e
construir status pages, **não** para enriquecer relatórios de ETP/TR.

Para indicadores reais de mercado público use:
- `compras_pncp_contratacoes_publicacao` (contagem por período + filtros)
- `compras_pgc_agregacao` (totais por órgão/ano)
- agregação manual de `compras_pesquisar_preco_*`

Cache TTL longo (1h).
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.config import get_settings
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.tools._helpers import (
    envelope_dados_abertos,
    make_dados_abertos,
    with_latency,
)


_indicadores_cache = cache_from_env(
    "INDICADORES", default_ttl=3600, default_max_size=100
)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_indicadores_consolidados(
    pagina: Annotated[int, Field(description="Página (1-based). Padrão 1.")] = 1,
    tamanho_pagina: Annotated[
        int,
        Field(
            description="Registros por página (default 50, máximo 500).",
            ge=1,
            le=500,
        ),
    ] = 50,
) -> dict[str, Any]:
    """Métricas operacionais consolidadas da API Dados Abertos.

    Endpoint `/modulo-indicadores/1_consultarIndicadoresConsolidados`.
    Retorna: total de serviços disponíveis, total de requisições no
    período, percentual de sucesso, latência média (ms), volume total
    e médio de download (GB). Útil para diagnóstico/observabilidade,
    **não** para indicadores de mercado público (ver docstring do módulo).

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("ind_consol", pagina, tamanho_pagina)
    cached = await _indicadores_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-indicadores/1_consultarIndicadoresConsolidados",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _indicadores_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_indicadores_por_periodo(
    ano: Annotated[
        int,
        Field(
            description="Ano de referência dos indicadores (4 dígitos).",
            ge=2010,
            le=2100,
        ),
    ],
    mes: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Mês (1-12). Se omitido, agrega o ano inteiro. Se informado, "
                "filtra apenas o mês especificado."
            ),
            ge=1,
            le=12,
        ),
    ] = None,
    pagina: Annotated[int, Field(description="Página (1-based).")] = 1,
    tamanho_pagina: Annotated[
        int,
        Field(description="Registros por página.", ge=1, le=500),
    ] = 50,
) -> dict[str, Any]:
    """Métricas operacionais da API por período (ano/mês).

    Endpoint Dados Abertos `/modulo-indicadores/2_consultarIndicadoresPorPeriodo`.
    Retorna métricas de USO da API (requisições, latência, downloads),
    não dados de compras. Útil para análise temporal de disponibilidade
    do upstream.

    Cache 1h.
    """
    started = time.perf_counter()
    key = _ck("ind_per", ano, mes, pagina, tamanho_pagina)
    cached = await _indicadores_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {"ano": ano}
    if mes is not None:
        filtros["mes"] = mes

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-indicadores/2_consultarIndicadoresPorPeriodo",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _indicadores_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
