# Reporte à CGU — API do Portal da Transparência

**Destino:**
- E-mail: `api.dadosabertos@cgu.gov.br`
- Formulário de contato: <https://falabr.cgu.gov.br/>
- (Não há repositório público da CGU para issue formal — sugiro e-mail
  com cópia ao formulário Fala.BR para rastreabilidade.)

**Assunto sugerido:** AWS WAF da API api.portaldatransparencia.gov.br
classificando clientes HTTP legítimos como bots (HTTP 405 + página de
Human Verification)

---

## Resumo

A API pública do Portal da Transparência está protegida por AWS WAF que
classifica como bot **qualquer cliente HTTP que use o User-Agent default
de bibliotecas comuns** (`python-httpx`, `requests`, `curl`, etc.),
mesmo enviando chave de API válida cadastrada em
`/api-de-dados/cadastrar-email`. O bloqueio retorna HTTP 405 com payload
HTML de "Human Verification" da AWS Goku Props, não JSON estruturado.

Isso impacta diretamente integradores legítimos: desenvolvedores que
seguem o `cadastrar-email` ganham uma chave que **não funciona** com
qualquer cliente padrão da indústria, sem qualquer documentação ou
aviso sobre essa restrição.

## Reproduzir o problema

```bash
# Chave válida cadastrada via /api-de-dados/cadastrar-email
KEY="<sua-chave>"

# Cliente Python padrão httpx (o que qualquer SDK gerado a partir do
# OpenAPI da CGU usaria) — UA "python-httpx/0.27.x"
python3 -c "
import httpx, sys
r = httpx.get(
    'https://api.portaldatransparencia.gov.br/api-de-dados/cnep',
    params={'cnpjSancionado': '11111111000111', 'pagina': 1},
    headers={'chave-api-dados': '$KEY'},
    timeout=120,
)
print(f'HTTP {r.status_code}', '— content-type:', r.headers.get('content-type'))
print(r.text[:200])
"
# HTTP 405 — content-type: text/html
# <!DOCTYPE html><html lang="en">... AWS WAF Human Verification ...
```

```bash
# Mesma chave, mesma URL, UA browser-like — funciona
curl -s -o /tmp/r.json -w "HTTP %{http_code}\n" \
  -H "chave-api-dados: $KEY" \
  -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36" \
  "https://api.portaldatransparencia.gov.br/api-de-dados/cnep?cnpjSancionado=11111111000111&pagina=1"
# HTTP 200
cat /tmp/r.json | head -c 200
# [{"id":359510,"dataReferencia":"15/05/2026","dataInicioSancao":"29/07/2025", ...}]
```

A **única diferença** entre as duas chamadas é o header `User-Agent`.
A chave é a mesma, idêntica, válida.

## Por que isso é problema

1. **Quebra o contrato implícito da CGU com integradores.** A CGU
   pública um swagger em
   `https://api.portaldatransparencia.gov.br/swagger-ui/index.html` e
   um processo de cadastro de chave em
   `/api-de-dados/cadastrar-email`. Um desenvolvedor que segue o
   processo e usa qualquer cliente HTTP padrão (httpx, requests, axios,
   etc.) recebe 405 sem entender o motivo.

2. **A mensagem de erro é HTML, não JSON.** Clientes que esperam JSON
   da API quebram com `JSONDecodeError`. Sem stacktrace acionável.

3. **Workaround é se passar por browser.** Para que a API funcione,
   o cliente precisa enviar um `User-Agent` que se pareça com um
   navegador (Mozilla/5.0...). Isso é **anti-padrão**: a documentação
   oficial deveria especificar o UA exigido, ou o WAF deveria permitir
   UA identificáveis de integradores legítimos.

4. **Comportamento intermitente piora o diagnóstico.** Em algumas
   janelas o WAF deixa o UA padrão passar; em outras (especialmente
   horário comercial?) bloqueia. Isso confunde quem está construindo
   o cliente — o desenvolvedor pensa que é bug seu, não config do
   servidor.

## Sugestões (em ordem de preferência da CGU)

1. **Ideal:** documentar no swagger e na página de cadastro de chave
   que o WAF da CGU exige `User-Agent: <algum-formato-específico>`.
   Permite que SDKs sejam gerados corretamente.

2. **Aceitável:** isentar do bloqueio requisições que apresentam
   `chave-api-dados` válida. A chave já é a credencial — não há razão
   para o WAF tratar como bot um cliente autenticado.

3. **Mínimo:** trocar o response do WAF de HTML 405 para JSON 401/429
   com mensagem clara ("Requisição classificada como automatizada;
   inclua User-Agent identificável") e header `Retry-After`.

## Impacto operacional documentado

Estou construindo um servidor MCP (Model Context Protocol) para
auxiliar analistas de licitação a consultar bases públicas:
[opedrosoares/MCP_Compras](https://github.com/opedrosoares/MCP_Compras).

Durante 9 baterias E2E sequenciais, o bloqueio do WAF disparou em
**100% das chamadas** com UA padrão do httpx ao longo de pelo menos
24 horas. Tive que aplicar workaround na release v0.2.13 trocando o
UA do cliente para `Mozilla/5.0 ... compras-mcp/0.2.13` — taxa de
sucesso passou de 0% para próxima de 100% no mesmo período.

Isso significa que, sem o workaround, **a API da Transparência não é
utilizável por nenhuma integração automatizada** que respeite as
convenções padrão da indústria. Considero isso um bug crítico de
contrato de API pública.

## Disposição para colaborar

À disposição para fornecer payloads completos, headers, janelas
temporais e logs do cliente. Se houver canal técnico direto com o
time responsável pela infraestrutura do `api.portaldatransparencia.gov.br`,
posso descrever o caso em mais detalhe.

---

**Cliente afetado:**
[compras-mcp v0.2.13](https://github.com/opedrosoares/MCP_Compras)
**Mitigação aplicada:** commit
[e464470](https://github.com/opedrosoares/MCP_Compras/commit/e464470)
"fix v0.2.13: UA browser-like no TransparenciaClient (95% menos WAF
block)"
