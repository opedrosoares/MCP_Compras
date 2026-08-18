"""Helpers compartilhados pelas tools.

- `desc(Model, "campo")`: lê a description do Pydantic Field (SSOT).
- `measure_ms`: latência em ms para anexar como `_latency_ms` na resposta.
- `make_client(...)`: factories que respeitam Settings (timeout/retries/baseURL).
- `pagination_summary(...)`: insere `_proxima_pagina` e `_total_registros`
  consistentemente em tools `listar_*`.
"""

from __future__ import annotations

import time
from typing import Any

from compras_mcp.clients.comprasnet_contratos import ComprasnetContratosClient
from compras_mcp.clients.dados_abertos import DadosAbertosClient
from compras_mcp.clients.pncp import PNCPClient
from compras_mcp.clients.transparencia import TransparenciaClient
from compras_mcp.config import Settings, get_settings  # noqa: F401


def desc(model: type, field: str) -> str:
    """Lê a description do Pydantic Field (SSOT).

    Falha rápido em import-time se o autor esqueceu a description.
    """
    d = model.model_fields[field].description
    if not d:
        raise RuntimeError(
            f"{model.__name__}.{field} sem `description` em schemas.py "
            f"— exigida para tool MCP"
        )
    return d


def measure_ms(started: float) -> float:
    """Latência em ms com 1 casa decimal."""
    return round((time.perf_counter() - started) * 1000, 1)


def make_dados_abertos(settings: Settings) -> DadosAbertosClient:
    return DadosAbertosClient(
        base_url=settings.dados_abertos_base_url,
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
    )


def make_pncp(settings: Settings) -> PNCPClient:
    return PNCPClient(
        base_url=settings.pncp_base_url,
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
    )


def make_transparencia(settings: Settings, *, api_key: str | None = None) -> TransparenciaClient:
    return TransparenciaClient(
        base_url=settings.transparencia_base_url,
        api_key=api_key if api_key is not None else settings.transparencia_api_key,
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
    )


def make_comprasnet(settings: Settings) -> ComprasnetContratosClient:
    return ComprasnetContratosClient(
        base_url=settings.comprasnet_contratos_base_url,
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
    )


def envelope_dados_abertos(
    response: dict[str, Any],
    *,
    pagina_atual: int,
) -> dict[str, Any]:
    """Resposta padronizada para tools `listar_*` baseadas em Dados Abertos.

    Mantém o `resultado` original e adiciona metadados de paginação que o
    LLM usa para decidir continuar.
    """
    total_paginas = int(response.get("totalPaginas") or 0)
    total_registros = int(response.get("totalRegistros") or 0)
    proxima = pagina_atual + 1 if pagina_atual < total_paginas else None
    return {
        "resultado": response.get("resultado") or [],
        "_pagina_atual": pagina_atual,
        "_total_paginas": total_paginas,
        "_total_registros": total_registros,
        "_proxima_pagina": proxima,
    }


def envelope_pncp(
    response: dict[str, Any],
    *,
    pagina_atual: int,
) -> dict[str, Any]:
    """Resposta padronizada para tools `listar_*` baseadas em PNCP.

    PNCP usa `data` em vez de `resultado`. Normalizamos para `resultado`
    no MCP para coerência entre tools, mas preservamos os metadados.
    """
    total_paginas = int(response.get("totalPaginas") or 0)
    total_registros = int(response.get("totalRegistros") or 0)
    proxima = pagina_atual + 1 if pagina_atual < total_paginas else None
    return {
        "resultado": response.get("data") or [],
        "_pagina_atual": pagina_atual,
        "_total_paginas": total_paginas,
        "_total_registros": total_registros,
        "_proxima_pagina": proxima,
    }


def envelope_comprasnet(
    response: Any,
    *,
    pagina_atual: int = 1,
) -> dict[str, Any]:
    """Resposta padronizada para tools baseadas em Comprasnet Contratos.

    Os endpoints `/api/*` retornam tipicamente uma lista JSON crua. Alguns
    retornam objeto com `data`. Esta função normaliza para o envelope do MCP.
    """
    if isinstance(response, list):
        return {
            "resultado": response,
            "_pagina_atual": pagina_atual,
            "_total_paginas": 1,
            "_total_registros": len(response),
            "_proxima_pagina": None,
        }
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, list):
            total = int(response.get("total") or len(data))
            return {
                "resultado": data,
                "_pagina_atual": pagina_atual,
                "_total_paginas": int(response.get("last_page") or 1),
                "_total_registros": total,
                "_proxima_pagina": (
                    pagina_atual + 1
                    if pagina_atual < int(response.get("last_page") or 1)
                    else None
                ),
            }
    # Fallback: devolve como recurso singular
    return {
        "resultado": [response] if response else [],
        "_pagina_atual": pagina_atual,
        "_total_paginas": 1,
        "_total_registros": 1 if response else 0,
        "_proxima_pagina": None,
    }


def with_latency(payload: dict[str, Any], started: float) -> dict[str, Any]:
    """Adiciona `_latency_ms` ao payload e retorna."""
    payload["_latency_ms"] = measure_ms(started)
    return payload
