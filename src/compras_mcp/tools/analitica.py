"""Tools analíticas — agregação temporal e comparação de períodos.

Diferente das tools de "lookup" (que consultam um registro singular ou
listam itens), estas tools varrem janelas amplas do PNCP e devolvem séries
temporais agregadas. São o suporte natural para análise de tendência em
ETPs, justificativa de contratação e estudos de mercado.

Endpoints usados:
- `/v1/contratacoes/publicacao` (PNCP) — todas as contratações publicadas
  no período.

Estratégia:
- Bucketing por `dia`, `semana`, `mes` ou `ano`.
- Modo `count` (rápido): 1 chamada por bucket × modalidade, lê apenas
  `totalRegistros` da resposta paginada (não precisa varrer páginas).
- Modo `valor` (paginado): varre todas as páginas para somar os valores
  estimado / homologado. Limites de proteção: `MAX_PAGES_PER_BUCKET = 25`
  e `MAX_BUCKETS = 200`.
- Concurrency: `asyncio.gather` com `Semaphore(4)` para não estressar o PNCP.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.config import get_settings
from compras_mcp.esfera import ESFERA_VALORES, matches_esfera
from compras_mcp.mcp_instance import mcp
from compras_mcp.tools._helpers import make_pncp, with_latency

_analitica_cache = cache_from_env(
    "ANALITICA", default_ttl=1800, default_max_size=200
)

# Limites duros para proteger latência e o upstream.
MAX_BUCKETS = 200
# Antes era 25 × 500 itens/página = 12.500 registros máx por bucket. O PNCP
# rejeita tamanho_pagina > 50, então elevamos o cap de páginas para manter
# poder de varredura comparável: 100 × 50 = 5.000 registros máx por bucket.
MAX_PAGES_PER_BUCKET = 100
PNCP_MAX_DATE_RANGE_DAYS = 365  # PNCP rejeita janelas > 365d por chamada
MAX_AGGREGATION_DAYS = 1825  # ~5 anos no total
CONCURRENCY = 4
# Timeout por chamada paginada — generoso o suficiente para combinações
# pesadas (modalidade 8 + UF urbana em dia cheio), mas baixo o bastante para
# falhar rápido se o upstream estiver degradado.
PAGINA_TIMEOUT_S = 45.0

Granularidade = Literal["dia", "semana", "mes", "ano"]
Metrica = Literal["count", "valor_estimado", "valor_homologado"]


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _bucket_key(d: date, gran: Granularidade) -> str:
    if gran == "ano":
        return f"{d.year}"
    if gran == "mes":
        return f"{d.year}-{d.month:02d}"
    if gran == "dia":
        return d.isoformat()
    # Semana ISO
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _generate_buckets(
    data_inicial: date, data_final: date, gran: Granularidade
) -> list[tuple[date, date, str]]:
    """Gera buckets (start, end, key) cobrindo [data_inicial, data_final].

    Cada bucket nunca excede PNCP_MAX_DATE_RANGE_DAYS para caber numa
    chamada singular do PNCP. Buckets `ano` em janelas > 365d são
    automaticamente fatiados.
    """
    buckets: list[tuple[date, date, str]] = []
    cursor = data_inicial
    while cursor <= data_final:
        if gran == "dia":
            end = cursor
        elif gran == "semana":
            end = cursor + timedelta(days=6)
        elif gran == "mes":
            # último dia do mês de cursor
            if cursor.month == 12:
                next_month = date(cursor.year + 1, 1, 1)
            else:
                next_month = date(cursor.year, cursor.month + 1, 1)
            end = next_month - timedelta(days=1)
        elif gran == "ano":
            end = date(cursor.year, 12, 31)
        else:
            raise ValueError(f"granularidade inválida: {gran}")

        if end > data_final:
            end = data_final

        # Se o bucket excede o limite PNCP, fatia.
        if (end - cursor).days > PNCP_MAX_DATE_RANGE_DAYS:
            sub_end = cursor + timedelta(days=PNCP_MAX_DATE_RANGE_DAYS)
            buckets.append((cursor, sub_end, _bucket_key(cursor, gran)))
            cursor = sub_end + timedelta(days=1)
            continue

        buckets.append((cursor, end, _bucket_key(cursor, gran)))
        cursor = end + timedelta(days=1)
    return buckets


async def _consultar_count(
    client: Any, ini: date, fim: date, modalidade: int, uf: str | None
) -> int:
    """Lê apenas `totalRegistros` (pagina=1, tamanho=10) — modo rápido."""
    filtros: dict[str, Any] = {
        "dataInicial": format_date(ini, "pncp"),
        "dataFinal": format_date(fim, "pncp"),
        "codigoModalidadeContratacao": modalidade,
    }
    if uf:
        filtros["uf"] = uf.upper()
    resp = await client.list_resource(
        "/v1/contratacoes/publicacao", pagina=1, tamanho_pagina=10, **filtros
    )
    return int(resp.get("totalRegistros") or 0)


async def _varrer_paginado(
    client: Any,
    ini: date,
    fim: date,
    modalidade: int,
    uf: str | None,
    esfera: str | None,
) -> dict[str, Any]:
    """Varre páginas para somar valores. Aplica filtro `esfera` em memória.

    Retorna `{count, valor_estimado, valor_homologado, paginas_lidas, truncado}`.

    **Defesa contra timeouts**: tamanho de página 100 (em vez de 500) reduz
    payload por chamada; timeout local de 20s e `max_retries=0` por página
    evita acumular minutos por bucket. Exceções de timeout em qualquer
    página da varredura interrompem o bucket e propagam para que a tool
    chamadora registre o erro com diagnóstico.
    """
    filtros: dict[str, Any] = {
        "dataInicial": format_date(ini, "pncp"),
        "dataFinal": format_date(fim, "pncp"),
        "codigoModalidadeContratacao": modalidade,
    }
    if uf:
        filtros["uf"] = uf.upper()

    count = 0
    valor_est = 0.0
    valor_hom = 0.0
    pagina = 1
    truncado = False
    while pagina <= MAX_PAGES_PER_BUCKET:
        resp = await client.list_resource(
            "/v1/contratacoes/publicacao",
            pagina=pagina,
            tamanho_pagina=50,
            max_retries=0,
            timeout=PAGINA_TIMEOUT_S,
            **filtros,
        )
        data = resp.get("data") or []
        if not data:
            break
        for r in data:
            if esfera and not matches_esfera(r, esfera):
                continue
            count += 1
            v_est = r.get("valorTotalEstimado")
            v_hom = r.get("valorTotalHomologado")
            if isinstance(v_est, (int, float)):
                valor_est += float(v_est)
            if isinstance(v_hom, (int, float)):
                valor_hom += float(v_hom)
        total_paginas = int(resp.get("totalPaginas") or 0)
        if pagina >= total_paginas:
            break
        pagina += 1
    else:
        truncado = True
    return {
        "count": count,
        "valor_estimado": round(valor_est, 2),
        "valor_homologado": round(valor_hom, 2),
        "paginas_lidas": pagina,
        "truncado": truncado,
    }


@mcp.tool
async def compras_aggregate_contratacoes_por_periodo(
    data_inicial: Annotated[
        date,
        Field(description="Data inicial da janela de agregação (YYYY-MM-DD)."),
    ],
    data_final: Annotated[
        date,
        Field(description="Data final da janela de agregação (YYYY-MM-DD)."),
    ],
    codigo_modalidade: Annotated[
        int,
        Field(
            description=(
                "Modalidade PNCP a agregar. Comuns: 6=Pregão Eletrônico, "
                "8=Dispensa, 9=Inexigibilidade, 4=Concorrência Eletrônica."
            )
        ),
    ],
    granularidade: Annotated[
        Granularidade,
        Field(
            description=(
                "Tamanho de cada bucket da série: 'dia', 'semana', 'mes' ou 'ano'."
            )
        ),
    ] = "mes",
    metrica: Annotated[
        Metrica,
        Field(
            description=(
                "Métrica a calcular: 'count' (rápido, 1 call por bucket), "
                "'valor_estimado' ou 'valor_homologado' (paginado, mais lento). "
                "Use 'count' para tendência pura; só ative valores quando "
                "necessário."
            )
        ),
    ] = "count",
    uf: Annotated[
        str | None,
        Field(default=None, min_length=2, max_length=2, description="UF opcional."),
    ] = None,
    esfera: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Filtro de esfera (federal/estadual/municipal/distrital). "
                "Só tem efeito no modo 'valor_*' (precisa varrer páginas)."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Série temporal de contratações no PNCP por bucket.

    **Modo `count` (recomendado para tendência)**: 1 chamada por bucket
    lendo apenas `totalRegistros`. Janelas grandes (até 5 anos) são viáveis.

    **Modo `valor_*`**: varre todas as páginas de cada bucket para somar.
    Mais lento; limita-se a `MAX_PAGES_PER_BUCKET=25` páginas (× 500 itens =
    12.500 registros máx por bucket). Sinaliza `truncado=true` quando bate
    o teto.

    Concurrency interna: 4 calls simultâneas. Cache 30 min.
    """
    started = time.perf_counter()
    if data_final < data_inicial:
        raise ValueError("data_final deve ser >= data_inicial")
    delta_total = (data_final - data_inicial).days
    if delta_total > MAX_AGGREGATION_DAYS:
        raise ValueError(
            f"Janela total de {delta_total} dias excede o limite de "
            f"{MAX_AGGREGATION_DAYS}d (~5 anos). Reduza o range."
        )
    if esfera and esfera.lower() not in ESFERA_VALORES:
        raise ValueError(
            f"esfera inválida: {esfera!r}. Use {', '.join(ESFERA_VALORES)}."
        )
    if esfera and metrica == "count":
        # No modo count rápido, esfera não é aplicável porque só lemos
        # totalRegistros do upstream (que vem sem filtro de esfera).
        raise ValueError(
            "Filtro `esfera` requer modo paginado (metrica='valor_estimado' "
            "ou 'valor_homologado'). No modo 'count' rápido a esfera é "
            "ignorada pelo upstream — para contar com filtro, use métrica "
            "de valor + esfera; o count agregado virá filtrado."
        )

    buckets = _generate_buckets(data_inicial, data_final, granularidade)
    if len(buckets) > MAX_BUCKETS:
        raise ValueError(
            f"Janela gera {len(buckets)} buckets, acima do limite {MAX_BUCKETS}. "
            f"Reduza o range ou use granularidade maior."
        )

    cache_key = _ck(
        "agg",
        data_inicial,
        data_final,
        codigo_modalidade,
        granularidade,
        metrica,
        uf,
        esfera,
    )
    cached = await _analitica_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    sem = asyncio.Semaphore(CONCURRENCY)
    settings = get_settings()

    async def _processar_bucket(b: tuple[date, date, str]) -> dict[str, Any]:
        async with sem:
            async with make_pncp(settings) as client:
                if metrica == "count":
                    count = await _consultar_count(
                        client, b[0], b[1], codigo_modalidade, uf
                    )
                    return {
                        "bucket": b[2],
                        "data_inicial": b[0].isoformat(),
                        "data_final": b[1].isoformat(),
                        "count": count,
                        # Modo count lê apenas a 1ª página para extrair
                        # totalRegistros — 1 página por sub-janela. Exposto
                        # explicitamente para consistência com o modo paginado
                        # (achado bateria A v0.3.5).
                        "paginas_lidas": 1,
                        "truncado": False,
                    }
                else:
                    detalhe = await _varrer_paginado(
                        client, b[0], b[1], codigo_modalidade, uf, esfera
                    )
                    return {
                        "bucket": b[2],
                        "data_inicial": b[0].isoformat(),
                        "data_final": b[1].isoformat(),
                        **detalhe,
                    }

    serie_raw = await asyncio.gather(
        *(_processar_bucket(b) for b in buckets), return_exceptions=True
    )

    # Achata exceptions em payload de erro do bucket — preserva qual janela
    # falhou e o motivo, para o usuário poder reagir (reduzir janela,
    # retentar, etc.)
    serie: list[dict[str, Any]] = []
    erros_por_bucket: list[dict[str, Any]] = []
    for bucket_def, item in zip(buckets, serie_raw):
        if isinstance(item, Exception):
            label, b_ini, b_fim = bucket_def[2], bucket_def[0], bucket_def[1]
            registro_erro = {
                "bucket": label,
                "data_inicial": b_ini.isoformat(),
                "data_final": b_fim.isoformat(),
                "erro_tipo": type(item).__name__,
                "erro_mensagem": str(item)[:300],
            }
            erros_por_bucket.append(registro_erro)
            serie.append(
                {
                    "bucket": label,
                    "data_inicial": b_ini.isoformat(),
                    "data_final": b_fim.isoformat(),
                    "erro": f"{type(item).__name__}: {item}"[:300],
                }
            )
        else:
            serie.append(item)

    # Buckets do mesmo `key` (gerados por fatia de PNCP_MAX_DATE_RANGE_DAYS)
    # são consolidados.
    consolidado: dict[str, dict[str, Any]] = {}
    for row in serie:
        if "erro" in row:
            continue
        bk = row["bucket"]
        if bk not in consolidado:
            consolidado[bk] = {
                "bucket": bk,
                "data_inicial": row["data_inicial"],
                "data_final": row["data_final"],
                "count": 0,
                "paginas_lidas": 0,
                "truncado": False,
            }
            if metrica != "count":
                consolidado[bk]["valor_estimado"] = 0.0
                consolidado[bk]["valor_homologado"] = 0.0
        consolidado[bk]["count"] += row.get("count", 0)
        consolidado[bk]["data_final"] = row["data_final"]
        consolidado[bk]["paginas_lidas"] = (
            consolidado[bk].get("paginas_lidas", 0) + row.get("paginas_lidas", 0)
        )
        consolidado[bk]["truncado"] = (
            consolidado[bk]["truncado"] or row.get("truncado", False)
        )
        if metrica != "count":
            consolidado[bk]["valor_estimado"] += row.get("valor_estimado", 0.0)
            consolidado[bk]["valor_homologado"] += row.get("valor_homologado", 0.0)

    serie_final = list(consolidado.values())

    totais: dict[str, float | int] = {"count": sum(b["count"] for b in serie_final)}
    if metrica != "count":
        totais["valor_estimado"] = round(
            sum(b.get("valor_estimado", 0.0) for b in serie_final), 2
        )
        totais["valor_homologado"] = round(
            sum(b.get("valor_homologado", 0.0) for b in serie_final), 2
        )

    # Achado bateria A v0.3.11: quando há erros parciais, `totais.count` não
    # sinaliza incompletude visível e quem só lê o agregado usa dado errado.
    # `_amostra_incompleta` é flag explícita top-level para o agente reagir.
    amostra_incompleta = len(erros_por_bucket) > 0
    aviso_amostra: str | None = None
    if amostra_incompleta:
        pct_perdido = round(
            100.0 * len(erros_por_bucket) / max(len(buckets), 1), 1
        )
        aviso_amostra = (
            f"{len(erros_por_bucket)} de {len(buckets)} buckets internos "
            f"falharam ({pct_perdido}% da amostra perdida). Os `totais` "
            "estão **subdimensionados** pelo total perdido. Inspecione "
            "`erros_por_bucket` para reexecutar janelas específicas com "
            "concurrency menor ou retentar."
        )

    payload: dict[str, Any] = {
        "data_inicial": data_inicial.isoformat(),
        "data_final": data_final.isoformat(),
        "codigo_modalidade": codigo_modalidade,
        "granularidade": granularidade,
        "metrica": metrica,
        "uf": uf,
        "esfera": esfera,
        "serie": serie_final,
        "totais": totais,
        "buckets_processados": len(buckets),
        "erros": len(erros_por_bucket),
        "erros_por_bucket": erros_por_bucket or None,
        "_amostra_incompleta": amostra_incompleta,
        "_aviso_amostra_incompleta": aviso_amostra,
        "_cache_hit": False,
    }
    await _analitica_cache.set(cache_key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_comparar_periodos_contratacoes(
    periodo_a_inicio: Annotated[
        date, Field(description="Data inicial do período A (YYYY-MM-DD).")
    ],
    periodo_a_fim: Annotated[
        date, Field(description="Data final do período A (YYYY-MM-DD).")
    ],
    periodo_b_inicio: Annotated[
        date, Field(description="Data inicial do período B (YYYY-MM-DD).")
    ],
    periodo_b_fim: Annotated[
        date, Field(description="Data final do período B (YYYY-MM-DD).")
    ],
    codigo_modalidade: Annotated[
        int,
        Field(
            description=(
                "Modalidade PNCP a comparar. Comuns: 6=Pregão Eletrônico, "
                "8=Dispensa, 4=Concorrência Eletrônica."
            )
        ),
    ],
    label_a: Annotated[
        str,
        Field(
            description="Rótulo amigável do período A (ex.: 'Jun/2024').",
            min_length=1,
            max_length=80,
        ),
    ] = "Periodo A",
    label_b: Annotated[
        str,
        Field(
            description="Rótulo amigável do período B (ex.: 'Jun/2025').",
            min_length=1,
            max_length=80,
        ),
    ] = "Periodo B",
    metrica: Annotated[
        Metrica,
        Field(
            description=(
                "Métrica a comparar: 'count' (rápido) ou 'valor_estimado' / "
                "'valor_homologado' (paginado)."
            )
        ),
    ] = "count",
    uf: Annotated[
        str | None,
        Field(default=None, min_length=2, max_length=2, description="UF opcional."),
    ] = None,
    esfera: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Esfera federativa. Requer métrica de valor (modo paginado)."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Compara dois períodos lado a lado para a mesma modalidade.

    Wrapper sobre `compras_aggregate_contratacoes_por_periodo` chamado duas
    vezes (granularidade='ano' implícita — soma todo o período em 1 bucket).

    Retorna totais de A e B + delta absoluto + delta percentual.

    Caso de uso típico: _"Houve antecipação de licitações em Jun/2024 (ano
    eleitoral) comparado a Jun/2025?"_ Ou _"As dispensas em Dez/2024 foram
    maiores que Dez/2023 no mesmo órgão?"_.
    """
    started = time.perf_counter()

    async def _agg(ini: date, fim: date) -> dict[str, Any]:
        # Reusa a tool para garantir consistência de filtros e cache.
        return await compras_aggregate_contratacoes_por_periodo.fn(
            data_inicial=ini,
            data_final=fim,
            codigo_modalidade=codigo_modalidade,
            granularidade="ano",
            metrica=metrica,
            uf=uf,
            esfera=esfera,
        )

    res_a, res_b = await asyncio.gather(
        _agg(periodo_a_inicio, periodo_a_fim),
        _agg(periodo_b_inicio, periodo_b_fim),
    )

    def _total(payload: dict[str, Any], key: str) -> float:
        v = (payload.get("totais") or {}).get(key)
        return float(v) if isinstance(v, (int, float)) else 0.0

    delta: dict[str, dict[str, float]] = {}
    chaves = ["count"] if metrica == "count" else [
        "count",
        "valor_estimado",
        "valor_homologado",
    ]
    for k in chaves:
        a = _total(res_a, k)
        b = _total(res_b, k)
        abs_delta = b - a
        pct = (abs_delta / a * 100.0) if a else None
        delta[k] = {
            "a": a,
            "b": b,
            "delta_absoluto": round(abs_delta, 2),
            "delta_percentual": (
                round(pct, 2) if pct is not None else None
            ),
        }

    payload: dict[str, Any] = {
        "codigo_modalidade": codigo_modalidade,
        "uf": uf,
        "esfera": esfera,
        "metrica": metrica,
        "periodo_a": {
            "label": label_a,
            "data_inicial": periodo_a_inicio.isoformat(),
            "data_final": periodo_a_fim.isoformat(),
            "totais": res_a.get("totais"),
        },
        "periodo_b": {
            "label": label_b,
            "data_inicial": periodo_b_inicio.isoformat(),
            "data_final": periodo_b_fim.isoformat(),
            "totais": res_b.get("totais"),
        },
        "delta": delta,
        "_cache_hit": False,
    }
    return with_latency(payload, started)
