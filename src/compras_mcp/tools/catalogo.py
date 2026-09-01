"""Tools do catálogo CATMAT (materiais) e CATSER (serviços).

Wraps dos endpoints /modulo-material/* e /modulo-servico/* dos Dados Abertos
do Compras.gov.br.

Cache com TTL longo (24h) — os catálogos são atualizados raramente. Quando
REDIS_URL está configurada, o cache é compartilhado entre pods (Railway).
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.config import get_settings
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    BuscarItemCatalogoInput,
    ConsultarCatmatInput,
    ConsultarCatserInput,
    ListarPaginadoInput,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    make_dados_abertos,
    with_latency,
)


_catalogo_cache = cache_from_env("CATALOGO", default_ttl=86400, default_max_size=500)


def _cache_key(*parts: Any) -> str:
    return "|".join(str(p) if p is not None else "" for p in parts)


# ============================================================================
# CATMAT — Catálogo de Materiais
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catmat_listar_grupos(
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista os grupos do CATMAT (Catálogo de Materiais).

    Grupos são o nível mais alto da hierarquia CATMAT (ex.: 10=ARMAMENTO,
    11=MATERIAIS BÉLICOS NUCLEARES). Use esta tool para enquadrar a
    contratação no grupo correto antes de descer para classes/PDM/itens.

    Cache de 24h: os grupos mudam muito raramente. Total atual ~79 grupos.
    """
    started = time.perf_counter()
    key = _cache_key("catmat_grupos", pagina, tamanho_pagina)
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-material/1_consultarGrupoMaterial",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catmat_listar_classes(
    codigo_grupo: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Restringe a classes pertencentes a este grupo CATMAT. "
                "Se omitido, lista classes de todos os grupos."
            ),
        ),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista as classes do CATMAT, opcionalmente filtradas por grupo.

    Classes são o segundo nível da hierarquia (ex.: dentro do grupo 71
    Mobiliário, a classe 7110 é "Mobiliário de escritório").

    Cache de 24h.
    """
    started = time.perf_counter()
    key = _cache_key("catmat_classes", codigo_grupo, pagina, tamanho_pagina)
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-material/2_consultarClasseMaterial",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            codigoGrupo=codigo_grupo,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catmat_consultar(
    codigo_item: Annotated[
        int, Field(description=desc(ConsultarCatmatInput, "codigo_item"))
    ],
) -> dict[str, Any]:
    """Consulta detalhes de um item CATMAT específico pelo código.

    Devolve nome do item, PDM, grupo, classe, características, NCM e
    unidades de fornecimento. Útil para confirmar o código antes de
    fazer pesquisa de preços ou listar contratações similares.

    Cache de 24h.
    """
    started = time.perf_counter()
    key = _cache_key("catmat_item", codigo_item)
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-material/4_consultarItemMaterial",
            pagina=1,
            tamanho_pagina=1,
            codigoItem=codigo_item,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": codigo_item,
        "item": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catmat_buscar(
    termo: Annotated[str, Field(description=desc(BuscarItemCatalogoInput, "termo"))],
    codigo_grupo: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Filtro estrutural por grupo CATMAT (1-99). "
                "**FORTEMENTE RECOMENDADO** porque o filtro textual upstream "
                "está quebrado (veja docstring). Obtenha o código em "
                "`compras_catmat_listar_grupos`."
            ),
        ),
    ] = None,
    codigo_classe: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Filtro estrutural por classe CATMAT (4 dígitos). "
                "Use em conjunto com `codigo_grupo` para focar a busca."
            ),
        ),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(BuscarItemCatalogoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(BuscarItemCatalogoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Busca itens CATMAT.

    **⚠️ Atenção upstream**: o filtro textual `descricao` do Dados Abertos
    está ignorando o valor enviado e devolvendo o universo CATMAT inteiro
    (~340k itens, começando por arma de fogo) desde meados de 2026.
    Confirmado via probe direto. Os filtros estruturais (`codigo_grupo`,
    `codigo_classe`, `codigo_pdm`) continuam funcionando.

    **Workflow recomendado enquanto o filtro textual não voltar**:
    1. `compras_catmat_listar_grupos()` → escolher o grupo (ex.: 71=Mobiliários).
    2. `compras_catmat_listar_classes(codigo_grupo=71)` → escolher a classe
       (ex.: 7110=Mobiliário para Escritório).
    3. `compras_catmat_buscar(termo='cadeira', codigo_grupo=71, codigo_classe=7110)`
       → o `termo` ainda é enviado (mantém compatibilidade), mas a redução
       real virá dos códigos estruturais.

    Esta tool emite `_aviso_filtro` no payload quando detecta que o upstream
    devolveu o universo inteiro.

    Cache 24h por (termo + filtros + página).
    """
    started = time.perf_counter()
    key = _cache_key(
        "catmat_buscar", termo, codigo_grupo, codigo_classe, pagina, tamanho_pagina
    )
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {"descricao": termo}
    if codigo_grupo is not None:
        filtros["codigoGrupo"] = codigo_grupo
    if codigo_classe is not None:
        filtros["codigoClasse"] = codigo_classe

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-material/4_consultarItemMaterial",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False

    # Detecção do bug upstream: filtro textual `descricao` ignorado. Duas
    # heurísticas — antes da v0.3.6 só tínhamos (A):
    # (A) Universo absoluto inteiro: total >= 200k → catálogo completo.
    # (B) Termo ausente dos resultados: se o usuário passou `termo` mas ele
    #     aparece em <50% da 1ª página, o filtro foi ignorado — só que
    #     dentro do sub-recorte de grupo/classe. Caso real (bateria A
    #     v0.3.5): termo='notebook' + codigo_classe=7010 devolveu 1312
    #     itens (servidores/desktops), 0 com "notebook" na primeira página.
    total = payload.get("_total_registros", 0)
    items = payload.get("resultado") or []
    aviso_motivo: str | None = None
    tipo_aviso: str | None = None  # Achado bateria A v0.3.11: discriminar
                                    # ramos para teste programático.

    if total >= 200_000:
        tipo_aviso = "universo_completo"
        aviso_motivo = (
            f"Upstream retornou {total} itens — universo completo, filtro "
            "textual ignorado. Reforce com `codigo_grupo` e `codigo_classe`."
        )
    elif termo and items:
        termo_norm = termo.lower().strip()
        if len(termo_norm) >= 3:
            def _tem_termo(it: dict[str, Any]) -> bool:
                desc = it.get("descricaoItem") or it.get("descricao") or ""
                return termo_norm in str(desc).lower()

            amostra = items[: min(len(items), 20)]
            hits = sum(1 for it in amostra if _tem_termo(it))
            taxa = hits / len(amostra) if amostra else 0.0
            if taxa < 0.5:
                tipo_aviso = "termo_ausente"
                aviso_motivo = (
                    f"Termo '{termo}' apareceu em apenas {hits}/{len(amostra)} "
                    f"itens da primeira página ({taxa:.0%}) — filtro textual "
                    "ignorado pelo upstream mesmo com filtros estruturais. "
                    "Use `codigo_classe` mais específico ou filtre client-side "
                    "pelo campo `descricaoItem` dos resultados."
                )

    if aviso_motivo:
        payload["_aviso_filtro"] = aviso_motivo
        payload["_tipo_aviso_filtro"] = tipo_aviso

    # B4 v0.3.12: quando o termo está presente E o filtro textual está
    # quebrado upstream, reordena os resultados client-side colocando
    # primeiro os que CONTÊM o termo na descrição. Sem isso, a 1ª página
    # frequentemente esconde o PDM relevante (caso real: termo='notebook'
    # + classe=7010 devolvia servidores de impressão antes do PDM 8435).
    # Sort estável: mantém a ordem upstream entre itens do mesmo grupo.
    if termo and items and len(termo.strip()) >= 3:
        termo_lower = termo.lower().strip()

        def _contem_termo(it: dict[str, Any]) -> int:
            desc = str(it.get("descricaoItem") or it.get("descricao") or "")
            return 0 if termo_lower in desc.lower() else 1

        reordenado = sorted(items, key=_contem_termo)
        if reordenado != items:
            payload["resultado"] = reordenado
            payload["_reordenado_client_side"] = (
                "Itens contendo o termo na descrição foram movidos para o "
                "topo (filtro textual upstream está quebrado — sem isso o "
                "PDM relevante ficaria escondido em páginas profundas)."
            )

    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)


# ============================================================================
# CATSER — Catálogo de Serviços
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catser_listar_secoes(
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista as seções do CATSER (Catálogo de Serviços).

    Seções são o nível mais alto da hierarquia CATSER (baseada no CPC ONU).
    Use para enquadrar a contratação de serviços em uma seção antes de
    descer para divisões/grupos/classes/itens.

    Cache de 24h.
    """
    started = time.perf_counter()
    key = _cache_key("catser_secoes", pagina, tamanho_pagina)
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-servico/1_consultarSecaoServico",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catser_listar_classes(
    codigo_grupo: Annotated[
        int | None,
        Field(
            default=None,
            description="Restringe a classes do grupo CATSER informado.",
        ),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista as classes CATSER, opcionalmente filtradas por grupo.

    Cache de 24h.
    """
    started = time.perf_counter()
    key = _cache_key("catser_classes", codigo_grupo, pagina, tamanho_pagina)
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-servico/4_consultarClasseServico",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            codigoGrupo=codigo_grupo,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_catser_consultar(
    codigo_item: Annotated[
        int, Field(description=desc(ConsultarCatserInput, "codigo_item"))
    ],
) -> dict[str, Any]:
    """Consulta detalhes de um item CATSER pelo código.

    Devolve nome do serviço, descrição, seção/divisão/grupo/classe e
    unidades de medida. Use para confirmar o código antes de pesquisar
    preços ou contratações similares.

    Cache de 24h.
    """
    started = time.perf_counter()
    key = _cache_key("catser_item", codigo_item)
    cached = await _catalogo_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-servico/6_consultarItemServico",
            pagina=1,
            tamanho_pagina=1,
            codigoServico=codigo_item,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": codigo_item,
        "item": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _catalogo_cache.set(key, json.loads(json.dumps(payload)))
    return with_latency(payload, started)
