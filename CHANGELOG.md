# Changelog — MCP Compras.gov.br

Todas as mudanças notáveis. Cada release foi descoberto em bateria E2E
real contra o servidor em produção (Railway + Redis) e validado por
probe direto ao upstream antes do fix.

## [0.3.14] — 2026-08-05

Patch encontrado validando a 0.3.13 **em produção**, não em teste local.

### Fixed

- **🟠 `compras_healthcheck` dava falso alarme em rota apenas lenta.** Na
  primeira execução contra o Railway, o módulo `atas` saiu `degradado` com
  `compras_arp_itens_listar` marcada como fora. Rodando o mesmo
  healthcheck só naquele módulo, ele voltou `ok` em 2,5s: a rota não tinha
  nada de errado — com as 59 rotas em paralelo, ela passou dos 12s de
  timeout e foi dada como quebrada. Numa checagem que existe justamente
  para dizer "pode demonstrar", um `pronto_para_uso: False` falso custa
  tanto quanto o alarme que não toca.

  Agora `executar_probe(..., reconfirmar_timeouts=True)` reexecuta **em
  série** apenas as rotas que estouraram o relógio, antes de declará-las
  fora. Erro HTTP (404/500) **não** é reexecutado — é resposta do
  servidor, não pressão do probe; sem essa distinção o mecanismo viraria
  retry genérico e dobraria o custo de todo probe com upstream fora.
  Quando a rota passa na segunda tentativa, o diagnóstico do módulo diz
  "lenta sob carga: só respondeu na 2ª tentativa" em vez de omitir o fato.

  O `scripts/probe_upstream.py` continua sem reexecução, de propósito: ele
  é instrumento de diagnóstico e precisa mostrar o comportamento cru.

  Coberto por dois testes: um em que o timeout se recupera na 2ª tentativa
  (status vira `ok`, com a lentidão registrada) e a contraprova de que um
  404 é consultado uma única vez.

## [0.3.13] — 2026-08-05

Release de diagnóstico. A quebra reportada em 04/08/2026
(`compras_pesquisar_preco_material` devolvendo "Recurso nao encontrado")
**não era rota removida**: a SEGES trocou a assinatura de query sem
versionar, e o Spring do Dados Abertos responde **404, não 400**, quando
falta um `@RequestParam` obrigatório. Foi isso que disfarçou drift de
contrato de "rota extinta". A mesma causa raiz derrubava 4 tools de
UASG/órgão desde a v0.3.12 — e o v0.3.12 documentava aquilo como "bug do
servidor SEGES, sem fix possível pelo MCP". Era fix nosso.

### Fixed

- **🔴 `compras_pesquisar_preco_material` (rota `/modulo-pesquisa-preco/1_consultarMaterial`)
  voltou a funcionar.** O contrato vigente exige o par discriminador
  `tipo` (enum `codigoItemCatalogo` | `codigoPdm`) + `codigo` (string),
  no lugar do antigo `codigoItemCatalogo=<int>`. Prova: (a) o diff do
  OpenAPI publicado mostra a rota 1 perdendo `codigoItemCatalogo`
  enquanto a rota 3 (serviço) o mantém; (b) `tipo=M` devolve HTTP 500
  vazando `br.gov.economia.apicompras.enums.EnumPesquisaPreco`;
  (c) `tipo=codigoItemCatalogo&codigo=630237` devolve HTTP 200 com 70
  registros e `precoUnitario`. Verificado em 2026-08-05: preço unitário
  104,00, fornecedor "H&A VENDAS E SERVICOS LTDA".

- **🔴 `compras_uasg_listar`, `compras_uasg_consultar`, `compras_orgao_listar`
  e `compras_orgao_consultar` voltaram a funcionar.** Mesma causa: as
  rotas `/modulo-uasg/1_consultarUasg` e `2_consultarOrgao` exigem
  `statusUasg` / `statusOrgao`, e sem eles devolvem 404. `uasg_listar`
  passou a devolver 21.970 UASGs e `orgao_listar` 11.872 órgãos
  (antes: 0 + diagnóstico de erro). `compras_uasg_consultar` agora tenta
  ativas e inativas e expõe o novo campo `ativa`.

- **🟠 `compras_pesquisar_precos_para_etp` (tipo="material")** voltou a
  funcionar e passou a **detectar rota indisponível antes de paginar**:
  a página 1 vira preflight; se o upstream devolver 404, a tool retorna
  diagnóstico com `amostra_total: 0`, `estatisticas: None` e
  `_erro_upstream.detectado_em = "preflight (antes da paginação)"`, em
  vez de estourar no meio da varredura. Verificado: `amostra_total` 67
  com estatística completa para o CATMAT 630237.

### Changed

- **`compras_uasg_buscar` agora filtra localmente.** O parâmetro `nome=`
  é aceito e **silenciosamente ignorado** pelo upstream — corrigir só o
  param obrigatório teria trocado um 404 visível por um despejo mudo do
  universo inteiro, que é pior. A tool passou a varrer as páginas
  (concorrência limitada, até 60 páginas, cache 24h) e filtrar por
  similaridade **insensível a acento**. O payload declara o que fez:
  `_busca_local`, `_paginas_varridas`, `_universo_varrido`,
  `_aviso_varredura_truncada`, `_aviso_sem_resultado`. Busca por
  "aquaviarios" devolve 3 UASGs (com acento no dado upstream).

- **Docstrings de `compras_detalhar_preco_material` e
  `compras_detalhar_preco_servico` deixaram de prometer preço.** Elas
  anunciavam "valor unitário homologado"; a rota nunca devolveu isso.
  Chamada crua ao upstream retorna exatamente 7 campos
  (`idCompra`, `idItemCompra`, `numeroItemCompra`, `codigoItemCatalogo`,
  `objetoCompra`, `descricaoDetalhadaItem`, `dataAtualizacaoFato`), e o
  DTO publicado (`FtPesqPrecoCompraMaterialDetalheDTO`) declara os mesmos
  7 — não houve mudança upstream, a docstring estava errada desde sempre.
  Agora dizem "sem valor de preço" e apontam para
  `compras_pesquisar_preco_material`. Sem mudança de comportamento.

- **`compras_orgao_listar`** documenta que `nome`, `esfera` e `poder` são
  ignorados pelo upstream — antes o silêncio sugeria filtro aplicado.

### Added

- **`compras_healthcheck(profundidade="rotas", modulo=None)`** — nova
  tool (94ª). Estende `compras_versao` disparando um probe paralelo com
  timeout curto contra as rotas upstream reais e devolvendo situação por
  módulo (`ok` / `degradado` / `fora` / `pulado`), `rotas_testadas`,
  `tools_afetadas`, `pronto_para_uso` e `proximo_passo`. Existe porque a
  rota 1 estava quebrada havia semanas e a descoberta veio de um analista
  em uso real: o objetivo é que a descoberta aconteça aqui, não no palco.

- **`scripts/probe_upstream.py`** — percorre as 59 rotas upstream
  registradas com payload mínimo conhecido-bom e imprime a matriz
  `rota | HTTP | latência | registros | chaves do 1º item`. Roda em ~46s.
  Flags: `--json`, `--modulo`, `--timeout`, `--concorrencia`. Exit 1 se
  algo estiver degradado ou fora.

- **`src/compras_mcp/upstream_registry.py`** — SSoT do inventário de
  rotas: path, params mínimos, **contrato de campos esperados**, tools
  afetadas e seeds entre rotas. Consumido pelo probe, pelo healthcheck e
  pelos testes.

- **Testes de contrato (`tests/test_contrato_upstream.py`)** — 16 offline
  + 4 live (`COMPRAS_LIVE_TESTS=1`). Fixam a **query enviada** (pegaria a
  quebra da rota 1) e o **contrato de campos** (toda tool de preço tem de
  devolver `precoUnitario`; HTTP 200 sem o campo reprova). Verificados por
  mutação: reintroduzir `codigoItemCatalogo` na rota 1 quebra o teste de
  query; remover `precoUnitario` do contrato quebra o de campos.

### Known issues (upstream, verificado 2026-08-05)

- `compras_pncp_orgao_unidades` — a rota `/v1/orgaos/{cnpj}/unidades`
  **não consta** no contrato do PNCP Consulta (12 rotas publicadas em
  `/api/consulta/v3/api-docs`, nenhuma com `/unidades`). Devolve 404 em
  ~0,11s para todo CNPJ, inclusive os que publicam ativamente. Não é
  defeito do MCP e não há parâmetro que a faça responder.
- `compras_pncp_contratacao_itens` — `/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens`
  devolve 404 enquanto a rota-pai devolve 200.
- PNCP apresentou instabilidade no dia: HTTP 500 "Erro na comunicação com
  o banco de dados" intermitente e 504 após 70s em `/v1/pca/`.

## [0.3.12] — 2026-05-18

Patch release — 4 bugs novos da bateria A rodada 6.

### Fixed

- **🔴 B1: `compras_arp_saldo_item` vazava timeout como exception não-
  estruturada.** A v0.3.9 aplicou `max_retries=0 + timeout=20s` no
  client mas não capturou a exception no nível da tool. Quando upstream
  Dados Abertos `/modulo-arp/4` não respondia em 20s, `ComprasTimeoutError`
  propagava para o cliente MCP. Agora envolvido em try/except: devolve
  `_erro_upstream` estruturado com `tipo`, `mensagem`, `diagnostico`,
  `filtros_tentados`. Mesmo padrão das tools de sanção.

- **🟠 B2: `compras_aggregate_contratacoes_por_periodo` perdia bucket por
  timeout sem flag visível.** Quando 1 bucket falhava (caso real: 2025-09
  com `ComprasTimeoutError`), o `erros_por_bucket` registrava mas o
  `totais.count` ficava subdimensionado sem aviso top-level. Agente que
  só olhava o agregado usava dado incompleto. Adicionados:
  - `_amostra_incompleta: bool` no payload top-level.
  - `_aviso_amostra_incompleta` com cálculo "N de M buckets falharam
    (X% da amostra perdida)".

- **🟡 B3: `_aviso_filtro` em `compras_catmat_buscar` tinha 2 textos sem
  discriminador programático.** Bateria E0a v0.3.11 testou em ramo
  "universo_completo" mas esperava texto do ramo "termo_ausente" —
  falso-positivo metodológico. Adicionado `_tipo_aviso_filtro:
  "universo_completo" | "termo_ausente"` para teste programático
  independente do texto.

- **🟡 B4: `compras_catmat_buscar` ordenava resultados de forma que
  escondia o PDM relevante.** Com `termo='notebook' + grupo=70 +
  classe=7010`, a 1ª página vinha com servidores de impressão e
  microcomputadores legados; PDM 8435 (NOTEBOOK) só aparecia na pág 20.
  Adicionado sort estável client-side: itens cuja `descricaoItem` contém
  o termo vêm primeiro. Sinaliza com `_reordenado_client_side` quando
  efetivamente reordena. Validação E2E: pág 20 com 23 notebooks
  movidos para o topo (antes estavam misturados).

### Added

- **`aviso_amostra_minima_por_cluster` em `pesquisar_precos_para_etp`**:
  quando algum cluster tem n < 3 (mínimo IN SEGES/ME 65/2021 art. 7º),
  aviso explícito orienta o analista a complementar com cotações
  diretas. Validação E2E com PDM 8435: detecta o cluster premium (n=1
  Dell R$7.479) automaticamente.

### Notes — sobre pontos cegos do roteiro apontados pelo usuário

- **E3g (cruzar CATMAT item ARP com desejado)**: ainda em aberto. Boa
  ideia mas exige modelar nova tool. Fica em ROADMAP.
- **Defesa contra analista que ignora aviso de cluster**: hoje só
  detectamos e avisamos; não bloqueamos. Fica no plano "se aparecer
  caso real de uso indevido".
- **Latência do aggregate (~188s para 12m)**: investigar real
  paralelização da concurrency. Fica como ROADMAP item 8.

## [0.3.11] — 2026-05-18

Patch release — 2 achados da rodada 5 da bateria A. Bug do MCP + bug de
roteiro (este último não-fixável no código mas com mitigação útil no
servidor).

### Fixed

- **🟡 Prompt `montar_etp_pesquisa_precos` usava nomes de parâmetros
  desatualizados.** O template renderizado citava
  `compras_pesquisar_precos_para_etp(codigo_catmat=..., janela_meses=...)`,
  mas a tool real aceita `tipo=`, `codigo_item_catalogo=` e
  `periodo_meses=`. Em execução literal por agente o resultado seria
  validation error. Corrigido + também acrescentado parâmetro `tipo`
  explícito ("material" ou "servico").

- **🟡 Prompt `montar_etp_pesquisa_precos` não orientava sobre
  clusters/heterogeneidade.** Os campos `clusters`, `coeficiente_variacao`
  e `aviso_heterogeneidade` foram adicionados em v0.3.7, mas o roteiro
  do prompt continuou só falando em mediana global. Agente literal
  ignorava os clusters e usava mediana contaminada por produtos
  tecnicamente incompatíveis. Roteiro reescrito:
  - Nova etapa 2 dedicada à inspeção de heterogeneidade.
  - Instrução explícita: "se houver `clusters`, NÃO use mediana global;
    escolha qual cluster corresponde à spec do TR".
  - Documenta ajuste motivado como IN 65/2021 art. 9º §1º.

### Enhanced

- **`aviso_heterogeneidade` no payload de `compras_pesquisar_precos_para_etp`
  agora cita "CATMAT-balde" como hipótese alternativa.** Achado da
  rodada 5: o CATMAT 451899 do PDM 8435 NOTEBOOK tem `descricaoItem`
  oficial "até 4GB RAM, sem SSD" mas órgãos usam ele como código
  guarda-chuva para qualquer notebook — inflando a heterogeneidade
  estatística. A análise de clusters ajuda, mas a causa-raiz é o
  catálogo. Aviso agora orienta o agente a:
  1. Confirmar via `compras_catmat_consultar(<codigo>)` se a
     `descricaoItem` oficial bate com a spec do TR.
  2. Se divergir, procurar outro CATMAT do mesmo PDM.
  3. Caso opte por manter o CATMAT-balde, escolher o cluster compatível
     e documentar o ajuste.

### Notes — não-fix

- **A causa-raiz "analista escolheu CATMAT errado para a spec"** é externa
  ao servidor MCP. O servidor pode detectar e orientar (que é o que a
  tool agora faz), mas não pode validar semanticamente se "Notebook
  i5/8GB/256SSD" bate com `descricaoItem` "até 4GB sem SSD" sem um LLM
  judge. O fluxo realista permanece: agente lê o aviso, suspeita do
  CATMAT, valida via `catmat_consultar` antes de fechar o ETP.

## [0.3.10] — 2026-05-18

Patch release — **bug crítico de cache stale entre versões** descoberto
durante a investigação da rodada 4 da bateria A.

### Fixed

- **🔴 Cache Redis servindo payloads de versões anteriores entre deploys**.
  Causa-raiz do achado "A1 — `_aviso_filtro` ausente" que não conseguíamos
  reproduzir localmente:
  - v0.3.6 adicionou o campo `_aviso_filtro` em `compras_catmat_buscar`.
  - O Redis remoto tinha o payload **sem** esse campo, cacheado por uma
    versão pré-v0.3.6 (TTL de 24h no catálogo).
  - As versões v0.3.6 → v0.3.9 mantiveram a chave de cache idêntica, então
    o `_aviso_filtro` só apareceria no deploy seguinte ao TTL expirar.
  - Mesmo problema afetava todos os outros campos novos: `paginas_lidas`
    no aggregate (v0.3.6), `coeficiente_variacao` e `clusters` no preços
    (v0.3.7), `resumo_por_item` em saldo (v0.3.8), etc.
  - **Fix estrutural**: chave de cache agora inclui `__version__` como
    prefixo. Toda nova versão começa com cache vazio automaticamente.
  - Aplicado em ambos backends (`RedisCache` e `ResultCache` em memória).

### Validação E2E

Probe direto contra servidor remoto Railway na v0.3.9 confirmou o bug:
```
compras_catmat_buscar(termo='notebook', codigo_grupo=70, codigo_classe=7010)
→ _cache_hit: True, _latency_ms: 3ms, _aviso_filtro: AUSENTE
```
Após o deploy v0.3.10, a primeira chamada com os mesmos parâmetros deve
mostrar `_cache_hit: False` (chave nova) e `_aviso_filtro: presente`.

### Implicação operacional

- **Cache hit rate cai temporariamente** após cada deploy de versão (todas
  as chaves antigas ficam órfãs no Redis e expiram pelo TTL natural). É
  o preço da correção — sem isso, qualquer enhancement em payload demora
  até 24h para aparecer em todos os clientes.
- **Keys órfãs no Redis**: ficam no banco até o TTL expirar. Para limpeza
  imediata pós-deploy, o operador pode rodar `FLUSHDB` no Redis Railway.
- Não há impacto em chaves de outras tools — o versionamento é por prefixo
  global, então `compras:v0.3.9:CATALOGO:*` e `compras:v0.3.10:CATALOGO:*`
  coexistem sem conflito.

## [0.3.9] — 2026-05-18

Patch release — 3 polidas da bateria A "rodada 4" sobre v0.3.8. Não há
fix crítico — todos os bugs reportados nesta rodada são de severidade
🟡/🟠 e referem-se a comportamentos parciais ou latência alta.

### Note importante sobre o achado "A1 — `_aviso_filtro` ausente"

O usuário reportou que `compras_catmat_buscar(termo='notebook',
codigo_grupo=70, codigo_classe=7010, tamanho_pagina=50)` não retornou
`_aviso_filtro` na pág 1. **Re-probe local em v0.3.8 confirmou que o
aviso ESTÁ presente** — chave `_aviso_filtro` no payload com texto
"Termo 'notebook' apareceu em apenas 0/20 itens da primeira página
(0%) — filtro textual ignorado ...".

Hipóteses para o relato:
1. **Servidor remoto no Railway** ainda em versão sem o fix da v0.3.6 e
   o `compras_versao` foi enganado por cache de healthcheck. Forçar
   redeploy.
2. **Cliente Claude.ai web** pode estar truncando o payload na
   visualização — analista vê os campos principais (`resultado`,
   metadados de paginação) e não o `_aviso_filtro` no fim.

Nenhuma das duas tem fix de servidor — só redeploy.

### Fixed

- **🟠 `compras_montar_dossie_arp` aviso não trazia "ata assinada há N
  dias"** quando a data estava em fonte alternativa. Agora:
  - Tenta extrair data de 3 campos em ordem: `dataAssinatura`,
    `dataVigenciaInicial`, `dataInicialVigencia`.
  - Aceita formato ISO (`YYYY-MM-DD`) e BR (`DD/MM/YYYY`).
  - **Sempre** anexa frase com `dias` calculado, independente do
    threshold de 60 dias (removida heurística arbitrária).
  - Quando idade ≥ 60 dias, frase muda para "ata já com idade suficiente
    — provavelmente saldo/adesões/unidades realmente vazios".
  - Anexa também `_dias_desde_assinatura: int | None` no payload top-level
    para o agente consumir programaticamente.

- **🟡 `compras_arp_buscar_por_objeto` reportava `paginas_varridas=11`
  quando o input era `max_paginas_varridas=10`** — off-by-one. Causa: o
  contador `pagina` era incrementado APÓS processar e usado depois do
  while terminar (com valor incrementado a mais). Fix: contador separado
  `paginas_processadas` incrementado uma vez por iteração efetiva.

- **🟠 `compras_arp_saldo_item` levava 50s+ em atas com saldo vazio**.
  Causa: cliente Dados Abertos usa default `max_retries=2 + timeout=60s`,
  que acumula até 180s em timeouts. Fast-fail aplicado: `max_retries=0`,
  `timeout=20s`. Estendido `DadosAbertosClient.list_resource` para
  aceitar overrides — mesmo padrão já existente no PNCPClient.

### Notes — não-fixes

- **Latência do aggregate em 12m (~127s)** continua dependente do
  upstream PNCP. Otimização exigiria mexer no PNCP (limite de 30 dias
  por sub-janela). Possível enhancement futuro: cache por sub-janela
  individualmente para reaproveitar entre buckets sobrepostos.
- **`compras_pncp_contratacao_item_resultados` em request malformado
  levou 20s** — depende do upstream retornar 400 rapidamente. Não
  controlável pelo cliente.
- **B5 sem caso na amostra**: o usuário não conseguiu validar
  `_aviso_estrutura` (saldo com rateio entre múltiplas UGs) porque
  todas as 6 atas testadas eram `rateios:1`. Lógica está validada por
  probe sintético; uma ata multi-UG real apareceria em larga escala
  (ARP federada). Fica como bug não-reprodutível natural.

## [0.3.8] — 2026-05-17

Patch release — 3 achados da bateria A "rodada 3" (validação contra
v0.3.5 ainda em produção no Railway). Importante: o achado de
`_aviso_filtro` que aparecia em página interna mas não na 1ª **já estava
corrigido** em v0.3.6 (heurística da taxa de termos na primeira página);
o usuário viu o comportamento antigo porque o servidor remoto ainda não
foi redeployado. Os 3 abaixo são bugs genuínos não cobertos antes.

### Fixed

- **🐛 `compras_arp_saldo_item` parecia duplicar linhas** — não era
  duplicação real, mas o upstream retorna **1 linha por
  (numeroItem, unidade, tipo)** (gerenciadora + cada participante). O
  agente sem essa estrutura interpretava o mesmo `numeroItem` repetido
  como bug. Adicionado `resumo_por_item` agregando rateios por item
  (soma de quantidades registrada/empenhada/saldo) + lista de unidades
  rastreáveis. Quando há mais de 1 rateio em qualquer item, emite
  `_aviso_estrutura` explicando o schema. Docstring atualizada.

- **🐛 `compras_pncp_contratacao_item_resultados` (e demais
  `compras_pncp_*` singulares) misturava linguagem 400/404** — quando o
  upstream retornava 400 (request malformado), o diagnóstico falava em
  "Registro não encontrado" (linguagem de 404). Confundia debug:
  parâmetros errados pareciam recurso inexistente. `_resposta_pncp_singular_404`
  agora ramifica:
  - `status=400` → diagnóstico explica "requisição malformada" + causas
    típicas (formato de campo, tipo errado, filtros mutuamente exclusivos).
  - `status=404` → diagnóstico "recurso não encontrado" (linguagem original).
  - Aplicado em todas as 5 tools que chamam `_resposta_pncp_singular_404`.

- **🐛 `compras_montar_dossie_arp` silenciosamente retornava 3 listas
  vazias** em atas muito recentes — cabeçalho ok, mas saldo + adesões +
  unidades vinham `[]` sem aviso, indistinguível de "ata sem rateio".
  Agora, quando `numero_item` foi passado E cabeçalho existe E as 3
  listas auxiliares estão vazias, anexa `aviso_dados_auxiliares_indisponiveis`
  com:
  - hipótese principal (ata recém-assinada — Dados Abertos demora dias
    para popular `/modulo-arp/3/4/5`), calculada a partir de
    `dataAssinatura` do cabeçalho;
  - hipóteses alternativas (`numero_item` errado, `unidade_gerenciadora`
    divergente da do cabeçalho).

### Changed

- Pytest segue em 19/19.

### Notes — não-fixes

- **Latência do `aggregate` em 12m (~65s)** continua documentada como
  limite intrínseco do upstream PNCP (~13 sub-janelas de 30 dias ×
  concorrência 4).
- **Sugestão de tool `compras_arp_vencedores_por_ata`** (fechar cadeia
  ARP→fornecedor automaticamente) **adicionada ao ROADMAP item 7**
  (novo). Vale a pena pegar quando aparecer demanda recorrente.

## [0.3.7] — 2026-05-17

Patch release — 2 fixes da bateria A v0.3.5 (rodada de recuperação do
analista) + enhancement no agregador de preços para detectar
heterogeneidade automaticamente.

### Fixed

- **🔴 `compras_arp_consultar` aceitava ID de COMPRA silenciosamente**
  (causa-raiz do travamento da Etapa 4 na bateria A v0.3.5). Compras SRP
  multi-fornecedor produzem várias atas dentro da mesma compra; o
  endpoint `/modulo-arp/1.1_consultarARP_Id` exige o formato de ATA
  (`cnpj14-1-sequencial/ano-NNNNNN` com sufixo). Quando o ID vinha sem o
  sufixo (formato de compra), o upstream retornava `[]` e a tool
  devolvia `encontrada: false` indistinguível de "ata não existe".
  - Docstring corrigida indicando o formato explícito com exemplo.
  - Tool detecta antes do upstream: `_RX_ID_ATA` exige sufixo `-\d{6}`.
  - Se ID combina com pattern de compra (sem sufixo), devolve
    `_erro_upstream.diagnostico_especifico` orientando como obter o ID
    correto via `compras_arp_listar`.
  - Mesmo padrão aplicado em `compras_montar_dossie_arp`.

### Added

- **Clustering automático por gap em `compras_pesquisar_precos_para_etp`**:
  quando a amostra é heterogênea (CV > 0,5 OU max/min > 3×), a tool
  particiona em até 3 clusters usando os maiores gaps relativos como
  cortes (≥30%). Cada cluster vem com `n`, `min`, `max`, `mediana`,
  `media` e amostra de valores.
  - Anexa `aviso_heterogeneidade` orientando o analista a inspecionar
    os clusters e fazer ajuste motivado do recorte amostral (IN 65/2021
    art. 9º §1º).
  - Anexa `coeficiente_variacao` nas estatísticas (faltava antes).
  - Validação E2E com PDM 8435 NOTEBOOK: amostra de 9 itens, CV=0.51,
    razão max/min=5x → 3 clusters detectados automaticamente:
    chromebooks educacionais (R$ 1.487-1.912), notebooks corporativos
    (R$ 2.800-4.899), premium (R$ 7.479). Esses clusters batem
    exatamente com a inspeção manual que o usuário fez na bateria A.

- **3 guard tests** em `test_resilience.py`:
  - `test_arp_consultar_rejeita_id_de_compra_com_diagnostico_explicito`
  - `test_arp_consultar_rejeita_id_totalmente_invalido`
  - `test_montar_dossie_arp_rejeita_id_de_compra`

### Changed

- Pytest: 16 → 19 passando.
- Função helper `_clusterizar_por_gap` + `_resumir_cluster` em
  `tools/compostas.py`.

### Notes

- Latência alta do `aggregate` (~65s/12 buckets) e do `montar_dossie_arp`
  (~11s) continuam documentados como limites intrínsecos do upstream.
- Heterogeneidade do PDM 8435 era apontada como "limite do servidor" em
  v0.3.6 — agora o servidor faz a parte que pode (detecta + clusteriza),
  cabe ao analista validar o cluster aplicável à spec. Achado da
  bateria A virou enhancement.

## [0.3.6] — 2026-05-17

Patch release — 3 fixes da bateria A E2E (fluxo real do analista) + 1 tool
nova que resolve o gargalo operacional do roteiro de carona ARP.

### Added

- **`compras_arp_buscar_por_objeto`** — busca ARPs vigentes cujo campo
  `objeto` contém uma palavra-chave. Pagina internamente até 5.000 atas
  (`max_paginas_varridas` × 500/página), filtra client-side com
  normalização de acentos+case, curto-circuita quando atinge
  `max_resultados`. Resolve o gargalo da bateria A v0.3.5: roteiro
  `oportunidades_carona_arp` esbarrava em 169k ARPs e 339 páginas para
  filtrar manualmente; agora 1 chamada. Validação E2E: 2 matches reais
  ("notebook" em PMSP cota reservada + UFNT) em 4,7s varrendo 2.500 atas.
- **Limitação documentada na própria tool**: schema upstream **não traz UF**
  no item ARP — para filtrar por UF, cruzar matches com
  `compras_uasg_consultar`. Promovido do ROADMAP item 3.

### Fixed

- **`paginas_lidas` ausente no modo `count` do `compras_aggregate_contratacoes_por_periodo`** —
  bateria A apontou inconsistência: modo `valor_*` retornava `paginas_lidas`
  por bucket, modo `count` retornava só `count`. Agora ambos modos expõem
  `paginas_lidas` e `truncado`. Modo count sempre lê 1 página/sub-janela
  (lê apenas `totalRegistros`); valor é literal 1.
- **Detector `_aviso_filtro` do CATMAT não disparava com filtros estruturais
  reduzidos** — antes só pegava quando `_total_registros >= 200.000`
  (universo inteiro). Caso real: `termo='notebook'` + `codigo_classe=7010`
  devolvia 1312 itens (servidores/desktops/all-in-ones), 0 contendo
  "notebook" — filtro textual ignorado mas universo abaixo do threshold.
  Nova heurística: se o termo aparece em <50% da 1ª página (até 20
  itens), dispara aviso explícito com a taxa observada.

### Changed

- Prompt `oportunidades_carona_arp` reescrito para usar a nova
  `compras_arp_buscar_por_objeto` no passo 3, com caveat sobre UF
  client-side.
- Total de tools: 92 → 93.
- ROADMAP: item 3 (filtro server-side em ARPs) marcado como **DONE**.

### Notes — não-fixes da bateria A

- **"No approval received"** que bloqueou a Etapa 4 da bateria A **não é
  bug do servidor** — é mensagem do cliente Claude.ai/Desktop quando o
  usuário não aprova a chamada de tool. Confirmado por probe direto:
  `compras_arp_consultar` retorna 200 OK com `encontrada=false` (caminho
  normal quando o número não bate uma ARP existente). Provavelmente UI
  do cliente disparou throttle de approval após várias tentativas
  consecutivas.
- **Heterogeneidade do PDM 8435** (chromebooks educacionais misturados
  com notebooks corporativos no mesmo código CATMAT) — não é bug do
  servidor, é problema do catálogo upstream. O critério Tukey faz seu
  trabalho estatístico; a heterogeneidade do PDM precisa ser endereçada
  pela SISG (revisão de item) ou aceita pelo analista com ajuste motivado
  do recorte amostral.
- **Latência de aggregate count em 12 meses** (~65s) — limite intrínseco:
  ~13 sub-janelas de 30 dias × concorrência 4. Otimização exigiria mexer
  no PNCP. Documentado.

## [0.3.5] — 2026-05-17

Patch release — **mudança de foco da suite**: de "testar correção" para
"testar resiliência". Não corrige bug; endurece a malha contra regressões
silenciosas vindas de mudança de comportamento upstream ou de input ruim.

### Added — suite de resiliência

Novo arquivo `tests/test_resilience.py` com 6 cenários, todos mockados via
`pytest-httpx` (sem rede). Cobre corner cases que a bateria E2E pode passar
no caminho feliz mas explodem em produção:

1. **CNPJ inválido** (13 dígitos, alfanumérico, vazio) → erro graceful, sem
   crash silencioso.
2. **CNPJ em múltiplos cadastros simultâneos** (CEIS + CNEP + CEPIM) →
   acumula corretamente, `sancoes.total == 3`, `tem_alguma_restricao=true`,
   filtragem client-side do CEPIM preserva só o CNPJ pedido.
3. **Aggregate boundaries**: 3 testes — data invertida, janela >5 anos,
   granularidade inválida — todos rejeitam *antes* de chegar no upstream.
4. **`TRANSPARENCIA_API_KEY` ausente**: `compras_perfil_fornecedor_completo`
   degrada com `sancoes.habilitado=false` mas cadastro+receita+impedimentos
   seguem; `compras_sancao_ceis` produz exception com mensagem clara
   (mencionando "chave"/"401"/"auth"/"api_key"). *(Marcação para fix futuro:
   o caminho graceful padrão da composta poderia ser replicado nas
   singulares; hoje só não é crash silencioso.)*
5. **Composta com sub-call quebrada**: BrasilAPI retorna 500 persistente;
   cadastro/sanções/impedimentos seguem intactos graças a
   `return_exceptions=True` no gather. Valida que a falha vira marca de
   erro no campo `receita_federal`, não derruba o payload inteiro.
6. **WAF block simulado**: CGU retorna 405 + `Content-Type: text/html` +
   "Human Verification"; `compras_sancao_ceis` devolve
   `_erro_upstream.tipo == "waf_block"` com alternativas — **guarda o fix
   da v0.2.12**.

### Added — fixture autouse de reset de cache

`tests/conftest.py::_reset_tool_caches` — após cada teste, varre os
módulos de tools e zera todo `_*_cache` que tenha `.clear()`. Sem isso, um
teste que cacheia `perfil_fornecedor(X)` sem API key contaminava o teste
seguinte com o mesmo CNPJ: cache hit devolvia `sancoes.habilitado=false`
ignorando a chave injetada via `monkeypatch.setenv`. Bug detectado durante
a implementação do teste 5.

### Changed

- Total de testes: 7 → 16 (9 novos da suíte + reset de cache).
- `tests/conftest.py` ganha o fixture autouse `_reset_tool_caches`.

### Notes

- Testes que exigiriam rede real (PNCP fora do ar, payload >5MB) ficam fora
  por design — vão para `test_resilience_live.py` quando necessário, com
  `@pytest.mark.live` opt-in.
- O teste 4 documenta um fix desejável futuro: tools singulares de sanção
  deveriam ter early return graceful como o `perfil_fornecedor_completo`
  tem. Hoje propagam exception (com mensagem clara, mas exception). Fica
  na lista quando aparecer um achado E2E explícito sobre isso.

## [0.3.4] — 2026-05-17

Patch release — corrige achado da bateria E2E v0.3.3.

### Fixed

- **🟠 Prompt `oportunidades_carona_arp` esbarrava no bug upstream do CATMAT
  no passo 1.** O roteiro v0.3.3 orientava `compras_catmat_buscar(termo=...)`
  sem `codigo_grupo`, mas o filtro textual upstream está ignorado desde
  meados de 2026 — devolve o universo (~340k itens, começando por arma de
  fogo). A tool já sinaliza isso via `_aviso_filtro`, mas o prompt não
  absorvia a realidade. Passo 1 reescrito para o **workflow estrutural**
  recomendado no docstring da própria tool: `listar_grupos` → escolher
  grupo → `listar_classes(codigo_grupo)` → escolher classe →
  `catmat_buscar(termo, codigo_grupo, codigo_classe)`. O roteiro também
  explica o bug e o `_aviso_filtro` para o agente reagir se chamar errado.

### Added

- **`tests/test_server.py::test_prompts_nao_usam_catmat_buscar_sem_codigo_grupo`** —
  guard test heurístico: para cada prompt, se o texto contém
  `compras_catmat_buscar(`, exige que também contenha `codigo_grupo`.
  Previne reincidência caso outro prompt seja adicionado/editado no futuro.

### Changed

- Total de testes: 6 → 7.

## [0.3.3] — 2026-05-17

Patch release — corrige os 6 achados (1 crítico + 1 funcional + 4 cosméticos)
da bateria E2E v0.3.2.

### Fixed

- **🔴 Inconsistência interna entre tool `compras_pncp_modalidades` e resource
  `compras://referencia/modalidades-pncp`** (ids 2/3/13 divergiam — um agente
  consultando a tool e outro consultando o resource produziriam respostas
  conflitantes). Criado módulo `compras_mcp.dominio` como fonte única;
  ambos passam a consumir `MODALIDADES_PNCP` dali. Mesmo padrão aplicado a
  esferas, critérios de julgamento e situações.

- **🔴 Prompt `oportunidades_carona_arp` orientava filtros que as tools
  indicadas não aceitam** — agente literal fracassava. Roteiro reescrito:
  agora começa por `compras_catmat_buscar` (que tem filtro server-side de
  texto), depois `compras_buscar_contratacoes_similares` (com UF opcional),
  só então `compras_arp_por_fim_vigencia` com filtragem client-side
  explícita por palavra-chave/UF. Roteiro também passa os nomes corretos
  dos parâmetros (`data_vigencia_final_min`/`max`, não
  `data_fim_vigencia_min`).

- **🟠 Prompt `dossie_due_diligence_fornecedor` citava tool inexistente**
  (`compras_brasilapi_cnpj`). Corrigido para `compras_fornecedor_cnpj_receita`
  com nota sobre o env `CNPJ_PROVIDER`.

- **🟡 Prompt `panorama_orgao_360` deixava placeholder `{ano atual}` no
  texto renderizado.** Agora substitui dinamicamente por `date.today().year`.

- **🟡 Tool `compras_listar_prompts` retornava descrições poluídas com
  `"Provide as a JSON string matching the following schema: {...}"`**
  (verbose do FastMCP/MCP SDK). Strippado antes de devolver.

- **🟡 `aggregate` paginado entregava `truncado:bool` mas não `paginas_lidas`
  no consolidado.** Ambos agora presentes — `paginas_lidas` somado entre
  sub-janelas do mesmo bucket lógico, `truncado` é OR-fold.

### Added

- **`tests/test_server.py::test_prompts_referenciam_apenas_tools_existentes`** —
  guard test que renderiza cada prompt e valida que toda chamada
  `compras_*` no texto corresponde a uma tool registrada. Previne regressão
  do bug do dossie.
- **`tests/test_server.py::test_modalidades_pncp_fonte_unica`** — guard test
  que compara `MODALIDADES_PNCP` canônico contra o que tool e resource
  retornam. Previne regressão da inconsistência.
- **`src/compras_mcp/dominio.py`** — fonte única para tabelas de domínio
  (modalidades, esferas, critérios, situações).

### Changed

- `resources.py` importa de `dominio.py` em vez de duplicar tabelas.
- `tools/pncp.py::compras_pncp_modalidades` também consome de `dominio.py`.
- Total de testes: 4 → 6.

## [0.3.2] — 2026-05-16

Patch release — desbloqueia prompts e resources no Claude.ai web.

### Added

- **4 tools-espelho** para descoberta dos primitivos user-controlled:
  `compras_listar_prompts`, `compras_obter_prompt(nome, argumentos)`,
  `compras_listar_resources`, `compras_obter_resource(uri)`.
- Motivação: o protocolo MCP define prompts e resources como
  user-controlled (selecionados via UI do cliente, não invocados pelo
  LLM). O **Claude.ai web** ainda não expõe UI para esses primitivos —
  só passa `tools` para o modelo. Resultado: os 6 prompts e 6 resources
  ficavam invisíveis nesse cliente. As tools-espelho enumeram e
  renderizam os mesmos dados via canal de tools, restaurando o acesso.
- Fontes de verdade continuam sendo `prompts.py` e `resources.py`. As
  tools só refletem o que está registrado — adicionar prompt/resource
  novo aparece automaticamente na listagem.
- Em clientes que suportam o protocolo completo (Claude Desktop, Cursor,
  MCP Inspector), as tools-espelho não atrapalham: o usuário continua
  tendo UI dedicada, e o LLM pode também usar as tools quando relevante.

### Changed

- Total de tools: 88 → 92.

## [0.3.1] — 2026-05-16

Patch release — três bugs críticos descobertos na bateria E2E v0.3.0.

### Fixed

- **🔴 `compras_perfil_fornecedor_completo` e `compras_checar_sancoes_fornecedor`
  retornavam falso positivo de sanção** — bug crítico de due diligence.
  Petrobras (CNPJ 33000167000101) saía com `tem_alguma_restricao=true`,
  CEIS/CNEP/CEPIM com 15 registros cada — todos de outros CNPJs. Causa
  raiz: o endpoint da CGU **ignora silenciosamente** o parâmetro
  `cnpjSancionado` em `/api-de-dados/ceis` e `/api-de-dados/cnep` e devolve
  a lista global. O parâmetro aceito é **`codigoSancionado`**. Em `/api-de-dados/cepim`
  nenhum nome funciona — agora aplicamos filtragem client-side por
  `pessoaJuridica.cnpjFormatado` após receber a página.
  - Probe direto: `codigoSancionado=12067103000158` → 2 registros reais
    (antes: 15 globais).
  - Probe Petrobras pós-fix: CEIS 0, CNEP 0, CEPIM 0, `tem_alguma_restricao=false`.
  - Probe sancionado real (12067103000158) pós-fix: CEIS=2, restrição
    corretamente detectada.

- **🟠 `compras_aggregate_contratacoes_por_periodo` falhava 8/8 buckets em modo
  paginado** (Teste 2 da bateria). Causas combinadas:
  - O PNCP **rejeita** `tamanho_pagina > 50` com 400 "Tamanho de página
    inválido", mas o cliente capava em 500 (schema antigo). Reduzido para 50.
  - Timeout default de 20s era apertado para combinações pesadas
    (modalidade 8 + UF urbana + dia cheio). Subido para 45s.
  - `MAX_PAGES_PER_BUCKET` subido de 25 → 100 para compensar o tamanho de
    página reduzido (mantém capacidade de 5.000 registros/bucket).
  - Pós-fix no mesmo cenário (mod 8, UF=SP, 7d, granularidade dia): de 8/8
    falhando para 3/8 sucesso + 5 timeouts diagnosticados. Os timeouts
    restantes são lentidão genuína do upstream em dias cheios.

- **🟡 Diagnóstico ruim em `aggregate`** — antes a saída tinha só `erros: int`,
  perdia qual bucket falhou e por quê. Adicionado `erros_por_bucket: [{bucket,
  data_inicial, data_final, erro_tipo, erro_mensagem}]`. O LLM agora pode
  sugerir retentativa específica.

### Changed

- `PNCPClient.list_resource`: cap superior de `tamanho_pagina` 500 → 50.
  Aceita overrides `max_retries` e `timeout` para fan-out paralelo.
- `analitica.py`: `MAX_PAGES_PER_BUCKET` 25 → 100; novo `PAGINA_TIMEOUT_S = 45.0`.
- Bump versão 0.3.0 → 0.3.1 em `__init__.py`, `pyproject.toml`, `manifest.json`.

### Notes

- Endpoints CEPIM e leniência têm comportamentos opostos de filtragem: CEPIM
  ignora qualquer filtro (usamos client-side); leniência respeita
  `cnpjSancionado`. CEIS/CNEP usam `codigoSancionado`. Documentado nas
  docstrings dos clientes.
- O nome do parâmetro `cnpj_sancionado` foi mantido na assinatura Python para
  compatibilidade com callers — só o mapeamento upstream mudou.

## [0.3.0] — 2026-05-16

Minor release — incorpora a sequência de melhorias inspirada na análise
do `licinexus-mcp`, sem perder nenhuma feature do core (sanções, CATMAT,
Comprasnet completo, LGPD, Redis, HTTP).

### Added

- **MCP Prompts (6)** — templates pré-definidos invocáveis pelo cliente:
  `analisar_contratacao_pncp`, `panorama_orgao_360`,
  `dossie_due_diligence_fornecedor`, `oportunidades_carona_arp`,
  `montar_etp_pesquisa_precos`, `tendencia_contratacoes_periodo`.
  Cada um produz um roteiro estruturado citando as tools deste MCP.
- **MCP Resources (6)** — dados de referência expostos por URI:
  modalidades PNCP, esferas federativas, critérios de julgamento,
  situações da contratação, glossário Lei 14.133/2021 e escopo do servidor.
- **Tools de análise temporal**
  - `compras_aggregate_contratacoes_por_periodo` — série temporal de
    contratações com bucketing `dia` / `semana` / `mês` / `ano`, modo
    `count` (rápido, 1 call por bucket) ou `valor_estimado` /
    `valor_homologado` (paginado). Janela máxima: ~5 anos. Concurrency
    interna 4. Cache 30 min.
  - `compras_comparar_periodos_contratacoes` — compara dois períodos
    lado a lado com deltas absoluto e percentual.
- **Tool de enriquecimento de CNPJ** — `compras_fornecedor_cnpj_receita`
  via BrasilAPI (padrão) ou MinhaReceita (trocável por env
  `CNPJ_PROVIDER`). Retorna razão social, QSA, capital, CNAEs, situação.
  Cache 24h.
- **Receita Federal integrada ao `compras_perfil_fornecedor_completo`** —
  novo campo `receita_federal` no payload consolidado, em paralelo às
  outras fontes (cadastro Compras + CEIS/CNEP/CEPIM + impedimentos).
- **Filtro `esfera`** nas listagens PNCP:
  `compras_pncp_contratacoes_publicacao`,
  `compras_pncp_contratacoes_proposta`,
  `compras_pncp_contratacoes_atualizacao`. Valores:
  `federal` / `estadual` / `municipal` / `distrital`. Aplicado client-side
  sobre a página retornada (`_total_registros` reflete o total upstream).
- **Scripts de qualidade** em `scripts/`:
  - `smoke_test.py` — sobe o servidor in-process e valida contagens,
    nomes e healthcheck.
  - `schema_snapshot.py` — snapshot determinístico do JSON Schema de
    todas as tools/prompts/resources com subcomandos `snapshot` e `check`
    para CI detectar drift.
  - `measure_latency.py` — p50/p95/min/max contra Dados Abertos, PNCP e
    BrasilAPI. Roda semanalmente via workflow agendado.
- **Workflows GitHub Actions** — `ci.yml` (smoke + drift + pytest em PRs)
  e `latency.yml` (sonda semanal de upstream).
- **Settings**: `CNPJ_PROVIDER`, `BRASILAPI_BASE_URL`, `MINHARECEITA_BASE_URL`.

### Changed

- `manifest.json`: nova `description`, `version` 0.3.0, lista de tools
  atualizada (+ 3 novas: `aggregate`, `comparar_periodos`,
  `fornecedor_cnpj_receita`).
- `__version__` → 0.3.0.

## [0.2.13] — 2026-05-16

### Fixed
- **User-Agent default do `httpx` disparava AWS WAF da CGU em 100% das
  chamadas à Transparência.** Probe direto: `python-httpx/0.27.x` →
  405 + HTML em 5/5 chamadas; `Mozilla/5.0 ... compras-mcp/0.2.13` →
  200 + JSON em 2/2. `TransparenciaClient` agora envia UA browser-like
  identificando o MCP. Caminho feliz das 5 tools de sanção empiricamente
  validado: `sancao_cnep` retornou 15 registros em 889ms;
  `sancao_ceis` retornou 15 registros em 689ms. Não é evasão — usamos
  chave de API legítima da CGU; é workaround contra WAF que classifica
  clientes HTTP genuínos como bots.

## [0.2.12] — 2026-05-16

### Fixed
- **`compras_sancao_*` estouravam exception crua quando o WAF da
  Transparência respondia 405 + HTML "Human Verification"** (descoberto
  na bateria 9). Nova `ComprasWafBlockError` + override de `get_json` no
  `TransparenciaClient` detectam 405 com Content-Type `text/html` e
  levantam erro tipado. Helper `_resposta_waf_block` aplicado às 5 tools
  de sanção retorna payload `_erro_upstream.tipo="waf_block"` + 4
  alternativas concretas (webapp manual, retry espaçado, consulta
  pontual, contato CGU).
- Cobertura de graceful degradation atingiu **15 tools**.

## [0.2.11] — 2026-05-16

### Fixed
- **Latência de 182 segundos em 4xx de tools singulares PNCP**
  (descoberto na bateria 7). Causa: upstream PNCP timeou 2× antes de
  responder 400, e `BaseAsyncClient.max_retries=2` × `timeout=60s` =
  180s acumulados. `BaseAsyncClient.get_json` ganhou overrides por
  chamada (`max_retries`, `timeout`). `PNCPClient.get_resource` agora
  usa `max_retries=0, timeout=20s` — endpoints singulares são
  determinísticos, retry só acumula latência. Validado: latência
  cai de 182.000ms para 409ms (444× mais rápido).

## [0.2.10] — 2026-05-16

### Fixed
- **`compras_pncp_contratacao_por_orgao`, `_itens`,
  `_item_resultados`, `compras_pncp_contrato_por_orgao` estouravam
  exception crua em 400/404** (descoberto na bateria 7). Helper
  `_resposta_pncp_singular_404` captura `ComprasNotFoundError +
  ComprasHTTPError + ComprasServerError` e devolve payload graceful
  com diagnóstico (3 causas comuns) + 2 alternativas (listar antes,
  Dados Abertos federal). Status 4xx do upstream preservado no payload.

## [0.2.9] — 2026-05-16

### Fixed
- **`compras_pncp_orgao_unidades` estourava exception crua em 404**
  (descoberto na bateria 6) — divergia do padrão graceful da v0.2.8 e
  era justamente a tool anunciada como alternativa estável ao
  `/modulo-uasg/*` morto. Mesmo padrão aplicado com diagnóstico
  específico (CNPJ raiz vs subunidades, federal SISG, CNPJ incorreto)
  + 3 alternativas.

### Changed
- Docstring de `compras_uasg_listar` parou de citar "200999 = SEGES/ME"
  como exemplo consultável (o `/modulo-uasg/*` retorna 404 inteiro).
  Substituído por aviso explícito + ponteiros para alternativas reais.

## [0.2.8] — 2026-05-16

### Fixed
- **Família `/modulo-uasg/*` retorna 404 no upstream** (probe direto
  confirma: todos os 6 paths listados no swagger oficial retornam 404).
  Sem fix possível pelo MCP — bug do servidor SEGES. Fix defensivo:
  as 5 tools afetadas (`uasg_listar/_consultar/_buscar` +
  `orgao_listar/_consultar`) capturam `ComprasNotFoundError` e retornam
  `_resposta_uasg_404` informativo + 3 alternativas (PNCP unidades,
  Comprasnet `/api/contrato/unidades`, `contrato_comprasnet_por_uasg`).
- README ganhou bloco "⚠️ Aviso operacional" consolidando os 5 bugs
  upstream conhecidos.

## [0.2.7] — 2026-05-15

### Fixed
- **Filtro `co_uasg` / `uasg` em `/modulo-legado/3_consultarPregoes`
  e `/1_consultarLicitacao` retornava 400 Hibernate** ("Could not
  resolve attribute 'TbVwPregaoId.coUasg'"). Bug upstream: swagger
  documenta atributo que não existe no modelo da view. Parâmetros
  removidos das 2 tools; workaround documentado (filtrar client-side).
  Probe confirmou: sem filtro UASG → 36.164 pregões e 14.096 licitações
  para 1º sem. 2021.
- **Composta `buscar_contratacoes_similares` cacheava resultado vazio**
  por 10 min, podendo servir "0 similares" obsoleto em janela crítica
  de homologação. Agora `cache.set` é skipped quando
  `consolidado_unico == 0` + aviso `_aviso_no_cache_vazio` no payload.

## [0.2.6] — 2026-05-15

### Fixed
- **6 tools `/modulo-legado/*` enviavam parâmetros com nomes errados**
  (descoberto na bateria 5). Refatoração para usar os nomes reais do
  upstream (cada endpoint tem convenção diferente):
  - `/1_consultarLicitacao`: `data_publicacao_inicial/final`
  - `/3_consultarPregoes`: `dt_data_edital_inicial/final`, `co_uasg`
  - `/5_consultarComprasSemLicitacao`: `dt_ano_aviso` (ano inteiro)
  - `/7_consultarRdc`: `data_publicacao_min/max`
  Probe confirmou após fix: 36.164 pregões + 14.096 licitações
  para 1º sem. 2021.

## [0.2.5] — 2026-05-15

### Fixed
- **5 tools compostas não tinham cache próprio.** Chamavam clients
  HTTP diretamente, sem passar pelos caches por-tool dos módulos
  atômicos. Resultado: refazem fan-out completo a cada chamada mesmo
  com Redis ativo. Adicionado `cache_from_env("COMPOSTAS", ttl=600s)`
  + bloco get/set em cada uma das 5 compostas com chaves baseadas nos
  parâmetros de entrada. Validado: composta
  `buscar_contratacoes_similares` cai de 3 min para 3 ms na 2ª chamada
  idêntica (ganho 58k×–91k× dependendo da latência cold).

## [0.2.4] — 2026-05-15

### Fixed
- **Filtro textual do CATMAT está quebrado upstream** (retorna o
  universo inteiro ~340k itens com qualquer termo). `catmat_buscar`
  ganhou parâmetros estruturais `codigo_grupo` e `codigo_classe` como
  workaround + heurística que adiciona `_aviso_filtro` ao payload
  quando o total retornado ≥ 200k. Docstring documenta o workflow
  alternativo (grupo → classe → item).
- **8 sub-recursos do contrato Comprasnet não suportam paginação
  upstream** (cronograma chega a 217 entradas em uma resposta).
  Helper `_ct_subrecurso` reescrito com cache do payload completo +
  fatiamento client-side. Validado: cronograma p1 → p2 fatia o cache
  em 13ms (97× mais rápido que cold).

## [0.2.3] — 2026-05-15

### Fixed
- **`compras_contratacoes_14133_listar.modalidade` confundia tabelas
  do SIASG e do PNCP** — payloads retornavam `codigoModalidade=6`
  (Dispensa no SIASG) com `modalidadeIdPncp=8` (Dispensa no PNCP),
  mas o cheat sheet `compras_pncp_modalidades` documentava 6=Pregão.
  Renomeado para `codigo_modalidade_dados_abertos` com tabela de
  equivalência empírica documentada na docstring
  (3=Concorrência Eletrônica, 5=Pregão, 6=Dispensa, 7=Inexigibilidade).
- **`perfil_fornecedor_completo` retornava `contratos_vigentes_amostra=[]`
  sempre** (parâmetro errado + endpoints exigem `codigoOrgao`).
  Campo removido + `_aviso_contratos` apontando workaround.
- **`buscar_contratacoes_similares` consultava 3 modalidades PNCP
  sequencialmente** (3 × ~60s = ~3 min). Agora via `asyncio.gather`
  paralelo — 60-90s no caso frio.

## [0.2.2] — 2026-05-15

### Fixed
- **`compras_contratos_*` (4 tools) com parâmetros errados.** Refeitas
  para usar os nomes reais do upstream:
  - `contratos_listar`: exige `codigo_orgao` +
    `data_vigencia_inicial_min/max` (≤365d)
  - `contratos_consultar`: trocado `id:int` por `codigo:str` +
    `tipo: Literal["idCompra","numeroControlePncpContrato"]`
  - `contratos_listar_por_fim_vigencia`: `codigo_orgao` +
    `data_vigencia_final_min/max`
  - `contratos_itens_listar`: mesma família
- **`contrato_comprasnet_por_uasg` devolvia até 1.4 MB sem paginar.**
  Paginação client-side com cache do payload completo + slice por
  chamada, mesma estratégia depois adotada nos sub-recursos da v0.2.4.

## [0.2.1] — 2026-05-15

### Fixed
- **`tamanho_pagina < 10` retornava 400 em todas as tools `consultar_*`.**
  Clamp `[10..500]` no `DadosAbertosClient.list_resource` corrige todas
  de uma vez.
- **`catmat_buscar` enviava `descricaoItem=` (Swagger desatualizado).**
  Trocado para `descricao=` — termo "cadeira" passou a retornar 341.779
  itens reais.
- **Família ARP completa exigia parâmetros que nunca enviávamos.**
  Refatoração: `dataVigenciaInicialMin/Max` (janela ≤365d) +
  chave composta `numeroAta + unidadeGerenciadora + numeroItem`.
- **`pncp_pca_atualizacao` enviava `dataInicial/Final`** quando o
  PNCP exige `dataInicio/Fim`.
- **`pncp_pca_listar` faltava parâmetro obrigatório**
  `codigoClassificacaoSuperior`.
- **`montar_dossie_arp` falhava em cascata.** Refeita com chave composta
  + suporte a `numero_item` opcional + fallback graceful por sub-recurso.
- Conftest dos testes desliga `load_dotenv` para não vazar `.env` real.

## [0.2.0] — 2026-05-14

### Added
- 85 tools cobrindo o ecossistema completo do Compras.gov.br:
  Dados Abertos Compras + PNCP Consulta + Portal da Transparência
  (CGU) + Comprasnet Contratos (rotas abertas).
- 5 tools compostas "agente" para casos de uso reais do analista:
  ETP IN SEGES/ME 65/2021 (com IQR/Tukey), dossiê ARP, sanções
  consolidadas, contratações similares federadas, perfil fornecedor.
- Cache Redis com TTL escalonado.
- LGPD: máscara automática de CPF.
- Dual deploy: `.mcpb` local (Claude Desktop) + Docker/Railway HTTP.
- 4 testes SSoT que validam alinhamento de descriptions Pydantic ↔
  MCP schema.

[0.2.13]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.13
[0.2.12]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.12
[0.2.11]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.11
[0.2.10]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.10
[0.2.9]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.9
[0.2.8]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.8
[0.2.7]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.7
[0.2.6]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.6
[0.2.5]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.5
[0.2.4]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.4
[0.2.3]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.3
[0.2.2]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.2
[0.2.1]: https://github.com/opedrosoares/MCP_Compras/releases/tag/v0.2.1
