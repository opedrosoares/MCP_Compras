"""Mede latência p50/p95/min/max dos endpoints upstream que o MCP consome.

Roda um conjunto pequeno de probes contra Dados Abertos, PNCP e BrasilAPI
(não toca Transparência por exigir chave). Cada probe roda N vezes e o
script imprime um JSON com estatísticas para o stdout.

Uso típico em workflow agendado (semanal):

    LATENCY_SAMPLES=3 PYTHONPATH=src python scripts/measure_latency.py

Saída JSON:
    {
      "timestamp": "2026-05-16T15:00:00",
      "samples": 3,
      "results": [
        {"endpoint": "...", "p50_ms": 450, "p95_ms": 1200, "ok": 3, "failed": 0},
        ...
      ]
    }
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

from compras_mcp.clients.cnpj import make_cnpj_client
from compras_mcp.clients.dados_abertos import DadosAbertosClient
from compras_mcp.clients.pncp import PNCPClient
from compras_mcp.config import get_settings


SAMPLES = int(os.environ.get("LATENCY_SAMPLES", "3"))


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return round(s[idx], 1)


async def _probe(name: str, coro_factory) -> dict[str, float | int | str]:
    timings: list[float] = []
    failed = 0
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        try:
            await coro_factory()
            timings.append((time.perf_counter() - t0) * 1000)
        except Exception:
            failed += 1
    return {
        "endpoint": name,
        "p50_ms": _pct(timings, 0.5),
        "p95_ms": _pct(timings, 0.95),
        "min_ms": round(min(timings), 1) if timings else 0.0,
        "max_ms": round(max(timings), 1) if timings else 0.0,
        "ok": len(timings),
        "failed": failed,
    }


async def main() -> int:
    settings = get_settings()

    async def _dados_abertos_orgaos():
        async with DadosAbertosClient(
            base_url=settings.dados_abertos_base_url,
            timeout=15.0,
            max_retries=0,
        ) as c:
            await c.list_resource("/modulo-uasg/1_consultarOrgaos", pagina=1, tamanho_pagina=10)

    async def _pncp_modalidades():
        async with PNCPClient(
            base_url=settings.pncp_base_url, timeout=15.0, max_retries=0
        ) as c:
            await c.list_resource(
                "/v1/contratacoes/publicacao",
                pagina=1,
                tamanho_pagina=10,
                dataInicial="20260101",
                dataFinal="20260131",
                codigoModalidadeContratacao=6,
            )

    async def _brasilapi_cnpj():
        # CNPJ da Petrobras — público, estável.
        async with make_cnpj_client(settings) as c:
            await c.consultar("33000167000101")

    probes = [
        ("dados_abertos:/modulo-uasg/1_consultarOrgaos", _dados_abertos_orgaos),
        ("pncp:/v1/contratacoes/publicacao", _pncp_modalidades),
        ("brasilapi:/api/cnpj/v1/{cnpj}", _brasilapi_cnpj),
    ]

    results = []
    for name, factory in probes:
        results.append(await _probe(name, factory))

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "samples": SAMPLES,
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    # Não falha o build se latência alta — só reporta. Falha se TODOS quebraram.
    if all(r["ok"] == 0 for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
