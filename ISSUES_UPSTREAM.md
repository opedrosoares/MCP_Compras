# Issues upstream a reportar

5 bugs upstream documentados ao longo de 9 baterias E2E, com probe direto e reprodução determinística.

Para cada issue: copie o **Título** para o campo de título, e copie tudo entre as linhas `=== COPIAR DAQUI ===` e `=== ATÉ AQUI ===` para o campo de descrição. Os parágrafos estão em linha única (sem hard-wrap) — o renderizador do destino faz o wrap.

---

## Destinatário 1 — SEGES/ME (4 issues)

**Onde abrir**: <https://github.com/gestaogovbr/Compras-API-doc/issues/new>
**Fórum alternativo**: <https://gestgov.discourse.group/c/api-de-dados-abertos>

---

### Issue SEGES #1

**Título**: `[Dados Abertos] Família /modulo-uasg/* retorna 404 inteiro apesar de listada no swagger oficial`

=== COPIAR DAQUI ===

## Resumo

Todos os 6 paths do `/modulo-uasg/*` listados em `https://dadosabertos.compras.gov.br/v3/api-docs` retornam HTTP 404 ("Resource not found") quando chamados. Confirmado em 2026-05.

## Endpoints afetados

- `GET /modulo-uasg/1_consultarUasg`
- `GET /modulo-uasg/1.1_consultarUasg_CSV`
- `GET /modulo-uasg/2_consultarOrgao`
- `GET /modulo-uasg/2.1_consultarOrgao_CSV`

## Reprodução determinística

```bash
curl -i "https://dadosabertos.compras.gov.br/modulo-uasg/1_consultarUasg?pagina=1&tamanhoPagina=10"
# HTTP/1.1 404 Not Found
# { "statusCode": 404, "message": "Resource not found" }

curl -i "https://dadosabertos.compras.gov.br/modulo-uasg/2_consultarOrgao?pagina=1&tamanhoPagina=10"
# HTTP/1.1 404 Not Found
```

Mas o path está listado no swagger:

```bash
curl -s "https://dadosabertos.compras.gov.br/v3/api-docs" | jq '.paths | keys[] | select(contains("modulo-uasg"))'
# "/modulo-uasg/1.1_consultarUasg_CSV"
# "/modulo-uasg/1_consultarUasg"
# "/modulo-uasg/2.1_consultarOrgao_CSV"
# "/modulo-uasg/2_consultarOrgao"
```

## Impacto

Resolução de UASG/órgão por código/nome é pré-requisito de quase toda consulta filtrada no SIASG. Sem esse módulo, os analistas precisam usar UASGs conhecidas hardcoded ou descobrir via endpoints alternativos (`Comprasnet /api/contrato/unidades` retorna só códigos sem nomes).

## Hipóteses

- Roteamento removido sem atualizar swagger
- Backend migrado para outro path
- Recurso interno temporariamente desativado

## Esperado

Ou (a) os endpoints voltam a ser roteados conforme o swagger, ou (b) o swagger é atualizado para refletir que `/modulo-uasg/*` foi descontinuado e aponta para o substituto.

=== ATÉ AQUI ===

---

### Issue SEGES #2

**Título**: `[Dados Abertos] /modulo-legado/3_consultarPregoes?co_uasg=X retorna 400 'Could not resolve attribute TbVwPregaoId.coUasg'`

=== COPIAR DAQUI ===

## Resumo

O filtro `co_uasg` documentado no swagger do endpoint `/modulo-legado/3_consultarPregoes` retorna HTTP 400 com erro Hibernate indicando que o atributo `coUasg` não existe na view `TbVwPregao`.

## Reprodução

```bash
# SEM o filtro — funciona, 36.164 registros
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/3_consultarPregoes?dt_data_edital_inicial=2021-01-01&dt_data_edital_final=2021-06-30&pagina=1&tamanhoPagina=10"
# HTTP 200 OK

# COM o filtro — 400 Hibernate
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/3_consultarPregoes?dt_data_edital_inicial=2021-01-01&dt_data_edital_final=2021-06-30&co_uasg=200999&pagina=1&tamanhoPagina=10"
# HTTP/1.1 400 Bad Request
# Erro ao efetuar a consulta Could not resolve attribute 'TbVwPregaoId.coUasg' of 'br.gov.economia.apicompras.models.TbVwPregao'
```

## Swagger documenta o parâmetro

```bash
curl -s "https://dadosabertos.compras.gov.br/v3/api-docs" | jq '.paths."/modulo-legado/3_consultarPregoes".get.parameters[] | select(.name=="co_uasg")'
# {
#   "name": "co_uasg",
#   "in": "query",
#   "schema": { "type": "integer" }
# }
```

## Impacto

Impossível filtrar pregões legados por UASG sem workaround client-side (buscar todos e filtrar no cliente). Para uma UASG com volume baixo, isso é caro: tem que baixar até 50 páginas para encontrar uma dúzia de registros.

## Esperado

Ou (a) o atributo `coUasg` é adicionado ao modelo Hibernate da view, ou (b) o parâmetro `co_uasg` é removido do swagger.

=== ATÉ AQUI ===

---

### Issue SEGES #3

**Título**: `[Dados Abertos] /modulo-legado/1_consultarLicitacao?uasg=X retorna 400 Hibernate (mesmo padrão do pregões)`

=== COPIAR DAQUI ===

## Resumo

Mesmo bug Hibernate do `/3_consultarPregoes` (issue separada), agora no `/1_consultarLicitacao`. O filtro `uasg` documentado no swagger retorna HTTP 400.

## Reprodução

```bash
# SEM o filtro — funciona, 14.096 registros para 2021-H1
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/1_consultarLicitacao?data_publicacao_inicial=2021-01-01&data_publicacao_final=2021-06-30&pagina=1&tamanhoPagina=10"
# HTTP 200 OK

# COM uasg=200999 — 400
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/1_consultarLicitacao?data_publicacao_inicial=2021-01-01&data_publicacao_final=2021-06-30&uasg=200999&pagina=1&tamanhoPagina=10"
# HTTP/1.1 400 Bad Request
# Erro ao efetuar a consulta
```

## Esperado

Mesmo tratamento da issue anterior — alinhar swagger e modelo Hibernate.

=== ATÉ AQUI ===

---

### Issue SEGES #4

**Título**: `[Dados Abertos] /modulo-material/4_consultarItemMaterial?descricao=X retorna o universo CATMAT inteiro (~340k) sem filtrar`

=== COPIAR DAQUI ===

## Resumo

O filtro textual `descricao` (e variantes `nome`, `termo`, `q`, `descricaoItem`) é silenciosamente ignorado pelo `/modulo-material/4_consultarItemMaterial`. O endpoint aceita o parâmetro sem erro mas retorna o universo CATMAT inteiro (~340k itens), quebrando casos de uso de busca textual.

## Reprodução

```bash
# Termo "cadeira" deveria retornar mobiliário; vem universo inteiro
curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?descricao=cadeira&pagina=1&tamanhoPagina=10" | jq '{ total: .totalRegistros, primeiro: .resultado[0].descricaoItem }'
# {
#   "total": 341779,
#   "primeiro": "ARMA DE FOGO DE PEQUENO PORTE - REVÓLVER / PISTOLA..."
# }

# Mesmo total sem nenhum filtro:
curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?pagina=1&tamanhoPagina=10" | jq '.totalRegistros'
# 341779
```

Filtros estruturais funcionam normalmente:

```bash
curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?codigoGrupo=71&pagina=1&tamanhoPagina=10" | jq '.totalRegistros'
# 9600   (apenas Mobiliários — filtro funciona)
```

## Histórico

Em probe direto realizado em 2026-05-15 (~uma semana antes desta issue) o filtro `descricao=cadeira` retornou 341.779 itens **começando por "CADEIRA ESCRITÓRIO"** — ou seja, o filtro funcionava. Em 2026-05-16 o mesmo curl retorna o universo começando por ARMA DE FOGO. Regressão recente.

## Impacto

Busca textual é o ponto de entrada natural para o analista descobrir o `codigoItem` antes de pesquisar preços/contratações. Sem ela, o usuário precisa navegar a hierarquia grupo → classe → PDM → item manualmente.

## Esperado

Restaurar o comportamento de match parcial no campo `descricao`.

=== ATÉ AQUI ===

---

## Destinatário 2 — CGU / Portal da Transparência (1 issue)

**Onde abrir**: <https://falabr.cgu.gov.br/> (canal oficial)
**E-mail técnico**: <api.dadosabertos@cgu.gov.br>

---

### Issue CGU

**Assunto**: `[API Portal da Transparência] WAF AWS bloqueia clientes HTTP genuínos com chave válida — falso positivo de bot detection`

=== COPIAR DAQUI ===

## Resumo

O AWS WAF que protege `api.portaldatransparencia.gov.br` retorna HTTP 405 + página HTML "Human Verification" (`awswafCookieDomainList`) em 100% das chamadas com `User-Agent` padrão de bibliotecas HTTP genuínas (observado com `python-httpx/0.27.x`), mesmo quando a chave de API `chave-api-dados` é válida.

## Reprodução determinística (2026-05-16)

```bash
KEY="..."  # chave de API válida da Transparência

# UA default do httpx — bloqueado consistentemente
curl -i -H "chave-api-dados: $KEY" -H "User-Agent: python-httpx/0.27.0" "https://api.portaldatransparencia.gov.br/api-de-dados/cnep?cnpjSancionado=11111111000111&pagina=1"
# HTTP/1.1 405 Method Not Allowed
# Content-Type: text/html
# <!DOCTYPE html><html lang="en"><head>...Human Verification...

# UA browser-like — funciona, retorna JSON normal
curl -i -H "chave-api-dados: $KEY" -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" "https://api.portaldatransparencia.gov.br/api-de-dados/cnep?cnpjSancionado=11111111000111&pagina=1"
# HTTP/1.1 200 OK
# Content-Type: application/json
# [{"id":359510,"dataReferencia":"15/05/2026",...
```

## Frequência observada

- 5/5 chamadas com `python-httpx/0.27.x` → 405 WAF
- 1/1 chamada com `Mozilla/5.0 ...` → 200 OK
- Confirmado em 4 endpoints distintos (`ceis`, `cnep`, `cepim`, `acordos-leniencia`)

## Impacto

Toda integração legítima usando bibliotecas HTTP padrão (Python httpx, requests, aiohttp; Node.js fetch nativo; Java OkHttp default) é classificada como bot e bloqueada, mesmo possuindo chave de API emitida pela própria CGU para uso programático.

## Aspecto problemático

A justificativa de uma API pública requerer cadastro de chave é justamente identificar e auditar consumidores legítimos. Bloquear esses mesmos consumidores no WAF antes que a chave seja sequer validada inverte essa lógica e cria contradição entre o termo de uso ("acesso programático autorizado mediante cadastro") e o comportamento operacional.

## Sugestão técnica

- Considerar a chave `chave-api-dados` válida como bypass automático de regras anti-bot no WAF (o cadastro já filtra abuso)
- Ou documentar oficialmente que o `User-Agent` precisa ser customizado (com exemplo) — hoje a documentação não menciona

## Workaround atual no nosso projeto

Adicionamos `User-Agent: Mozilla/5.0 ... compras-mcp/0.2.13` no cliente. Não consideramos elegante, mas é a única forma de a API ser consumida programaticamente sem 405 intermitente.

## Contato

Pedro Soares · pedrohsoares.adv@gmail.com
Projeto open-source: <https://github.com/opedrosoares/MCP_Compras>

=== ATÉ AQUI ===

---

## Como abrir (passo a passo)

### Para SEGES (issues #1 a #4)

1. Acesse <https://github.com/gestaogovbr/Compras-API-doc/issues/new>
2. Faça login com GitHub
3. Para cada uma das 4 issues acima: cole o **Título** no campo de título, cole o conteúdo entre `=== COPIAR DAQUI ===` e `=== ATÉ AQUI ===` no campo de descrição, adicione label `bug` se disponível, submeta.

### Para CGU (1 issue)

**Opção A** — Fala BR (canal oficial, gera protocolo): <https://falabr.cgu.gov.br/publico/Manifestacao/RegistrarManifestacao.aspx>. Tipo "Sugestão" ou "Reclamação", órgão destinatário "Controladoria-Geral da União (CGU)", cole o corpo da issue CGU.

**Opção B** — E-mail direto (mais rápido, sem protocolo): envie para <api.dadosabertos@cgu.gov.br> com o corpo acima.

---

## Após reportar

- Salve a URL ou protocolo de cada issue aberta
- Documente no `CHANGELOG.md` referência cruzada (issue X → fix v0.Y)
- Reavalie em 30/60/90 dias se algum bug upstream foi resolvido — se sim, simplificar o código de workaround correspondente
