"""Tools de fornecedores: cadastro (Dados Abertos) + impedimentos (Comprasnet).

Endpoints cobertos:
- /modulo-fornecedor/1_consultarFornecedor       — Dados Abertos
- POST /api/comprasnet/compras/impedimentos      — Comprasnet (impedimentos por item)
- POST /api/comprasnet/contratosempenhos         — Comprasnet (contratos por item)
- GET  /api/comprasnet/contratos                 — Comprasnet (lista contratos por item)

Cache TTL 1h — dados cadastrais mudam pouco; impedimentos por item são
consultados sob demanda em momentos críticos (homologação) e podem ter
TTL mais curto se necessário.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.config import get_settings
from compras_mcp.mcp_instance import mcp
from compras_mcp.schemas import (
    ConsultarFornecedorInput,
    ListarPaginadoInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_comprasnet,
    envelope_dados_abertos,
    make_comprasnet,
    make_dados_abertos,
    with_latency,
)


_fornecedores_cache = cache_from_env(
    "FORNECEDORES", default_ttl=3600, default_max_size=300
)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _so_digitos(s: str | None) -> str | None:
    if s is None:
        return None
    return "".join(c for c in s if c.isdigit())


@mcp.tool
async def compras_fornecedor_consultar(
    cnpj_cpf: Annotated[
        str, Field(description=desc(ConsultarFornecedorInput, "cnpj_cpf"))
    ],
) -> dict[str, Any]:
    """Consulta cadastro de um fornecedor pelo CNPJ ou CPF.

    Endpoint Dados Abertos `/modulo-fornecedor/1_consultarFornecedor`.
    Devolve razão social, CNAE, porte da empresa, natureza jurídica.

    Cache 1h.
    """
    started = time.perf_counter()
    doc = _so_digitos(cnpj_cpf) or ""
    if len(doc) not in (11, 14):
        raise ValueError(
            f"Documento '{cnpj_cpf}' não tem 11 (CPF) nem 14 (CNPJ) dígitos."
        )

    key = _ck("fornecedor_consultar", doc)
    cached = await _fornecedores_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {"ativo": "true"}
    if len(doc) == 14:
        filtros["cnpj"] = doc
    else:
        filtros["cpf"] = doc

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-fornecedor/1_consultarFornecedor",
            pagina=1,
            tamanho_pagina=5,
            **filtros,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": doc,
        "fornecedor": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _fornecedores_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_fornecedor_listar(
    cnpj: Annotated[
        str | None,
        Field(default=None, description="Filtrar por CNPJ (14 dígitos)."),
    ] = None,
    cpf: Annotated[
        str | None,
        Field(default=None, description="Filtrar por CPF (11 dígitos)."),
    ] = None,
    porte_empresa: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Código de porte da empresa (1=ME, 2=EPP, 3=Demais). "
                "Consulte os códigos no manual do Compras.gov.br."
            ),
        ),
    ] = None,
    codigo_cnae: Annotated[
        int | None,
        Field(default=None, description="Código CNAE para filtrar por atividade."),
    ] = None,
    natureza_juridica: Annotated[
        int | None,
        Field(default=None, description="Código da natureza jurídica."),
    ] = None,
    ativo: Annotated[
        bool,
        Field(
            description=(
                "True (default) para listar apenas ativos, False para apenas inativos."
            ),
        ),
    ] = True,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista fornecedores no Compras.gov.br com filtros estruturais.

    Endpoint Dados Abertos `/modulo-fornecedor/1_consultarFornecedor`. Use
    para mapear fornecedores potenciais por porte/CNAE — ex.: levantar
    todas as MEs com CNAE de TI.

    Cache 1h.
    """
    started = time.perf_counter()
    cnpj_limpo = _so_digitos(cnpj)
    cpf_limpo = _so_digitos(cpf)
    key = _ck(
        "forn_listar",
        cnpj_limpo,
        cpf_limpo,
        porte_empresa,
        codigo_cnae,
        natureza_juridica,
        ativo,
        pagina,
        tamanho_pagina,
    )
    cached = await _fornecedores_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {"ativo": "true" if ativo else "false"}
    if cnpj_limpo:
        filtros["cnpj"] = cnpj_limpo
    if cpf_limpo:
        filtros["cpf"] = cpf_limpo
    if porte_empresa is not None:
        filtros["porteEmpresaId"] = porte_empresa
    if codigo_cnae is not None:
        filtros["codigoCnae"] = codigo_cnae
    if natureza_juridica is not None:
        filtros["naturezaJuridicaId"] = natureza_juridica

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-fornecedor/1_consultarFornecedor",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _fornecedores_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_fornecedor_impedimentos_por_itens(
    codigos_catmat: Annotated[
        list[int] | None,
        Field(
            default=None,
            description=(
                "Lista de códigos CATMAT (materiais) a verificar. "
                "Use junto com codigos_catser ou separadamente."
            ),
        ),
    ] = None,
    codigos_catser: Annotated[
        list[int] | None,
        Field(default=None, description="Lista de códigos CATSER (serviços)."),
    ] = None,
) -> dict[str, Any]:
    """Consulta impedimentos no Comprasnet por lista de itens (CATMAT/CATSER).

    Endpoint `POST /api/comprasnet/compras/impedimentos`. Retorna fornecedores
    impedidos de participar de contratações dos itens informados (sanções
    aplicadas no SICAF). Essencial antes de homologar pregões eletrônicos.

    Cache 1h.
    """
    started = time.perf_counter()
    if not codigos_catmat and not codigos_catser:
        raise ValueError(
            "Informe pelo menos um código CATMAT ou CATSER para consultar."
        )

    body: dict[str, Any] = {}
    if codigos_catmat:
        body["catmat"] = codigos_catmat
    if codigos_catser:
        body["catser"] = codigos_catser

    key = _ck(
        "forn_imped",
        ",".join(map(str, sorted(codigos_catmat or []))),
        ",".join(map(str, sorted(codigos_catser or []))),
    )
    cached = await _fornecedores_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_comprasnet(get_settings()) as client:
        resp = await client.post_json(
            "/comprasnet/compras/impedimentos", json_body=body
        )
    payload = envelope_comprasnet(resp)
    payload["_cache_hit"] = False
    await _fornecedores_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_fornecedor_contratos_por_item(
    codigos_catmat: Annotated[
        list[int] | None,
        Field(default=None, description="Códigos CATMAT a buscar."),
    ] = None,
    codigos_catser: Annotated[
        list[int] | None,
        Field(default=None, description="Códigos CATSER a buscar."),
    ] = None,
) -> dict[str, Any]:
    """Lista contratos e empenhos por itens (CATMAT/CATSER) no Comprasnet.

    Endpoint `POST /api/comprasnet/contratosempenhos`. Útil para descobrir
    quem fornece esses itens hoje no governo (potenciais participantes em
    novos certames).

    Cache 1h.
    """
    started = time.perf_counter()
    if not codigos_catmat and not codigos_catser:
        raise ValueError("Informe pelo menos um CATMAT ou CATSER.")

    body: dict[str, Any] = {}
    if codigos_catmat:
        body["catmat"] = codigos_catmat
    if codigos_catser:
        body["catser"] = codigos_catser

    key = _ck(
        "forn_ct_item",
        ",".join(map(str, sorted(codigos_catmat or []))),
        ",".join(map(str, sorted(codigos_catser or []))),
    )
    cached = await _fornecedores_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_comprasnet(get_settings()) as client:
        resp = await client.post_json(
            "/comprasnet/contratosempenhos", json_body=body
        )
    payload = envelope_comprasnet(resp)
    payload["_cache_hit"] = False
    await _fornecedores_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
