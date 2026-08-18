"""Instância única do FastMCP, isolada para evitar imports circulares.

server.py importa esta instância e também os módulos tools/*; cada módulo
tools/*.py importa `mcp` daqui e registra suas @mcp.tool no carregamento.
"""

from __future__ import annotations

from fastmcp import FastMCP

mcp: FastMCP = FastMCP(
    name="compras-mcp",
    instructions=(
        "MCP server unificando APIs públicas do ecossistema Compras.gov.br "
        "(Dados Abertos, PNCP e Portal da Transparência/CGU). Voltado a "
        "analistas e técnicos de planejamento de contratação e execução "
        "contratual: apoio a Estudos Técnicos Preliminares (ETP), Termos "
        "de Referência (TR), pesquisa de preços (IN SEGES/ME 65/2021), "
        "consulta de atas de registro de preço, contratos vigentes, "
        "fornecedores e sanções (CEIS/CNEP/CEAF). Os dados são oficiais "
        "do governo federal; algumas consultas podem demorar 2-5s."
    ),
)
