"""Stub de autenticação reservado para v2 (Comprasnet Contratos / gov.br).

A v1 do MCP cobre apenas APIs públicas (Dados Abertos, PNCP e Portal da
Transparência — esta última com `chave-api-dados` em header simples). Nada
exige OAuth 2.1 ou JWT do gov.br aqui.

Quando a v2 incorporar Comprasnet Contratos, expandir este módulo com:
- Login OAuth gov.br com PKCE
- Refresh token automático
- Encripta credenciais no JWT do MCP (padrão do mcp-sei)
"""

from __future__ import annotations

import os

from compras_mcp.errors import ComprasAuthError


def get_transparencia_api_key() -> str:
    """Lê e valida a chave da API do Portal da Transparência.

    Levanta ComprasAuthError se ausente, com instruções de cadastro.
    """
    key = os.environ.get("TRANSPARENCIA_API_KEY", "").strip()
    if not key:
        raise ComprasAuthError(
            "TRANSPARENCIA_API_KEY não configurada. Cadastre um e-mail "
            "(gratuito) em https://api.portaldatransparencia.gov.br/"
            "api-de-dados/cadastrar-email e adicione a chave no .env "
            "ou nas variáveis do Railway."
        )
    return key


def get_comprasnet_bearer() -> str:
    """Stub: retorna token Bearer do Comprasnet Contratos quando v2 chegar.

    Hoje retorna o conteúdo da env `COMPRASNET_BEARER_TOKEN` se setada;
    caso contrário levanta erro pedindo para aguardar a v2.
    """
    token = os.environ.get("COMPRASNET_BEARER_TOKEN", "").strip()
    if not token:
        raise ComprasAuthError(
            "Tools de execução contratual (Comprasnet Contratos) estão "
            "previstas para a v2. v1 do MCP cobre apenas APIs públicas "
            "(Dados Abertos, PNCP e Portal da Transparência). "
            "Defina COMPRASNET_BEARER_TOKEN manualmente para uso experimental."
        )
    return token
