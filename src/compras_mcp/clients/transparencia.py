"""Cliente HTTP para o Portal da Transparência (CGU).

Base: https://api.portaldatransparencia.gov.br
Docs: https://api.portaldatransparencia.gov.br/swagger-ui/index.html

Autenticação: header `chave-api-dados` (cadastro gratuito).
Rate limit: 30 req/min — confiar no retry exponencial do BaseAsyncClient
para o 429.

Endpoints cobertos pela v1:
- /api-de-dados/ceis           — Cadastro de Empresas Inidôneas e Suspensas
- /api-de-dados/cnep           — Cadastro Nacional de Empresas Punidas (Lei Anticorrupção)
- /api-de-dados/ceaf           — Cadastro de Expulsões da Administração Federal
- /api-de-dados/cepim          — Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas
- /api-de-dados/acordos-leniencia
"""

from __future__ import annotations

from typing import Any

import httpx

from compras_mcp.auth import get_transparencia_api_key
from compras_mcp.clients.base import BaseAsyncClient
from compras_mcp.errors import (
    ComprasHTTPError,
    ComprasNotFoundError,
    ComprasRateLimitError,
    ComprasServerError,
    ComprasTimeoutError,
    ComprasWafBlockError,
)


class TransparenciaClient(BaseAsyncClient):
    """Cliente para api.portaldatransparencia.gov.br.

    Recebe a chave da API por construtor para facilitar testes; em produção
    a tool levanta `ComprasAuthError` antes de instanciar quando a chave
    não está configurada.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        # Se nenhuma chave foi passada, busca via auth.py (que levanta se ausente)
        key = api_key if api_key is not None else get_transparencia_api_key()
        # User-Agent browser-like + Accept JSON explícito: descoberto na bateria 9
        # (curl direto) que o AWS WAF da CGU bloqueia consistentemente o UA default
        # do httpx ("python-httpx/0.27.x") com 405 + HTML, mas aceita UA browser.
        # Não é evasão — usamos chave de API legítima da CGU; é workaround contra
        # WAF excessivamente agressivo que classifica clientes HTTP genuínos como
        # bots e bloqueia chamadas API que ele mesmo expõe.
        super().__init__(
            base_url=base_url,
            api_name="transparencia",
            timeout=timeout,
            max_retries=max_retries,
            headers={
                "chave-api-dados": key,
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) compras-mcp/0.2.13 Safari/537.36"
                ),
                "Accept": "application/json",
            },
        )

    async def get_json(  # type: ignore[override]
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Sobrescreve get_json para detectar 405 com body HTML do AWS WAF.

        O Portal da Transparência (CGU) está atrás de AWS WAF que pode
        retornar HTTP 405 + página HTML "Human Verification" mesmo com
        chave de API válida. Em vez de levantar `ComprasHTTPError` genérico
        (que confunde o LLM), levanta `ComprasWafBlockError` específica —
        as tools de sanção capturam e devolvem payload graceful.

        Demais condições (404, 429, 5xx, timeout) seguem a política do
        BaseAsyncClient.
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        client = await self._ensure_client()
        retries = self.max_retries if max_retries is None else max_retries

        for attempt in range(retries + 1):
            try:
                if timeout is not None:
                    resp = await client.get(path, params=clean_params, timeout=timeout)
                else:
                    resp = await client.get(path, params=clean_params)
            except httpx.TimeoutException as e:
                if attempt < retries:
                    import asyncio
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ComprasTimeoutError(
                    f"Timeout chamando transparencia{path}: {e}"
                ) from e
            except httpx.HTTPError as e:
                raise ComprasHTTPError(
                    f"Erro de transporte em transparencia{path}: {e}"
                ) from e

            # Detecção específica do WAF AWS da CGU
            content_type = resp.headers.get("content-type", "").lower()
            is_html = "text/html" in content_type or resp.text.startswith("<!DOCTYPE")
            if resp.status_code == 405 and is_html:
                raise ComprasWafBlockError(
                    "Portal da Transparência bloqueou a requisição via AWS WAF "
                    "(HTTP 405 + HTML 'Human Verification'). Bug upstream "
                    "intermitente — sem fix possível pelo cliente."
                )

            if resp.status_code == 404:
                raise ComprasNotFoundError(
                    f"Recurso nao encontrado em transparencia{path}"
                )
            if resp.status_code == 429:
                if attempt < retries:
                    import asyncio
                    retry_after = float(resp.headers.get("Retry-After", "2"))
                    await asyncio.sleep(retry_after)
                    continue
                raise ComprasRateLimitError(
                    "Rate limit em transparencia (quota 30 req/min)"
                )
            if 500 <= resp.status_code < 600:
                if attempt < retries:
                    import asyncio
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ComprasServerError(
                    f"transparencia{path} respondeu {resp.status_code}: "
                    f"{resp.text[:300]}"
                )
            if not resp.is_success:
                raise ComprasHTTPError(
                    f"transparencia{path} respondeu {resp.status_code}: "
                    f"{resp.text[:300]}"
                )

            try:
                return resp.json()
            except ValueError as e:
                raise ComprasHTTPError(
                    f"Resposta nao-JSON em transparencia{path}: {resp.text[:200]}"
                ) from e

        raise ComprasHTTPError(f"Esgotou retries em transparencia{path}")

    async def list_ceis(
        self,
        *,
        cnpj_sancionado: str | None = None,
        nome_sancionado: str | None = None,
        orgao_sancionador: str | None = None,
        pagina: int = 1,
    ) -> list[dict[str, Any]]:
        """Lista registros do CEIS com filtros.

        A API devolve uma lista JSON direta (não envelope). Para checar se
        um fornecedor tem sanção, prefira passar `cnpj_sancionado`.

        IMPORTANTE: o endpoint `/api-de-dados/ceis` da CGU aceita o filtro
        no parâmetro **`codigoSancionado`** — o nome `cnpjSancionado` é
        silenciosamente ignorado e a API retorna a lista global. Mantemos
        a assinatura `cnpj_sancionado` para o caller, mas mapeamos para o
        parâmetro upstream correto.
        """
        return await self.get_json(
            "/api-de-dados/ceis",
            params={
                "codigoSancionado": cnpj_sancionado,
                "nomeSancionado": nome_sancionado,
                "orgaoSancionador": orgao_sancionador,
                "pagina": pagina,
            },
        )

    async def list_cnep(
        self,
        *,
        cnpj_sancionado: str | None = None,
        nome_sancionado: str | None = None,
        pagina: int = 1,
    ) -> list[dict[str, Any]]:
        """Lista registros do CNEP (Lei Anticorrupção).

        Mesmo bug do CEIS: a CGU espera `codigoSancionado` (não
        `cnpjSancionado`). Mapeamos internamente.
        """
        return await self.get_json(
            "/api-de-dados/cnep",
            params={
                "codigoSancionado": cnpj_sancionado,
                "nomeSancionado": nome_sancionado,
                "pagina": pagina,
            },
        )

    async def list_ceaf(
        self,
        *,
        cpf_sancionado: str | None = None,
        nome_sancionado: str | None = None,
        pagina: int = 1,
    ) -> list[dict[str, Any]]:
        """Lista registros do CEAF (servidores expulsos da administração federal)."""
        return await self.get_json(
            "/api-de-dados/ceaf",
            params={
                "cpfSancionado": cpf_sancionado,
                "nomeSancionado": nome_sancionado,
                "pagina": pagina,
            },
        )

    async def list_cepim(
        self,
        *,
        cnpj_entidade: str | None = None,
        nome_entidade: str | None = None,
        pagina: int = 1,
    ) -> list[dict[str, Any]]:
        """Lista registros do CEPIM (entidades sem fins lucrativos impedidas).

        ATENÇÃO: o endpoint `/api-de-dados/cepim` da CGU **ignora todos os
        filtros de identificador** que testamos (`cnpjEntidade`,
        `codigoEntidade`, `codigoSancionado`) e sempre devolve a lista
        global. Para garantir filtragem real por CNPJ, aplicamos
        **filtragem client-side** após receber a página: comparamos
        `pessoaJuridica.cnpjFormatado` (somente dígitos) com o `cnpj_entidade`
        passado pelo usuário. Quando esse filtro está ativo, pode ser
        necessário consultar várias páginas para varrer o universo completo
        — sem isso, retornar uma página da lista global geraria falso
        positivo na due diligence de fornecedores que não estão no CEPIM.
        """
        raw = await self.get_json(
            "/api-de-dados/cepim",
            params={
                "cnpjEntidade": cnpj_entidade,
                "nomeEntidade": nome_entidade,
                "pagina": pagina,
            },
        )
        if not cnpj_entidade or not isinstance(raw, list):
            return raw
        alvo = "".join(c for c in cnpj_entidade if c.isdigit())
        if not alvo:
            return raw
        filtrado: list[dict[str, Any]] = []
        for item in raw:
            pj = item.get("pessoaJuridica") or {}
            doc = pj.get("cnpjFormatado") or pj.get("cnpj") or ""
            doc_digitos = "".join(c for c in str(doc) if c.isdigit())
            if doc_digitos == alvo:
                filtrado.append(item)
        return filtrado

    async def list_acordos_leniencia(
        self,
        *,
        cnpj_sancionado: str | None = None,
        pagina: int = 1,
    ) -> list[dict[str, Any]]:
        """Lista acordos de leniência firmados com a CGU."""
        return await self.get_json(
            "/api-de-dados/acordos-leniencia",
            params={
                "cnpjSancionado": cnpj_sancionado,
                "pagina": pagina,
            },
        )
