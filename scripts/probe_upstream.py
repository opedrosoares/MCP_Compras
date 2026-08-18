"""Probe de todas as rotas upstream registradas no MCP.

Percorre o inventário de `compras_mcp.upstream_registry` com um payload
mínimo conhecido-bom por rota e imprime a matriz:

    rota | status HTTP | latência | nº de registros | chaves do 1º item

Por que existe: em 04/08/2026 a SEGES trocou a assinatura de query de
`/modulo-pesquisa-preco/1_consultarMaterial` e a rota passou a devolver
**404**. Ninguém percebeu até um analista tentar usar a tool. Este probe
transforma essa descoberta num comando de 30 segundos.

Uso:

    PYTHONPATH=src python scripts/probe_upstream.py            # matriz completa
    PYTHONPATH=src python scripts/probe_upstream.py --json     # saída JSON
    PYTHONPATH=src python scripts/probe_upstream.py --modulo pesquisa_preco
    PYTHONPATH=src python scripts/probe_upstream.py --timeout 8 --concorrencia 12

Não exige nada além das credenciais já configuradas: rotas que dependem
de `TRANSPARENCIA_API_KEY` saem como `pulado` quando ela não existe, e não
contaminam o exit code.

Exit code: 0 se nenhuma rota está `fora` ou `degradado`; 1 caso contrário.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime

from compras_mcp.upstream_probe import (
    STATUS_DEGRADADO,
    STATUS_FORA,
    STATUS_OK,
    STATUS_PULADO,
    executar_probe,
    resumir_por_modulo,
)
from compras_mcp.upstream_registry import MODULOS, ROTAS

_SIMBOLO = {
    STATUS_OK: "OK  ",
    STATUS_DEGRADADO: "DEGR",
    STATUS_FORA: "FORA",
    STATUS_PULADO: "----",
}


def _truncar(texto: str, largura: int) -> str:
    return texto if len(texto) <= largura else texto[: largura - 1] + "…"


def _imprimir_matriz(resultados, elapsed: float) -> None:
    print()
    print(f"{'ST':<5} {'ROTA':<52} {'HTTP':>5} {'ms':>7} {'REGS':>8}  CHAVES DO 1º ITEM")
    print("─" * 150)
    modulo_atual = ""
    for r in resultados:
        if r.modulo != modulo_atual:
            modulo_atual = r.modulo
            print(f"\n· {modulo_atual.upper()} ({r.api})")
        chaves = ", ".join(r.chaves_primeiro_item[:6])
        if len(r.chaves_primeiro_item) > 6:
            chaves += f" (+{len(r.chaves_primeiro_item) - 6})"
        if not chaves:
            chaves = r.detalhe or "—"
        registros = "—" if r.registros is None else str(r.registros)
        print(
            f"{_SIMBOLO[r.status]:<5} {_truncar(r.path, 52):<52} "
            f"{r.http_status or '—':>5} {r.latencia_ms:>7.0f} {registros:>8}  "
            f"{_truncar(chaves, 68)}"
        )
        if r.status in (STATUS_DEGRADADO, STATUS_FORA) and r.detalhe:
            print(f"{'':>5} └─ {_truncar(r.detalhe, 130)}")
            if r.tools:
                print(f"{'':>5}    tools afetadas: {', '.join(r.tools)}")

    print()
    print("─" * 150)
    resumo = resumir_por_modulo(resultados)
    for modulo, m in resumo.items():
        print(
            f"  {_SIMBOLO[m['situacao']]:<5} {modulo:<18} "
            f"ok={m['rotas_ok']:<3} degradado={m['rotas_degradadas']:<3} "
            f"fora={m['rotas_fora']:<3} pulado={m['rotas_puladas']:<3} "
            f"lat.média={m['latencia_media_ms']:.0f}ms"
        )
    total_ok = sum(1 for r in resultados if r.status == STATUS_OK)
    total_degr = sum(1 for r in resultados if r.status == STATUS_DEGRADADO)
    total_fora = sum(1 for r in resultados if r.status == STATUS_FORA)
    total_pulado = sum(1 for r in resultados if r.status == STATUS_PULADO)
    print()
    print(
        f"  TOTAL {len(resultados)} rotas em {elapsed:.1f}s — "
        f"ok={total_ok} degradado={total_degr} fora={total_fora} pulado={total_pulado}"
    )
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="saída JSON em vez de matriz")
    parser.add_argument(
        "--modulo",
        choices=MODULOS,
        help="testa apenas um módulo (default: todos)",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="timeout por rota (s)")
    parser.add_argument("--concorrencia", type=int, default=8, help="requisições em paralelo")
    args = parser.parse_args()

    rotas = tuple(r for r in ROTAS if not args.modulo or r.modulo == args.modulo)
    started = time.perf_counter()
    resultados = await executar_probe(
        rotas, timeout=args.timeout, concorrencia=args.concorrencia
    )
    elapsed = time.perf_counter() - started

    if args.json:
        print(
            json.dumps(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "elapsed_s": round(elapsed, 2),
                    "resultados": [r.to_dict() for r in resultados],
                    "por_modulo": resumir_por_modulo(resultados),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _imprimir_matriz(resultados, elapsed)

    problemas = [r for r in resultados if r.status in (STATUS_DEGRADADO, STATUS_FORA)]
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
