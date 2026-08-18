"""Cliente HTTP para o Comprasnet Contratos (rotas abertas `/api/*`).

Base: https://contratos.comprasnet.gov.br/api
Código-fonte: https://gitlab.com/comprasnet/contratos (routes/api.php)

A v1 deste MCP cobre apenas as rotas `/api/*` sem versão, que são **públicas**
e atendem a maior parte das consultas de execução contratual: contratos,
empenhos, garantias, faturas, ocorrências, responsáveis (fiscais/gestores),
publicações DOU, cronograma financeiro e itens.

As rotas `/api/v1/*` (que exigem JWT do gov.br + RateLimitPerUser) ficam
fora deste cliente — quando a v2 do MCP entrar, expandir aqui.

Datas no formato YYYY-MM-DD HH:mm:ss (usar `format_date(d, "comprasnet")`).
A resposta é tipicamente uma lista JSON direta (sem envelope tipo
`{resultado: [...], totalPaginas: ...}`) ou um objeto único quando o path
é singular (ex.: `/api/contrato/id/{id}`). Helpers em `tools/_helpers.py`
adaptam isso para o envelope padronizado do MCP.
"""

from __future__ import annotations

from typing import Any

from compras_mcp.clients.base import BaseAsyncClient


class ComprasnetContratosClient(BaseAsyncClient):
    """Cliente para contratos.comprasnet.gov.br/api (rotas abertas)."""

    def __init__(
        self, *, base_url: str, timeout: float = 60.0, max_retries: int = 2
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_name="comprasnet_contratos",
            timeout=timeout,
            max_retries=max_retries,
        )

    async def get_list(self, path: str, **params: Any) -> Any:
        """GET para endpoints de listagem (resposta tipicamente é lista JSON).

        Retorna o payload bruto — pode ser lista (`[...]`) ou dict
        (alguns endpoints já vêm com envelope `{data: [...]}`). Helpers
        em `tools/_helpers.py` normalizam isso para o cliente MCP.
        """
        return await self.get_json(path, params=params)

    async def get_one(self, path: str, **params: Any) -> Any:
        """GET para endpoints singulares (resposta é objeto único)."""
        return await self.get_json(path, params=params)

    async def post_json(
        self, path: str, json_body: dict[str, Any]
    ) -> Any:
        """POST com corpo JSON. Usado em `/api/comprasnet/compras/impedimentos`
        (e tools relacionadas que esperam lista de itens no body).
        """
        client = await self._ensure_client()
        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.post(path, json=json_body)
            except Exception as e:
                self.log.warning(
                    "client.post.error",
                    path=path,
                    attempt=attempt,
                    error=type(e).__name__,
                )
                if attempt < self.max_retries:
                    import asyncio

                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                from compras_mcp.errors import ComprasHTTPError

                raise ComprasHTTPError(f"POST {path} falhou: {e}") from e

            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except ValueError as e:
                    from compras_mcp.errors import ComprasHTTPError

                    raise ComprasHTTPError(
                        f"Resposta nao-JSON em POST {path}: {resp.text[:200]}"
                    ) from e
            if 500 <= resp.status_code < 600 and attempt < self.max_retries:
                import asyncio

                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            from compras_mcp.errors import ComprasHTTPError, ComprasNotFoundError

            if resp.status_code == 404:
                raise ComprasNotFoundError(f"POST {path} retornou 404")
            raise ComprasHTTPError(
                f"POST {path} respondeu {resp.status_code}: {resp.text[:300]}"
            )

        from compras_mcp.errors import ComprasHTTPError

        raise ComprasHTTPError(f"POST {path} esgotou retries")
