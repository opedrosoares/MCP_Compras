# Roadmap — compras-mcp

Próximas direções identificadas após a série v0.3.x. **Sem datas** — fica
em backlog até aparecer demanda concreta ou achado E2E que justifique.

Ordenado aproximadamente por "valor entregue / esforço". Última atualização:
2026-05-17 (pós v0.3.5).

---

## 1. README polido + onboarding multilíngue

**O que**: README atual é técnico; falta versão narrativa + tradução EN +
demo gif + troubleshooting de problemas comuns + instruções específicas
para os 7 clientes MCP (Claude Desktop, Claude.ai web, Cursor, Continue.dev,
Cline, Zed, ChatGPT via Agents SDK).

**Quando vale a pena**: quando quiser comunicar o projeto publicamente ou
abrir para contribuição externa.

**Esforço estimado**: meio dia (texto + 1 gif de demo + revisão EN).

**Inspiração**: licinexus-mcp tem uma referência boa.

---

## 2. Distribuição via PyPI (`uvx compras-mcp`)

**O que**: empacotar e publicar como `compras-mcp` no PyPI. Onboarding
vira `uvx compras-mcp` ou `pip install compras-mcp` em vez do `.mcpb`
atual (que é específico do Claude Desktop).

**Quando vale a pena**: quando aparecer dev fora do nicho Claude Desktop
querendo usar (Cursor, Continue.dev, integração custom).

**Esforço estimado**: 2-3h (pyproject já está pronto; falta credentials
PyPI + workflow de publish em tag + smoke pós-publicação).

**Pré-requisito**: nome `compras-mcp` precisa estar livre no PyPI.

---

## 3. ~~Tools-espelho com filtro server-side em ARPs~~ ✅ **DONE em v0.3.6**

**Status**: implementado como `compras_arp_buscar_por_objeto` em v0.3.6,
após a bateria A v0.3.5 confirmar que filtragem manual em 339 páginas é
operacionalmente inviável.

**Forma final**: tool dedicada nova (em vez de poluir
`compras_arp_listar`/`compras_arp_por_fim_vigencia` com novos params).
Aceita `palavra_chave`, janela de vigência, `max_paginas_varridas` (cap
de proteção, default 10 = 5.000 atas), `max_resultados` (curto-circuito,
default 20). Normaliza acentos+case. UF não foi implementada na tool
porque o schema upstream não traz UF no item — agente cruza com
`compras_uasg_consultar` se precisar.

**Validação E2E**: 2 matches reais ("notebook") em 4,7s varrendo 2.500
atas, vs ~3 minutos da rota manual da bateria A.

---

## 4. `test_resilience_live.py` opt-in com `@pytest.mark.live`

**O que**: testes que bateriam no upstream real — PNCP fora do ar
(timeout absurdo), payload >5MB (CNEP em listagem larga), CNPJ realmente
sancionado em múltiplos cadastros, etc. Excluído do CI default; rodado
manualmente com `pytest -m live` ou via workflow agendado semanal.

**Quando vale a pena**: quando aparecer achado E2E que **só** seja
detectável com chamada real (até agora, todos foram detectáveis
estaticamente ou com mocks).

**Esforço estimado**: 1-2h depois de identificar o cenário concreto.

---

## 5. Early-return graceful em tools singulares de sanção

**O que**: hoje, quando `TRANSPARENCIA_API_KEY` está ausente,
`compras_sancao_ceis/cnep/cepim/ceaf/acordos_leniencia` propagam exception
(com mensagem clara, capturada pelo teste 4b da suite de resiliência).
O `compras_perfil_fornecedor_completo` já faz early return graceful
nesse cenário. Replicar o mesmo padrão nas 5 tools singulares.

**Quando vale a pena**: se aparecer relato de agente que crasha em vez de
informar o usuário sobre a chave faltando. Hoje não é crash silencioso —
é exception com mensagem clara — então prioridade baixa.

**Esforço estimado**: 1h (pattern já existe, é copy-paste com adaptação).

---

## 7. `compras_arp_vencedores_por_ata` — fechar a cadeia ARP→fornecedor

**O que**: tool que recebe um `numeroControlePncpAta` e devolve a lista
de fornecedores vencedores dos itens da ata, já com CNPJ extraído.
Sugerida na bateria A "rodada 3" v0.3.5 — hoje a cadeia ARP→vencedor
exige sequência manual `arp_consultar` → `arp_itens_listar` →
`pncp_contratacao_item_resultados` (que retorna 400 em IDs de ARP
porque espera id de compra).

**Quando vale a pena**: se o fluxo "carona ARP → due diligence do
vencedor" virar caso de uso recorrente. Como o `compras_arp_buscar_por_objeto`
(v0.3.6) já entrega ARPs candidatas e o `compras_perfil_fornecedor_completo`
faz a DD, o gap é só o connector entre os dois.

**Esforço estimado**: 4-6h (entender qual endpoint upstream traz
vencedor — provavelmente cruzamento de `/modulo-arp/2_consultarARPItem`
com algum endpoint de resultado da compra original; modelagem nova).

**Trade-off**: aumenta acoplamento entre as áreas ARP e Resultados.
Avaliar se virou maturidade necessária ou se a cadeia manual basta.


## 6. DCO + CONTRIBUTING + CODE_OF_CONDUCT + SECURITY

**O que**: pré-requisitos formais para abrir o projeto a forks e
contribuições externas. Inclui Developer Certificate of Origin no PR
template + guia de contribuição + processo de reportar bug de segurança.

**Quando vale a pena**: junto com o item 1 (README polido), se decidir
comunicar publicamente.

**Esforço estimado**: 1-2h (templates padrão da indústria + revisão).

---

## Achados conhecidos sem fix aplicado

Itens documentados na bateria mas que não viraram release próprio porque
o impacto é baixo ou o trade-off não compensa:

- **CGU `/api-de-dados/cepim` ignora todos os filtros** (workaround:
  filtragem client-side já aplicada).
- **PNCP rejeita `tamanho_pagina > 50`** com 400 (workaround: cap em 50
  no cliente).
- **PNCP timeoutou 5-6/8 dias em janelas com modalidade 8 + UF urbana**
  (variabilidade upstream — agora visível via `erros_por_bucket`).
- **CATMAT filtro textual `descricao` ignorado** desde meados de 2026
  (workaround: docstring + aviso `_aviso_filtro` + roteiro orienta
  workflow estrutural).

Esses são reportáveis para CGU/SISG/PNCP via canais oficiais; o servidor
faz o melhor que pode com workaround.

---

## Princípio orientador

Após v0.3.5, **a malha de testes está pronta** para detectar regressão
de comportamento upstream e de input ruim. Qualquer trabalho futuro deve
ser puxado por **demanda concreta** (usuário relatou X, achado E2E em Y),
não por especulação. O reflexo de "achar mais bug" foi substituído por
"esperar bug aparecer e ter rede de proteção".
