"""Cliente HTTP para a API Dados Abertos do Compras.gov.br.

Base: https://dadosabertos.compras.gov.br
Docs: https://dadosabertos.compras.gov.br/swagger-ui/index.html
OAS:  https://dadosabertos.compras.gov.br/v3/api-docs

Pública (não exige autenticação para a quase totalidade dos endpoints).
Resposta padrão: {resultado: [...], totalRegistros, totalPaginas, paginasRestantes}.
Datas no formato YYYY-MM-DD.
"""

from __future__ import annotations

from typing import Any

from compras_mcp.clients.base import BaseAsyncClient


class DadosAbertosClient(BaseAsyncClient):
    """Cliente para dadosabertos.compras.gov.br.

    Os módulos cobertos pela v1:
    - 01 Catálogo Material (CATMAT): /modulo-material/...
    - 02 Catálogo Serviço (CATSER): /modulo-servico/...
    - 03 Pesquisa de Preço: /modulo-pesquisa-preco/...
    - 04 PGC: /modulo-pgc/...
    - 05 UASG: /modulo-uasg/...
    - 06 Legado 8.666: /modulo-legado/...
    - 07 Contratações 14.133: /modulo-contratacoes/...
    - 08 ARP (Atas): /modulo-uasg/... e /modulo-comprasnet/...
    - 09 Contratos: /modulo-contratos/...
    - 10 Fornecedor: /modulo-fornecedor/...
    - 11 OCDS: /ocds/...
    """

    def __init__(self, *, base_url: str, timeout: float = 60.0, max_retries: int = 2) -> None:
        super().__init__(
            base_url=base_url,
            api_name="dados_abertos",
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
        """GET genérico para endpoints de listagem do Dados Abertos.

        Retorna o payload bruto com `resultado`, `totalRegistros`, `totalPaginas`,
        `paginasRestantes` para que a tool decida como apresentar/paginar.

        Clamp interno: Dados Abertos exige `tamanhoPagina` no intervalo 10–500.
        Tools `consultar_*` que passariam 1 são silenciosamente promovidas a 10.

        Aceita overrides de `max_retries` e `timeout` para tools que
        preferem falhar rápido em vez de acumular retries (ex.:
        `arp_saldo_item` em ata vazia — antes 50s+ por causa de 2 retries
        no timeout default).
        """
        if tamanho_pagina < 10:
            tamanho_pagina = 10
        elif tamanho_pagina > 500:
            tamanho_pagina = 500
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
        """GET para endpoints de consulta singular (por código/id)."""
        return await self.get_json(path, params=params)
