# Como funciona a extensão `.mcpb`

[**Baixar `compras.mcpb`**](https://github.com/opedrosoares/MCP_Compras/releases/latest/download/compras.mcpb) — 23 KB, cinco arquivos, zero dependências.

**Instalação e primeiro uso em vídeo:**

[![Pesquisa de preços para ETP em 1 minuto | MCP Compras.gov.br](https://img.youtube.com/vi/ERIxOW1UyzA/hqdefault.jpg)](https://youtu.be/ERIxOW1UyzA)

## O bundle é um cliente, não o servidor

Desde a **v0.4.0** o bundle não carrega mais o servidor: ele é um *cliente* do servidor oficial
hospedado. O que vai dentro dele é `manifest.json`, `bridge.js`, ícone, README e licença — nada mais.

```
Claude Desktop  ──stdio──▶  bridge.js  ──HTTPS (Streamable HTTP)──▶  mcp-compras.up.railway.app/mcp
 (Node embutido)             23 KB, 0 deps                                        │
                                                                                  ▼
                                        Dados Abertos · PNCP · CGU · Comprasnet · BrasilAPI
```

O `bridge.js` roda no **Node que já acompanha o Claude Desktop** — é o único runtime que o app
garante no macOS e no Windows, e o formato `.mcpb` não tem um tipo "remoto" que dispensaria a
ponte. Daí a extensão não exigir Python nem venv.

**O que fica do lado do servidor**, e por isso a extensão não te pergunta nada na instalação:
a chave do Portal da Transparência, o Redis do cache e a máscara de CPF exigida pela LGPD.

## O que a ponte trata

Além de repassar mensagens:

| Situação | Comportamento |
|----------|---------------|
| Resposta em SSE ou em `application/json` | aceita as duas formas do Streamable HTTP |
| Servidor reiniciou e perdeu a sessão (HTTP 404) | refaz o handshake por baixo e repete a chamada |
| Cold start, 5xx ou 429 | retenta com backoff |
| Servidor inacessível | devolve erro JSON-RPC — a pergunta não fica pendurada |
| Fim da conversa | `DELETE` da sessão, sem sessão órfã no servidor |

Cada uma dessas linhas tem teste em [`tests/test_mcpb_bridge.py`](../tests/test_mcpb_bridge.py),
que sobe a ponte do mesmo jeito que o Claude Desktop sobe, contra um servidor MCP falso.

## Apontar para a sua própria instância

Troque o campo **Endpoint do servidor MCP**, nas configurações da extensão, pela URL `/mcp` do
seu deploy — veja [Deploy remoto (Railway)](deploy-railway.md). O padrão é o servidor público oficial.

## Vindo da v0.3.x

Remova a extensão antiga antes de instalar. Até a v0.3.x o bundle empacotava o servidor inteiro
e montava um venv com `pip install` no primeiro start, o que falhava com `Server disconnected` em
máquinas sem um binário chamado `python` no PATH — o caso do macOS, que só tem `python3`.

## Quando *não* usar o `.mcpb`

Se as consultas não podem sair da sua rede, ou se você quer usar a sua própria chave da CGU,
rode o servidor localmente — [caminho 2 do guia de instalação](instalacao.md#2-local-via-uv-claude-code-cursor-desenvolvimento).
