#!/usr/bin/env python3
"""Bootstrap do MCP Compras.gov.br (Desktop Extension .mcpb).

Na primeira execucao, cria um venv em ~/.mcp-compras/.venv e instala
as dependencias. Nas execucoes seguintes, apenas executa o servidor.

Usa apenas a stdlib do Python para nao depender de pacotes externos
antes do bootstrap.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

VENV_HOME = Path.home() / ".mcp-compras"
VENV_DIR = VENV_HOME / ".venv"
IS_WINDOWS = platform.system() == "Windows"
PYTHON = (
    VENV_DIR / "Scripts" / "python.exe"
    if IS_WINDOWS
    else VENV_DIR / "bin" / "python"
)
COMPRAS_MCP = (
    VENV_DIR / "Scripts" / "compras-mcp.exe"
    if IS_WINDOWS
    else VENV_DIR / "bin" / "compras-mcp"
)
SRC_DIR = Path(__file__).resolve().parent


def setup() -> None:
    """Cria venv e instala o pacote na primeira execucao."""
    print("Compras MCP: configurando ambiente (primeira execucao)...", file=sys.stderr)
    VENV_HOME.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR)],
        check=True,
    )
    subprocess.run(
        [str(PYTHON), "-m", "pip", "install", "--quiet", str(SRC_DIR)],
        check=True,
    )
    print("Compras MCP: ambiente configurado.", file=sys.stderr)


def main() -> None:
    if not COMPRAS_MCP.exists():
        setup()

    if IS_WINDOWS:
        sys.exit(subprocess.call([str(COMPRAS_MCP)]))
    else:
        os.execv(str(COMPRAS_MCP), [str(COMPRAS_MCP)])


if __name__ == "__main__":
    main()
