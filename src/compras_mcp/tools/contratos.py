"""Tools de contratos: Dados Abertos (visão SIASG) + Comprasnet (execução).

Endpoints cobertos (parâmetros conforme `/v3/api-docs` do upstream):

Dados Abertos:
- /modulo-contratos/1_consultarContratos              — exige codigoOrgao + dataVigenciaInicialMin/Max
- /modulo-contratos/1.1_consultarContratos_Id         — exige tipo + codigo
- /modulo-contratos/1.2_consultarContratos_FimVigencia — exige codigoOrgao + dataVigenciaFinalMin/Max
- /modulo-contratos/2_consultarContratosItem          — exige codigoOrgao + dataVigenciaInicialMin/Max
- /modulo-contratos/2.1_consultarContratosItem_Id     — exige tipo + codigo

Comprasnet (rotas abertas /api/*):
- /api/contrato/id/{id}                — detalhe
- /api/contrato/ug/{uasg}              — por UASG (resposta NÃO paginada no upstream — fatiamos client-side)
- /api/contrato/{id}/historico         — aditivos
- /api/contrato/{id}/garantias         — garantias contratuais
- /api/contrato/{id}/faturas           — NF/faturas
- /api/contrato/{id}/ocorrencias       — penalidades de execução
- /api/contrato/{id}/responsaveis      — fiscais/gestores (CPF mascarado por LGPD)
- /api/contrato/{id}/empenhos          — empenhos vinculados
- /api/contrato/{id}/publicacoes       — publicações DOU
- /api/contrato/{id}/cronograma        — cronograma financeiro

Cache TTL 15 min.
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field

from compras_mcp.access_control import apply_lgpd, aviso_lgpd
from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.config import get_settings
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import ListarPaginadoInput
from compras_mcp.tools._helpers import (
    desc,
    envelope_comprasnet,
    envelope_dados_abertos,
    make_comprasnet,
    make_dados_abertos,
    with_latency,
)


_contratos_cache = cache_from_env(
    "CONTRATOS", default_ttl=900, default_max_size=300
)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


# ============================================================================
# Dados Abertos — visão SIASG
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratos_listar(
    codigo_orgao: Annotated[
        int,
        Field(
            description=(
                "Código do órgão (obrigatório no upstream). Use "
                "`compras_orgao_listar` para descobrir."
            )
        ),
    ],
    data_vigencia_inicial_min: Annotated[
        date,
        Field(
            description=(
                "Data MÍNIMA de início de vigência do contrato (YYYY-MM-DD). "
                "Janela max ≤ 365 dias até data_vigencia_inicial_max."
            )
        ),
    ],
    data_vigencia_inicial_max: Annotated[
        date,
        Field(description="Data MÁXIMA de início de vigência (YYYY-MM-DD)."),
    ],
    codigo_unidade_gestora: Annotated[
        int | None,
        Field(default=None, description="Filtra pela UASG gestora do contrato."),
    ] = None,
    numero_contrato: Annotated[
        str | None,
        Field(default=None, description="Filtra por número do contrato (ex.: '00031/2015')."),
    ] = None,
    codigo_modalidade_compra: Annotated[
        int | None,
        Field(default=None, description="Modalidade da compra que originou o contrato."),
    ] = None,
    ni_fornecedor: Annotated[
        str | None,
        Field(
            default=None,
            description="CPF/CNPJ do fornecedor (apenas dígitos). Opcional.",
        ),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratos federais (Dados Abertos /modulo-contratos/1).

    O upstream exige `codigoOrgao` + janela `dataVigenciaInicialMin/Max`
    (≤ 365 dias). Para sub-recursos detalhados (garantias, faturas,
    ocorrências), use `compras_contrato_*` que consulta o Comprasnet.

    Cache 15 min.
    """
    started = time.perf_counter()
    ni_clean = "".join(c for c in ni_fornecedor if c.isdigit()) if ni_fornecedor else None
    key = _ck(
        "contratos_listar",
        codigo_orgao,
        data_vigencia_inicial_min,
        data_vigencia_inicial_max,
        codigo_unidade_gestora,
        numero_contrato,
        codigo_modalidade_compra,
        ni_clean,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "codigoOrgao": codigo_orgao,
        "dataVigenciaInicialMin": format_date(data_vigencia_inicial_min, "dados_abertos"),
        "dataVigenciaInicialMax": format_date(data_vigencia_inicial_max, "dados_abertos"),
    }
    if codigo_unidade_gestora is not None:
        filtros["codigoUnidadeGestora"] = codigo_unidade_gestora
    if numero_contrato:
        filtros["numeroContrato"] = numero_contrato
    if codigo_modalidade_compra is not None:
        filtros["codigoModalidadeCompra"] = codigo_modalidade_compra
    if ni_clean:
        filtros["niFornecedor"] = ni_clean

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratos/1_consultarContratos",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratos_consultar(
    codigo: Annotated[
        str,
        Field(
            description=(
                "Identificador do contrato no upstream — interpretação depende "
                "de `tipo`. Para tipo='idCompra' é o id da compra (string numérica). "
                "Para tipo='numeroControlePncpContrato' é o número de controle "
                "PNCP completo (ex.: '00000000000000-1-000001/2024')."
            )
        ),
    ],
    tipo: Annotated[
        Literal["idCompra", "numeroControlePncpContrato"],
        Field(
            description=(
                "Como interpretar `codigo`: 'idCompra' (id interno da compra) "
                "ou 'numeroControlePncpContrato' (identificador PNCP)."
            )
        ),
    ] = "numeroControlePncpContrato",
) -> dict[str, Any]:
    """Consulta um contrato no Dados Abertos (endpoint 1.1).

    O upstream exige `codigo + tipo`. Tipos aceitos pela API:
    `idCompra` e `numeroControlePncpContrato`.

    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck("contratos_consultar", codigo, tipo)
    cached = await _contratos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratos/1.1_consultarContratos_Id",
            pagina=1,
            tamanho_pagina=10,
            codigo=codigo,
            tipo=tipo,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": codigo,
        "tipo": tipo,
        "contrato": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _contratos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratos_listar_por_fim_vigencia(
    codigo_orgao: Annotated[
        int,
        Field(description="Código do órgão (obrigatório)."),
    ],
    data_vigencia_final_min: Annotated[
        date,
        Field(description="Data MÍNIMA de fim de vigência (YYYY-MM-DD). Janela ≤ 365 dias."),
    ],
    data_vigencia_final_max: Annotated[
        date,
        Field(description="Data MÁXIMA de fim de vigência (YYYY-MM-DD)."),
    ],
    codigo_unidade_gestora: Annotated[
        int | None,
        Field(default=None, description="UASG gestora (opcional)."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratos com vencimento na janela informada (endpoint 1.2).

    Inventário do que precisa renovar. Upstream exige `codigoOrgao` +
    `dataVigenciaFinalMin/Max` (≤ 365 dias). Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck(
        "contratos_fim_vig",
        codigo_orgao,
        data_vigencia_final_min,
        data_vigencia_final_max,
        codigo_unidade_gestora,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "codigoOrgao": codigo_orgao,
        "dataVigenciaFinalMin": format_date(data_vigencia_final_min, "dados_abertos"),
        "dataVigenciaFinalMax": format_date(data_vigencia_final_max, "dados_abertos"),
    }
    if codigo_unidade_gestora is not None:
        filtros["codigoUnidadeGestora"] = codigo_unidade_gestora

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratos/1.2_consultarContratos_FimVigencia",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratos_itens_listar(
    codigo_orgao: Annotated[
        int,
        Field(description="Código do órgão (obrigatório)."),
    ],
    data_vigencia_inicial_min: Annotated[
        date,
        Field(description="Data mínima de início de vigência (YYYY-MM-DD). Janela ≤ 365 dias."),
    ],
    data_vigencia_inicial_max: Annotated[
        date,
        Field(description="Data máxima de início de vigência (YYYY-MM-DD)."),
    ],
    codigo_item: Annotated[
        int | None,
        Field(default=None, description="Código CATMAT ou CATSER (opcional)."),
    ] = None,
    tipo_item: Annotated[
        str | None,
        Field(default=None, description="Tipo do item: 'M' (material) ou 'S' (serviço)."),
    ] = None,
    ni_fornecedor: Annotated[
        str | None,
        Field(default=None, description="CPF/CNPJ do fornecedor."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de contratos (endpoint 2).

    Upstream exige `codigoOrgao + dataVigenciaInicialMin/Max`. Cache 15 min.
    """
    started = time.perf_counter()
    ni_clean = "".join(c for c in ni_fornecedor if c.isdigit()) if ni_fornecedor else None
    key = _ck(
        "contratos_itens",
        codigo_orgao,
        data_vigencia_inicial_min,
        data_vigencia_inicial_max,
        codigo_item,
        tipo_item,
        ni_clean,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "codigoOrgao": codigo_orgao,
        "dataVigenciaInicialMin": format_date(data_vigencia_inicial_min, "dados_abertos"),
        "dataVigenciaInicialMax": format_date(data_vigencia_inicial_max, "dados_abertos"),
    }
    if codigo_item is not None:
        filtros["codigoItem"] = codigo_item
    if tipo_item:
        filtros["tipoItem"] = tipo_item.upper()
    if ni_clean:
        filtros["niFornecedor"] = ni_clean

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratos/2_consultarContratosItem",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


# ============================================================================
# Comprasnet Contratos — execução contratual (rotas abertas /api/*)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_comprasnet_consultar(
    id_contrato: Annotated[
        int,
        Field(
            description=(
                "ID interno do contrato no Comprasnet (pode ser diferente do id "
                "no Dados Abertos). Obtenha em `compras_contrato_comprasnet_por_uasg`."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Consulta detalhe completo de um contrato no Comprasnet (/api/contrato/id/{id}).

    Devolve contrato com sub-recursos embutidos. CPFs mascarados por LGPD.
    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck("ct_cnet_consultar", id_contrato)
    cached = await _contratos_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()
    async with make_comprasnet(settings) as client:
        resp = await client.get_one(f"/contrato/id/{id_contrato}")

    payload: dict[str, Any] = {
        "encontrado": bool(resp),
        "codigo_consultado": id_contrato,
        "contrato": apply_lgpd(resp, incluir_cpf_completo=settings.incluir_cpf_completo),
        "_aviso_lgpd": aviso_lgpd(),
        "_cache_hit": False,
    }
    await _contratos_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_comprasnet_por_uasg(
    codigo_uasg: Annotated[
        int, Field(description="Código UASG (5-6 dígitos).")
    ],
    ativos: Annotated[
        bool,
        Field(
            description=(
                "Se True (padrão), lista apenas contratos ativos. "
                "Set False para incluir inativos via /api/contrato/inativo/ug/{uasg}."
            ),
        ),
    ] = True,
    pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratos de uma UASG no Comprasnet.

    **Atenção**: o upstream `/api/contrato/ug/{uasg}` não suporta paginação —
    devolve a lista completa em uma resposta única (pode passar de 1 MB). Esta
    tool fatia o resultado client-side conforme `pagina + tamanho_pagina` para
    evitar inundar o LLM.

    Cache 15 min do payload completo; fatiamento por chamada é barato.
    """
    started = time.perf_counter()
    path = f"/contrato/ug/{codigo_uasg}" if ativos else f"/contrato/inativo/ug/{codigo_uasg}"
    cache_key = _ck("ct_cnet_por_uasg_full", codigo_uasg, ativos)

    settings = get_settings()
    # Cache a resposta bruta inteira; fatiamento é puro client-side.
    full_resp = await _contratos_cache.get(cache_key)
    cached_full = full_resp is not None
    if full_resp is None:
        async with make_comprasnet(settings) as client:
            full_resp = await client.get_list(path)
        await _contratos_cache.set(
            cache_key, json.loads(json.dumps(full_resp, default=str))
        )

    # Normaliza: se vier dict com 'data', usa data; se vier lista, usa direto.
    if isinstance(full_resp, list):
        items = full_resp
    elif isinstance(full_resp, dict):
        items = full_resp.get("data") or []
    else:
        items = []

    total = len(items)
    inicio = (pagina - 1) * tamanho_pagina
    fim = inicio + tamanho_pagina
    fatia = items[inicio:fim]
    total_paginas = (total + tamanho_pagina - 1) // tamanho_pagina if total else 0
    proxima = pagina + 1 if pagina < total_paginas else None

    payload: dict[str, Any] = {
        "resultado": apply_lgpd(
            fatia, incluir_cpf_completo=settings.incluir_cpf_completo
        ),
        "_pagina_atual": pagina,
        "_total_paginas": total_paginas,
        "_total_registros": total,
        "_proxima_pagina": proxima,
        "_aviso_lgpd": aviso_lgpd(),
        "_aviso_paginacao": (
            "Upstream Comprasnet não suporta paginação — fatiamento feito no MCP."
        ),
        "_cache_hit": cached_full,
    }
    return with_latency(payload, started)


async def _ct_subrecurso(
    id_contrato: int,
    sufixo: str,
    cache_tag: str,
    pagina: int = 1,
    tamanho_pagina: int = 50,
) -> dict[str, Any]:
    """Helper interno para sub-recursos do contrato (mesma estrutura).

    O upstream Comprasnet **não suporta paginação** nesses endpoints —
    devolve a lista completa de uma vez. Aplicamos fatiamento client-side
    com cache do payload completo, mesmo padrão de `_por_uasg`.
    """
    started = time.perf_counter()
    full_key = _ck(f"{cache_tag}_full", id_contrato)
    cached_full = await _contratos_cache.get(full_key)
    is_cache_hit = cached_full is not None

    settings = get_settings()
    if cached_full is None:
        async with make_comprasnet(settings) as client:
            cached_full = await client.get_list(f"/contrato/{id_contrato}/{sufixo}")
        await _contratos_cache.set(
            full_key, json.loads(json.dumps(cached_full, default=str))
        )

    if isinstance(cached_full, list):
        items = cached_full
    elif isinstance(cached_full, dict):
        items = cached_full.get("data") or []
    else:
        items = []

    total = len(items)
    inicio = (pagina - 1) * tamanho_pagina
    fim = inicio + tamanho_pagina
    fatia = items[inicio:fim]
    total_paginas = (total + tamanho_pagina - 1) // tamanho_pagina if total else 0
    proxima = pagina + 1 if pagina < total_paginas else None

    payload: dict[str, Any] = {
        "resultado": apply_lgpd(
            fatia, incluir_cpf_completo=settings.incluir_cpf_completo
        ),
        "_pagina_atual": pagina,
        "_total_paginas": total_paginas,
        "_total_registros": total,
        "_proxima_pagina": proxima,
        "_aviso_lgpd": aviso_lgpd(),
        "_aviso_paginacao": (
            "Upstream Comprasnet não pagina este sub-recurso — fatiamento "
            "client-side."
        ),
        "_cache_hit": is_cache_hit,
    }
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_historico_aditivos(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista aditivos do contrato (/api/contrato/{id}/historico).

    Paginação client-side (upstream não pagina). Cache 15 min do payload completo.
    """
    return await _ct_subrecurso(id_contrato, "historico", "ct_historico", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_garantias(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista garantias contratuais (/api/contrato/{id}/garantias).

    Paginação client-side. Cache 15 min.
    """
    return await _ct_subrecurso(id_contrato, "garantias", "ct_garantias", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_faturas(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista NFs/faturas (/api/contrato/{id}/faturas).

    Paginação client-side. Cache 15 min. **Atenção LGPD**: o campo
    `infcomplementar` (texto livre) pode conter nome de servidor + matrícula
    SIAPE não estruturados — o mascaramento LGPD só cobre CPFs em campos
    nominais (cpf, niResponsavel, etc.).
    """
    return await _ct_subrecurso(id_contrato, "faturas", "ct_faturas", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_ocorrencias(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista ocorrências/penalidades (/api/contrato/{id}/ocorrencias).

    Indicador-chave da confiabilidade do fornecedor. Paginação client-side.
    Cache 15 min.
    """
    return await _ct_subrecurso(id_contrato, "ocorrencias", "ct_ocorrencias", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_responsaveis(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista fiscais/gestores (/api/contrato/{id}/responsaveis).

    CPFs mascarados por LGPD (`123.***.***-45`). Paginação client-side. Cache 15 min.
    """
    return await _ct_subrecurso(id_contrato, "responsaveis", "ct_responsaveis", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_empenhos(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista empenhos do contrato (/api/contrato/{id}/empenhos).

    Paginação client-side. Cache 15 min.
    """
    return await _ct_subrecurso(id_contrato, "empenhos", "ct_empenhos", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_publicacoes(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista publicações DOU (/api/contrato/{id}/publicacoes).

    Paginação client-side. Cache 15 min.
    """
    return await _ct_subrecurso(id_contrato, "publicacoes", "ct_publicacoes", pagina, tamanho_pagina)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contrato_cronograma(
    id_contrato: Annotated[int, Field(description="ID do contrato no Comprasnet.")],
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista cronograma financeiro (/api/contrato/{id}/cronograma).

    Paginação client-side — alguns contratos têm 200+ entradas mensais.
    Cache 15 min.
    """
    return await _ct_subrecurso(id_contrato, "cronograma", "ct_cronograma", pagina, tamanho_pagina)
