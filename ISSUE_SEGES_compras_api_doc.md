# Issue formal — `gestaogovbr/Compras-API-doc`

**Destino:** <https://github.com/gestaogovbr/Compras-API-doc/issues/new>
**Título sugerido:** Quatro inconsistências confirmadas entre o Swagger oficial e o servidor `dadosabertos.compras.gov.br` (probes reproduzíveis)
**Labels sugeridas:** `bug`, `api`, `swagger`

---

## Resumo

Durante construção de um cliente automatizado (MCP server) consumindo a API
Dados Abertos do Compras.gov.br, identifiquei **4 inconsistências** entre o
contrato documentado em
`https://dadosabertos.compras.gov.br/v3/api-docs` e o comportamento real do
servidor em produção. Todas reproduzíveis com `curl`, validadas múltiplas
vezes ao longo de 2026-05.

Cada bug está apresentado com: endpoint, parâmetros, resposta esperada
segundo o swagger, resposta real, e impacto operacional.

---

## Bug 1 — Família `/modulo-uasg/*` retorna 404 apesar de listada no swagger

**Severidade:** 🔴 crítica — derruba a resolução de UASG/órgão por código,
pré-requisito de quase toda consulta filtrada.

### Reproduzir

```bash
# Probe direto, sem auth
curl -i "https://dadosabertos.compras.gov.br/modulo-uasg/1_consultarUasg?pagina=1&tamanhoPagina=10"
# HTTP/2 404
# { "statusCode": 404, "message": "Resource not found" }

curl -i "https://dadosabertos.compras.gov.br/modulo-uasg/2_consultarOrgao?pagina=1&tamanhoPagina=10"
# HTTP/2 404
# { "statusCode": 404, "message": "Resource not found" }

curl -i "https://dadosabertos.compras.gov.br/modulo-uasg/1_consultarUasg?nome=ANTAQ&pagina=1&tamanhoPagina=10"
# HTTP/2 404
# { "statusCode": 404, "message": "Resource not found" }
```

### Swagger documenta

```json
"paths": {
  "/modulo-uasg/1_consultarUasg": { "get": { ... } },
  "/modulo-uasg/1.1_consultarUasg_CSV": { "get": { ... } },
  "/modulo-uasg/2_consultarOrgao": { "get": { ... } },
  "/modulo-uasg/2.1_consultarOrgao_CSV": { "get": { ... } }
}
```

### Comportamento real

Todos os 6 paths da família `/modulo-uasg/*` retornam 404 com mensagem
genérica "Resource not found". A família inteira parece ter sido removida
do roteamento do servidor sem atualização do swagger.

### Impacto

- Clientes não conseguem listar UASGs ou órgãos por nome/código.
- Documentação induz analistas a construir queries que sempre falham.
- Sem alternativa equivalente no Dados Abertos (PNCP `/v1/orgaos/{cnpj}/unidades`
  é parcial e não cobre todos os entes federais SISG).

### Sugestão

Ou (a) reativar o roteamento dos 6 paths, ou (b) remover esses paths do
`/v3/api-docs` se a remoção foi intencional. Hoje o swagger mente sobre o
contrato disponível.

---

## Bug 2 — `/modulo-legado/3_consultarPregoes?co_uasg=X` retorna 400 com erro Hibernate

**Severidade:** 🟠 alta — quebra filtragem por UASG em consulta de pregões legado.

### Reproduzir

```bash
# SEM filtro UASG (funciona)
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/3_consultarPregoes?dt_data_edital_inicial=2021-01-01&dt_data_edital_final=2021-06-30&pagina=1&tamanhoPagina=10"
# HTTP/2 200 — 36.164 registros

# COM filtro UASG (quebra)
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/3_consultarPregoes?dt_data_edital_inicial=2021-01-01&dt_data_edital_final=2021-06-30&co_uasg=200999&pagina=1&tamanhoPagina=10"
# HTTP/2 400
# Erro ao efetuar a consulta Could not resolve attribute 'TbVwPregaoId.coUasg' of 'br.gov.economia.apicompras.models.TbVwPregao'
```

### Swagger documenta

```yaml
/modulo-legado/3_consultarPregoes:
  get:
    parameters:
      - name: co_uasg     # ← documentado
        in: query
        schema: { type: integer }
      - name: co_orgao    # ← documentado
        in: query
        schema: { type: integer }
      - name: dt_data_edital_inicial
        required: true
      - name: dt_data_edital_final
        required: true
      ...
```

### Comportamento real

O backend (provavelmente JPA/Hibernate na view `TbVwPregao`) não mapeia o
atributo `coUasg`. A mensagem de erro é diagnóstica direta:
`Could not resolve attribute 'TbVwPregaoId.coUasg' of 'br.gov.economia.apicompras.models.TbVwPregao'`.

### Sugestão

Adicionar o atributo `coUasg` na view (preferível, mantém contrato) ou
remover do swagger se o filtro nunca poderá ser oferecido. O comportamento
"documenta e quebra" é o pior dos mundos para integradores.

---

## Bug 3 — `/modulo-legado/1_consultarLicitacao?uasg=X` retorna 400 genérico

**Severidade:** 🟠 alta — mesmo cenário do Bug 2, em outro endpoint da família legado.

### Reproduzir

```bash
# SEM filtro UASG (funciona)
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/1_consultarLicitacao?data_publicacao_inicial=2021-01-01&data_publicacao_final=2021-06-30&pagina=1&tamanhoPagina=10"
# HTTP/2 200 — 14.096 registros

# COM filtro UASG (quebra)
curl -i "https://dadosabertos.compras.gov.br/modulo-legado/1_consultarLicitacao?data_publicacao_inicial=2021-01-01&data_publicacao_final=2021-06-30&uasg=200999&pagina=1&tamanhoPagina=10"
# HTTP/2 400
# Erro ao efetuar a consulta
```

### Swagger documenta

`uasg` aparece como query parameter opcional. A mensagem de erro do
servidor é genérica ("Erro ao efetuar a consulta") — sem stacktrace.
Provável que seja o mesmo padrão do Bug 2 (atributo não mapeado na view)
mas sem detalhe expostal.

### Sugestão

Mesma do Bug 2 — adicionar o atributo ou remover do swagger.

---

## Bug 4 — `/modulo-material/4_consultarItemMaterial?descricao=*` ignora o filtro

**Severidade:** 🔴 crítica — torna a busca textual no CATMAT inútil; o
analista precisa navegar manualmente pela hierarquia grupo → classe → PDM → item.

### Reproduzir

```bash
# Todos retornam EXATAMENTE o mesmo total
curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?pagina=1&tamanhoPagina=10" | jq .totalRegistros
# 341779

curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?descricao=cadeira&pagina=1&tamanhoPagina=10" | jq .totalRegistros
# 341779  ← filtro ignorado, retorna universo

curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?descricao=resma%20papel%20A4&pagina=1&tamanhoPagina=10" | jq .totalRegistros
# 341779  ← idem

# Filtros estruturais ainda funcionam
curl -s "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?codigoGrupo=71&pagina=1&tamanhoPagina=10" | jq .totalRegistros
# 9600  ← OK
```

### Swagger documenta

```yaml
parameters:
  - name: descricaoItem
    in: query
    schema: { type: string }
```

### Comportamento observado

- `descricaoItem` (nome no swagger): retorna 0 registros para qualquer valor.
- `descricao` (variante que historicamente funcionava): aceito pelo servidor
  sem 400, mas **ignorado** — retorna sempre os 341.779 do universo.
- Filtros estruturais (`codigoGrupo`, `codigoClasse`) **continuam funcionando**.

Há evidência histórica de que `descricao=cadeira` funcionava até início de
2026, retornando ~340k registros com "cadeira" no termo. A mudança parece
ter sido silenciosa.

### Impacto

Sem busca textual, o analista precisa conhecer **a priori** a hierarquia
CATMAT completa (grupo → classe → PDM → item) para chegar a um código. A
descoberta de um item desconhecido fica inviável dentro do Dados Abertos.

### Sugestão

Restaurar o comportamento do filtro `descricao` (ou de qualquer um dos
nomes alternativos: `nome`, `termo`, `q`, `nomePadronizadoItem`) — o que
for o nome oficial. Hoje nenhum funciona.

---

## Como cheguei a estes achados

Operando o cliente MCP `compras-mcp` em produção (85 tools cobrindo
Dados Abertos + PNCP + Comprasnet + Transparência), realizei 9 baterias
E2E sequenciais contra o servidor. A cada bug detectado nos próprios
clientes, fiz probe direto por `curl` para isolar onde estava o defeito.

Os 4 bugs acima resistiram a todas as tentativas de workaround do lado
cliente — são, comprovadamente, do servidor.

## Disposição para colaborar

Posso fornecer mais payloads, headers ou janelas temporais conforme
solicitado. Se houver acesso a logs internos do servidor que ajude
a confirmar/refutar essas reproduções, fico à disposição.

---

**Cliente afetado:** [compras-mcp](https://github.com/opedrosoares/MCP_Compras)
v0.2.13 (commit `e464470`)
**Documentação interna dos bugs:** `CHANGELOG.md` releases v0.2.4, v0.2.7,
v0.2.8 e bloco "Aviso operacional" no `README.md`.
