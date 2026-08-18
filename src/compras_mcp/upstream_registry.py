"""Inventário das rotas upstream que este MCP consome — SSoT do probe.

Por que existe
-------------
Em 04/08/2026 a rota `/modulo-pesquisa-preco/1_consultarMaterial` quebrou
sem aviso: a SEGES trocou a assinatura de query (`codigoItemCatalogo` →
`tipo` + `codigo`) e o servidor responde **404** — não 400 — quando um
parâmetro obrigatório falta. Um 404 é indistinguível de "recurso não
existe", então a quebra passou como se a rota tivesse sumido.

Só descobrimos porque um humano tentou usar a tool. Este registro existe
para que a descoberta aconteça num probe de 30s, não no palco.

Cada entrada declara:

- `params`: payload mínimo conhecido-bom (validado contra o contrato
  OpenAPI oficial em https://dadosabertos.compras.gov.br/v3/api-docs).
- `campos_esperados`: **contrato de campos** — chaves que o primeiro item
  do `resultado` precisa ter. É isto que separa "HTTP 200" de "HTTP 200
  com payload útil". A rota 2 devolvia 200 com zero campos de preço e
  ninguém percebeu por meses.
- `seed`: rotas que exigem um ID real (ata, contrato, contratação) puxam
  esse ID da rota-pai em tempo de execução, em vez de fixar um valor
  volátil que apodrece no código.

Consumidores: `scripts/probe_upstream.py` (matriz completa em CLI) e a
tool `compras_healthcheck` (status por módulo, em paralelo, timeout curto).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Janelas de data relativas: o probe precisa continuar válido daqui a um ano.
# Datas fixas apodrecem — em 2027 uma janela de 2026 devolve zero registros e
# o probe acusaria "degradado" sem defeito nenhum.
# ---------------------------------------------------------------------------


def _hoje() -> date:
    return date.today()


def _dias_atras(dias: int) -> str:
    return (_hoje() - timedelta(days=dias)).isoformat()


def _hoje_iso() -> str:
    return _hoje().isoformat()


def _ano_passado() -> int:
    return _hoje().year - 1


# Módulo legado = Lei 8.666, estoque histórico e fechado. Janela fixa em 2019
# (auge do regime anterior) porque uma janela relativa devolveria vazio e o
# probe acusaria quebra onde só há fim de vida natural da base.
_JANELA_LEGADO_PUBLICACAO = {
    "data_publicacao_inicial": "2019-01-01",
    "data_publicacao_final": "2019-03-31",
}


@dataclass(frozen=True)
class RotaUpstream:
    """Uma rota upstream + o payload mínimo que a exercita de verdade."""

    id: str
    api: str
    modulo: str
    path: str
    tools: tuple[str, ...]
    params: dict = field(default_factory=dict)
    campos_esperados: tuple[str, ...] = ()
    # (id_da_rota_pai, {param_desta_rota: campo_no_item_da_pai})
    # O campo aceita caminho pontuado: "orgaoEntidade.cnpj".
    seed: tuple[str, dict[str, str]] | None = None
    # Params que vão no PATH (`/v1/orgaos/{cnpj}/...`) e não na query.
    path_params: tuple[str, ...] = ()
    # Env var exigida; sem ela o probe marca "pulado" em vez de "fora".
    exige_credencial: str | None = None
    # Rotas que legitimamente devolvem 0 registros não são "degradado".
    aceita_vazio: bool = False
    # PNCP responde em 5-20s; 15s de timeout global marcaria "fora" à toa.
    timeout_s: float | None = None
    # Rotas CSV recusam `Accept: application/json` com 500.
    accept: str = "application/json"
    observacao: str = ""


# ---------------------------------------------------------------------------
# DADOS ABERTOS — dadosabertos.compras.gov.br
# ---------------------------------------------------------------------------

_DADOS_ABERTOS: list[RotaUpstream] = [
    # --- 03 Pesquisa de preço (o módulo que quebrou) -----------------------
    RotaUpstream(
        id="preco_material",
        api="dados_abertos",
        modulo="pesquisa_preco",
        path="/modulo-pesquisa-preco/1_consultarMaterial",
        tools=("compras_pesquisar_preco_material", "compras_pesquisar_precos_para_etp"),
        # tipo/codigo desde 2026-08; antes era codigoItemCatalogo (ver CHANGELOG 0.3.13).
        params={"tipo": "codigoItemCatalogo", "codigo": "630237"},
        campos_esperados=("precoUnitario", "niFornecedor", "nomeFornecedor", "dataCompra"),
    ),
    RotaUpstream(
        id="preco_material_detalhe",
        api="dados_abertos",
        modulo="pesquisa_preco",
        path="/modulo-pesquisa-preco/2_consultarMaterialDetalhe",
        tools=("compras_detalhar_preco_material",),
        params={"codigoItemCatalogo": 630237},
        # Sem campo de preço: o DTO upstream tem 7 campos e nenhum é valor.
        campos_esperados=("idCompra", "idItemCompra", "descricaoDetalhadaItem"),
        observacao="DTO upstream não expõe preço — ver compras_pesquisar_preco_material",
    ),
    RotaUpstream(
        id="preco_servico",
        api="dados_abertos",
        modulo="pesquisa_preco",
        path="/modulo-pesquisa-preco/3_consultarServico",
        tools=("compras_pesquisar_preco_servico", "compras_pesquisar_precos_para_etp"),
        # 25089 = LOCAÇÃO DE VEÍCULOS: serviço contratado em volume por toda
        # a Administração, então a amostra não seca.
        params={"codigoItemCatalogo": 25089},
        campos_esperados=("precoUnitario", "niFornecedor", "nomeFornecedor"),
    ),
    RotaUpstream(
        id="preco_servico_detalhe",
        api="dados_abertos",
        modulo="pesquisa_preco",
        path="/modulo-pesquisa-preco/4_consultarServicoDetalhe",
        tools=("compras_detalhar_preco_servico",),
        params={"codigoItemCatalogo": 25089},
        campos_esperados=("idCompra", "idItemCompra"),
        observacao="DTO upstream não expõe preço (idem rota 2)",
    ),
    # --- 01 Catálogo material ---------------------------------------------
    RotaUpstream(
        id="catmat_grupos",
        api="dados_abertos",
        modulo="catalogo",
        path="/modulo-material/1_consultarGrupoMaterial",
        tools=("compras_catmat_listar_grupos",),
        campos_esperados=("codigoGrupo", "nomeGrupo"),
    ),
    RotaUpstream(
        id="catmat_classes",
        api="dados_abertos",
        modulo="catalogo",
        path="/modulo-material/2_consultarClasseMaterial",
        tools=("compras_catmat_listar_classes",),
        campos_esperados=("codigoClasse", "nomeClasse"),
    ),
    RotaUpstream(
        id="catmat_itens",
        api="dados_abertos",
        modulo="catalogo",
        path="/modulo-material/4_consultarItemMaterial",
        tools=("compras_catmat_buscar", "compras_catmat_consultar"),
        params={"codigoItem": 630237},
        campos_esperados=("codigoItem", "descricaoItem"),
    ),
    # --- 02 Catálogo serviço ----------------------------------------------
    RotaUpstream(
        id="catser_secoes",
        api="dados_abertos",
        modulo="catalogo",
        path="/modulo-servico/1_consultarSecaoServico",
        tools=("compras_catser_listar_secoes",),
        campos_esperados=("codigoSecao", "nomeSecao"),
    ),
    RotaUpstream(
        id="catser_classes",
        api="dados_abertos",
        modulo="catalogo",
        path="/modulo-servico/4_consultarClasseServico",
        tools=("compras_catser_listar_classes",),
        campos_esperados=("codigoClasse", "nomeClasse"),
    ),
    RotaUpstream(
        id="catser_itens",
        api="dados_abertos",
        modulo="catalogo",
        path="/modulo-servico/6_consultarItemServico",
        tools=("compras_catser_consultar",),
        params={"codigoServico": 25089},
        campos_esperados=("codigoServico", "nomeServico"),
    ),
    # --- 05 UASG / Órgãos --------------------------------------------------
    RotaUpstream(
        id="uasg_listar",
        api="dados_abertos",
        modulo="organizacoes",
        path="/modulo-uasg/1_consultarUasg",
        tools=("compras_uasg_listar", "compras_uasg_consultar", "compras_uasg_buscar"),
        # statusUasg é obrigatório; sem ele o upstream devolve 404 (não 400).
        params={"statusUasg": "true"},
        campos_esperados=("codigoUasg", "nomeUasg"),
    ),
    RotaUpstream(
        id="orgao_listar",
        api="dados_abertos",
        modulo="organizacoes",
        path="/modulo-uasg/2_consultarOrgao",
        tools=("compras_orgao_listar", "compras_orgao_consultar"),
        params={"statusOrgao": "true"},
        campos_esperados=("codigoOrgao", "nomeOrgao"),
    ),
    # --- 08 ARP (atas) -----------------------------------------------------
    RotaUpstream(
        id="arp_listar",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/1_consultarARP",
        tools=("compras_arp_listar", "compras_arp_buscar_por_objeto"),
        params={
            "dataVigenciaInicialMin": _dias_atras(180),
            "dataVigenciaInicialMax": _hoje_iso(),
        },
        campos_esperados=("numeroControlePncpAta", "nomeUnidadeGerenciadora"),
    ),
    RotaUpstream(
        id="arp_por_id",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/1.1_consultarARP_Id",
        tools=("compras_arp_consultar", "compras_montar_dossie_arp"),
        seed=("arp_listar", {"numeroControlePncpAta": "numeroControlePncpAta"}),
        campos_esperados=("numeroControlePncpAta",),
    ),
    RotaUpstream(
        id="arp_fim_vigencia",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/1.2_consultarARP_FimVigencia",
        tools=("compras_arp_por_fim_vigencia",),
        params={
            "dataVigenciaFinalMin": _hoje_iso(),
            "dataVigenciaFinalMax": (_hoje() + timedelta(days=180)).isoformat(),
        },
        campos_esperados=("numeroControlePncpAta",),
    ),
    RotaUpstream(
        id="arp_itens",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/2_consultarARPItem",
        tools=("compras_arp_itens_listar",),
        params={
            "dataVigenciaInicialMin": _dias_atras(180),
            "dataVigenciaInicialMax": _hoje_iso(),
        },
        # valorUnitario é preço: a ata é fonte de preço tanto quanto a
        # pesquisa de preço, então entra no contrato de campos.
        campos_esperados=("numeroItem", "valorUnitario", "niFornecedor"),
    ),
    RotaUpstream(
        id="arp_unidades_item",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/3_consultarUnidadesItem",
        tools=("compras_arp_unidades_item",),
        seed=(
            "arp_itens",
            {
                "numeroAta": "numeroAtaRegistroPreco",
                "unidadeGerenciadora": "codigoUnidadeGerenciadora",
                "numeroItem": "numeroItem",
            },
        ),
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="arp_saldo_item",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/4_consultarEmpenhosSaldoItem",
        tools=("compras_arp_saldo_item",),
        seed=(
            "arp_itens",
            {
                "numeroAta": "numeroAtaRegistroPreco",
                "unidadeGerenciadora": "codigoUnidadeGerenciadora",
            },
        ),
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="arp_adesoes_item",
        api="dados_abertos",
        modulo="atas",
        path="/modulo-arp/5_consultarAdesoesItem",
        tools=("compras_arp_adesoes_item",),
        seed=(
            "arp_itens",
            {
                "numeroAta": "numeroAtaRegistroPreco",
                "unidadeGerenciadora": "codigoUnidadeGerenciadora",
                "numeroItem": "numeroItem",
            },
        ),
        aceita_vazio=True,
    ),
    # --- 07 Contratações 14.133 -------------------------------------------
    RotaUpstream(
        id="contratacoes_listar",
        api="dados_abertos",
        modulo="contratacoes",
        path="/modulo-contratacoes/1_consultarContratacoes_PNCP_14133",
        tools=("compras_contratacoes_14133_listar", "compras_buscar_contratacoes_similares"),
        params={
            "dataPublicacaoPncpInicial": _dias_atras(60),
            "dataPublicacaoPncpFinal": _hoje_iso(),
            "codigoModalidade": 6,
        },
        campos_esperados=("numeroControlePNCP", "objetoCompra"),
    ),
    RotaUpstream(
        id="contratacoes_por_id",
        api="dados_abertos",
        modulo="contratacoes",
        path="/modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id",
        tools=("compras_contratacoes_14133_consultar",),
        params={"tipo": "numeroControlePNCPCompra"},
        seed=("contratacoes_listar", {"codigo": "numeroControlePNCP"}),
        campos_esperados=("numeroControlePNCP",),
    ),
    RotaUpstream(
        id="contratacoes_itens",
        api="dados_abertos",
        modulo="contratacoes",
        path="/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133",
        tools=("compras_contratacoes_14133_itens_listar",),
        params={
            "dataInclusaoPncpInicial": _dias_atras(60),
            "dataInclusaoPncpFinal": _hoje_iso(),
        },
        campos_esperados=("codItemCatalogo", "idCompraItem"),
    ),
    RotaUpstream(
        id="contratacoes_itens_por_id",
        api="dados_abertos",
        modulo="contratacoes",
        path="/modulo-contratacoes/2.1_consultarItensContratacoes_PNCP_14133_Id",
        tools=("compras_contratacoes_14133_itens_por_contratacao",),
        params={"tipo": "numeroControlePNCPCompra"},
        seed=("contratacoes_listar", {"codigo": "numeroControlePNCP"}),
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="contratacoes_resultados",
        api="dados_abertos",
        modulo="contratacoes",
        path="/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133",
        tools=("compras_contratacoes_14133_resultados_listar",),
        params={
            "dataResultadoPncpInicial": _dias_atras(60),
            "dataResultadoPncpFinal": _hoje_iso(),
        },
        campos_esperados=("niFornecedor",),
    ),
    RotaUpstream(
        id="contratacoes_resultados_por_id",
        api="dados_abertos",
        modulo="contratacoes",
        path="/modulo-contratacoes/3.1_consultarResultadoItensContratacoes_PNCP_14133_Id",
        tools=("compras_contratacoes_14133_resultados_por_contratacao",),
        params={"tipo": "numeroControlePNCPCompra"},
        seed=("contratacoes_listar", {"codigo": "numeroControlePNCP"}),
        aceita_vazio=True,
    ),
    # --- 09 Contratos ------------------------------------------------------
    RotaUpstream(
        id="contratos_listar",
        api="dados_abertos",
        modulo="contratos",
        path="/modulo-contratos/1_consultarContratos",
        tools=("compras_contratos_listar",),
        params={
            "codigoOrgao": "52121",
            "dataVigenciaInicialMin": _dias_atras(365),
            "dataVigenciaInicialMax": _hoje_iso(),
        },
        campos_esperados=("numeroContrato", "numeroControlePncpContrato", "valorGlobal"),
    ),
    RotaUpstream(
        id="contratos_por_id",
        api="dados_abertos",
        modulo="contratos",
        path="/modulo-contratos/1.1_consultarContratos_Id",
        tools=("compras_contratos_consultar",),
        params={"tipo": "numeroControlePncpContrato"},
        seed=("contratos_listar", {"codigo": "numeroControlePncpContrato"}),
        campos_esperados=("numeroContrato",),
    ),
    RotaUpstream(
        id="contratos_fim_vigencia",
        api="dados_abertos",
        modulo="contratos",
        path="/modulo-contratos/1.2_consultarContratos_FimVigencia",
        tools=("compras_contratos_listar_por_fim_vigencia",),
        params={
            "codigoOrgao": "52121",
            "dataVigenciaFinalMin": _hoje_iso(),
            "dataVigenciaFinalMax": (_hoje() + timedelta(days=365)).isoformat(),
        },
        campos_esperados=("numeroContrato",),
    ),
    RotaUpstream(
        id="contratos_itens",
        api="dados_abertos",
        modulo="contratos",
        path="/modulo-contratos/2_consultarContratosItem",
        tools=("compras_contratos_itens_listar",),
        params={
            "codigoOrgao": "52121",
            "dataVigenciaInicialMin": _dias_atras(365),
            "dataVigenciaInicialMax": _hoje_iso(),
        },
        aceita_vazio=True,
    ),
    # --- 10 Fornecedor -----------------------------------------------------
    RotaUpstream(
        id="fornecedor",
        api="dados_abertos",
        modulo="fornecedores",
        path="/modulo-fornecedor/1_consultarFornecedor",
        tools=("compras_fornecedor_listar", "compras_fornecedor_consultar"),
        params={"ativo": "true"},
        campos_esperados=("cnpj", "nomeRazaoSocialFornecedor", "habilitadoLicitar"),
    ),
    # --- 97 Indicadores ----------------------------------------------------
    RotaUpstream(
        id="indicadores_consolidados",
        api="dados_abertos",
        modulo="indicadores",
        path="/modulo-indicadores/1_consultarIndicadoresConsolidados",
        tools=("compras_indicadores_consolidados",),
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="indicadores_periodo",
        api="dados_abertos",
        modulo="indicadores",
        path="/modulo-indicadores/2_consultarIndicadoresPorPeriodo",
        tools=("compras_indicadores_por_periodo",),
        params={"ano": _ano_passado()},
        aceita_vazio=True,
    ),
    # --- 06 Legado 8.666 ---------------------------------------------------
    RotaUpstream(
        id="legado_licitacoes",
        api="dados_abertos",
        modulo="legado",
        path="/modulo-legado/1_consultarLicitacao",
        tools=("compras_legado_licitacoes_listar",),
        # Janela histórica fixa, não relativa: o módulo legado cobre a Lei
        # 8.666, cujo estoque parou de crescer com a 14.133. Uma janela
        # "últimos 12 meses" devolveria zero e acusaria falsa quebra.
        params=dict(_JANELA_LEGADO_PUBLICACAO),
        campos_esperados=("id_compra", "data_publicacao"),
    ),
    RotaUpstream(
        id="legado_licitacao_id",
        api="dados_abertos",
        modulo="legado",
        path="/modulo-legado/1.1_consultarLicitacao_Id",
        tools=("compras_legado_licitacao_consultar",),
        seed=("legado_licitacoes", {"id_compra": "id_compra"}),
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="legado_itens",
        api="dados_abertos",
        modulo="legado",
        path="/modulo-legado/2_consultarItemLicitacao",
        tools=("compras_legado_itens_licitacao_listar",),
        params={"modalidade": 5},
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="legado_pregoes",
        api="dados_abertos",
        modulo="legado",
        path="/modulo-legado/3_consultarPregoes",
        tools=("compras_legado_pregoes_listar",),
        params={
            "dt_data_edital_inicial": "2019-01-01",
            "dt_data_edital_final": "2019-03-31",
        },
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="legado_sem_licitacao",
        api="dados_abertos",
        modulo="legado",
        path="/modulo-legado/5_consultarComprasSemLicitacao",
        tools=("compras_legado_compras_sem_licitacao",),
        params={"dt_ano_aviso": _ano_passado()},
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="legado_rdc",
        api="dados_abertos",
        modulo="legado",
        path="/modulo-legado/7_consultarRdc",
        tools=("compras_legado_rdc_listar",),
        params={
            "data_publicacao_min": "2019-01-01",
            "data_publicacao_max": "2019-12-31",
        },
        aceita_vazio=True,
    ),
    # --- 04 PGC ------------------------------------------------------------
    RotaUpstream(
        id="pgc_detalhe",
        api="dados_abertos",
        modulo="planejamento",
        path="/modulo-pgc/1_consultarPgcDetalhe",
        tools=("compras_pgc_listar",),
        params={"orgao": "52121", "anoPcaProjetoCompra": _hoje().year},
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="pgc_detalhe_csv",
        api="dados_abertos",
        modulo="planejamento",
        path="/modulo-pgc/1.1_consultarPgcDetalhe_CSV",
        tools=("compras_pgc_listar_csv",),
        params={"orgao": "52121", "anoPcaProjetoCompra": _hoje().year},
        aceita_vazio=True,
        # Com `Accept: application/json` esta rota responde 500
        # ("No acceptable representation") — é CSV-only.
        accept="*/*",
        observacao="Responde text/csv, não JSON",
    ),
    RotaUpstream(
        id="pgc_por_catalogo",
        api="dados_abertos",
        modulo="planejamento",
        path="/modulo-pgc/2_consultarPgcDetalheCatalogo",
        tools=("compras_pgc_por_catalogo",),
        params={
            "anoPcaProjetoCompra": _hoje().year,
            "tipo": "Material",
            "codigo": 630237,
        },
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="pgc_agregacao",
        api="dados_abertos",
        modulo="planejamento",
        path="/modulo-pgc/3_consultarPgcAgregacao",
        tools=("compras_pgc_agregacao",),
        params={"orgao": "52121", "ano": _hoje().year},
        aceita_vazio=True,
    ),
]


# ---------------------------------------------------------------------------
# PNCP — pncp.gov.br/api/consulta
#
# O PNCP responde entre 2s e 20s conforme a janela; por isso todas as rotas
# levam timeout próprio (45s). Com o timeout global de 15s, rotas saudáveis
# porém lentas apareceriam como "fora" e mandariam a equipe caçar defeito
# onde só há latência.
# ---------------------------------------------------------------------------

_TIMEOUT_PNCP = 45.0

_PNCP: list[RotaUpstream] = [
    RotaUpstream(
        id="pncp_contratacoes_publicacao",
        api="pncp",
        modulo="pncp",
        path="/v1/contratacoes/publicacao",
        tools=("compras_pncp_contratacoes_publicacao",),
        params={
            "dataInicial": (_hoje() - timedelta(days=30)).strftime("%Y%m%d"),
            "dataFinal": _hoje().strftime("%Y%m%d"),
            "codigoModalidadeContratacao": 6,
        },
        campos_esperados=("numeroControlePNCP", "orgaoEntidade", "sequencialCompra"),
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_contratacoes_proposta",
        api="pncp",
        modulo="pncp",
        path="/v1/contratacoes/proposta",
        tools=("compras_pncp_contratacoes_proposta",),
        params={
            "dataFinal": (_hoje() + timedelta(days=30)).strftime("%Y%m%d"),
            "codigoModalidadeContratacao": 6,
        },
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_contratacoes_atualizacao",
        api="pncp",
        modulo="pncp",
        path="/v1/contratacoes/atualizacao",
        tools=("compras_pncp_contratacoes_atualizacao",),
        params={
            "dataInicial": (_hoje() - timedelta(days=7)).strftime("%Y%m%d"),
            "dataFinal": _hoje().strftime("%Y%m%d"),
            "codigoModalidadeContratacao": 6,
        },
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_atas",
        api="pncp",
        modulo="pncp",
        path="/v1/atas",
        tools=("compras_pncp_atas_listar",),
        params={
            "dataInicial": (_hoje() - timedelta(days=30)).strftime("%Y%m%d"),
            "dataFinal": _hoje().strftime("%Y%m%d"),
        },
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_contratos",
        api="pncp",
        modulo="pncp",
        path="/v1/contratos",
        tools=("compras_pncp_contratos_listar",),
        params={
            "dataInicial": (_hoje() - timedelta(days=30)).strftime("%Y%m%d"),
            "dataFinal": _hoje().strftime("%Y%m%d"),
        },
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_pca",
        api="pncp",
        modulo="pncp",
        path="/v1/pca/",
        tools=("compras_pncp_pca_listar",),
        params={"anoPca": _hoje().year, "codigoClassificacaoSuperior": "979"},
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_pca_atualizacao",
        api="pncp",
        modulo="pncp",
        path="/v1/pca/atualizacao",
        tools=("compras_pncp_pca_atualizacao",),
        params={
            "dataInicio": (_hoje() - timedelta(days=30)).strftime("%Y%m%d"),
            "dataFim": _hoje().strftime("%Y%m%d"),
        },
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    # Rotas aninhadas: o CNPJ/ano/sequencial vêm de uma contratação real,
    # colhida da rota de publicação — nada de ID fixo no código.
    RotaUpstream(
        id="pncp_compra_por_orgao",
        api="pncp",
        modulo="pncp",
        path="/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}",
        tools=("compras_pncp_contratacao_por_orgao",),
        path_params=("cnpj", "ano", "sequencial"),
        seed=(
            "pncp_contratacoes_publicacao",
            {
                "cnpj": "orgaoEntidade.cnpj",
                "ano": "anoCompra",
                "sequencial": "sequencialCompra",
            },
        ),
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
    ),
    RotaUpstream(
        id="pncp_compra_itens",
        api="pncp",
        modulo="pncp",
        path="/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens",
        tools=(
            "compras_pncp_contratacao_itens",
            "compras_pncp_contratacao_item_resultados",
        ),
        path_params=("cnpj", "ano", "sequencial"),
        seed=(
            "pncp_contratacoes_publicacao",
            {
                "cnpj": "orgaoEntidade.cnpj",
                "ano": "anoCompra",
                "sequencial": "sequencialCompra",
            },
        ),
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
        observacao="Sub-rota não consta no contrato /api/consulta/v3/api-docs",
    ),
    RotaUpstream(
        id="pncp_orgao_unidades",
        api="pncp",
        modulo="pncp",
        path="/v1/orgaos/{cnpj}/unidades",
        tools=("compras_pncp_orgao_unidades",),
        path_params=("cnpj",),
        # Semeia das atas (e não das contratações) porque /v1/atas se mantém
        # de pé nos dias em que /v1/contratacoes/* cai.
        seed=("pncp_atas", {"cnpj": "cnpjOrgao"}),
        aceita_vazio=True,
        timeout_s=_TIMEOUT_PNCP,
        observacao=(
            "Rota NÃO consta no contrato do PNCP Consulta (12 rotas publicadas "
            "em /api/consulta/v3/api-docs, nenhuma com /unidades) — 404 para "
            "todo CNPJ, inclusive os que publicam ativamente. Verificado 2026-08-05."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Portal da Transparência (CGU) — exige TRANSPARENCIA_API_KEY
# ---------------------------------------------------------------------------

_TRANSPARENCIA: list[RotaUpstream] = [
    RotaUpstream(
        id="ceis",
        api="transparencia",
        modulo="sancoes",
        path="/api-de-dados/ceis",
        tools=("compras_sancao_ceis", "compras_checar_sancoes_fornecedor"),
        params={"pagina": 1},
        exige_credencial="TRANSPARENCIA_API_KEY",
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="cnep",
        api="transparencia",
        modulo="sancoes",
        path="/api-de-dados/cnep",
        tools=("compras_sancao_cnep", "compras_checar_sancoes_fornecedor"),
        params={"pagina": 1},
        exige_credencial="TRANSPARENCIA_API_KEY",
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="cepim",
        api="transparencia",
        modulo="sancoes",
        path="/api-de-dados/cepim",
        tools=("compras_sancao_cepim",),
        params={"pagina": 1},
        exige_credencial="TRANSPARENCIA_API_KEY",
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="ceaf",
        api="transparencia",
        modulo="sancoes",
        path="/api-de-dados/ceaf",
        tools=("compras_sancao_ceaf",),
        params={"pagina": 1},
        exige_credencial="TRANSPARENCIA_API_KEY",
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="acordos_leniencia",
        api="transparencia",
        modulo="sancoes",
        path="/api-de-dados/acordos-leniencia",
        tools=("compras_sancao_acordos_leniencia",),
        params={"pagina": 1},
        exige_credencial="TRANSPARENCIA_API_KEY",
        aceita_vazio=True,
    ),
]


# ---------------------------------------------------------------------------
# Comprasnet Contratos (rotas abertas) e BrasilAPI (CNPJ)
# ---------------------------------------------------------------------------

_OUTRAS: list[RotaUpstream] = [
    RotaUpstream(
        id="comprasnet_contratos_uasg",
        api="comprasnet",
        modulo="comprasnet",
        # A base URL já termina em /api — o path não repete o prefixo.
        path="/contrato/ug/160240",
        tools=("compras_contrato_comprasnet_por_uasg",),
        campos_esperados=("id", "contratante", "fornecedor", "valor_global"),
        aceita_vazio=True,
    ),
    RotaUpstream(
        id="cnpj_receita",
        api="cnpj",
        modulo="enriquecimento",
        path="/api/cnpj/v1/33000167000101",
        tools=("compras_fornecedor_cnpj_receita",),
        campos_esperados=("cnpj",),
        observacao="BrasilAPI — CNPJ da Petrobras",
    ),
]


ROTAS: tuple[RotaUpstream, ...] = tuple(
    _DADOS_ABERTOS + _PNCP + _TRANSPARENCIA + _OUTRAS
)

MODULOS: tuple[str, ...] = tuple(dict.fromkeys(r.modulo for r in ROTAS))


def rotas_por_modulo(modulo: str) -> tuple[RotaUpstream, ...]:
    return tuple(r for r in ROTAS if r.modulo == modulo)


def rota_por_id(rota_id: str) -> RotaUpstream | None:
    return next((r for r in ROTAS if r.id == rota_id), None)
