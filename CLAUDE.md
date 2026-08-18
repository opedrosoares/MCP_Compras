# CLAUDE.md — Notas para sessões futuras

Servidor MCP que reúne as APIs públicas do ecossistema Compras.gov.br
(Dados Abertos, PNCP Consulta, Portal da Transparência/CGU e rotas abertas
do Comprasnet Contratos) em ~70 tools voltadas a analistas de licitação.

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

## Build & deploy

- `.mcpb`: `python build_mcpb.py` → `dist/compras.mcpb`. Atualizar a lista
  `tools` em `manifest.json` quando registrar novas tools.
- Railway: `Dockerfile` + `railway.toml` + `Procfile` configurados. Setar
  `TRANSPARENCIA_API_KEY` e (opcional) plugar Redis.
