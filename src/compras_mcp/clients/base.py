"""Cliente HTTP base assíncrono com retry, logging estruturado e helpers
comuns às APIs do Compras.gov.br.

As 4 APIs upstream têm convenções diferentes de paginação e datas:

- Dados Abertos Compras:
  - pagina + tamanhoPagina
  - resposta: {resultado: [...], totalRegistros, totalPaginas, paginasRestantes}
  - datas: YYYY-MM-DD

- PNCP:
  - pagina (>=1) + tamanhoPagina (10..500)
  - resposta: {data: [...], totalRegistros, totalPaginas, numeroPagina, paginasRestantes, empty}
  - datas: yyyyMMdd (SEM hifens!)

- Portal da Transparência (CGU):
  - paginação por `pagina` (1-based), tamanho fixo da API (~100)
  - header `chave-api-dados`
  - 30 req/min — confiar em retry/backoff

- Comprasnet Contratos (v2 escopo):
  - offset + limit
  - Bearer JWT

`format_date` e `auto_paginate` lidam com isso transparentemente para que
as tools nunca precisem se preocupar.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any, Literal

import httpx
import structlog

from compras_mcp.errors import (
    ComprasHTTPError,
    ComprasNotFoundError,
    ComprasRateLimitError,
    ComprasServerError,
    ComprasTimeoutError,
)

DateFlavor = Literal["dados_abertos", "pncp", "comprasnet"]


def format_date(value: date | datetime | str | None, flavor: DateFlavor) -> str | None:
    """Converte uma data para o formato exigido pela API alvo.

    - dados_abertos: YYYY-MM-DD
    - pncp:          yyyyMMdd (sem separador)
    - comprasnet:    YYYY-MM-DD HH:mm:ss (default 00:00:00)

    Se `value` for string, tenta parsear ISO; se já estiver no formato esperado,
    devolve sem alteração. Retorna None se a entrada for None.
    """
    if value is None:
        return None

    if isinstance(value, str):
        # Tenta normalizar via fromisoformat; se falhar, devolve a string.
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            try:
                value = datetime.strptime(value, "%Y%m%d")
            except ValueError:
                return value

    if isinstance(value, datetime):
        d = value.date()
        dt = value
    else:
        d = value
        dt = datetime.combine(value, datetime.min.time())

    if flavor == "dados_abertos":
        return d.isoformat()
    if flavor == "pncp":
        return d.strftime("%Y%m%d")
    if flavor == "comprasnet":
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    raise ValueError(f"flavor desconhecido: {flavor}")


class BaseAsyncClient:
    """Cliente HTTP assíncrono com retries para timeout/5xx e logging.

    Não é um context manager: tools curtas instanciam, chamam `get_json`
    e descartam. Para sequências de chamadas (tools compostas), use
    `async with client:` para reaproveitar a conexão TCP.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_name: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_name = api_name
        self.timeout = timeout
        self.max_retries = max_retries
        self._default_headers = {
            "Accept": "application/json",
            "User-Agent": "compras-mcp/0.2.0 (+https://github.com/opedrosoares/MCP_Compras)",
            **(headers or {}),
        }
        self._client: httpx.AsyncClient | None = None
        self.log = structlog.get_logger(__name__).bind(api=api_name)

    async def __aenter__(self) -> BaseAsyncClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._default_headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._default_headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        """GET com retry exponencial em timeout/5xx/429.

        Levanta:
        - ComprasNotFoundError em 404
        - ComprasRateLimitError em 429 após esgotar retries
        - ComprasServerError em 5xx após esgotar retries
        - ComprasTimeoutError em timeout após esgotar retries
        - ComprasHTTPError em outros erros HTTP

        Overrides opcionais por chamada (úteis em endpoints singulares
        determinísticos, onde retry de timeout só acumula latência):
        - `max_retries`: substitui o default da instância (ex.: 0 para
          falhar rápido em 4xx persistentes)
        - `timeout`: substitui o default da instância (ex.: 15s em vez
          de 60s para singular)
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        client = await self._ensure_client()
        retries = self.max_retries if max_retries is None else max_retries
        per_call_timeout = timeout if timeout is not None else None

        get_kwargs: dict[str, Any] = {"params": clean_params}
        if per_call_timeout is not None:
            get_kwargs["timeout"] = per_call_timeout

        for attempt in range(retries + 1):
            try:
                resp = await client.get(path, **get_kwargs)
            except httpx.TimeoutException as e:
                self.log.warning(
                    "client.timeout",
                    path=path,
                    attempt=attempt,
                    max=retries,
                )
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ComprasTimeoutError(
                    f"Timeout chamando {self.api_name}{path}: {e}"
                ) from e
            except httpx.HTTPError as e:
                raise ComprasHTTPError(
                    f"Erro de transporte HTTP em {self.api_name}{path}: {e}"
                ) from e

            if resp.status_code == 404:
                raise ComprasNotFoundError(
                    f"Recurso nao encontrado em {self.api_name}{path}"
                )
            if resp.status_code == 429:
                if attempt < self.max_retries:
                    retry_after = float(resp.headers.get("Retry-After", "2"))
                    self.log.warning(
                        "client.rate_limit",
                        path=path,
                        retry_after=retry_after,
                        attempt=attempt,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                raise ComprasRateLimitError(
                    f"Rate limit em {self.api_name}{path} (quota da API)"
                )
            if 500 <= resp.status_code < 600:
                if attempt < self.max_retries:
                    self.log.warning(
                        "client.server_error",
                        path=path,
                        status=resp.status_code,
                        attempt=attempt,
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ComprasServerError(
                    f"{self.api_name}{path} respondeu {resp.status_code}: {resp.text[:300]}"
                )
            if not resp.is_success:
                raise ComprasHTTPError(
                    f"{self.api_name}{path} respondeu {resp.status_code}: {resp.text[:300]}"
                )

            try:
                data = resp.json()
            except ValueError as e:
                raise ComprasHTTPError(
                    f"Resposta nao-JSON de {self.api_name}{path}: {resp.text[:200]}"
                ) from e

            self.log.info(
                "client.ok",
                path=path,
                status=resp.status_code,
                bytes=len(resp.content),
            )
            return data

        # Inalcançável (raises acima já cobrem todos os caminhos)
        raise ComprasHTTPError(f"Esgotou retries em {self.api_name}{path}")


async def auto_paginate(
    fetcher,  # callable: (pagina:int, tamanho_pagina:int) -> awaitable[dict]
    *,
    extract_items,  # callable: (response_dict) -> list
    extract_total: str | callable = "totalPaginas",
    inicio: int = 1,
    tamanho_pagina: int = 100,
    max_paginas: int = 10,
) -> list[dict[str, Any]]:
    """Chama `fetcher(pagina, tamanho_pagina)` em loop até max_paginas ou fim.

    Útil em tools compostas que precisam agregar (ex.: pesquisar_precos_para_etp).
    Não usado em tools "listar_*" individuais — essas devolvem 1 página por vez
    com `_proxima_pagina` no payload para o LLM decidir continuar.
    """
    items: list[dict[str, Any]] = []
    pagina = inicio
    total_paginas: int | None = None
    while pagina < inicio + max_paginas:
        resp = await fetcher(pagina, tamanho_pagina)
        batch = extract_items(resp)
        if not batch:
            break
        items.extend(batch)
        if total_paginas is None:
            if callable(extract_total):
                total_paginas = extract_total(resp)
            else:
                total_paginas = resp.get(extract_total)
        if total_paginas is not None and pagina >= total_paginas:
            break
        pagina += 1
    return items
