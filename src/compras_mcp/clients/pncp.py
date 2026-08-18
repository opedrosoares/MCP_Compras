"""Cliente HTTP para o PNCP — Portal Nacional de Contratações Públicas.

Base: https://pncp.gov.br/api/consulta
Docs: https://pncp.gov.br/api/consulta/swagger-ui/index.html

Pública para consultas (manutenção exige Bearer JWT, fora de escopo).
Resposta padrão: {data: [...], totalRegistros, totalPaginas, numeroPagina,
paginasRestantes, empty}.
Datas no formato yyyyMMdd (SEM hifens).
"""

from __future__ import annotations

from typing import Any

from compras_mcp.clients.base import BaseAsyncClient


class PNCPClient(BaseAsyncClient):
    """Cliente para pncp.gov.br/api/consulta.

    Endpoints principais (v1):
    - /v1/pca/             — Plano de Contratações Anual
    - /v1/contratacoes/publicacao — contratações publicadas no período
    - /v1/contratacoes/proposta  — contratações com prazo aberto agora
    - /v1/contratacoes/atualizacao — atualizações no período
    - /v1/orgaos/{cnpj}/compras/{ano}/{sequencial} — contratação singular
    - /v1/contratos/       — contratos
    - /v1/atas/            — atas de registro de preço
    """

    def __init__(self, *, base_url: str, timeout: float = 60.0, max_retries: int = 2) -> None:
        super().__init__(
            base_url=base_url,
            api_name="pncp",
            timeout=timeout,
            max_retries=max_retries,
        )

    async def list_resource(
        self,
        path: str,
        *,
        pagina: int = 1,
        tamanho_pagina: int = 50,
        max_retries: int | None = None,
        timeout: float | None = None,
        **filtros: Any,
    ) -> dict[str, Any]:
        """GET genérico para endpoints de listagem do PNCP.

        Tamanho de página: mínimo 10, máximo **50** (validado empiricamente
        contra o PNCP — valores como 100/200/500 são rejeitados com 400
        "Tamanho de página inválido"). Schemas antigos sugeriam até 500;
        o endpoint atual recusa qualquer coisa acima de 50.

        Aceita overrides de `max_retries` e `timeout` para tools que fazem
        muitas chamadas paralelas e preferem falhar rápido (ex.: aggregate
        com fan-out de buckets — retry acumulado por timeout estoura
        minutos sem trazer dado novo).
        """
        if tamanho_pagina < 10:
            tamanho_pagina = 10
        elif tamanho_pagina > 50:
            tamanho_pagina = 50
        params = {
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
            **filtros,
        }
        return await self.get_json(
            path,
            params=params,
            max_retries=max_retries,
            timeout=timeout,
        )

    async def get_resource(self, path: str, **params: Any) -> dict[str, Any]:
        """GET para endpoints de consulta singular.

        **Fast-fail**: usa `max_retries=0` e `timeout=20s` em vez do default
        do cliente (2 retries × 60s = até 180s acumulados). Endpoints
        singulares do PNCP retornam respostas determinísticas (200 ou 4xx);
        retry de timeout sobre eles só acumula latência sem chance de
        sucesso. Descoberto na 7ª bateria E2E: CNPJ inválido demorava
        ~182s para retornar 400 porque o upstream timeou e o cliente
        retentou 3 vezes.
        """
        return await self.get_json(path, params=params, max_retries=0, timeout=20)
