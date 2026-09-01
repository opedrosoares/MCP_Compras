#!/usr/bin/env python3
"""Bootstrap do MCP Compras.gov.br (Desktop Extension .mcpb).

Na primeira execucao, cria um venv em ~/.mcp-compras/.venv e instala
as dependencias. Nas execucoes seguintes, apenas executa o servidor.

Usa apenas a stdlib do Python para nao depender de pacotes externos
antes do bootstrap.

Nenhum comando passa por shell: a criação do venv usa `venv.EnvBuilder`
(stdlib, sem processo filho) e a instalação usa `subprocess.run` com argv em
lista e `shell=False` — não há interpolação de string nem entrada do usuário
em nenhum dos dois. O `os.execv` do final substitui este processo pelo
servidor para preservar stdin/stdout, que o transporte stdio do MCP exige.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import venv
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
    venv.EnvBuilder(with_pip=True).create(VENV_DIR)
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
