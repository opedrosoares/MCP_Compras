# Configuração

Estas variáveis são do **servidor** — valem para quem roda local (via `uv`/`pip`) e para o seu
deploy remoto. Quem instala a extensão [`.mcpb`](extensao-mcpb.md) não configura nada disso: a
extensão só fala com o endpoint, e o único campo que ela expõe é a URL dele.

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `TRANSPARENCIA_API_KEY` | Não | Habilita as tools de sanções (CEIS, CNEP, CEPIM, CEAF, leniência). Sem ela, as demais ~90 tools (Dados Abertos, PNCP, Comprasnet, BrasilAPI) continuam funcionando normalmente. |
| `REDIS_URL` | Não | Cache TTL compartilhado em Redis. Recomendado em produção/Railway com múltiplos pods. Sem ela, o cache fica em memória local (TTL+LRU). |
| `INCLUIR_CPF_COMPLETO` | Não | `false` (padrão): CPFs de servidores são mascarados (`123.***.***-45`). `true` retorna completo — use com critério (LGPD). |
| `LOG_LEVEL` | Não | `DEBUG`, `INFO` (padrão), `WARNING` ou `ERROR`. |
| `COMPRASNET_BEARER_TOKEN` | Não | Reservado para v2 (rotas autenticadas do Comprasnet Contratos via login gov.br). Sem efeito na v1. |

## Como obter a chave do Portal da Transparência

Cadastro gratuito, em minutos, em
<https://api.portaldatransparencia.gov.br/api-de-dados/cadastrar-email>.
A chave chega por e-mail e vai direto na variável `TRANSPARENCIA_API_KEY`.

## Ajuste fino

[`.env.example`](../.env.example) lista todas as variáveis configuráveis, incluindo TTLs de cache
por domínio, timeouts HTTP e base URLs (estas últimas só para testes/mocks — os padrões já apontam
para produção).

Como cache, LGPD e formatos de data funcionam por dentro está em [Arquitetura](arquitetura.md).
