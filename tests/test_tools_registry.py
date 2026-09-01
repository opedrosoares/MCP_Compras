"""Contrato de registro das tools MCP.

Cada tool é nomeada explicitamente aqui e vira um caso de teste próprio via
`parametrize`. Isso cumpre dois papéis:

1. **Detecção de drift**: tool renomeada, removida ou registrada por engano
   quebra o teste imediatamente — o mesmo espírito dos testes SSoT de
   description em `test_server.py`.
2. **Contrato de annotations**: hosts MCP e diretórios (Claude Desktop,
   diretório da OpenAI, índices de confiança) tratam hint ausente como
   *desconhecido*, não como falso. Os quatro hints precisam existir e ser
   booleanos em todas as tools.

Para atualizar após registrar uma tool nova: acrescente o nome no bloco do
módulo correspondente. A lista é intencionalmente literal — gerá-la a partir
do próprio registro tornaria o teste tautológico.
"""

from __future__ import annotations

import pytest

from compras_mcp import server  # noqa: F401  (força o registro das tools)
from compras_mcp.mcp_instance import mcp

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# Todas as 94 tools registradas, agrupadas pelo módulo que as define.
TOOLS: tuple[str, ...] = (
    # analitica (2)
    "compras_aggregate_contratacoes_por_periodo",
    "compras_comparar_periodos_contratacoes",
    # atas (9)
    "compras_arp_listar",
    "compras_arp_consultar",
    "compras_arp_por_fim_vigencia",
    "compras_arp_buscar_por_objeto",
    "compras_arp_itens_listar",
    "compras_arp_unidades_item",
    "compras_arp_saldo_item",
    "compras_arp_adesoes_item",
    "compras_pncp_atas_listar",
    # catalogo (7)
    "compras_catmat_listar_grupos",
    "compras_catmat_listar_classes",
    "compras_catmat_consultar",
    "compras_catmat_buscar",
    "compras_catser_listar_secoes",
    "compras_catser_listar_classes",
    "compras_catser_consultar",
    # compostas (5)
    "compras_pesquisar_precos_para_etp",
    "compras_checar_sancoes_fornecedor",
    "compras_montar_dossie_arp",
    "compras_buscar_contratacoes_similares",
    "compras_perfil_fornecedor_completo",
    # contratacoes (12)
    "compras_contratacoes_14133_listar",
    "compras_contratacoes_14133_consultar",
    "compras_contratacoes_14133_itens_listar",
    "compras_contratacoes_14133_itens_por_contratacao",
    "compras_contratacoes_14133_resultados_listar",
    "compras_contratacoes_14133_resultados_por_contratacao",
    "compras_legado_licitacoes_listar",
    "compras_legado_licitacao_consultar",
    "compras_legado_itens_licitacao_listar",
    "compras_legado_pregoes_listar",
    "compras_legado_compras_sem_licitacao",
    "compras_legado_rdc_listar",
    # contratos (14)
    "compras_contratos_listar",
    "compras_contratos_consultar",
    "compras_contratos_listar_por_fim_vigencia",
    "compras_contratos_itens_listar",
    "compras_contrato_comprasnet_consultar",
    "compras_contrato_comprasnet_por_uasg",
    "compras_contrato_historico_aditivos",
    "compras_contrato_garantias",
    "compras_contrato_faturas",
    "compras_contrato_ocorrencias",
    "compras_contrato_responsaveis",
    "compras_contrato_empenhos",
    "compras_contrato_publicacoes",
    "compras_contrato_cronograma",
    # discovery (4)
    "compras_listar_prompts",
    "compras_obter_prompt",
    "compras_listar_resources",
    "compras_obter_resource",
    # enriquecimento (1)
    "compras_fornecedor_cnpj_receita",
    # fornecedores (4)
    "compras_fornecedor_consultar",
    "compras_fornecedor_listar",
    "compras_fornecedor_impedimentos_por_itens",
    "compras_fornecedor_contratos_por_item",
    # indicadores (2)
    "compras_indicadores_consolidados",
    "compras_indicadores_por_periodo",
    # organizacoes (6)
    "compras_uasg_listar",
    "compras_uasg_consultar",
    "compras_orgao_listar",
    "compras_orgao_consultar",
    "compras_pncp_orgao_unidades",
    "compras_uasg_buscar",
    # pesquisa_precos (4)
    "compras_pesquisar_preco_material",
    "compras_detalhar_preco_material",
    "compras_pesquisar_preco_servico",
    "compras_detalhar_preco_servico",
    # planejamento (8)
    "compras_pgc_listar",
    "compras_pgc_por_catalogo",
    "compras_pgc_agregacao",
    "compras_pgc_listar_csv",
    "compras_pncp_pca_listar",
    "compras_pncp_pca_atualizacao",
    "compras_pncp_pca_por_usuario",
    "compras_pncp_pca_por_classificacao_superior",
    # pncp (9)
    "compras_pncp_contratacoes_publicacao",
    "compras_pncp_contratacoes_proposta",
    "compras_pncp_contratacoes_atualizacao",
    "compras_pncp_contratacao_por_orgao",
    "compras_pncp_contratacao_itens",
    "compras_pncp_contratacao_item_resultados",
    "compras_pncp_contratos_listar",
    "compras_pncp_contrato_por_orgao",
    "compras_pncp_modalidades",
    # sancoes (5)
    "compras_sancao_ceis",
    "compras_sancao_cnep",
    "compras_sancao_ceaf",
    "compras_sancao_cepim",
    "compras_sancao_acordos_leniencia",
    # server (2)
    "compras_versao",
    "compras_healthcheck",
)


@pytest.fixture(scope="module")
async def registro() -> dict:
    return await mcp.get_tools()


@pytest.mark.parametrize("nome", TOOLS)
async def test_tool_registrada(nome: str, registro: dict) -> None:
    """A tool nomeada existe no registro do servidor."""
    assert nome in registro, f"tool {nome} não está registrada"


@pytest.mark.parametrize("nome", TOOLS)
async def test_tool_declara_os_quatro_hints(nome: str, registro: dict) -> None:
    """Os quatro hints do MCP são declarados explicitamente como booleanos."""
    annotations = registro[nome].annotations
    assert annotations is not None, f"tool {nome} sem annotations"
    for hint in HINTS:
        valor = getattr(annotations, hint, None)
        assert isinstance(valor, bool), f"{nome}.{hint} = {valor!r} (esperado bool)"


@pytest.mark.parametrize("nome", TOOLS)
async def test_tool_e_somente_leitura(nome: str, registro: dict) -> None:
    """Nenhuma tool escreve no upstream: todas são wrappers de consulta."""
    annotations = registro[nome].annotations
    assert annotations.readOnlyHint is True, f"{nome} deixou de ser somente-leitura"
    assert annotations.destructiveHint is False, f"{nome} marcada como destrutiva"


async def test_registro_nao_tem_tool_fora_da_lista(registro: dict) -> None:
    """Toda tool registrada está declarada em TOOLS (pega registro esquecido)."""
    extras = sorted(set(registro) - set(TOOLS))
    assert not extras, f"tools registradas mas ausentes de TOOLS: {extras}"


async def test_lista_sem_duplicatas() -> None:
    assert len(TOOLS) == len(set(TOOLS)) == 94
