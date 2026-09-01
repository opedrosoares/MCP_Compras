"""Snapshot do JSON Schema de todas as tools MCP registradas.

Gera um arquivo determinístico em `scripts/snapshots/tools_schema.json`
que pode ser comparado em CI para detectar drift involuntário (mudança
de description, novo parâmetro, alteração de tipo etc.).

Subcomandos:
    python scripts/schema_snapshot.py snapshot  # grava o arquivo
    python scripts/schema_snapshot.py check     # falha se houver diff

Uso típico em CI:
    1. Pull request altera tools.
    2. Autor roda `snapshot` localmente e commita o JSON.
    3. CI roda `check` e exige que o JSON commitado bata com o atual.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import compras_mcp.server  # noqa: F401
from compras_mcp.mcp_instance import mcp

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "tools_schema.json"


def _normalize(d: dict) -> dict:
    """Ordena chaves recursivamente para o JSON ser estável entre runs."""
    if isinstance(d, dict):
        return {k: _normalize(d[k]) for k in sorted(d.keys())}
    if isinstance(d, list):
        return [_normalize(x) for x in d]
    return d


async def _dump() -> dict:
    tools = await mcp.get_tools()
    prompts = await mcp.get_prompts()
    resources = await mcp.get_resources()
    out: dict = {
        "tools": {},
        "prompts": {},
        "resources": {},
    }
    for name in sorted(tools.keys()):
        t = tools[name]
        params = t.parameters if isinstance(t.parameters, dict) else {}
        # Hints entram no snapshot para que perder uma annotation conte como
        # drift em CI, e não só como achado de auditoria externa.
        annotations = {
            hint: getattr(t.annotations, hint, None)
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
        }
        out["tools"][name] = _normalize(
            {
                "annotations": annotations,
                "description": (t.description or "").strip(),
                "parameters": params,
            }
        )
    for name in sorted(prompts.keys()):
        p = prompts[name]
        out["prompts"][name] = {
            "description": (p.description or "").strip(),
            "arguments": [
                {"name": a.name, "description": a.description, "required": a.required}
                for a in (p.arguments or [])
            ],
        }
    for uri in sorted(resources.keys()):
        r = resources[uri]
        out["resources"][uri] = {
            "name": getattr(r, "name", None),
            "description": (getattr(r, "description", "") or "").strip(),
            "mime_type": getattr(r, "mime_type", None),
        }
    return out


async def snapshot() -> int:
    payload = await _dump()
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"snapshot escrito em {SNAPSHOT.relative_to(Path.cwd())}")
    return 0


async def check() -> int:
    if not SNAPSHOT.exists():
        print(
            f"erro: snapshot {SNAPSHOT.relative_to(Path.cwd())} não existe — "
            "rode `python scripts/schema_snapshot.py snapshot` antes.",
            file=sys.stderr,
        )
        return 2

    atual = await _dump()
    salvo = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if atual == salvo:
        print(f"OK — snapshot bate ({len(atual['tools'])} tools).")
        return 0

    # Diff resumido: nomes que mudaram.
    drift: list[str] = []
    for kind in ("tools", "prompts", "resources"):
        novos = set(atual[kind]) - set(salvo[kind])
        removidos = set(salvo[kind]) - set(atual[kind])
        comuns = set(atual[kind]) & set(salvo[kind])
        diferentes = [n for n in comuns if atual[kind][n] != salvo[kind][n]]
        if novos:
            drift.append(f"{kind}: novos = {sorted(novos)}")
        if removidos:
            drift.append(f"{kind}: removidos = {sorted(removidos)}")
        if diferentes:
            drift.append(f"{kind}: alterados = {sorted(diferentes)[:10]}")

    print("DRIFT DETECTADO:", file=sys.stderr)
    for line in drift:
        print("  - " + line, file=sys.stderr)
    print(
        "\nRode `python scripts/schema_snapshot.py snapshot` para atualizar.",
        file=sys.stderr,
    )
    return 1


async def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("snapshot", "check"):
        print(
            "uso: python scripts/schema_snapshot.py {snapshot|check}",
            file=sys.stderr,
        )
        return 2
    return await (snapshot() if sys.argv[1] == "snapshot" else check())


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
