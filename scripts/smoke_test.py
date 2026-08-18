"""Smoke test do servidor MCP.

Sobe o servidor in-process, lista tools/prompts/resources e exercita um
conjunto seguro de tools que não exigem credenciais (não bate na
Transparência, que precisa de chave). Falha rápido se:

- O server não carrega.
- Alguma tool registrada não tem description.
- A lista esperada de prompts/resources mudou inadvertidamente.

Para rodar:
    PYTHONPATH=src python scripts/smoke_test.py

Saída: JSON com contagens e nomes para o stdout; exit 0 se OK, 1 se NOK.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

# Inicializa o servidor (registra tools/prompts/resources)
import compras_mcp.server  # noqa: F401
from compras_mcp.mcp_instance import mcp


ESPERADO_MIN_TOOLS = 85  # 85 originais + 3 novas (analitica + receita)
PROMPTS_ESPERADOS = {
    "analisar_contratacao_pncp",
    "panorama_orgao_360",
    "dossie_due_diligence_fornecedor",
    "oportunidades_carona_arp",
    "montar_etp_pesquisa_precos",
    "tendencia_contratacoes_periodo",
}
RESOURCES_ESPERADOS = {
    "compras://referencia/modalidades-pncp",
    "compras://referencia/esferas-federativas",
    "compras://referencia/criterios-julgamento",
    "compras://referencia/situacoes-contratacao",
    "compras://glossario/lei-14133",
    "compras://meta/escopo",
}


async def main() -> int:
    started = time.perf_counter()
    errors: list[str] = []

    tools = await mcp.get_tools()
    prompts = await mcp.get_prompts()
    resources = await mcp.get_resources()

    # 1. Contagens mínimas
    if len(tools) < ESPERADO_MIN_TOOLS:
        errors.append(
            f"tools insuficientes: {len(tools)} < {ESPERADO_MIN_TOOLS}"
        )

    # 2. Toda tool tem description não vazia
    sem_desc = [name for name, t in tools.items() if not (t.description or "").strip()]
    if sem_desc:
        errors.append(f"tools sem description: {sem_desc[:10]}")

    # 3. Naming
    fora = [name for name in tools if not name.startswith("compras_")]
    if fora:
        errors.append(f"tools fora do padrão compras_*: {fora}")

    # 4. Prompts esperados presentes
    nomes_prompts = set(prompts.keys())
    faltando_p = PROMPTS_ESPERADOS - nomes_prompts
    if faltando_p:
        errors.append(f"prompts esperados ausentes: {faltando_p}")

    # 5. Resources esperados presentes
    nomes_res = set(resources.keys())
    faltando_r = RESOURCES_ESPERADOS - nomes_res
    if faltando_r:
        errors.append(f"resources esperados ausentes: {faltando_r}")

    # 6. Healthcheck respondeu
    healthcheck = tools.get("compras_versao")
    if healthcheck is None:
        errors.append("compras_versao não registrada")
    else:
        result = await healthcheck.run({})
        payload = (
            result.structured_content
            if hasattr(result, "structured_content")
            else result
        )
        if not isinstance(payload, dict) or payload.get("nome") != "compras-mcp":
            errors.append(f"compras_versao retornou payload inválido: {payload}")

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    report = {
        "ok": not errors,
        "tools_count": len(tools),
        "prompts_count": len(prompts),
        "resources_count": len(resources),
        "elapsed_ms": elapsed_ms,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
