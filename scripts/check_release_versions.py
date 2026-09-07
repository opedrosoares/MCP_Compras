#!/usr/bin/env python3
"""Confere que a versão da tag bate com todos os lugares que a carregam.

A versão do projeto vive em cinco pontos independentes, e cada um quebra uma
coisa diferente quando diverge:

- `pyproject.toml`            → é a versão que vai para o PyPI
- `src/compras_mcp/__init__.py` → é o que `compras_versao` devolve ao usuário
- `manifest.json`             → é o que o Claude Desktop instala do `.mcpb`
- `server.json` (raiz)        → é a versão do servidor no registry oficial
- `server.json` (packages[])  → o registry **recusa** o publish se essa versão
                                não existir no PyPI

Rodar antes de publicar evita descobrir a divergência no meio do pipeline, com
o pacote já no PyPI (que não aceita reenvio da mesma versão).

Uso:
    python3 scripts/check_release_versions.py 0.3.17
    python3 scripts/check_release_versions.py            # infere de GITHUB_REF_NAME
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _de_pyproject() -> str:
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', texto, re.M)
    if not m:
        raise SystemExit("pyproject.toml: campo `version` não encontrado")
    return m.group(1)


def _de_init() -> str:
    texto = (RAIZ / "src" / "compras_mcp" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', texto, re.M)
    if not m:
        raise SystemExit("__init__.py: `__version__` não encontrado")
    return m.group(1)


def _de_manifest() -> str:
    return json.loads((RAIZ / "manifest.json").read_text(encoding="utf-8"))["version"]


def _de_server_json() -> list[tuple[str, str]]:
    dados = json.loads((RAIZ / "server.json").read_text(encoding="utf-8"))
    achados = [("server.json (version)", dados["version"])]
    for i, pacote in enumerate(dados.get("packages", [])):
        achados.append((f"server.json (packages[{i}].version)", pacote["version"]))
    return achados


def main() -> int:
    if len(sys.argv) > 1:
        esperada = sys.argv[1]
    else:
        ref = os.environ.get("GITHUB_REF_NAME", "")
        if not ref:
            raise SystemExit("passe a versão como argumento ou defina GITHUB_REF_NAME")
        esperada = ref
    esperada = esperada.removeprefix("v")

    encontradas: list[tuple[str, str]] = [
        ("pyproject.toml", _de_pyproject()),
        ("src/compras_mcp/__init__.py", _de_init()),
        ("manifest.json", _de_manifest()),
        *_de_server_json(),
    ]

    divergentes = [(onde, versao) for onde, versao in encontradas if versao != esperada]

    largura = max(len(onde) for onde, _ in encontradas)
    for onde, versao in encontradas:
        marca = "ok " if versao == esperada else "!! "
        print(f"  {marca}{onde.ljust(largura)}  {versao}")

    if divergentes:
        sys.stdout.flush()  # senão o log da CI intercala stdout e stderr
        print(f"\nesperado {esperada!r}, mas {len(divergentes)} local(is) divergem:", file=sys.stderr)
        for onde, versao in divergentes:
            print(f"  {onde}: {versao}", file=sys.stderr)
        return 1

    print(f"\ntodas as {len(encontradas)} versões batem com {esperada}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
