# MCP Compras.gov.br

Servidor MCP que reúne em um único pacote as APIs públicas do ecossistema **Compras.gov.br**, voltado a analistas e técnicos das áreas de **planejamento de contratação** e **execução contratual**.

Apoia a elaboração de:

- Estudos Técnicos Preliminares (ETP)
- Termos de Referência (TR)
- Pesquisa de preços no padrão IN SEGES/ME 65/2021
- Checagem de sanções de fornecedores (CEIS, CNEP, CEAF)
- Análise de atas de registro de preço (ARP) para adesão (carona)
- Benchmark inter-órgãos via Portal Nacional de Contratações Públicas (PNCP)

## APIs cobertas (v1)

| API | URL base | Autenticação |
|-----|----------|--------------|
| Dados Abertos Compras | `dadosabertos.compras.gov.br` | pública |
| PNCP — Portal Nacional | `pncp.gov.br/api/consulta` | pública |
| Portal da Transparência (CGU) | `api.portaldatransparencia.gov.br` | chave gratuita |
| Comprasnet Contratos | `contratos.comprasnet.gov.br/api` | pública (rotas `/api/*`) |

### ⚠️ Aviso operacional

- **Família `/modulo-uasg/*` morta no upstream Dados Abertos**: confirmado
  via probe direto em 2026-05 — `1_consultarUasg`, `2_consultarOrgao` e
  variantes CSV retornam HTTP 404 ("Resource not found") apesar de listadas
  no swagger oficial. Bug do servidor SEGES sem fix possível pelo MCP. As 5
  tools afetadas (`compras_uasg_listar/_consultar/_buscar` +
  `compras_orgao_listar/_consultar`) detectam o 404 e retornam payload
  educativo com alternativas: `compras_pncp_orgao_unidades(cnpj)` para
  resolver unidades por CNPJ (estável, pode levar 10-30s), ou
  `compras_contrato_comprasnet_por_uasg(uasg)` para listar UGs com
  contratos vigentes.
- **Filtro UASG em `/modulo-legado/*`**: pregões e licitações têm bug
  Hibernate confirmado no upstream — o swagger documenta `co_uasg`/`uasg`
  mas o atributo não existe no modelo da view. Os parâmetros foram
  removidos das tools `compras_legado_pregoes_listar` e
  `compras_legado_licitacoes_listar`; para filtrar por UASG, faça
  client-side no retorno.
- **Portal da Transparência (CGU)**: o servidor da CGU está protegido por
  AWS WAF "Human Verification" que pode bloquear chamadas API mesmo com
  chave válida (HTTP 405 + página HTML). Quando isso ocorre, todas as
  tools `compras_sancao_*` + a composta `compras_checar_sancoes_fornecedor`
  + as ramificações de sanção em `compras_perfil_fornecedor_completo`
  retornarão erro. É um problema upstream — não há fix do lado do MCP.
- **Comprasnet `/api/contrato/ug/{uasg}`**: o endpoint não suporta paginação
  e devolve a lista completa em uma resposta única (pode passar de 1 MB).
  A tool `compras_contrato_comprasnet_por_uasg` aplica fatiamento
  client-side com cache do payload completo para evitar inundar o LLM.
- **CATMAT busca textual quebrada**: o filtro `descricao` (e variantes
  `nome`, `termo`, `q`) do `/modulo-material/4_consultarItemMaterial`
  retorna o universo CATMAT inteiro (~340k itens). Use filtros estruturais
  via `compras_catmat_listar_grupos` → `_listar_classes` → `_buscar` com
  `codigo_grupo`/`codigo_classe`. A tool emite `_aviso_filtro` quando
  detecta o universo inteiro.

## Instalação

### Desktop Extension (.mcpb) — recomendado para Claude Desktop

```bash
python build_mcpb.py     # gera dist/compras.mcpb
open dist/compras.mcpb   # Claude Desktop instala
```

### Local (desenvolvimento)

```bash
uv sync
uv run compras-mcp
```

### Remoto (Railway via Docker)

```bash
railway login && railway link
railway add redis                                # cache compartilhado
railway variables --set "TRANSPARENCIA_API_KEY=..."
railway up
railway domain                                   # https://mcp-compras.up.railway.app/mcp
```

## Configuração

Cadastro gratuito da chave da Transparência: <https://api.portaldatransparencia.gov.br/api-de-dados/cadastrar-email>

Veja [.env.example](.env.example) para todas as variáveis configuráveis (timeouts, TTLs de cache, base URLs).

## Tools (85 no total)

Agrupadas por domínio funcional:

| Domínio | Tools | Cobertura |
|---------|-------|-----------|
| **Compostas (agente)** | 5 | `pesquisar_precos_para_etp` (IN SEGES 65/2021 com IQR), `checar_sancoes_fornecedor`, `montar_dossie_arp`, `buscar_contratacoes_similares`, `perfil_fornecedor_completo` |
| **Catálogo** (CATMAT/CATSER) | 7 | Grupos/classes/itens, busca textual |
| **Pesquisa de preço** | 4 | Material/serviço, detalhe por compra |
| **Planejamento** (PGC + PCA) | 8 | PGC SISG, PCA PNCP (federal + estados + municípios) |
| **Atas de Registro de Preço** | 8 | Listar, saldo, adesões, unidades participantes, PNCP |
| **Contratações** (14.133 + legado) | 12 | Lei 14.133, Lei 8.666, RDC, dispensas |
| **Contratos** | 12 | Dados Abertos + Comprasnet (garantias, faturas, ocorrências, fiscais, empenhos, cronograma) |
| **Fornecedores** | 4 | Cadastro, impedimentos, contratos por item |
| **Sanções** (Transparência/CGU) | 5 | CEIS, CNEP, CEAF, CEPIM, acordos de leniência |
| **PNCP** | 9 | Contratações (publicação, proposta, atualização), contratos, modalidades |
| **Organizações** | 6 | UASG (listar/consultar/buscar), órgãos, unidades PNCP |
| **Indicadores** | 2 | Consolidados, por período |
| **Diagnóstico** | 1 | `compras_versao` |

A lista completa está em [`manifest.json`](manifest.json) ou via `tools/list` no MCP Inspector.

### Fluxos típicos

**ETP de aquisição de cadeiras ergonômicas:**

1. `compras_catmat_buscar` com `termo="cadeira ergonomica"` → obter `codigo_item_catalogo`
2. `compras_pesquisar_precos_para_etp` (composta) com `tipo="material"` → mediana/média/desvio + descarte IQR
3. `compras_pgc_por_catalogo` para ver o que outros órgãos planejaram comprar
4. `compras_arp_listar` com `apenas_vigentes=True` → atas vigentes para possível adesão

**Análise de fornecedor antes de homologação:**

1. `compras_perfil_fornecedor_completo` (composta) com o CNPJ — uma chamada devolve cadastro + sanções + contratos vigentes

**Inventário de contratos a renovar:**

1. `compras_contratos_listar_por_fim_vigencia` com `data_fim_vigencia` próxima
2. Para cada contrato relevante: `compras_contrato_historico_aditivos`, `compras_contrato_ocorrencias`

## Padrões internos

- **Envelope padrão** das tools `listar_*`: `{resultado, _pagina_atual, _total_paginas, _total_registros, _proxima_pagina, _cache_hit, _latency_ms}`.
- **SSoT de descriptions**: descrições de parâmetros vivem em [`src/compras_mcp/schemas.py`](src/compras_mcp/schemas.py); tools leem via `_helpers.desc(Model, "campo")`. Teste em [`tests/test_server.py`](tests/test_server.py) detecta drift.
- **LGPD**: CPFs mascarados como `123.***.***-45`; ajuste com `INCLUIR_CPF_COMPLETO=true`. Tools afetadas incluem `_aviso_lgpd` no payload.
- **Cache**: TTL+LRU em memória (default) ou Redis quando `REDIS_URL` setada. Cada domínio tem seu prefixo (CATALOGO, PRECOS, ATAS, etc.) — ajustáveis via `CACHE_<PREFIX>_TTL` e `CACHE_<PREFIX>_MAX_SIZE`.
- **Datas**: 3 formatos por API (`YYYY-MM-DD`, `yyyyMMdd`, `YYYY-MM-DD HH:mm:ss`) convertidos transparentemente por `format_date(value, flavor)`.
- **Framework**: FastMCP 2.x. Transporte detectado por `PORT` — presente → HTTP em `0.0.0.0:$PORT`, ausente → stdio.

## Status

v0.2.0 — 85 tools registradas e prontas para teste end-to-end com o Claude Desktop e Railway.
