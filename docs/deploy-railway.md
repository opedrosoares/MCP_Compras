# Deploy remoto (Railway)

Rodar a sua própria instância faz sentido quando uma equipe inteira vai usar o servidor, quando
você quer usar a sua chave da CGU, ou quando quer acessar pelo navegador/celular sem instalar nada.

O servidor detecta a env var `PORT` (injetada pelo Railway) e sobe automaticamente em modo HTTP;
sem ela, sobe em stdio. Não há login por usuário — todas as APIs upstream são anônimas ou usam a
chave da Transparência configurada no próprio servidor.

## 1. Criar conta no Railway

Acesse [railway.com](https://railway.com?referralCode=jJJ7Xz), clique em **Sign Up** e faça login com GitHub, GitLab ou e-mail.

## 2. Instalar o Railway CLI

```bash
# macOS (Homebrew)
brew install railway

# npm (qualquer plataforma)
npm install -g @railway/cli

# Verificar
railway --version
```

## 3. Autenticar no terminal

```bash
railway login
```

## 4. Clonar o repositório

```bash
git clone https://github.com/opedrosoares/MCP_Compras.git
cd MCP_Compras
```

## 5. Criar o projeto no Railway

```bash
railway init -n mcp-compras
```

Se tiver mais de um workspace, adicione `--workspace "Nome do Workspace"`.

## 6. Adicionar Redis (recomendado)

```bash
railway add --database redis
```

O Redis vira cache compartilhado entre instâncias — sem ele, cada pod mantém seu próprio cache em memória.

## 7. Configurar variáveis de ambiente

```bash
railway variable set TRANSPARENCIA_API_KEY=sua-chave-aqui
```

`REDIS_URL` normalmente já é injetada automaticamente pelo plugin Redis do Railway (referência de
outra variável do próprio projeto) — confira em `railway variables` se precisa setar manualmente.
A lista completa está em [Configuração](configuracao.md).

## 8. Fazer o deploy

```bash
railway up
```

Aguarde o build (Dockerfile já incluso no repo, 2-3 minutos na primeira vez).

## 9. Gerar domínio público

```bash
railway domain
```

Gera uma URL como `https://mcp-compras-production.up.railway.app`.

## 10. Verificar o deploy

O endpoint MCP exige os headers do protocolo Streamable HTTP — uma requisição "crua" deve
responder **406** (não erro de conexão), confirmando que o servidor está de pé:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://SEU-PROJETO.up.railway.app/mcp
# 406
```

Para uma checagem mais completa (versão, fontes upstream, chave da Transparência configurada),
use a tool `compras_versao` ou `compras_healthcheck` a partir de um cliente MCP já conectado.

## 11. Conectar no Claude

Veja [Servidor remoto via HTTP](instalacao.md#servidor-remoto-via-http) no guia de instalação.

Se você usa a extensão `.mcpb`, dá para apontá-la para o seu deploy em vez do servidor público:
troque o campo **Endpoint do servidor MCP** nas configurações da extensão.

## Domínio customizado (opcional)

```bash
railway domain mcp.seu-orgao.gov.br
```

O comando devolve os registros DNS a configurar. Crie um CNAME no DNS do seu órgão apontando para
o valor indicado; o certificado SSL é provisionado automaticamente. Para checar se a propagação e
o certificado já estão ok:

```bash
railway domain status mcp.seu-orgao.gov.br
```

## Atualizar o servidor

```bash
git pull
railway up
```
