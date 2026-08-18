"""MCP Resources — dados de referência expostos por URI.

Resources não são tools: o cliente MCP lista-os e o usuário (ou o LLM,
quando pertinente) lê o conteúdo sob demanda. Útil para:

- Tabelas de domínio (modalidades, esferas, situações) — evita chamada de
  rede só para descobrir um código.
- Glossário e cheat-sheets da Lei 14.133/2021.
- Metadados do servidor (escopo, fontes, limites).
"""

from __future__ import annotations

import json

from compras_mcp.dominio import (
    CRITERIOS_JULGAMENTO,
    ESFERAS_FEDERATIVAS,
    MODALIDADES_PNCP,
    SITUACOES_CONTRATACAO,
)
from compras_mcp.mcp_instance import mcp


@mcp.resource(
    "compras://referencia/modalidades-pncp",
    name="Modalidades de contratação (PNCP / Lei 14.133)",
    description=(
        "Tabela de referência dos códigos de modalidade aceitos pelo PNCP. "
        "Use antes de chamar tools que exigem `codigo_modalidade`."
    ),
    mime_type="application/json",
    tags={"referencia", "pncp", "lei-14133"},
)
def modalidades_pncp() -> str:
    return json.dumps({"modalidades": MODALIDADES_PNCP}, ensure_ascii=False, indent=2)


@mcp.resource(
    "compras://referencia/esferas-federativas",
    name="Esferas federativas (PNCP)",
    description=(
        "Códigos de esfera (`esferaId`) usados pelo PNCP para classificar "
        "órgãos. F=Federal, E=Estadual, M=Municipal, D=Distrital. Usado pelo "
        "filtro `esfera` das listagens."
    ),
    mime_type="application/json",
    tags={"referencia", "pncp"},
)
def esferas_federativas() -> str:
    return json.dumps({"esferas": ESFERAS_FEDERATIVAS}, ensure_ascii=False, indent=2)


@mcp.resource(
    "compras://referencia/criterios-julgamento",
    name="Critérios de julgamento (Lei 14.133)",
    description=(
        "Códigos de critério de julgamento expostos pelo PNCP, conforme "
        "art. 33 da Lei 14.133/2021."
    ),
    mime_type="application/json",
    tags={"referencia", "lei-14133"},
)
def criterios_julgamento() -> str:
    return json.dumps(
        {"criterios": CRITERIOS_JULGAMENTO}, ensure_ascii=False, indent=2
    )


@mcp.resource(
    "compras://referencia/situacoes-contratacao",
    name="Situações da contratação (PNCP)",
    description="Códigos de situação que aparecem em `situacaoCompraId`.",
    mime_type="application/json",
    tags={"referencia", "pncp"},
)
def situacoes_contratacao() -> str:
    return json.dumps(
        {"situacoes": SITUACOES_CONTRATACAO}, ensure_ascii=False, indent=2
    )


# ============================================================================
# Glossário e cheat-sheets
# ============================================================================


GLOSSARIO_14133 = """\
# Glossário — Lei 14.133/2021 e ecossistema Compras.gov.br

## Conceitos centrais da lei

**ETP — Estudo Técnico Preliminar** (art. 18)
Documento que precede o TR e fundamenta a contratação: necessidade, demanda,
levantamento de mercado, pesquisa de preços, escolha da modalidade.

**TR — Termo de Referência** (art. 6º, XXIII)
Para bens e serviços comuns. Conjunto mínimo de elementos: objeto, especificação,
quantidade, prazo, critérios de aceitação, fiscalização, sanções.

**Projeto Básico / Executivo** (art. 6º, XXV/XXVI)
Para obras e serviços de engenharia.

**Pesquisa de preços** (IN SEGES/ME 65/2021)
Mínimo de 3 fontes; preferência por painel oficial, contratações similares,
fornecedores. Tratamento estatístico obrigatório (mediana ou média + desvio).

## Modalidades (art. 28-32)

- **Pregão**: bens e serviços comuns. Forma eletrônica é regra (art. 17 § 2º).
- **Concorrência**: bens/serviços/obras com critério de menor preço, melhor
  técnica, técnica e preço.
- **Concurso**: trabalho intelectual, prêmio ou remuneração.
- **Leilão**: alienação de bens.
- **Diálogo Competitivo**: para objeto complexo, técnica refinada durante
  diálogo prévio.
- **Dispensa** (art. 75) e **Inexigibilidade** (art. 74): exceções.

## Sistema de Registro de Preços (SRP) — art. 82-86

**Ata de Registro de Preços (ARP)**: registra preços para futuras contratações,
sem obrigação de aquisição. Vigência máxima 1 ano + 1 ano (prorrogação).
**Adesão / Carona** (art. 86): órgão não-participante pode aderir, limitado a
**50% do quantitativo registrado** por órgão aderente e **200% no total** das
adesões (Decreto 11.462/2023 art. 32).

## Sanções administrativas (art. 156-163)

- **Advertência**
- **Multa** (até 30% do valor do contrato)
- **Impedimento de licitar e contratar** com o ente público sancionador
  (até 3 anos)
- **Declaração de inidoneidade** (até 6 anos — apenas autoridade máxima)

**Cadastros públicos de sanções**:
- **CEIS**: Cadastro Nacional de Empresas Inidôneas e Suspensas (Lei 12.846 + Lei 8.666)
- **CNEP**: Cadastro Nacional de Empresas Punidas (Lei Anticorrupção, Lei 12.846)
- **CEPIM**: Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas
- **CEAF**: Cadastro de Expulsões da Administração Federal (servidores)

## Catálogos

**CATMAT**: materiais. Cada item tem código numérico, código de PDM, classe,
grupo. SISG mantém. **CATSER**: serviços.

## Identificadores

**UASG**: Unidade Administrativa de Serviços Gerais — 6 dígitos.
**CNPJ Órgão**: 14 dígitos. PNCP usa CNPJ como chave principal.
**Sequencial PNCP**: número da compra dentro do órgão, no ano.
**Número Controle PNCP**: `CNPJ-1-SEQUENCIAL/ANO` (formato textual unificado).

## Datas e formatos

- Dados Abertos: `YYYY-MM-DD`
- PNCP: `yyyyMMdd` (sem hifens)
- Comprasnet: `YYYY-MM-DD HH:mm:ss`
"""


@mcp.resource(
    "compras://glossario/lei-14133",
    name="Glossário Lei 14.133/2021 + ecossistema Compras",
    description=(
        "Cheat-sheet textual de conceitos centrais da Lei 14.133/2021 (ETP, TR, "
        "modalidades, SRP, sanções) e do ecossistema de dados (CATMAT, UASG, "
        "formatos de data). Útil como contexto inicial para o LLM."
    ),
    mime_type="text/markdown",
    tags={"glossario", "lei-14133"},
)
def glossario_14133() -> str:
    return GLOSSARIO_14133


# ============================================================================
# Metadados do servidor
# ============================================================================


SCOPE_DOC = """\
# compras-mcp — escopo

## O que o servidor expõe

- **Dados Abertos Compras.gov.br** (SISG federal): CATMAT/CATSER, fornecedores,
  contratos, atas de registro de preço, indicadores, PGC, legado.
- **PNCP** (Lei 14.133, todos os entes): contratações, propostas abertas,
  contratos, atas, planos anuais de contratação, órgãos e unidades.
- **Portal da Transparência / CGU**: sanções (CEIS, CNEP, CEPIM, CEAF) e
  acordos de leniência.
- **Comprasnet Contratos** (rotas abertas): cronograma, empenhos, faturas,
  garantias, aditivos, ocorrências, publicações, responsáveis, impedimentos.
- **BrasilAPI** (Receita Federal Open Data): enriquecimento de CNPJ (QSA,
  capital, CNAEs, atividades).

## O que o servidor faz além de consultar

- **Tools compostas**: pesquisa de preços no padrão IN SEGES/ME 65/2021 com
  estatística (mediana, IQR), dossiê de ARP, perfil consolidado de fornecedor,
  busca de contratações similares federando 2 APIs.
- **Análise temporal**: agregação por dia/semana/mês/ano, comparação de
  períodos.
- **LGPD**: mascaramento configurável de CPF (servidor/fiscal/preposto).
- **Cache duplo**: Redis (produção) ou LRU em memória (desenvolvimento).

## O que o servidor NÃO faz

- Não envia propostas, não preenche editais, não acessa portais privados de
  licitação.
- Não persiste dados do usuário; não tem banco de dados próprio (apenas cache).
- Não consulta APIs internas privadas.
- Sem autenticação por usuário (todas as APIs upstream são anônimas, exceto
  Portal da Transparência que usa chave do servidor).

## Como referenciar

Em respostas geradas, sempre que possível cite a base normativa (artigo da Lei
14.133, da IN 65/2021, do Decreto 11.462/2023) e o endpoint upstream usado.
"""


@mcp.resource(
    "compras://meta/escopo",
    name="Escopo do compras-mcp",
    description=(
        "O que este servidor expõe, o que ele faz além de consultar APIs, e o "
        "que ele explicitamente não faz."
    ),
    mime_type="text/markdown",
    tags={"meta"},
)
def escopo() -> str:
    return SCOPE_DOC
