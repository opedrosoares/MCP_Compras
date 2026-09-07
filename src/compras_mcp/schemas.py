"""Single source of truth para descrições e validações de parâmetros das tools.

As `description` dos Pydantic Fields ficam SOMENTE aqui — os módulos
`tools/*.py` importam via helper `desc(Model, "campo")`. Teste em
`tests/test_server.py` valida que a descrição do schema MCP é idêntica
à do Pydantic Field (replica o padrão do mcp-inpi).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ============================================================================
# Diagnóstico / versão
# ============================================================================


class VersaoOutput(BaseModel):
    """Output da tool compras_versao — healthcheck/diagnóstico."""

    nome: str = Field(description="Nome do servidor MCP")
    versao: str = Field(description="Versão semântica do pacote")
    fontes: dict[str, str] = Field(
        description="Mapa de APIs upstream cobertas pelas tools, com a base URL configurada para cada uma"
    )
    redis_configurado: bool = Field(
        description="True quando REDIS_URL está setada — cache será compartilhado entre pods"
    )
    transparencia_configurada: bool = Field(
        description="True quando TRANSPARENCIA_API_KEY está setada — habilita tools de sanções"
    )


class HealthcheckInput(BaseModel):
    """Parâmetros da tool compras_healthcheck."""

    profundidade: Literal["basico", "rotas"] = Field(
        default="rotas",
        description=(
            "'rotas' (padrão) testa as rotas upstream reais em paralelo e "
            "devolve situação por módulo (ok/degradado/fora) em ~30s. "
            "'basico' devolve só versão e configuração, sem tocar a rede."
        ),
    )
    modulo: str | None = Field(
        default=None,
        description=(
            "Restringe o probe a um módulo funcional: 'pesquisa_preco', "
            "'catalogo', 'organizacoes', 'atas', 'contratacoes', 'contratos', "
            "'fornecedores', 'indicadores', 'legado', 'planejamento', 'pncp', "
            "'sancoes', 'comprasnet', 'enriquecimento'. Sem valor, testa todos."
        ),
    )


# ============================================================================
# Catálogo (CATMAT/CATSER)
# ============================================================================


class ListarPaginadoInput(BaseModel):
    """Parâmetros comuns de paginação para tools `listar_*`."""

    pagina: int = Field(
        default=1,
        ge=1,
        description="Página de resultados (1-based). Padrão 1.",
    )
    tamanho_pagina: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Quantidade de registros por página. Padrão 50, máximo 500.",
    )


class ConsultarCatmatInput(BaseModel):
    codigo_item: int = Field(
        description=(
            "Código numérico do item no CATMAT (Catálogo de Materiais). "
            "Inteiro de 4 a 8 dígitos. Exemplo: 460789."
        )
    )


class ConsultarCatserInput(BaseModel):
    codigo_item: int = Field(
        description=(
            "Código numérico do item no CATSER (Catálogo de Serviços). "
            "Inteiro de 4 a 6 dígitos. Exemplo: 27332."
        )
    )


class ListarPdmMaterialInput(BaseModel):
    """Filtros de `/modulo-material/3_consultarPdmMaterial`."""

    codigo_grupo: int | None = Field(
        default=None,
        description="Código do grupo CATMAT (2 dígitos) para listar seus PDMs.",
    )
    codigo_classe: int | None = Field(
        default=None,
        description=(
            "Código da classe CATMAT (4 dígitos). É o recorte mais útil: uma "
            "classe devolve suas dezenas de PDMs em uma única chamada."
        ),
    )
    codigo_pdm: int | None = Field(
        default=None, description="Código de um PDM específico."
    )
    apenas_ativos: bool | None = Field(
        default=None,
        description="Se `true`, só PDMs com status ativo no catálogo.",
    )


class BuscarItemCatalogoInput(BaseModel):
    termo: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "Termo de busca textual (descrição do material/serviço). Aceita "
            "fragmento — a API faz match parcial. Ex.: 'cadeira ergonomica'."
        ),
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


# ============================================================================
# Pesquisa de Preço
# ============================================================================


class PesquisarPrecoMaterialInput(BaseModel):
    codigo_item_catalogo: int = Field(
        description="Código CATMAT do material. Inteiro 4-8 dígitos. Ex.: 460789."
    )
    data_inicio: date | None = Field(
        default=None,
        description=(
            "Data inicial da compra (YYYY-MM-DD). Quando omitida, a API usa "
            "o início do ano corrente."
        ),
    )
    data_fim: date | None = Field(
        default=None,
        description=(
            "Data final da compra (YYYY-MM-DD). Quando omitida, a API usa "
            "a data atual."
        ),
    )
    uf: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Sigla da UF (ex.: 'DF'). Filtra compras realizadas pelo órgão da UF.",
    )
    codigo_municipio: int | None = Field(
        default=None,
        description="Código IBGE do município (7 dígitos). Filtro mais fino que UF.",
    )
    codigo_uasg: int | None = Field(
        default=None,
        description="Código da UASG compradora (filtro mais específico ainda).",
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


class PesquisarPrecoServicoInput(BaseModel):
    codigo_item_catalogo: int = Field(
        description="Código CATSER do serviço. Inteiro 4-6 dígitos. Ex.: 27332."
    )
    data_inicio: date | None = Field(
        default=None, description="Data inicial (YYYY-MM-DD)."
    )
    data_fim: date | None = Field(
        default=None, description="Data final (YYYY-MM-DD)."
    )
    uf: str | None = Field(
        default=None, min_length=2, max_length=2, description="Sigla da UF."
    )
    codigo_municipio: int | None = Field(
        default=None, description="Código IBGE do município."
    )
    codigo_uasg: int | None = Field(default=None, description="Código UASG.")
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


class PesquisarPrecosParaETPInput(BaseModel):
    """Composta — agrega preços no padrão IN SEGES/ME 65/2021."""

    tipo: Literal["material", "servico"] = Field(
        description=(
            "Tipo do item: 'material' (consulta CATMAT) ou 'servico' (consulta CATSER)."
        )
    )
    codigo_item_catalogo: int = Field(
        description="Código CATMAT (material) ou CATSER (serviço)."
    )
    periodo_meses: int = Field(
        default=12,
        ge=1,
        le=24,
        description=(
            "Janela de pesquisa em meses contados de hoje para trás. Default 12 "
            "(prazo recomendado pela IN SEGES/ME 65/2021 art. 5)."
        ),
    )
    uf: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Filtro opcional por UF (ex.: 'DF').",
    )
    max_paginas: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Número máximo de páginas a percorrer ao agregar. Cada página tem 500 registros. "
            "Default 5 (até 2500 contratações). Aumente para amostras maiores."
        ),
    )


# ============================================================================
# Contratações (Lei 14.133)
# ============================================================================


class ListarContratacoes14133Input(BaseModel):
    data_inicial_publicacao: date | None = Field(
        default=None,
        description="Data inicial de publicação (YYYY-MM-DD).",
    )
    data_final_publicacao: date | None = Field(
        default=None,
        description="Data final de publicação (YYYY-MM-DD).",
    )
    codigo_uasg: int | None = Field(
        default=None, description="Código UASG do órgão licitante."
    )
    cnpj_orgao: str | None = Field(
        default=None,
        description="CNPJ do órgão (14 dígitos, com ou sem pontuação).",
    )
    modalidade: int | None = Field(
        default=None,
        description=(
            "Código da modalidade de contratação (PNCP/14.133). Exemplos: "
            "6=Pregão Eletrônico, 8=Dispensa, 5=Concorrência, 9=Inexigibilidade."
        ),
    )
    codigo_orgao_pncp: int | None = Field(
        default=None,
        description=(
            "Código do órgão **no espaço de códigos interno do PNCP** — é o "
            "campo `codigoOrgao` que vem no payload desta mesma tool, e só ele. "
            "**Não é o código SIASG** de `compras_orgao_listar`/"
            "`compras_orgao_consultar`: os dois espaços não coincidem (a UFSC é "
            "26246 no SIASG e 86135 aqui) e passar o código SIASG devolve zero "
            "registros ou, quando o número existe nos dois, as contratações de "
            "OUTRO órgão. Para recortar por órgão partindo do que você conhece, "
            "use `cnpj_orgao` (CNPJ) ou `codigo_uasg`."
        ),
    )
    uf: str | None = Field(
        default=None,
        description="Sigla da UF da unidade compradora (2 letras, ex.: 'SP', 'MS').",
    )
    codigo_ibge_municipio: int | None = Field(
        default=None,
        description="Código IBGE do município da unidade compradora (7 dígitos).",
    )
    amparo_legal: int | None = Field(
        default=None,
        description=(
            "Código do amparo legal no PNCP (campo `amparoLegalCodigoPncp`). "
            "Ex.: 18 = Lei 14.133/2021, Art. 75, I (dispensa por valor)."
        ),
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


class ListarItensContratacoes14133Input(BaseModel):
    """Filtros de `/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133`."""

    data_inicial_inclusao: date = Field(
        description="Data inicial de inclusão dos itens no PNCP (YYYY-MM-DD).",
    )
    data_final_inclusao: date = Field(
        description="Data final de inclusão dos itens no PNCP (YYYY-MM-DD).",
    )
    cod_item_catalogo: int | None = Field(
        default=None,
        description=(
            "Código do item no catálogo (CATMAT para material, CATSER para "
            "serviço). É o filtro que transforma esta tool em pesquisa de preço: "
            "devolve, na mesma linha, quantidade, valor unitário estimado e "
            "valor unitário homologado do item."
        ),
    )
    material_ou_servico: str | None = Field(
        default=None,
        description="'M' para material, 'S' para serviço.",
    )
    codigo_grupo: int | None = Field(
        default=None,
        description=(
            "Código do grupo do catálogo. Recorte por família quando o código "
            "exato do item ainda não é conhecido."
        ),
    )
    codigo_classe: int | None = Field(
        default=None,
        description=(
            "Código da classe do catálogo. Vem nulo em boa parte dos serviços — "
            "nesses casos use `codigo_grupo`."
        ),
    )
    tem_resultado: bool | None = Field(
        default=None,
        description=(
            "Se `true`, só itens que tiveram vencedor — filtro aplicado pelo "
            "upstream. Use para pesquisa de preço: item deserto ou fracassado "
            "não é preço praticado e não pode entrar na média do ETP. Se "
            "`false`, o recorte é feito aqui, client-side, sobre a página "
            "trazida: o upstream grava `temResultado: null` (não `false`) nos "
            "itens sem vencedor, então mandar `temResultado=false` para ele "
            "devolveria zero registros sempre."
        ),
    )
    situacao_item: str | None = Field(
        default=None,
        description=(
            "Situação do item da compra. '2' = Homologado, '4' = Cancelado. "
            "Filtre por '2' antes de calcular qualquer estatística de preço."
        ),
    )
    cnpj_orgao: str | None = Field(
        default=None,
        description="CNPJ do órgão comprador (14 dígitos, com ou sem pontuação).",
    )
    codigo_uasg: int | None = Field(
        default=None, description="Código da UASG compradora (6 dígitos)."
    )
    cnpj_cpf_fornecedor: str | None = Field(
        default=None,
        description="CNPJ ou CPF do fornecedor vencedor do item (só dígitos).",
    )


class ListarResultadosContratacoes14133Input(BaseModel):
    """Filtros de `/modulo-contratacoes/3_consultarResultadoItensContratacoes...`."""

    data_inicial_resultado: date = Field(
        description="Data inicial do resultado/homologação (YYYY-MM-DD).",
    )
    data_final_resultado: date = Field(
        description="Data final do resultado/homologação (YYYY-MM-DD).",
    )
    ni_fornecedor: str | None = Field(
        default=None,
        description=(
            "Número de identificação do fornecedor vencedor (CNPJ ou CPF, só "
            "dígitos). Use para levantar tudo que um fornecedor ganhou no período."
        ),
    )
    porte_fornecedor: int | None = Field(
        default=None,
        description=(
            "Código do porte do fornecedor (ex.: 1=ME, 2=EPP, 3=Demais). "
            "Preenchimento irregular na origem — trate ausência como desconhecido."
        ),
    )
    situacao_resultado: int | None = Field(
        default=None,
        description=(
            "Código da situação do resultado. 1 = Informado. Use para descartar "
            "resultado cancelado antes de calcular média ou mediana de preço."
        ),
    )
    valor_unitario_min: float | None = Field(
        default=None, description="Valor unitário homologado mínimo (R$)."
    )
    valor_unitario_max: float | None = Field(
        default=None, description="Valor unitário homologado máximo (R$)."
    )
    valor_total_min: float | None = Field(
        default=None,
        description=(
            "Valor total homologado mínimo (R$). Combinado com a janela de datas, "
            "monta fila de auditoria por materialidade."
        ),
    )
    valor_total_max: float | None = Field(
        default=None, description="Valor total homologado máximo (R$)."
    )
    cnpj_orgao: str | None = Field(
        default=None,
        description="CNPJ do órgão comprador (14 dígitos, com ou sem pontuação).",
    )
    codigo_uasg: int | None = Field(
        default=None, description="Código da UASG compradora (6 dígitos)."
    )


class ListarItensPregaoLegadoInput(BaseModel):
    """Filtros de `/modulo-legado/4_consultarItensPregoes` (Lei 8.666).

    Com `id_compra` a consulta é redirecionada para a variante `4.1`, que busca
    os itens de um pregão específico e dispensa a janela de homologação.
    """

    data_homologacao_inicial: date | None = Field(
        default=None,
        description=(
            "Data inicial de homologação dos itens (YYYY-MM-DD). Obrigatória "
            "quando `id_compra` não é informado."
        ),
    )
    data_homologacao_final: date | None = Field(
        default=None,
        description=(
            "Data final de homologação dos itens (YYYY-MM-DD). Obrigatória "
            "quando `id_compra` não é informado."
        ),
    )
    id_compra: str | None = Field(
        default=None,
        description=(
            "Identificador do pregão, para trazer só os itens dele. É a "
            "concatenação zero-padded de UASG(6) + modalidade(2) + número(5) + "
            "ano(4) — ex.: '38916105000152022'. Também é o campo `id_compra` "
            "devolvido por `compras_legado_pregoes_listar`."
        ),
    )
    id_compra_item: str | None = Field(
        default=None,
        description="Identificador de um item específico dentro do pregão.",
    )
    codigo_uasg: int | None = Field(
        default=None,
        description="Código da UASG que realizou o pregão (só na busca por período).",
    )
    decreto_7174: str | None = Field(
        default=None,
        description=(
            "Filtra itens sujeitos ao Decreto 7.174/2010 (bens e serviços de "
            "informática)."
        ),
    )


class ListarItensSemLicitacaoLegadoInput(BaseModel):
    """Filtros de `/modulo-legado/6_consultarCompraItensSemLicitacao`.

    Com `id_compra` a consulta é redirecionada para a variante `6.1`, que busca
    os itens de uma compra específica e dispensa o ano do aviso.
    """

    ano_aviso: int | None = Field(
        default=None,
        description=(
            "Ano do aviso da contratação direta. Obrigatório quando `id_compra` "
            "não é informado. Cobertura útil principalmente entre 2019 e 2021, "
            "período anterior ao PNCP — para 2022 em diante prefira "
            "`compras_contratacoes_14133_itens_listar`."
        ),
    )
    id_compra: str | None = Field(
        default=None,
        description="Identificador da compra, para trazer só os itens dela.",
    )
    id_compra_item: str | None = Field(
        default=None,
        description="Identificador de um item específico dentro da compra.",
    )
    codigo_uasg: int | None = Field(
        default=None, description="Código da UASG contratante."
    )
    codigo_orgao: str | None = Field(
        default=None, description="Código do órgão contratante."
    )
    codigo_modalidade: int | None = Field(
        default=None,
        description="Código da modalidade legada (dispensa, inexigibilidade).",
    )
    codigo_conjunto_materiais: int | None = Field(
        default=None, description="Código do conjunto de materiais (CATMAT legado)."
    )
    codigo_servico: int | None = Field(
        default=None, description="Código do serviço (CATSER legado)."
    )
    cpf_cnpj_fornecedor: str | None = Field(
        default=None,
        description="CPF ou CNPJ do fornecedor vencedor (só dígitos).",
    )


class ConsultarContratacao14133Input(BaseModel):
    id_contratacao: str = Field(
        description=(
            "Identificador da contratação. Aceita dois formatos, conforme "
            "`tipo_identificador`: o `idCompra` (17 dígitos, campo `idCompra` das "
            "listagens, ex.: '15813206001272025') ou o número de controle PNCP "
            "(alfanumérico com barra, campo `numeroControlePNCP`, ex.: "
            "'10673078000120-1-000021/2025' — é o número que aparece no edital)."
        ),
    )
    tipo_identificador: str = Field(
        default="idCompra",
        description=(
            "Qual identificador está sendo passado em `id_contratacao`: "
            "'idCompra' (padrão) ou 'numeroControlePNCPCompra'. O upstream "
            "rejeita qualquer outro valor com HTTP 500."
        ),
    )


# ============================================================================
# Atas de Registro de Preço (ARP)
# ============================================================================


class ListarAtasInput(BaseModel):
    data_inicio_vigencia: date | None = Field(
        default=None,
        description="Data inicial de vigência (YYYY-MM-DD).",
    )
    data_fim_vigencia: date | None = Field(
        default=None,
        description="Data final de vigência (YYYY-MM-DD).",
    )
    codigo_uasg_gerenciadora: int | None = Field(
        default=None,
        description="Código UASG da unidade gerenciadora da ata.",
    )
    cnpj_fornecedor: str | None = Field(
        default=None,
        description="CNPJ do fornecedor da ata (14 dígitos, com ou sem pontuação).",
    )
    apenas_vigentes: bool = Field(
        default=True,
        description=(
            "Quando True (padrão), filtra apenas atas com vigência atual. "
            "Set False para incluir atas encerradas."
        ),
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


class ConsultarAtaInput(BaseModel):
    id_ata: int = Field(description="ID interno da ata (retornado em listar_atas).")


class MontarDossieARPInput(BaseModel):
    """Composta — dossie completo de uma ARP."""

    id_ata: int = Field(description="ID interno da ata.")


# ============================================================================
# Contratos (Dados Abertos)
# ============================================================================


class ListarContratosInput(BaseModel):
    data_inicio_vigencia: date | None = Field(
        default=None,
        description="Data inicial de vigência (YYYY-MM-DD).",
    )
    data_fim_vigencia: date | None = Field(
        default=None,
        description="Data final de vigência (YYYY-MM-DD).",
    )
    codigo_uasg: int | None = Field(
        default=None, description="Código UASG contratante."
    )
    cnpj_fornecedor: str | None = Field(
        default=None,
        description="CNPJ do fornecedor (14 dígitos, com ou sem pontuação).",
    )
    modalidade: int | None = Field(
        default=None,
        description="Código da modalidade de contratação.",
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


class ConsultarContratoItemInput(BaseModel):
    """Filtros de `/modulo-contratos/2.1_consultarContratosItem_Id`."""

    codigo: str = Field(
        description=(
            "Identificador do contrato: o `idCompra` numérico ou o número de "
            "controle PNCP do contrato, conforme `tipo_identificador`."
        ),
    )
    tipo_identificador: str = Field(
        default="idCompra",
        description=(
            "Qual identificador está em `codigo`: 'idCompra' (padrão) ou "
            "'numeroControlePncpContrato'. Outro valor devolve HTTP 500."
        ),
    )


class ConsultarContratoInput(BaseModel):
    id_contrato: int = Field(
        description="ID interno do contrato (campo `id` em listar_contratos).",
    )


# ============================================================================
# Fornecedores e Sanções
# ============================================================================


class ConsultarFornecedorInput(BaseModel):
    cnpj_cpf: str = Field(
        description=(
            "CNPJ (14 dígitos) ou CPF (11 dígitos) do fornecedor, "
            "com ou sem pontuação."
        ),
    )


class ConsultarSancaoCNPJInput(BaseModel):
    cnpj: str = Field(
        description="CNPJ do fornecedor (14 dígitos, com ou sem pontuação).",
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")


class ConsultarSancaoCPFInput(BaseModel):
    cpf: str = Field(
        description="CPF do servidor (11 dígitos, com ou sem pontuação).",
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")


class CheckarSancoesFornecedorInput(BaseModel):
    """Composta — consolida CEIS+CNEP+CEPIM+leniência em paralelo."""

    cnpj: str = Field(
        description="CNPJ do fornecedor (14 dígitos, com ou sem pontuação).",
    )


# ============================================================================
# PNCP — Portal Nacional
# ============================================================================


class PNCPListarContratacoesInput(BaseModel):
    data_inicial: date = Field(
        description="Data inicial de publicação (YYYY-MM-DD).",
    )
    data_final: date = Field(
        description="Data final de publicação (YYYY-MM-DD).",
    )
    codigo_modalidade: int = Field(
        description=(
            "Código da modalidade (obrigatório no PNCP). Códigos comuns: "
            "1=Leilão Eletrônico, 4=Concorrência Eletrônica, 6=Pregão Eletrônico, "
            "8=Dispensa, 9=Inexigibilidade, 13=Concurso."
        ),
    )
    uf: str | None = Field(
        default=None, min_length=2, max_length=2, description="Sigla da UF."
    )
    codigo_municipio_ibge: int | None = Field(
        default=None, description="Código IBGE do município (7 dígitos)."
    )
    cnpj_orgao: str | None = Field(
        default=None, description="CNPJ do órgão (14 dígitos)."
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=10, le=500, description="Registros por página (PNCP mínimo 10)."
    )


class PNCPListarPropostasAbertasInput(BaseModel):
    data_final: date = Field(
        description="Data limite para propostas (YYYY-MM-DD).",
    )
    codigo_modalidade: int = Field(
        description="Código da modalidade (ver PNCPListarContratacoesInput).",
    )
    uf: str | None = Field(
        default=None, min_length=2, max_length=2, description="Sigla da UF."
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(default=50, ge=10, le=500, description="Registros por página.")


class PNCPListarAtasInput(BaseModel):
    data_inicial: date = Field(description="Data inicial (YYYY-MM-DD).")
    data_final: date = Field(description="Data final (YYYY-MM-DD).")
    cnpj_orgao: str | None = Field(default=None, description="CNPJ do órgão (14 dígitos).")
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(default=50, ge=10, le=500, description="Registros por página.")


class BuscarContratacoesSimilaresInput(BaseModel):
    """Composta — federa Dados Abertos + PNCP."""

    codigo_catmat: int | None = Field(
        default=None,
        description="Código CATMAT do item. Mutuamente exclusivo com codigo_catser.",
    )
    codigo_catser: int | None = Field(
        default=None,
        description="Código CATSER do serviço. Mutuamente exclusivo com codigo_catmat.",
    )
    periodo_meses: int = Field(
        default=12,
        ge=1,
        le=24,
        description="Janela de busca em meses contados de hoje para trás.",
    )
    uf: str | None = Field(
        default=None, min_length=2, max_length=2, description="Filtro opcional por UF."
    )
    max_resultados: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Máximo de contratações similares a retornar (deduplicadas).",
    )


# ============================================================================
# Planejamento (PGC/PCA)
# ============================================================================


class ListarPGCInput(BaseModel):
    ano: int = Field(
        ge=2020,
        le=2099,
        description="Ano do PGC. Os PGCs do governo federal começam a aparecer a partir de 2020.",
    )
    codigo_orgao: int | None = Field(
        default=None,
        description="Código do órgão (filtra os PGCs desse órgão).",
    )
    codigo_uasg: int | None = Field(
        default=None,
        description="Código UASG (filtro mais específico que codigo_orgao).",
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(default=50, ge=1, le=500, description="Registros por página.")


class PNCPListarPCAInput(BaseModel):
    ano: int = Field(ge=2020, le=2099, description="Ano do PCA (Lei 14.133).")
    cnpj_orgao: str | None = Field(
        default=None,
        description="CNPJ do órgão (filtra PCAs desse órgão; 14 dígitos).",
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(default=50, ge=10, le=500, description="Registros por página.")


# ============================================================================
# Organizações (UASG/Órgão)
# ============================================================================


class ListarOrgaosInput(BaseModel):
    nome: str | None = Field(
        default=None, description="Filtro textual pelo nome do órgão (match parcial)."
    )
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(default=50, ge=1, le=500, description="Registros por página.")


class ConsultarUasgInput(BaseModel):
    codigo_uasg: int = Field(description="Código numérico da UASG.")


# ============================================================================
# Indicadores
# ============================================================================


class IndicadoresPorPeriodoInput(BaseModel):
    data_inicio: date = Field(description="Data inicial (YYYY-MM-DD).")
    data_fim: date = Field(description="Data final (YYYY-MM-DD).")


# ============================================================================
# Re-exports úteis (evita imports diretos em tools)
# ============================================================================

__all__ = [
    "Annotated",
    "BuscarContratacoesSimilaresInput",
    "BuscarItemCatalogoInput",
    "CheckarSancoesFornecedorInput",
    "ConsultarAtaInput",
    "ConsultarCatmatInput",
    "ConsultarCatserInput",
    "ConsultarContratacao14133Input",
    "ConsultarContratoInput",
    "ConsultarFornecedorInput",
    "ConsultarSancaoCNPJInput",
    "ConsultarSancaoCPFInput",
    "ConsultarUasgInput",
    "IndicadoresPorPeriodoInput",
    "ListarAtasInput",
    "ListarContratacoes14133Input",
    "ListarContratosInput",
    "ListarOrgaosInput",
    "ListarPGCInput",
    "ListarPaginadoInput",
    "Literal",
    "MontarDossieARPInput",
    "PNCPListarAtasInput",
    "PNCPListarContratacoesInput",
    "PNCPListarPCAInput",
    "PNCPListarPropostasAbertasInput",
    "PesquisarPrecoMaterialInput",
    "PesquisarPrecoServicoInput",
    "PesquisarPrecosParaETPInput",
    "VersaoOutput",
]
