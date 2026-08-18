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
    pagina: int = Field(default=1, ge=1, description="Página (1-based).")
    tamanho_pagina: int = Field(
        default=50, ge=1, le=500, description="Registros por página."
    )


class ConsultarContratacao14133Input(BaseModel):
    id_contratacao: int = Field(
        description="Identificador interno da contratação (campo `id` retornado em listar_contratacoes_14133).",
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
