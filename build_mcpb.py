#!/usr/bin/env python3
"""build_mcpb.py - Gera o arquivo compras.mcpb (Desktop Extension para Claude).

Uso:
    python3 build_mcpb.py

Produz: dist/compras.mcpb
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_NAME = "compras.mcpb"

# Arquivos e pastas incluidos no .mcpb (ordem nao importa).
INCLUDE = [
    "manifest.json",
    "pyproject.toml",
    "bootstrap.py",
    "README.md",
    "icon.png",
    "src/compras_mcp/",
]

# Padroes ignorados ao percorrer diretorios.
IGNORE_PATTERNS = {
    "__pycache__",
    ".pyc",
    ".egg-info",
    ".DS_Store",
    ".git",
    ".env",
}


def should_ignore(path: Path) -> bool:
    for part in path.parts:
        for pattern in IGNORE_PATTERNS:
            if pattern in part:
                return True
    return False


def build() -> None:
    print()
    print("=" * 50)
    print(f"  Build: {OUTPUT_NAME}")
    print("=" * 50)
    print()

    manifest_path = PROJECT_ROOT / "manifest.json"
    if not manifest_path.exists():
        print("  [ERRO] manifest.json nao encontrado.")
        return
    manifest = json.loads(manifest_path.read_text())
    print(f"  [*] {manifest['display_name']} v{manifest['version']}")

    DIST_DIR.mkdir(exist_ok=True)
    output = DIST_DIR / OUTPUT_NAME

    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for item_name in INCLUDE:
            item = PROJECT_ROOT / item_name
            if not item.exists():
                if item_name == "icon.png":
                    print("  [!] icon.png nao encontrado — pulando (manifest pode reclamar)")
                    continue
                print(f"  [!] Pulando {item_name} (nao existe)")
                continue

            if item.is_file():
                zf.write(item, item_name)
                count += 1
            elif item.is_dir():
                for root, _dirs, files in os.walk(item):
                    root_path = Path(root)
                    for f in files:
                        file_path = root_path / f
                        if should_ignore(file_path):
                            continue
                        arcname = str(file_path.relative_to(PROJECT_ROOT))
                        zf.write(file_path, arcname)
                        count += 1

    size_kb = output.stat().st_size / 1024
    print(f"  [*] {count} arquivos empacotados")
    print(f"  [*] Gerado: {output} ({size_kb:.0f} KB)")
    print()
    print("  Para instalar no Claude Desktop:")
    print(f"    Abra {output} com duplo-clique")
    print()


if __name__ == "__main__":
    build()
