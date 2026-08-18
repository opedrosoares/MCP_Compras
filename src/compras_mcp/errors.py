"""Hierarquia de exceções do MCP Compras.gov.br.

Mantemos uma raiz `ComprasError` para que tools possam capturar tudo de uma vez
e converter em mensagem amigável para o usuário do MCP. As subclasses
diferenciam a causa para que o handler decida se sugere retry, ajuste de
input, ou falha definitiva.
"""

from __future__ import annotations


class ComprasError(Exception):
    """Raiz para qualquer erro originado neste MCP."""


class ComprasConfigError(ComprasError):
    """Configuração ausente ou inválida (ex.: env var faltando)."""


class ComprasHTTPError(ComprasError):
    """Falha de transporte HTTP contra alguma das APIs upstream."""


class ComprasTimeoutError(ComprasHTTPError):
    """Timeout em chamada a API do Compras/PNCP/Transparência."""


class ComprasServerError(ComprasHTTPError):
    """Upstream respondeu 5xx — problema do servidor remoto, sugerir retry."""


class ComprasRateLimitError(ComprasHTTPError):
    """Upstream respondeu 429 — quota da API excedida (típico da Transparência)."""


class ComprasWafBlockError(ComprasHTTPError):
    """Upstream bloqueado por WAF anti-bot (HTML "Human Verification").

    Comum no Portal da Transparência (CGU): mesmo com chave válida,
    o AWS WAF da CGU retorna HTTP 405 + HTML de verificação humana
    quando detecta padrões automatizados. Não há fix do lado do cliente —
    tools devolvem payload `_erro_upstream` informativo apontando
    workarounds (consultar via webapp, contato CGU, etc.).
    """


class ComprasAuthError(ComprasError):
    """Falha de autenticação (token ausente/inválido/expirado).

    Disparado, por ex., quando uma tool de sanções é chamada sem
    `TRANSPARENCIA_API_KEY`, ou quando o Comprasnet Contratos exige
    bearer e ele não está configurado (v2).
    """


class ComprasNotFoundError(ComprasError):
    """O recurso (contrato, ata, fornecedor, etc.) não foi encontrado."""


class ComprasValidationError(ComprasError):
    """Parâmetro de entrada inválido ou fora do domínio esperado."""


class ComprasNotImplementedError(ComprasError):
    """Funcionalidade prevista mas ainda não implementada nesta versão.

    Usado pelo stub do Comprasnet Contratos (escopo v2).
    """
