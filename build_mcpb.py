#!/usr/bin/env python3
"""build_mcpb.py - Gera o arquivo compras.mcpb (Desktop Extension para Claude).

O bundle é uma casca fina: `mcpb/bridge.js` traduz o stdio do Claude Desktop
para o Streamable HTTP do servidor hospedado. Nada de `src/` aqui — o servidor
Python continua sendo distribuído pelo PyPI (`pip install compras-mcp`), para
quem quer rodar local.

Uso:
    python3 build_mcpb.py

Produz: dist/compras.mcpb
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_NAME = "compras.mcpb"

# (arquivo no repo, caminho dentro do .mcpb). O bridge sobe para a raiz do
# bundle porque é o `entry_point` declarado no manifest.
INCLUDE: list[tuple[str, str]] = [
    ("manifest.json", "manifest.json"),
    ("mcpb/bridge.js", "bridge.js"),
    ("icon.png", "icon.png"),
    ("README.md", "README.md"),
    ("LICENSE", "LICENSE"),
]


def conferir_manifest(manifest: dict, empacotados: set[str]) -> list[str]:
    """Erros que só apareceriam na hora de instalar, quando já é tarde."""
    problemas: list[str] = []

    entry_point = manifest.get("server", {}).get("entry_point")
    if entry_point not in empacotados:
        problemas.append(f"entry_point {entry_point!r} não está no bundle")

    args = manifest.get("server", {}).get("mcp_config", {}).get("args", [])
    alvos = [a.replace("${__dirname}/", "") for a in args if "${__dirname}/" in a]
    for alvo in alvos:
        if alvo not in empacotados:
            problemas.append(f"args aponta para {alvo!r}, que não está no bundle")

    icone = manifest.get("icon")
    if icone and icone not in empacotados:
        problemas.append(f"icon {icone!r} não está no bundle")

    return problemas


def build() -> int:
    print()
    print("=" * 50)
    print(f"  Build: {OUTPUT_NAME}")
    print("=" * 50)
    print()

    manifest_path = PROJECT_ROOT / "manifest.json"
    if not manifest_path.exists():
        print("  [ERRO] manifest.json nao encontrado.")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"  [*] {manifest['display_name']} v{manifest['version']}")

    faltando = [origem for origem, _ in INCLUDE if not (PROJECT_ROOT / origem).exists()]
    if faltando:
        for origem in faltando:
            print(f"  [ERRO] {origem} nao encontrado")
        return 1

    empacotados = {destino for _, destino in INCLUDE}
    problemas = conferir_manifest(manifest, empacotados)
    if problemas:
        for problema in problemas:
            print(f"  [ERRO] {problema}")
        return 1

    DIST_DIR.mkdir(exist_ok=True)
    output = DIST_DIR / OUTPUT_NAME
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for origem, destino in INCLUDE:
            zf.write(PROJECT_ROOT / origem, destino)

    size_kb = output.stat().st_size / 1024
    print(f"  [*] {len(INCLUDE)} arquivos empacotados")
    endpoint = manifest["user_config"]["endpoint_url"]["default"]
    print(f"  [*] Endpoint padrao: {endpoint}")
    print(f"  [*] Gerado: {output} ({size_kb:.0f} KB)")
    print()
    print("  Para instalar no Claude Desktop:")
    print(f"    Abra {output} com duplo-clique")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
