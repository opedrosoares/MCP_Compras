"""Tools de pesquisa de preço (Dados Abertos /modulo-pesquisa-preco/*).

Esta é a base do ETP: a IN SEGES/ME 65/2021 art. 5 exige amostragem de
preços praticados em contratações públicas. Cobre material (CATMAT) e
serviço (CATSER) em dois níveis:

- `compras_pesquisar_preco_*`: visão agregada por UASG/UF/município com
  filtros amplos.
- `compras_detalhar_preco_*`: unitários por compra individual no período.

Cache TTL curto (10min) — novas homologações entram diariamente.
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.config import get_settings
from compras_mcp.errors import ComprasNotFoundError
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    PesquisarPrecoMaterialInput,
    PesquisarPrecoServicoInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    make_dados_abertos,
    with_latency,
)


_precos_cache = cache_from_env("PRECOS", default_ttl=600, default_max_size=200)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _filtros_comuns(
    *,
    data_inicio: date | None,
    data_fim: date | None,
    uf: str | None,
    codigo_municipio: int | None,
    codigo_uasg: int | None,
) -> dict[str, Any]:
    """Filtros que material e serviço compartilham (data, UF, município, UASG).

    O que **não** é comum é a identificação do item: serviço continua em
    `codigoItemCatalogo`; material migrou para `tipo` + `codigo` em 2026-08.
    """
    return {
        "dataCompraInicio": format_date(data_inicio, "dados_abertos"),
        "dataCompraFim": format_date(data_fim, "dados_abertos"),
        "estado": uf.upper() if uf else None,
        "codigoMunicipio": codigo_municipio,
        "codigoUasg": str(codigo_uasg) if codigo_uasg is not None else None,
    }


def _params_material(codigo_item_catalogo: int, **filtros: Any) -> dict[str, Any]:
    """Identificação do item na rota 1 conforme o contrato vigente.

    Até 2026-07 a rota aceitava `codigoItemCatalogo=<int>`. Hoje o contrato
    exige o par discriminador `tipo` + `codigo`, onde `tipo` é o enum
    `EnumPesquisaPreco` (`codigoItemCatalogo` | `codigoPdm`). Mandar o
    parâmetro antigo devolve **404**, não 400 — foi assim que a quebra
    passou despercebida. Ver CHANGELOG 0.3.13.
    """
    return {
        "tipo": "codigoItemCatalogo",
        "codigo": str(codigo_item_catalogo),
        **filtros,
    }


def _resposta_rota_indisponivel(
    endpoint_path: str,
    *,
    codigo_item_catalogo: int,
    contexto: str = "",
) -> dict[str, Any]:
    """Envelope informativo quando a rota de preço devolve 404.

    Mesmo padrão de `compras_uasg_listar`: em vez de propagar
    `ComprasNotFoundError` (que o LLM lê como "item inexistente" e repassa
    ao analista como se não houvesse preço no mercado), devolvemos o
    diagnóstico e as alternativas de fonte.

    Um 404 nesta família quase nunca significa "não há dados": significa
    que a assinatura de query mudou de novo. Rode
    `python scripts/probe_upstream.py --modulo pesquisa_preco` para
    confirmar em 10 segundos.
    """
    return {
        "resultado": [],
        "_total_registros": 0,
        "_pagina_atual": 1,
        "_total_paginas": 0,
        "_proxima_pagina": None,
        "_cache_hit": False,
        "_erro_upstream": {
            "endpoint": f"dados_abertos{endpoint_path}",
            "status": 404,
            "diagnostico": (
                "A rota respondeu 404. Nesta API isso indica parâmetro "
                "obrigatório ausente ou assinatura de query alterada pela "
                "SEGES — não 'item sem preços'. Em 2026-08 a rota 1 trocou "
                "`codigoItemCatalogo` por `tipo`+`codigo` exatamente assim. "
                f"{contexto}"
            ).strip(),
            "verificar_com": "python scripts/probe_upstream.py --modulo pesquisa_preco",
            "alternativas": [
                f"`compras_detalhar_preco_material(codigo_item_catalogo={codigo_item_catalogo})` "
                "— lista as compras do item (sem valor unitário, ver docstring).",
                "`compras_arp_itens_listar(...)` — atas de registro de preço trazem "
                "`valorUnitario` homologado e servem de parâmetro para o ETP.",
                "`compras_contratacoes_14133_resultados_listar(...)` — resultados "
                "homologados por item, com valor e fornecedor.",
                "Painel de Preços (paineldeprecos.planejamento.gov.br) para "
                "conferência manual enquanto a rota não volta.",
            ],
        },
    }


# ============================================================================
# Pesquisa de preço — MATERIAL (CATMAT)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pesquisar_preco_material(
    codigo_item_catalogo: Annotated[
        int, Field(description=desc(PesquisarPrecoMaterialInput, "codigo_item_catalogo"))
    ],
    data_inicio: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoMaterialInput, "data_inicio")),
    ] = None,
    data_fim: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoMaterialInput, "data_fim")),
    ] = None,
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(PesquisarPrecoMaterialInput, "uf")),
    ] = None,
    codigo_municipio: Annotated[
        int | None,
        Field(
            default=None,
            description=desc(PesquisarPrecoMaterialInput, "codigo_municipio"),
        ),
    ] = None,
    codigo_uasg: Annotated[
        int | None,
        Field(default=None, description=desc(PesquisarPrecoMaterialInput, "codigo_uasg")),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoMaterialInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoMaterialInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Pesquisa preços praticados em compras de material (CATMAT) pelo governo.

    Endpoint Dados Abertos: `/modulo-pesquisa-preco/1_consultarMaterial`.
    Para visão consolidada estatística (média/mediana no padrão IN 65/2021),
    use a tool composta `compras_pesquisar_precos_para_etp`.

    Cada item da resposta traz `precoUnitario`, `quantidade`, `dataCompra`,
    `niFornecedor`/`nomeFornecedor` e a UASG compradora — é **esta** a tool
    que devolve valor unitário para material. A `compras_detalhar_preco_material`
    NÃO devolve preço (ver a docstring dela).

    **⚠️ Quebra upstream corrigida em 2026-08-05**: entre ~2026-07 e
    2026-08-05 esta tool respondia "Recurso nao encontrado" (HTTP 404). A
    SEGES trocou a assinatura de query da rota sem versionar: o parâmetro
    `codigoItemCatalogo` foi substituído pelo par `tipo` (enum
    `codigoItemCatalogo` | `codigoPdm`) + `codigo`. Como a API responde
    **404** — e não 400 — a parâmetros obrigatórios ausentes, a quebra se
    disfarçou de "rota removida". A rota nunca saiu do swagger oficial.
    Corrigido na v0.3.13; a assinatura de `compras_pesquisar_preco_servico`
    (rota 3) não mudou.

    Se voltar a devolver 404, a tool não levanta exception: devolve
    `_erro_upstream` com diagnóstico e alternativas.

    Cache 10 min.
    """
    started = time.perf_counter()
    key = _ck(
        "preco_material",
        codigo_item_catalogo,
        data_inicio,
        data_fim,
        uf,
        codigo_municipio,
        codigo_uasg,
        pagina,
        tamanho_pagina,
    )
    cached = await _precos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    params = _params_material(
        codigo_item_catalogo,
        **_filtros_comuns(
            data_inicio=data_inicio,
            data_fim=data_fim,
            uf=uf,
            codigo_municipio=codigo_municipio,
            codigo_uasg=codigo_uasg,
        ),
    )
    try:
        async with make_dados_abertos(get_settings()) as client:
            resp = await client.list_resource(
                "/modulo-pesquisa-preco/1_consultarMaterial",
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
                **params,
            )
    except ComprasNotFoundError:
        return with_latency(
            _resposta_rota_indisponivel(
                "/modulo-pesquisa-preco/1_consultarMaterial",
                codigo_item_catalogo=codigo_item_catalogo,
            ),
            started,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _precos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_detalhar_preco_material(
    codigo_item_catalogo: Annotated[
        int, Field(description=desc(PesquisarPrecoMaterialInput, "codigo_item_catalogo"))
    ],
    data_inicio: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoMaterialInput, "data_inicio")),
    ] = None,
    data_fim: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoMaterialInput, "data_fim")),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoMaterialInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoMaterialInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista as compras individuais de um item CATMAT — **sem valor de preço**.

    Endpoint: `/modulo-pesquisa-preco/2_consultarMaterialDetalhe`.

    **⚠️ Esta tool não devolve preço.** Até a v0.3.12 a docstring prometia
    "valor unitário homologado"; auditoria de 2026-08-05 mostrou que o DTO
    upstream (`FtPesqPrecoCompraMaterialDetalheDTO`) tem exatamente 7 campos
    e nenhum deles é valor:

        idCompra, idItemCompra, numeroItemCompra, codigoItemCatalogo,
        objetoCompra, descricaoDetalhadaItem, dataAtualizacaoFato

    Confirmado nos dois sentidos: chamada crua ao upstream (fora da camada
    do MCP) devolve as mesmas 7 chaves, e o contrato OpenAPI oficial
    declara as mesmas 7. Ou seja: **não somos nós que filtramos** — o campo
    nunca existiu nesta rota. A rota 4 (serviço detalhe) tem DTO idêntico.

    **Para preço unitário de material use `compras_pesquisar_preco_material`**,
    que devolve `precoUnitario`, `quantidade`, `dataCompra` e fornecedor por
    compra — é a fonte correta para a amostragem da IN SEGES/ME 65/2021.

    Use esta tool apenas para: descrição detalhada do item como comprado,
    objeto da compra e rastreio do `idCompra` para cruzar com outras bases.

    Cache 10 min.
    """
    started = time.perf_counter()
    key = _ck(
        "preco_material_detalhe",
        codigo_item_catalogo,
        data_inicio,
        data_fim,
        pagina,
        tamanho_pagina,
    )
    cached = await _precos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-pesquisa-preco/2_consultarMaterialDetalhe",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            codigoItemCatalogo=codigo_item_catalogo,
            dataCompraInicio=format_date(data_inicio, "dados_abertos"),
            dataCompraFim=format_date(data_fim, "dados_abertos"),
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _precos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


# ============================================================================
# Pesquisa de preço — SERVIÇO (CATSER)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pesquisar_preco_servico(
    codigo_item_catalogo: Annotated[
        int, Field(description=desc(PesquisarPrecoServicoInput, "codigo_item_catalogo"))
    ],
    data_inicio: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "data_inicio")),
    ] = None,
    data_fim: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "data_fim")),
    ] = None,
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "uf")),
    ] = None,
    codigo_municipio: Annotated[
        int | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "codigo_municipio")),
    ] = None,
    codigo_uasg: Annotated[
        int | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "codigo_uasg")),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoServicoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoServicoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Pesquisa preços praticados em compras de serviço (CATSER).

    Endpoint: `/modulo-pesquisa-preco/3_consultarServico`. Para visão
    consolidada (mediana, média, desvio no padrão IN 65/2021), use a tool
    composta `compras_pesquisar_precos_para_etp` com tipo='servico'.
    """
    started = time.perf_counter()
    key = _ck(
        "preco_servico",
        codigo_item_catalogo,
        data_inicio,
        data_fim,
        uf,
        codigo_municipio,
        codigo_uasg,
        pagina,
        tamanho_pagina,
    )
    cached = await _precos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    # Rota 3 mantém `codigoItemCatalogo` — a troca para `tipo`+`codigo`
    # de 2026-08 atingiu só a rota de material.
    params = {
        "codigoItemCatalogo": codigo_item_catalogo,
        **_filtros_comuns(
            data_inicio=data_inicio,
            data_fim=data_fim,
            uf=uf,
            codigo_municipio=codigo_municipio,
            codigo_uasg=codigo_uasg,
        ),
    }
    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-pesquisa-preco/3_consultarServico",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **params,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _precos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_detalhar_preco_servico(
    codigo_item_catalogo: Annotated[
        int, Field(description=desc(PesquisarPrecoServicoInput, "codigo_item_catalogo"))
    ],
    data_inicio: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "data_inicio")),
    ] = None,
    data_fim: Annotated[
        date | None,
        Field(default=None, description=desc(PesquisarPrecoServicoInput, "data_fim")),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoServicoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(PesquisarPrecoServicoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista as compras individuais de um serviço CATSER — **sem valor de preço**.

    Endpoint: `/modulo-pesquisa-preco/4_consultarServicoDetalhe`.

    **⚠️ Esta tool não devolve preço** (verificado 2026-08-05): o DTO
    upstream é idêntico ao da rota 2 — idCompra, idItemCompra,
    numeroItemCompra, codigoItemCatalogo, objetoCompra,
    descricaoDetalhadaItem, dataAtualizacaoFato. Nenhum campo de valor.

    **Para preço unitário de serviço use `compras_pesquisar_preco_servico`**,
    que devolve `precoUnitario` e fornecedor por compra.

    Cache 10 min.
    """
    started = time.perf_counter()
    key = _ck(
        "preco_servico_detalhe",
        codigo_item_catalogo,
        data_inicio,
        data_fim,
        pagina,
        tamanho_pagina,
    )
    cached = await _precos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-pesquisa-preco/4_consultarServicoDetalhe",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            codigoItemCatalogo=codigo_item_catalogo,
            dataCompraInicio=format_date(data_inicio, "dados_abertos"),
            dataCompraFim=format_date(data_fim, "dados_abertos"),
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _precos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
