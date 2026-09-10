# Qualidade das APIs públicas — diagnóstico

Consumir dado aberto de verdade é também descobrir onde ele quebra. Esta página é o registro do
que foi encontrado nas APIs upstream ao longo de nove baterias de teste ponta a ponta contra o
ambiente de produção, **com causa raiz identificada em cada caso** — não uma lista de reclamações.

Três coisas importam sobre esta lista:

- **Nada aqui é suposição.** Cada linha foi confirmada por probe direto ao upstream, com
  reprodução determinística.
- **Cada achado vira contribuição.** Os que dependem do órgão mantenedor estão redigidos como
  issue pronta para envio em [`ISSUES_UPSTREAM.md`](../ISSUES_UPSTREAM.md) (SEGES/ME e CGU).
- **O servidor não repassa o problema para você.** Onde a API falha, a tool devolve diagnóstico,
  caminho alternativo ou aviso explícito no payload — nunca um resultado silenciosamente errado.

Para o retrato **ao vivo** (e não este, que é o mais recente conhecido), peça ao assistente para
rodar `compras_healthcheck`: ele faz o probe na hora e diz o que está de pé.

---

## Resolvidos

Deixados aqui para quem encontrar issues antigas ou forks desatualizados — e porque provam que o
projeto corrige o que diagnostica.

- ✅ **Família `/modulo-uasg/*`** (`compras_uasg_*`, `compras_orgao_*`) — chegou a devolver 404
  para todo mundo, e a documentação atribuía isso a um bug de roteamento sem fix possível.
  Diagnóstico corrigido em 2026-08 (v0.3.13): faltava o parâmetro obrigatório
  `statusUasg`/`statusOrgao` — a API responde 404 (não 400) quando ele falta. Hoje devolve
  ~22 mil UASGs e ~12 mil órgãos normalmente.
- ✅ **`compras_pesquisar_preco_material`** — o contrato da rota
  `/modulo-pesquisa-preco/1_consultarMaterial` mudou de `codigoItemCatalogo=<int>` para o par
  `tipo` (`codigoItemCatalogo`|`codigoPdm`) + `codigo` (string), sem versionar. Corrigido em v0.3.13.

---

## Em aberto

Limitações do upstream, com o contorno já implementado do lado do MCP.

- **CATMAT não tem busca por substring.** O contrato de
  `/modulo-material/4_consultarItemMaterial` oferece `descricaoItem`, que é um filtro de
  **igualdade**: `descricaoItem='CADEIRA'` devolve zero registros, embora o catálogo tenha
  milhares de itens começando por "CADEIRA ESCRITÓRIO…". Como o termo livre digitado quase nunca
  casa com a descrição inteira do item, `compras_catmat_buscar` não envia o termo ao upstream —
  usa-o para ordenar e marcar os resultados de um recorte estrutural. O caminho que funciona é
  hierárquico: `compras_catmat_listar_grupos` → `_listar_classes` → `_buscar` com
  `codigo_grupo`/`codigo_classe`. A tool emite `_aviso_filtro` quando detecta a situação.
- **Filtro UASG em `/modulo-legado/*`**: pregões e licitações têm bug Hibernate confirmado no
  upstream — o swagger documenta `co_uasg`/`uasg`, mas o atributo não existe no modelo da view
  (`400 Bad Request`). Os parâmetros foram removidos das tools `compras_legado_pregoes_listar` e
  `compras_legado_licitacoes_listar`; para filtrar por UASG, faça client-side no retorno.
- **`compras_pncp_orgao_unidades`**: a rota `/v1/orgaos/{cnpj}/unidades` não é documentada no
  contrato oficial do PNCP Consulta — devolve 404 para CNPJs que não publicam diretamente (ex.:
  CNPJ raiz de órgão cujas unidades publicam com CNPJ próprio). A tool devolve diagnóstico com
  alternativas em vez de estourar exception.
- **`compras_pncp_contratacao_itens`**: pode devolver 404 mesmo quando a contratação-pai responde
  200 — inconsistência observada no upstream, não reproduzida de forma determinística.
- **Portal da Transparência (CGU)**: o servidor é protegido por AWS WAF que bloqueia (`405` +
  página HTML "Human Verification") clientes HTTP com `User-Agent` genérico, mesmo com chave
  válida. O cliente deste MCP já envia um `User-Agent` browser-like como mitigação; se a CGU mudar
  as regras do WAF, as tools `compras_sancao_*` podem voltar a falhar — não há fix definitivo do
  lado do MCP.
- **Comprasnet `/api/contrato/ug/{uasg}`**: o endpoint não pagina e devolve a lista completa em
  uma resposta única (pode passar de 1 MB). `compras_contrato_comprasnet_por_uasg` aplica
  fatiamento client-side com cache do payload completo para não inundar o contexto do assistente.

---

## O contrato de query que engana

`dadosabertos.compras.gov.br` responde **HTTP 200 a qualquer parâmetro de query desconhecido** e
devolve o resultado como se nenhum filtro tivesse sido pedido. Nome errado de parâmetro é, portanto,
indistinguível de filtro funcionando.

É a origem da maior parte dos achados acima, e por isso toda chave e todo valor de enum enviados
pelo servidor são travados contra o OpenAPI oficial em `tests/test_contrato_upstream.py` — a
regressão aparece no CI, não em produção. Detalhes em [Arquitetura](arquitetura.md).
