# CLAUDE.md — Notas para sessões futuras

Servidor MCP que reúne as APIs públicas do ecossistema Compras.gov.br
(Dados Abertos, PNCP Consulta, Portal da Transparência/CGU e rotas abertas
do Comprasnet Contratos) em 100 tools voltadas a analistas de licitação.

## Framework

**FastMCP 2.x** (não o SDK oficial `mcp[cli]`). Consistente com o mcp-inpi.
O SEI Pro usa `mcp[cli]` — os dois padrões coexistem no portfólio.

## Padrão SSoT para descriptions

Toda description de parâmetro vive em `src/compras_mcp/schemas.py` (Pydantic
`Field(description=...)`). As tools nunca duplicam — leem via
`from compras_mcp.tools._helpers import desc; desc(Modelo, "campo")`.
Drift é detectado pelo teste em `tests/test_server.py::test_ssot_*`.

## Convenções de naming

- Tools: `compras_<dominio>_<verbo>_<recurso>` (ex.: `compras_arp_saldo_item`).
- Módulos de tool: por domínio em `src/compras_mcp/tools/<dominio>.py`. Não
  expor a tool diretamente — registrar com `@mcp.tool` lendo a instância de
  `compras_mcp.mcp_instance`.
- Para registrar um novo módulo: adicionar `from compras_mcp.tools import <modulo>`
  na lista de imports em `server.py` (a ordem define a ordem de listagem).

## Envelope padrão das respostas

Tools `listar_*` devolvem:
```
{
  "resultado": [...],
  "_pagina_atual": int,
  "_total_paginas": int,
  "_total_registros": int,
  "_proxima_pagina": int | None,
  "_cache_hit": bool,
  "_latency_ms": float,
}
```
Tools `consultar_*` (singular) devolvem `{"encontrado": bool, "codigo_consultado": ..., "<recurso>": dict | None, "_cache_hit": ...}`.
Sempre passar pelo `with_latency(payload, started)` antes do return.

## Clientes HTTP

- `BaseAsyncClient` em [src/compras_mcp/clients/base.py](src/compras_mcp/clients/base.py): retry exponencial
  para timeout/5xx/429, logging structlog, `format_date(value, flavor)` para
  os 3 formatos de data: `dados_abertos` (YYYY-MM-DD), `pncp` (yyyyMMdd) e
  `comprasnet` (YYYY-MM-DD HH:mm:ss).
- Subclasses específicas por API. Factories em `tools/_helpers.py`:
  `make_dados_abertos(s)`, `make_pncp(s)`, `make_transparencia(s)`, `make_comprasnet(s)`.

## Contrato de query (o defeito que mais se repete aqui)

`dadosabertos.compras.gov.br` responde **HTTP 200 a qualquer parâmetro de
query desconhecido** e devolve o resultado como se nenhum filtro tivesse sido
pedido. Nome errado de parâmetro é, portanto, indistinguível de filtro
funcionando — foi assim que 7 filtros passaram meses sem filtrar nada
(v0.3.17). Ao sondar um filtro novo, mande junto um parâmetro inventado
(`zzzControle=1`) como controle: se o total não mudar em relação a ele, o
filtro está sendo ignorado.

Toda chave e todo valor de enum enviados são travados contra o OpenAPI oficial
em `tests/test_contrato_upstream.py` (snapshot em
`tests/fixtures/dadosabertos_openapi_params.json`). Para regenerar o snapshot
depois de uma mudança upstream, veja `_como_regerar` no próprio fixture.

## Cache

- `cache_from_env(prefix, default_ttl, default_max_size)` em [src/compras_mcp/cache.py](src/compras_mcp/cache.py).
- Se `REDIS_URL` estiver setada → `RedisCache` (decode_responses, JSON, fallback
  silencioso se Redis cair). Senão → `ResultCache` (TTL+LRU em memória).
- Cada módulo cria seu próprio cache no topo: `_cache = cache_from_env("CATALOGO", default_ttl=86400)`.
- TTLs sugeridos: catálogo 24h, órgãos 24h, preços 10 min, atas/contratos 15 min,
  sanções 1h.

## LGPD

`apply_lgpd(payload, incluir_cpf_completo=settings.incluir_cpf_completo)`
em [src/compras_mcp/access_control.py](src/compras_mcp/access_control.py).
Aplicar sempre que a resposta possa conter CPF de servidor (fiscal, gestor,
preposto, responsável). Por padrão mascara como `123.***.***-45`.
Anexar `_aviso_lgpd` ao payload.

## Transporte

`server.py::main()` detecta `PORT` em env — presente → HTTP em `0.0.0.0:$PORT`,
ausente → stdio. Mesma lógica do mcp-inpi.

## Onde estão os endpoints upstream

- Dados Abertos: <https://dadosabertos.compras.gov.br/swagger-ui/index.html>
  + OpenAPI: <https://dadosabertos.compras.gov.br/v3/api-docs>
- PNCP Consulta: <https://pncp.gov.br/api/consulta/swagger-ui/index.html>
- Portal Transparência: <https://api.portaldatransparencia.gov.br/swagger-ui/index.html>
- Comprasnet Contratos: <https://gitlab.com/comprasnet/contratos> (routes/api.php)
- ReadTheDocs Contratos: <https://comprasnet-contratos.readthedocs.io/pt-br/latest/>

## Autoria dos commits

Author **e** committer são sempre `Pedro Soares <pedrohsoares.adv@gmail.com>`.
Nunca `Lab2Code <lab2code@lab2code.com>`: o projeto saiu da Lab2Code e passou para
a autoria particular de opedrosoares.

**Não anexar `Co-Authored-By: Claude`** — nem as variantes (`Claude Opus 5`,
`Claude Sonnet 5`, `Claude Code`, `🤖 Generated with…`). `Co-Authored-By:` fica
reservado a humanos que de fato contribuíram, como @LeonardoDiasRR na PR #1. Isso
contraria o default de alguns agentes, que anexam o trailer sozinhos — a regra do
projeto prevalece.

O motivo é o grafo de Contributors do GitHub: cada trailer cria uma entrada
`claude` na lista, e a autoria Lab2Code criava outra. A história do `main` foi
reescrita em 07/09/2026 para remover as duas (`e83eb21` → `d9d500b`, com as 10
árvores idênticas e o commit do @LeonardoDiasRR intocado).

Como a regra escrita não segurou na prática, existe um hook determinístico. Ele
mora em `.git/hooks/`, que **não é versionado** — em clone novo, reinstalar:

```bash
cp scripts/commit-msg .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
```

Ele remove os trailers de IA da mensagem e recusa o commit se a identidade for
Lab2Code.

**Nunca rodar `git push --tags` nem `git push --all` neste repositório.** Os
branches locais `backup/local-main-lab2code` e `pub-docs` e as tags `v0.2.8`…
`v0.2.13` ainda apontam para a história antiga com Lab2Code e não existem no
remoto; um push amplo ressuscitaria a atribuição no GitHub. Empurrar sempre refs
nomeadas: `git push origin main`, `git push origin vX.Y.Z`.

## Build & deploy

- `.mcpb`: `python build_mcpb.py` → `dist/compras.mcpb`. Atualizar a lista
  `tools` em `manifest.json` quando registrar novas tools.
  O bundle **não** carrega o servidor desde a v0.4.0: leva só a ponte
  `mcpb/bridge.js` (stdio → Streamable HTTP, zero dependências), que fala com o
  deploy hospedado. O runtime é **Node** porque é o único que o Claude Desktop
  garante — `command: "python"` quebrava no macOS, que não tem esse binário no
  PATH. Mexeu no bridge? `pytest tests/test_mcpb_bridge.py` sobe ele como o
  Desktop sobe, contra um servidor MCP falso.
- Railway: `Dockerfile` + `railway.toml` + `Procfile` configurados. Setar
  `TRANSPARENCIA_API_KEY` e (opcional) plugar Redis.

### Release: só empurrar a tag

`git tag -a vX.Y.Z && git push origin vX.Y.Z` dispara
[.github/workflows/release.yml](.github/workflows/release.yml), que publica os
três destinos **sem nenhum segredo no repo** — PyPI por Trusted Publishing e
registry por `mcp-publisher login github-oidc`, ambos trocando o token OIDC
efêmero do Actions:

1. GitHub Release com o `.mcpb`
2. PyPI (`compras-mcp`)
3. Registry oficial (`io.github.opedrosoares/mcp-compras`)

A versão vive em **cinco** lugares (`pyproject.toml`,
`src/compras_mcp/__init__.py`, `manifest.json`, e duas vezes em `server.json` —
raiz e `packages[].version`). O job `build` roda
`scripts/check_release_versions.py` e aborta antes de publicar qualquer coisa se
algum divergir da tag. Isso importa porque o registry recusa o publish quando a
versão de `packages[]` não existe no PyPI, e o PyPI nunca aceita reenvio de uma
versão já publicada — descobrir a divergência no meio do pipeline deixaria a
release pela metade.

O `server.json` precisa acompanhar toda subida de versão. O badge do M8ven e o
comentário `<!-- mcp-name: ... -->` no README são provas de propriedade
(listagem e registry, respectivamente) — não remover nenhum dos dois.
