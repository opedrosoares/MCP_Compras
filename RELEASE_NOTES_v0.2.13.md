# v0.2.13 — Estabilização após 9 baterias E2E

13 releases de hotfix descobertos em 9 baterias E2E sequenciais contra o servidor em produção (Railway + Redis). Cada bug encontrado foi reproduzido por probe direto ao upstream, diagnosticado, corrigido e validado. Esta release fecha o ciclo de estabilização.

## TL;DR

- **85 tools** cobrindo Dados Abertos Compras.gov.br + PNCP Consulta + Portal Transparência/CGU + Comprasnet Contratos (rotas abertas).
- **5 tools compostas "agente"** para casos reais do analista de licitação: ETP IN SEGES/ME 65/2021 (com IQR/Tukey), dossiê ARP, sanções consolidadas, contratações similares federadas, perfil de fornecedor.
- **15 tools com graceful degradation explícita** para bugs upstream conhecidos — `_erro_upstream` estruturado em vez de exception crua.
- **Cache Redis em 2 camadas** (atômicas + compostas) com TTL escalonado.
- **Dual deploy**: `.mcpb` local (Claude Desktop) + Docker/Railway HTTP.

## Onde usar

- **HTTP remoto**: <https://mcp-compras.up.railway.app/mcp>
- **`.mcpb` local**: `dist/compras.mcpb` (72 KB) — duplo-clique no Claude Desktop.
- **Source**: <https://github.com/opedrosoares/MCP_Compras>

## Métricas de qualidade

| Métrica | Valor |
|---------|-------|
| Tools registradas | 85 |
| Tools compostas | 5 |
| Tools com graceful degradation | 15 |
| Famílias funcionais cobertas | 12 (Catálogo, Preço, Planejamento, ARP, Contratações 14.133, Legado 8.666, Contratos, Fornecedores, Sanções, PNCP, Organizações, Indicadores) |
| Releases nesta linha | 13 (v0.2.1 → v0.2.13) |
| Baterias E2E executadas | 9 (com agente externo crítico) |
| Bugs descobertos × fechados | 19 × 19 |
| Bugs upstream documentados (workaround) | 5 |
| Testes pytest | 4/4 (SSoT contract, healthcheck, descriptions, naming) |

## Validação empírica do caminho de sucesso da camada Transparência

Resolvido nesta release o problema que travou a bateria 9: o `User-Agent` default do `httpx` era consistentemente bloqueado pelo AWS WAF da CGU. `TransparenciaClient` agora envia UA browser-like identificando o MCP.

```
ANTES (UA default python-httpx/0.27.x): 405 + HTML em 5/5 chamadas
DEPOIS (UA Mozilla + compras-mcp/0.2.13): 200 + JSON em 2/2 testes
```

Smoke pós-deploy:

- `compras_sancao_cnep(cnpj="11111111000111")` → 15 registros, 889 ms ✅
- `compras_sancao_ceis(pagina=1)` → 15 registros, 689 ms ✅

## Cobertura de graceful degradation (15 tools)

| Família | Tools cobertas | Cenário |
|---------|----------------|---------|
| `/modulo-uasg/*` (Dados Abertos) | 5 | Endpoint 404 inteiro upstream — v0.2.8 |
| PNCP `/orgaos/{cnpj}/unidades` | 1 | CNPJ não indexado — v0.2.9 |
| PNCP `/compras/.../{ano}/{seq}` e variantes | 4 | Combinação inválida — v0.2.10 |
| Sanções Transparência (5 cadastros) | 5 | WAF block 405 — v0.2.12 |

Quando o upstream responde com erro, as 15 tools retornam:

```json
{
  "_erro_upstream": {
    "endpoint": "...",
    "status": 404,
    "tipo": "waf_block" | "endpoint_morto" | "registro_inexistente",
    "diagnostico": "...",
    "alternativas": ["...", "..."]
  }
}
```

## Bugs upstream documentados (sem fix MCP possível)

Confirmados via probe direto, com workaround/aviso embutido nas tools:

1. `/modulo-uasg/*` (6 paths) → 404 apesar de listados no swagger
2. `/modulo-legado/3_consultarPregoes` + `co_uasg` → 400 Hibernate `TbVwPregaoId.coUasg` não existe no modelo
3. `/modulo-legado/1_consultarLicitacao` + `uasg` → idem
4. `/modulo-material/4_consultarItemMaterial?descricao=*` → filtro textual ignorado, retorna universo CATMAT (~340k itens)
5. Portal Transparência com AWS WAF "Human Verification" intermitente (mitigado pelo UA browser-like na v0.2.13)

## O que vem nesta release específica (v0.2.13)

### Fixed

- **UA default do `httpx` disparava AWS WAF da CGU em 100% das chamadas à Transparência.** Probe direto: `python-httpx/0.27.x` → 405 + HTML em 5/5 chamadas; `Mozilla/5.0 ... compras-mcp/0.2.13` → 200 + JSON em 2/2. `TransparenciaClient` agora envia UA browser-like identificando o MCP. Caminho feliz das 5 tools de sanção empiricamente validado em produção.

## Changelog completo

Veja [`CHANGELOG.md`](https://github.com/opedrosoares/MCP_Compras/blob/main/CHANGELOG.md) para o histórico detalhado das 13 releases.

## Agradecimento

Esta release não seria o que é sem o agente da bateria 9 que recusou afirmações amplas demais e forçou diagnóstico raiz em vez de aceitar sintoma. Várias das críticas foram desconfortáveis e procedentes — todas viraram fix.
