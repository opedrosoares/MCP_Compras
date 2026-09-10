# Tools, prompts e resources

O servidor expõe **100 tools + 6 prompts + 6 resources**. Você não precisa decorar nenhum nome:
o assistente escolhe a ferramenta certa a partir da pergunta em português. Esta página serve para
quem quer saber exatamente o que está disponível — e para montar fluxos mais controlados.

## Tools (100)

Agrupadas por domínio funcional:

| Domínio | Tools | Cobertura |
|---------|-------|-----------|
| **Compostas (agente)** | 5 | `pesquisar_precos_para_etp` (IN SEGES 65/2021 com IQR), `checar_sancoes_fornecedor`, `montar_dossie_arp`, `buscar_contratacoes_similares`, `perfil_fornecedor_completo` |
| **Catálogo** (CATMAT/CATSER) | 8 | Grupos/classes/**PDMs**/itens; a API não busca por substring, então a navegação é hierárquica (ver [Qualidade das APIs](qualidade-das-apis.md)) |
| **Pesquisa de preço** | 4 | Material/serviço, detalhe por compra |
| **Planejamento** (PGC + PCA) | 8 | PGC SISG, PCA PNCP (federal + estados + municípios) |
| **Atas de Registro de Preço** | 9 | Listar, buscar por objeto, saldo, adesões, unidades participantes, PNCP |
| **Contratações** (14.133 + legado) | 14 | Lei 14.133 (filtros por UASG, CNPJ do órgão, UF, município, amparo legal, item de catálogo, fornecedor e faixa de valor homologado), Lei 8.666 **por item** (estimado → menor lance → homologado), RDC, dispensas |
| **Contratos** | 15 | Dados Abertos (contrato e itens do contrato) + Comprasnet (garantias, faturas, ocorrências, fiscais, empenhos, cronograma, publicações) |
| **Fornecedores** | 4 | Cadastro, impedimentos, contratos por item |
| **Sanções** (Transparência/CGU) | 5 | CEIS, CNEP, CEPIM, CEAF, acordos de leniência |
| **PNCP** | 11 | Contratações (publicação, proposta, atualização), contratos, modalidades, **arquivos de contratação e de ata** (Edital/TR/ETP e aditivos, com URL de download) |
| **Organizações** | 6 | UASG (listar/consultar/buscar), órgãos, unidades PNCP |
| **Indicadores** | 2 | Consolidados, por período |
| **Analítica** | 2 | Série temporal de contratações, comparação entre períodos |
| **Enriquecimento** | 1 | CNPJ na Receita Federal (BrasilAPI/MinhaReceita) — QSA, capital, CNAEs |
| **Descoberta** (tools-espelho) | 4 | `listar_prompts`/`obter_prompt`/`listar_resources`/`obter_resource` — para clientes que só consomem o primitivo *tools* (ex.: Claude.ai web) |
| **Diagnóstico** | 2 | `compras_versao`, `compras_healthcheck` |

A lista completa (nome + descrição de cada tool) está em [`manifest.json`](../manifest.json) ou
via `tools/list` no MCP Inspector.

## Prompts MCP (6)

Diferente das tools (que o assistente invoca sozinho), prompts são selecionados **por você** no
cliente MCP e expandem em um roteiro guiado usando as tools disponíveis. Úteis como ponto de
partida para fluxos recorrentes.

| Prompt | O que faz |
|--------|-----------|
| `analisar_contratacao_pncp` | Checklist de viabilidade de uma contratação publicada no PNCP: objeto, valor, prazos, itens críticos, riscos. |
| `panorama_orgao_360` | Perfil 360° de um órgão: identificação, contratações do último ano, principais fornecedores, PCA do ano corrente. |
| `dossie_due_diligence_fornecedor` | Dossiê completo de fornecedor: cadastro, sanções (CEIS/CNEP/CEPIM/CEAF + leniência), impedimentos, contratos. |
| `oportunidades_carona_arp` | Encontra ARPs vigentes com saldo disponível para adesão (carona). |
| `montar_etp_pesquisa_precos` | Monta a seção de pesquisa de preços de um ETP no padrão IN SEGES/ME 65/2021 (≥3 fontes, estatística, descarte IQR). |
| `tendencia_contratacoes_periodo` | Tendência de contratações com bucketing temporal e comparação A vs. B. |

Clientes que só consomem o primitivo *tools* (ex.: Claude.ai web) podem acessá-los via
`compras_listar_prompts` / `compras_obter_prompt`.

## Resources MCP (6)

Dados de referência que o cliente lista e lê sob demanda, sem gastar uma chamada de rede:

| Resource (URI) | Conteúdo |
|----------------|----------|
| `compras://referencia/modalidades-pncp` | Códigos de modalidade de contratação aceitos pelo PNCP |
| `compras://referencia/esferas-federativas` | Códigos de esfera (F/E/M/D) usados no filtro `esfera` das listagens |
| `compras://referencia/criterios-julgamento` | Critérios de julgamento do art. 33 da Lei 14.133/2021 |
| `compras://referencia/situacoes-contratacao` | Códigos de `situacaoCompraId` do PNCP |
| `compras://glossario/lei-14133` | Cheat-sheet de ETP, TR, modalidades, SRP, sanções, catálogos e formatos de data |
| `compras://meta/escopo` | O que o servidor expõe, o que faz além de consultar, e o que explicitamente não faz |

Clientes que só consomem o primitivo *tools* podem acessá-los via `compras_listar_resources` /
`compras_obter_resource`.

## Fontes de dados

| API | URL base | Autenticação | Contrato oficial |
|-----|----------|--------------|------------------|
| Dados Abertos Compras | `dadosabertos.compras.gov.br` | pública | [Swagger](https://dadosabertos.compras.gov.br/swagger-ui/index.html) |
| PNCP — Portal Nacional | `pncp.gov.br/api/consulta` | pública | [Swagger](https://pncp.gov.br/api/consulta/swagger-ui/index.html) |
| Portal da Transparência (CGU) | `api.portaldatransparencia.gov.br` | [chave gratuita](configuracao.md#como-obter-a-chave-do-portal-da-transparência) | [Swagger](https://api.portaldatransparencia.gov.br/swagger-ui/index.html) |
| Comprasnet Contratos | `contratos.comprasnet.gov.br/api` | pública (rotas `/api/*`) | [ReadTheDocs](https://comprasnet-contratos.readthedocs.io/pt-br/latest/) |
| BrasilAPI / MinhaReceita | `brasilapi.com.br` | pública | [brasilapi.com.br/docs](https://brasilapi.com.br/docs) |

Ver também: [exemplos de uso encadeando essas tools](exemplos.md).
