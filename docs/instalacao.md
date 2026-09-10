# Guia de instalação

Três caminhos, do mais simples ao mais técnico. Escolha um:

| Caminho | Para quem | Precisa instalar algo? |
|---------|-----------|------------------------|
| [1. Extensão `.mcpb`](#1-extensão-mcpb-claude-desktop) | Usuário de **Claude Desktop** que só quer usar | Não |
| [2. Local via `uv`](#2-local-via-uv-claude-code-cursor-desenvolvimento) | Claude Code, Cursor, desenvolvimento | Python 3.11+ |
| [3. Servidor remoto](#3-servidor-remoto-equipe-ou-uso-por-navegadorcelular) | Equipe, uso por navegador/celular | Conta no Railway |

---

## 1. Extensão `.mcpb` (Claude Desktop)

[**Baixar `compras.mcpb`**](https://github.com/opedrosoares/MCP_Compras/releases/latest/download/compras.mcpb) — o link aponta sempre para a versão mais recente.

1. Baixe o arquivo.
2. Abra com **duplo-clique**. O Claude Desktop reconhece o formato e instala.
3. Pronto. Não há chave de API para preencher, nem Python para instalar.

São 23 KB e nada roda na sua máquina além de uma ponte de rede — o porquê está em
[Como funciona a extensão `.mcpb`](extensao-mcpb.md).

Prefere gerar o arquivo a partir do código-fonte?

```bash
git clone https://github.com/opedrosoares/MCP_Compras.git
cd MCP_Compras
python3 build_mcpb.py     # gera dist/compras.mcpb
open dist/compras.mcpb    # macOS — no Windows/Linux, duplo-clique no arquivo
```

**Quando *não* usar o `.mcpb`:** se as consultas não podem sair da sua rede, ou se você quer
usar a sua própria chave do Portal da Transparência. Nesses casos, use o caminho 2.

---

## 2. Local via `uv` (Claude Code, Cursor, desenvolvimento)

```bash
git clone https://github.com/opedrosoares/MCP_Compras.git
cd MCP_Compras
uv sync
uv run compras-mcp
```

Ou instale do PyPI, sem clonar:

```bash
pip install compras-mcp    # ou: uv tool install compras-mcp
compras-mcp
```

Depois registre o comando no seu cliente — veja [Conectar a um cliente MCP](#conectar-a-um-cliente-mcp).
Variáveis de ambiente opcionais (chave da CGU, Redis, LGPD) estão em [Configuração](configuracao.md).

---

## 3. Servidor remoto (equipe ou uso por navegador/celular)

Não exige instalação local nenhuma: qualquer cliente MCP aponta para uma URL HTTP. O passo a
passo completo está em [Deploy remoto (Railway)](deploy-railway.md).

---

## Conectar a um cliente MCP

### Claude Code — `.mcp.json` do projeto ou `~/.claude.json` (global)

Servidor local via stdio (assume `compras-mcp` instalado no PATH — via `uv tool install .` ou `pip install .`):

```json
{
  "mcpServers": {
    "compras": {
      "command": "compras-mcp",
      "env": {
        "TRANSPARENCIA_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

Sem instalar globalmente, rodando direto do clone via `uv`:

```json
{
  "mcpServers": {
    "compras": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/para/MCP_Compras", "compras-mcp"],
      "env": {
        "TRANSPARENCIA_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

`TRANSPARENCIA_API_KEY` é opcional: sem ela, todas as tools funcionam exceto as de sanções
(`compras_sancao_*`, `compras_checar_sancoes_fornecedor` e as ramificações de sanção em
`compras_perfil_fornecedor_completo`).

### Claude Desktop (registro manual, sem o `.mcpb`)

Edite o `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "compras": {
      "command": "compras-mcp",
      "env": {
        "TRANSPARENCIA_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

### Servidor remoto via HTTP

Depois do [deploy](deploy-railway.md), o endpoint MCP fica em `https://SEU-PROJETO.up.railway.app/mcp`.
Não há autenticação própria — é o mesmo servidor, só que em modo HTTP em vez de stdio.

- **claude.ai / Claude Desktop**: Settings → Connectors → Adicionar conector personalizado → cole a URL.
- **Claude Code** — via CLI:

  ```bash
  claude mcp add --transport http compras-remoto https://SEU-PROJETO.up.railway.app/mcp
  ```

  Ou direto no `.mcp.json`:

  ```json
  {
    "mcpServers": {
      "compras-remoto": {
        "type": "http",
        "url": "https://SEU-PROJETO.up.railway.app/mcp"
      }
    }
  }
  ```

---

## Requisitos de sistema

Para a extensão `.mcpb` (caminho 1): **nenhum**. O Node já vem com o Claude Desktop.

Para rodar o servidor você mesmo (caminhos 2 e 3):

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (recomendado) ou `pip`
- Claude Code, Claude Desktop, ou qualquer cliente MCP compatível com stdio ou Streamable HTTP
- Redis (opcional, só para cache compartilhado em deploy com múltiplas instâncias)
- Chave gratuita do Portal da Transparência (opcional, só para as tools de sanções)

Nenhuma dependência de sistema além do Python — diferente de MCPs que fazem OCR ou scraping,
este servidor só consome APIs REST públicas.

---

## Deu problema?

- **`Server disconnected` logo na instalação da extensão**: se você tinha uma versão 0.3.x
  instalada, remova-a antes. Veja [Vindo da v0.3.x](extensao-mcpb.md#vindo-da-v03x).
- **Tools de sanções falhando**: confira a chave em [Configuração](configuracao.md) e a situação
  do WAF da CGU em [Qualidade das APIs públicas](qualidade-das-apis.md).
- **Alguma consulta voltando vazia ou com erro**: peça ao assistente para rodar
  `compras_healthcheck` — ele faz um probe real contra cada API upstream em ~30s e diz qual módulo
  está degradado.
