# Arquitetura e padrões internos

Para quem vai ler o código, contribuir ou auditar o comportamento do servidor.

## Framework e transporte

- **FastMCP 2.x**. O ponto de entrada é [`src/compras_mcp/server.py`](../src/compras_mcp/server.py).
- **Transporte detectado por ambiente**: com `PORT` setada (caso do Railway) sobe em HTTP
  (Streamable HTTP) em `0.0.0.0:$PORT`; sem ela, sobe em stdio.
- Tools ficam em `src/compras_mcp/tools/<dominio>.py`, uma por domínio funcional, registradas com
  `@mcp.tool`. Naming: `compras_<dominio>_<verbo>_<recurso>` (ex.: `compras_arp_saldo_item`).

## Envelope padrão das respostas

Tools `listar_*` devolvem sempre a mesma forma:

```json
{
  "resultado": [],
  "_pagina_atual": 1,
  "_total_paginas": 12,
  "_total_registros": 587,
  "_proxima_pagina": 2,
  "_cache_hit": false,
  "_latency_ms": 431.2
}
```

Tools `consultar_*` (singular) devolvem
`{"encontrado": bool, "codigo_consultado": ..., "<recurso>": dict | None, "_cache_hit": ...}`.

## SSoT de descriptions

As descrições de parâmetro vivem em um único lugar,
[`src/compras_mcp/schemas.py`](../src/compras_mcp/schemas.py) (Pydantic `Field(description=...)`),
e as tools leem de lá via `_helpers.desc(Model, "campo")` em vez de duplicar o texto. O teste em
[`tests/test_server.py`](../tests/test_server.py) detecta drift entre schema e tool.

## Cache

- TTL+LRU em memória por padrão; **Redis** quando `REDIS_URL` está setada (com fallback silencioso
  se o Redis cair).
- Cada domínio tem seu prefixo (CATALOGO, PRECOS, ATAS, ORGAOS, SANCOES, COMPOSTAS etc.),
  ajustável por `CACHE_<PREFIX>_TTL` e `CACHE_<PREFIX>_MAX_SIZE`.
- TTLs típicos: catálogo 24h, órgãos 24h, preços 10 min, atas/contratos 15 min, sanções 1h.

## LGPD

CPFs de servidores (fiscal, gestor, preposto, responsável) são mascarados como `123.***.***-45`
antes de sair do servidor. As tools afetadas incluem `_aviso_lgpd` no payload. Para retornar o CPF
completo — sob sua responsabilidade — use `INCLUIR_CPF_COMPLETO=true`.
Implementação em [`src/compras_mcp/access_control.py`](../src/compras_mcp/access_control.py).

## Clientes HTTP

`BaseAsyncClient` ([`src/compras_mcp/clients/base.py`](../src/compras_mcp/clients/base.py)) com
retry exponencial para timeout/5xx/429 e logging estruturado (structlog). Subclasses por API.

**Datas**: as três APIs usam três formatos diferentes — `YYYY-MM-DD` (Dados Abertos), `yyyyMMdd`
(PNCP) e `YYYY-MM-DD HH:mm:ss` (Comprasnet). A conversão é transparente, via
`format_date(value, flavor)`.

## Trava de contrato com o upstream

Como `dadosabertos.compras.gov.br` responde HTTP 200 a parâmetros de query inexistentes (ver
[Qualidade das APIs públicas](qualidade-das-apis.md)), um nome de parâmetro errado é
indistinguível de um filtro que funciona. Para que isso não volte a acontecer, todas as chaves e
valores de enum enviados são validados contra o OpenAPI oficial em
`tests/test_contrato_upstream.py`, com snapshot versionado em
`tests/fixtures/dadosabertos_openapi_params.json`.

## Rodar os testes

```bash
uv sync
uv run pytest
```

A suíte inclui o teste da ponte `.mcpb` ([`tests/test_mcpb_bridge.py`](../tests/test_mcpb_bridge.py)),
que sobe `bridge.js` do mesmo jeito que o Claude Desktop sobe, contra um servidor MCP falso.
