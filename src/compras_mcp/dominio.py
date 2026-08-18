"""Tabelas de domínio canônicas — fonte única para tool e resource.

Para evitar inconsistências entre `compras_pncp_modalidades` (tool) e
`compras://referencia/modalidades-pncp` (resource), ambas consomem desta
única lista.

Mantém também o mapeamento `equivalente_dados_abertos` que a tool original
expunha — Dados Abertos / SIASG usa enumeração diferente do PNCP, e o
agente precisa saber qual número passar em cada endpoint.
"""

from __future__ import annotations

from typing import Any

# Fonte: tabela oficial PNCP, Lei 14.133/2021.
# `codigo` é o usado em todas as tools `compras_pncp_*` e no campo
# `modalidadeIdPncp` dos payloads PNCP.
# `equivalente_dados_abertos` é o enum usado por
# `compras_contratacoes_14133_listar(codigo_modalidade_dados_abertos)` —
# `None` quando a modalidade não está disponível naquele endpoint.
MODALIDADES_PNCP: list[dict[str, Any]] = [
    {"codigo": 1, "nome": "Leilão Eletrônico", "equivalente_dados_abertos": None},
    {"codigo": 2, "nome": "Diálogo Competitivo", "equivalente_dados_abertos": None},
    {"codigo": 3, "nome": "Concurso", "equivalente_dados_abertos": None},
    {"codigo": 4, "nome": "Concorrência Eletrônica", "equivalente_dados_abertos": 3},
    {"codigo": 5, "nome": "Concorrência Presencial", "equivalente_dados_abertos": None},
    {"codigo": 6, "nome": "Pregão Eletrônico", "equivalente_dados_abertos": 5},
    {"codigo": 7, "nome": "Pregão Presencial", "equivalente_dados_abertos": None},
    {"codigo": 8, "nome": "Dispensa de Licitação", "equivalente_dados_abertos": 6},
    {"codigo": 9, "nome": "Inexigibilidade", "equivalente_dados_abertos": 7},
    {"codigo": 10, "nome": "Manifestação de Interesse", "equivalente_dados_abertos": None},
    {"codigo": 11, "nome": "Pré-qualificação", "equivalente_dados_abertos": None},
    {"codigo": 12, "nome": "Credenciamento", "equivalente_dados_abertos": None},
    {"codigo": 13, "nome": "Leilão Presencial", "equivalente_dados_abertos": None},
]


ESFERAS_FEDERATIVAS: list[dict[str, str]] = [
    {
        "codigo": "F",
        "nome": "Federal",
        "descricao": (
            "União — administração direta e autárquica, fundacional, empresas "
            "públicas e sociedades de economia mista."
        ),
    },
    {"codigo": "E", "nome": "Estadual", "descricao": "Estados-membros e suas entidades."},
    {"codigo": "M", "nome": "Municipal", "descricao": "Municípios e suas entidades."},
    {"codigo": "D", "nome": "Distrital", "descricao": "Distrito Federal."},
]


CRITERIOS_JULGAMENTO: list[dict[str, Any]] = [
    {"id": 1, "nome": "Menor preço"},
    {"id": 2, "nome": "Maior desconto"},
    {"id": 4, "nome": "Técnica e preço"},
    {"id": 5, "nome": "Maior lance (para leilão)"},
    {"id": 6, "nome": "Maior retorno econômico"},
    {"id": 7, "nome": "Não se aplica"},
    {"id": 8, "nome": "Melhor técnica"},
    {"id": 9, "nome": "Conteúdo artístico"},
]


SITUACOES_CONTRATACAO: list[dict[str, Any]] = [
    {"id": 1, "nome": "Divulgada no PNCP"},
    {"id": 2, "nome": "Revogada"},
    {"id": 3, "nome": "Anulada"},
    {"id": 4, "nome": "Suspensa"},
]
