# MCP Compras.gov.br

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![FastMCP](https://img.shields.io/badge/FastMCP-2.x-informational)](https://github.com/jlowin/fastmcp)

Servidor MCP que reúne em um único pacote as APIs públicas do ecossistema **Compras.gov.br**, voltado a analistas e técnicos das áreas de **planejamento de contratação** e **execução contratual**.

**94 tools + 6 prompts + 6 resources** cobrindo Dados Abertos, PNCP, Portal da Transparência/CGU, Comprasnet Contratos e BrasilAPI/Receita.

Apoia a elaboração de:

- Estudos Técnicos Preliminares (ETP)
- Termos de Referência (TR)
- Pesquisa de preços no padrão IN SEGES/ME 65/2021
- Checagem de sanções de fornecedores (CEIS, CNEP, CEPIM, CEAF)
- Análise de atas de registro de preço (ARP) para adesão (carona)
- Benchmark inter-órgãos via Portal Nacional de Contratações Públicas (PNCP)
- Due diligence de fornecedor (cadastro + sanções + Receita Federal)

## APIs cobertas

| API | URL base | Autenticação |
|-----|----------|--------------|
| Dados Abertos Compras | `dadosabertos.compras.gov.br` | pública |
| PNCP — Portal Nacional | `pncp.gov.br/api/consulta` | pública |
| Portal da Transparência (CGU) | `api.portaldatransparencia.gov.br` | chave gratuita |
| Comprasnet Contratos | `contratos.comprasnet.gov.br/api` | pública (rotas `/api/*`) |
| BrasilAPI / MinhaReceita | `brasilapi.com.br` | pública |

### ⚠️ Aviso operacional

Cada linha abaixo foi confirmada por probe direto ao upstream (não é suposição). Rode `compras_healthcheck` a qualquer momento para ver a situação **atual** de cada módulo — esta lista é o retrato mais recente conhecido, o healthcheck é o retrato ao vivo.

**Resolvidos** (deixados aqui para quem encontrar issues antigas ou forks desatualizados):

- ✅ **Família `/modulo-uasg/*`** (`compras_uasg_*`, `compras_orgao_*`) — chegou a devolver 404 para todo mundo e a documentação atribuía isso a bug de roteamento sem fix possível. Diagnóstico corrigido em 2026-08 (v0.3.13): faltava o parâmetro obrigatório `statusUasg`/`statusOrgao` — a API responde 404 (não 400) quando ele falta. Hoje devolve ~22 mil UASGs e ~12 mil órgãos normalmente.
- ✅ **`compras_pesquisar_preco_material`** — o contrato da rota `/modulo-pesquisa-preco/1_consultarMaterial` mudou de `codigoItemCatalogo=<int>` para o par `tipo` (`codigoItemCatalogo`|`codigoPdm`) + `codigo` (string), sem versionar. Corrigido em v0.3.13.

**Em aberto** (limitação real do upstream, não do MCP):

- **CATMAT busca textual quebrada**: o filtro `descricao` (e variantes `nome`, `termo`, `q`) de `/modulo-material/4_consultarItemMaterial` ignora o valor e devolve o universo CATMAT inteiro (~340k itens). Use `compras_catmat_listar_grupos` → `_listar_classes` → `_buscar` com `codigo_grupo`/`codigo_classe`. A tool emite `_aviso_filtro` quando detecta o problema.
- **Filtro UASG em `/modulo-legado/*`**: pregões e licitações têm bug Hibernate confirmado no upstream — o swagger documenta `co_uasg`/`uasg`, mas o atributo não existe no modelo da view (`400 Bad Request`). Os parâmetros foram removidos das tools `compras_legado_pregoes_listar` e `compras_legado_licitacoes_listar`; para filtrar por UASG, faça client-side no retorno.
- **`compras_pncp_orgao_unidades`**: a rota `/v1/orgaos/{cnpj}/unidades` não é documentada no contrato oficial do PNCP Consulta — devolve 404 para CNPJs que não publicam diretamente (ex.: CNPJ raiz de órgão cujas unidades publicam com CNPJ próprio). A tool devolve diagnóstico com alternativas em vez de estourar exception.
- **`compras_pncp_contratacao_itens`**: pode devolver 404 mesmo quando a contratação-pai responde 200 — inconsistência observada no upstream, não reproduzida de forma determinística.
- **Portal da Transparência (CGU)**: o servidor é protegido por AWS WAF que bloqueia (`405` + página HTML "Human Verification") clientes HTTP com `User-Agent` genérico, mesmo com chave válida. O cliente deste MCP já envia um `User-Agent` browser-like como mitigação; se a CGU mudar as regras do WAF, as tools `compras_sancao_*` podem voltar a falhar — não há fix definitivo do lado do MCP.
- **Comprasnet `/api/contrato/ug/{uasg}`**: o endpoint não pagina e devolve a lista completa em uma resposta única (pode passar de 1 MB). `compras_contrato_comprasnet_por_uasg` aplica fatiamento client-side com cache do payload completo para não inundar o contexto do LLM.

## Instalação

### Opção 1 — Desktop Extension (.mcpb), recomendado para Claude Desktop

Baixe o `compras.mcpb` mais recente em [Releases](https://github.com/opedrosoares/MCP_Compras/releases/latest) e abra com duplo-clique — o Claude Desktop instala e pede as configurações (chave da Transparência, Redis, etc.) automaticamente.

Ou gere localmente a partir do código-fonte:

```bash
git clone https://github.com/opedrosoares/MCP_Compras.git
cd MCP_Compras
python3 build_mcpb.py     # gera dist/compras.mcpb
open dist/compras.mcpb    # macOS — no Windows/Linux, abra com duplo-clique no Claude Desktop
```

### Opção 2 — Local via uv (desenvolvimento ou Claude Code)

```bash
git clone https://github.com/opedrosoares/MCP_Compras.git
cd MCP_Compras
uv sync
uv run compras-mcp
```

Veja [Conectar a um cliente MCP](#conectar-a-um-cliente-mcp) para registrar esse comando no Claude Desktop ou Claude Code.

### Opção 3 — Remoto (Railway), para uso via web/mobile ou compartilhado por uma equipe

Não exige instalação local nenhuma — qualquer cliente MCP aponta para uma URL HTTP. Veja o passo a passo completo em [Deploy remoto (Railway)](#deploy-remoto-railway).

## Conectar a um cliente MCP

### Claude Code — `.mcp.json` do projeto ou `~/.claude.json` (global)

Servidor local via stdio (assume `compras-mcp` instalado no PATH — via `uv tool install .` ou `pip install .`):

```json
{
  "mcpServers": {
    "compras": {
      "command": "compras-mcp",
      "env": {
        "TRANSPARENCIA_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

Sem instalar globalmente, rodando direto do clone via `uv`:

```json
{
  "mcpServers": {
    "compras": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/para/MCP_Compras", "compras-mcp"],
      "env": {
        "TRANSPARENCIA_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

`TRANSPARENCIA_API_KEY` é opcional: sem ela, todas as tools funcionam exceto as de sanções (`compras_sancao_*`, `compras_checar_sancoes_fornecedor`, ramificações de sanção em `compras_perfil_fornecedor_completo`).

### Claude Desktop (registro manual, sem o `.mcpb`)

Edite o `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "compras": {
      "command": "compras-mcp",
      "env": {
        "TRANSPARENCIA_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

### Servidor remoto (Railway) via HTTP

Depois do deploy (ver seção abaixo), o endpoint MCP fica em `https://SEU-PROJETO.up.railway.app/mcp`. Não há autenticação própria — é o mesmo servidor, só que em modo HTTP em vez de stdio.

- **claude.ai / Claude Desktop**: Settings → Connectors → Adicionar conector personalizado → cole a URL.
- **Claude Code** — via CLI:

  ```bash
  claude mcp add --transport http compras-remoto https://SEU-PROJETO.up.railway.app/mcp
  ```

  Ou direto no `.mcp.json`:

  ```json
  {
    "mcpServers": {
      "compras-remoto": {
        "type": "http",
        "url": "https://SEU-PROJETO.up.railway.app/mcp"
      }
    }
  }
  ```

## Configuração

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `TRANSPARENCIA_API_KEY` | Não | Habilita as tools de sanções (CEIS, CNEP, CEPIM, CEAF, leniência). Sem ela, as demais ~90 tools (Dados Abertos, PNCP, Comprasnet, BrasilAPI) continuam funcionando normalmente. |
| `REDIS_URL` | Não | Cache TTL compartilhado em Redis. Recomendado em produção/Railway com múltiplos pods. Sem ela, cache fica em memória local (TTL+LRU). |
| `INCLUIR_CPF_COMPLETO` | Não | `false` (padrão): CPFs de servidores são mascarados (`123.***.***-45`). `true` retorna completo — use com critério (LGPD). |
| `LOG_LEVEL` | Não | `DEBUG`, `INFO` (padrão), `WARNING` ou `ERROR`. |
| `COMPRASNET_BEARER_TOKEN` | Não | Reservado para v2 (rotas autenticadas do Comprasnet Contratos via login gov.br). Sem efeito na v1. |

> **Dica: como obter a chave do Portal da Transparência**
>
> Cadastro gratuito, em minutos, em <https://api.portaldatransparencia.gov.br/api-de-dados/cadastrar-email>. A chave chega por e-mail e vai direto na variável `TRANSPARENCIA_API_KEY`.

Veja [.env.example](.env.example) para todas as variáveis configuráveis, incluindo TTLs de cache por domínio, timeouts HTTP e base URLs (só para testes/mocks — os padrões já apontam para produção).

## Deploy remoto (Railway)

O servidor detecta a env var `PORT` (injetada pelo Railway) e sobe automaticamente em modo HTTP; sem ela, sobe em stdio. Não há login por usuário — todas as APIs upstream são anônimas ou usam a chave da Transparência configurada no próprio servidor.

### 1. Criar conta no Railway

Acesse [railway.com](https://railway.com), clique em **Sign Up** e faça login com GitHub, GitLab ou e-mail.

### 2. Instalar o Railway CLI

```bash
# macOS (Homebrew)
brew install railway

# npm (qualquer plataforma)
npm install -g @railway/cli

# Verificar
railway --version
```

### 3. Autenticar no terminal

```bash
railway login
```

### 4. Clonar o repositório

```bash
git clone https://github.com/opedrosoares/MCP_Compras.git
cd MCP_Compras
```

### 5. Criar o projeto no Railway

```bash
railway init -n mcp-compras
```

Se tiver mais de um workspace, adicione `--workspace "Nome do Workspace"`.

### 6. Adicionar Redis (recomendado)

```bash
railway add --database redis
```

O Redis vira cache compartilhado entre instâncias — sem ele, cada pod mantém seu próprio cache em memória.

### 7. Configurar variáveis de ambiente

```bash
railway variable set TRANSPARENCIA_API_KEY=sua-chave-aqui
```

`REDIS_URL` normalmente já é injetada automaticamente pelo plugin Redis do Railway (referência de outra variável do próprio projeto) — confira em `railway variables` se precisa setar manualmente.

### 8. Fazer o deploy

```bash
railway up
```

Aguarde o build (Dockerfile já incluso no repo, 2-3 minutos na primeira vez).

### 9. Gerar domínio público

```bash
railway domain
```

Gera uma URL como `https://mcp-compras-production.up.railway.app`.

### 10. Verificar o deploy

O endpoint MCP exige os headers do protocolo Streamable HTTP — uma requisição "crua" deve responder **406** (não erro de conexão), confirmando que o servidor está de pé:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://SEU-PROJETO.up.railway.app/mcp
# 406
```

Para uma checagem mais completa (versão, fontes upstream, chave da Transparência configurada), use a tool `compras_versao` ou `compras_healthcheck` a partir de um cliente MCP já conectado.

### 11. Conectar no Claude

Veja [Servidor remoto (Railway) via HTTP](#servidor-remoto-railway-via-http) acima.

### Domínio customizado (opcional)

```bash
railway domain mcp.seu-orgao.gov.br
```

O comando devolve os registros DNS a configurar. Crie um CNAME no DNS do seu órgão apontando para o valor indicado; o certificado SSL é provisionado automaticamente. Para checar se a propagação/certificado já está ok:

```bash
railway domain status mcp.seu-orgao.gov.br
```

### Atualizar o servidor

```bash
git pull
railway up
```

## Requisitos de sistema

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (recomendado) ou `pip`
- Claude Code, Claude Desktop, ou qualquer cliente MCP compatível com stdio ou Streamable HTTP
- Redis (opcional, só para cache compartilhado em deploy com múltiplas instâncias)
- Chave gratuita do Portal da Transparência (opcional, só para tools de sanções)

Nenhuma dependência de sistema além do Python — diferente de MCPs que fazem OCR/scraping, este servidor só consome APIs REST públicas.

## Tools (94 no total)

Agrupadas por domínio funcional:

| Domínio | Tools | Cobertura |
|---------|-------|-----------|
| **Compostas (agente)** | 5 | `pesquisar_precos_para_etp` (IN SEGES 65/2021 com IQR), `checar_sancoes_fornecedor`, `montar_dossie_arp`, `buscar_contratacoes_similares`, `perfil_fornecedor_completo` |
| **Catálogo** (CATMAT/CATSER) | 7 | Grupos/classes/itens, busca textual (com limitação upstream, ver aviso) |
| **Pesquisa de preço** | 4 | Material/serviço, detalhe por compra |
| **Planejamento** (PGC + PCA) | 8 | PGC SISG, PCA PNCP (federal + estados + municípios) |
| **Atas de Registro de Preço** | 9 | Listar, buscar por objeto, saldo, adesões, unidades participantes, PNCP |
| **Contratações** (14.133 + legado) | 12 | Lei 14.133, Lei 8.666, RDC, dispensas |
| **Contratos** | 14 | Dados Abertos + Comprasnet (garantias, faturas, ocorrências, fiscais, empenhos, cronograma, publicações) |
| **Fornecedores** | 4 | Cadastro, impedimentos, contratos por item |
| **Sanções** (Transparência/CGU) | 5 | CEIS, CNEP, CEPIM, CEAF, acordos de leniência |
| **PNCP** | 9 | Contratações (publicação, proposta, atualização), contratos, modalidades |
| **Organizações** | 6 | UASG (listar/consultar/buscar), órgãos, unidades PNCP |
| **Indicadores** | 2 | Consolidados, por período |
| **Analítica** | 2 | Série temporal de contratações, comparação entre períodos |
| **Enriquecimento** | 1 | CNPJ na Receita Federal (BrasilAPI/MinhaReceita) — QSA, capital, CNAEs |
| **Descoberta** (tools-espelho) | 4 | `listar_prompts`/`obter_prompt`/`listar_resources`/`obter_resource` — para clientes que só consomem o primitivo *tools* (ex.: Claude.ai web) |
| **Diagnóstico** | 2 | `compras_versao`, `compras_healthcheck` |

A lista completa (nome + descrição de cada tool) está em [`manifest.json`](manifest.json) ou via `tools/list` no MCP Inspector.

### Fluxos típicos

**ETP de aquisição de cadeiras ergonômicas:**

1. `compras_catmat_buscar` com `termo="cadeira ergonomica"` → obter `codigo_item_catalogo`
2. `compras_pesquisar_precos_para_etp` (composta) com `tipo="material"` → mediana/média/desvio + descarte IQR
3. `compras_pgc_por_catalogo` para ver o que outros órgãos planejaram comprar
4. `compras_arp_listar` com `apenas_vigentes=True` → atas vigentes para possível adesão

**Análise de fornecedor antes de homologação:**

1. `compras_perfil_fornecedor_completo` (composta) com o CNPJ — uma chamada devolve cadastro + sanções + contratos vigentes + dados da Receita

**Inventário de contratos a renovar:**

1. `compras_contratos_listar_por_fim_vigencia` com `data_fim_vigencia` próxima
2. Para cada contrato relevante: `compras_contrato_historico_aditivos`, `compras_contrato_ocorrencias`

**Antes de uma demonstração ou de instruir processo:**

1. `compras_healthcheck(profundidade="rotas")` — probe real contra o upstream em ~30s, retorna `pronto_para_uso` e qual módulo está degradado/fora, se algum.

## Prompts MCP (6)

Diferente de tools (que o LLM invoca sozinho), prompts são selecionados pelo usuário no cliente MCP e expandem em um roteiro guiado usando as tools disponíveis. Úteis como ponto de partida para fluxos recorrentes.

| Prompt | O que faz |
|--------|-----------|
| `analisar_contratacao_pncp` | Checklist de viabilidade de uma contratação publicada no PNCP: objeto, valor, prazos, itens críticos, riscos. |
| `panorama_orgao_360` | Perfil 360° de um órgão: identificação, contratações do último ano, principais fornecedores, PCA do ano corrente. |
| `dossie_due_diligence_fornecedor` | Dossiê completo de fornecedor: cadastro, sanções (CEIS/CNEP/CEPIM/CEAF + leniência), impedimentos, contratos. |
| `oportunidades_carona_arp` | Encontra ARPs vigentes com saldo disponível para adesão (carona). |
| `montar_etp_pesquisa_precos` | Monta a seção de pesquisa de preços de um ETP no padrão IN SEGES/ME 65/2021 (≥3 fontes, estatística, descarte IQR). |
| `tendencia_contratacoes_periodo` | Tendência de contratações com bucketing temporal e comparação A vs. B. |

Clientes que só consomem o primitivo *tools* (ex.: Claude.ai web) podem acessá-los via `compras_listar_prompts` / `compras_obter_prompt`.

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

Clientes que só consomem o primitivo *tools* podem acessá-los via `compras_listar_resources` / `compras_obter_resource`.

## Padrões internos

- **Envelope padrão** das tools `listar_*`: `{resultado, _pagina_atual, _total_paginas, _total_registros, _proxima_pagina, _cache_hit, _latency_ms}`.
- **SSoT de descriptions**: descrições de parâmetros vivem em [`src/compras_mcp/schemas.py`](src/compras_mcp/schemas.py); tools leem via `_helpers.desc(Model, "campo")`. Teste em [`tests/test_server.py`](tests/test_server.py) detecta drift.
- **LGPD**: CPFs mascarados como `123.***.***-45`; ajuste com `INCLUIR_CPF_COMPLETO=true`. Tools afetadas incluem `_aviso_lgpd` no payload.
- **Cache**: TTL+LRU em memória (default) ou Redis quando `REDIS_URL` setada. Cada domínio tem seu prefixo (CATALOGO, PRECOS, ATAS, ORGAOS, SANCOES, COMPOSTAS etc.) — ajustáveis via `CACHE_<PREFIX>_TTL` e `CACHE_<PREFIX>_MAX_SIZE`.
- **Datas**: 3 formatos por API (`YYYY-MM-DD`, `yyyyMMdd`, `YYYY-MM-DD HH:mm:ss`) convertidos transparentemente por `format_date(value, flavor)`.
- **Framework**: FastMCP 2.x. Transporte detectado por `PORT` — presente → HTTP em `0.0.0.0:$PORT`, ausente → stdio.

## Links

- [Changelog](CHANGELOG.md) — cada release documenta a causa raiz encontrada, não só o sintoma
- [Roadmap](ROADMAP.md)
- [Issues upstream conhecidas](ISSUES_UPSTREAM.md) — bugs reportáveis aos mantenedores da SEGES/CGU
- [Repositório](https://github.com/opedrosoares/MCP_Compras)
- [Dados Abertos Compras — Swagger](https://dadosabertos.compras.gov.br/swagger-ui/index.html)
- [PNCP Consulta — Swagger](https://pncp.gov.br/api/consulta/swagger-ui/index.html)
- [Portal da Transparência — Swagger](https://api.portaldatransparencia.gov.br/swagger-ui/index.html)

## Licença

MIT — veja [LICENSE](LICENSE).

## Status

v0.3.14 — 94 tools + 6 prompts + 6 resources, em produção (Railway + Redis). Cada release recente foi validada em bateria de testes ponta a ponta contra o ambiente de produção, não apenas local — ver [Changelog](CHANGELOG.md).
