"""Tools "agente" compostas — agregam várias APIs em uma única resposta.

São o diferencial deste MCP. Cada tool aqui faz fan-out paralelo
(`asyncio.gather`) para múltiplos endpoints upstream e devolve um payload
consolidado pronto para análise/colagem em documentos.

- `compras_pesquisar_precos_para_etp` — preços no padrão IN SEGES/ME 65/2021
  (mediana, média, desvio, IQR para descarte de outliers).
- `compras_checar_sancoes_fornecedor` — CEIS + CNEP + CEPIM + leniência +
  impedimentos Comprasnet em paralelo.
- `compras_montar_dossie_arp` — ata + itens + saldo + adesões + unidades
  participantes em uma chamada.
- `compras_buscar_contratacoes_similares` — federa Dados Abertos + PNCP.
- `compras_perfil_fornecedor_completo` — cadastro + contratos + sanções.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.base import format_date
from compras_mcp.clients.cnpj import make_cnpj_client
from compras_mcp.config import get_settings
from compras_mcp.errors import ComprasAuthError, ComprasNotFoundError
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import (
    BuscarContratacoesSimilaresInput,
    CheckarSancoesFornecedorInput,
    PesquisarPrecosParaETPInput,
)
from compras_mcp.tools._helpers import (
    desc,
    make_comprasnet,
    make_dados_abertos,
    make_pncp,
    make_transparencia,
    with_latency,
)

# Cache exclusivo das compostas. As atômicas têm caches próprios, mas as
# compostas chamam clients diretamente (sem passar pelas tools), então
# precisam de cache aqui. TTL 10 min — payloads grandes e dados de mercado
# variam ao longo do dia.
_compostas_cache = cache_from_env("COMPOSTAS", default_ttl=600, default_max_size=300)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _so_digitos(s: str | None) -> str | None:
    if s is None:
        return None
    return "".join(c for c in s if c.isdigit())


def _resposta_etp_rota_indisponivel(
    path: str,
    *,
    tipo: str,
    codigo_item_catalogo: int,
) -> dict[str, Any]:
    """Diagnóstico quando a rota de preço cai antes da agregação começar.

    Sem isto, a tool estourava `ComprasNotFoundError` no meio da paginação
    e o LLM traduzia para "não há preços para este item" — conclusão falsa
    que ia parar dentro de um ETP. Mesmo padrão de envelope das tools
    `compras_uasg_*`.
    """
    return {
        "tipo": tipo,
        "codigo_item_catalogo": codigo_item_catalogo,
        "amostra_total": 0,
        "registros": [],
        "estatisticas": None,
        "_cache_hit": False,
        "_erro_upstream": {
            "endpoint": f"dados_abertos{path}",
            "status": 404,
            "detectado_em": "preflight (antes da paginação)",
            "diagnostico": (
                "A rota de pesquisa de preço respondeu 404 na primeira "
                "página; a agregação não foi iniciada. Nesta API o 404 "
                "indica assinatura de query alterada ou parâmetro "
                "obrigatório ausente — NÃO significa que o item não tem "
                "preços praticados. Não use esta resposta para concluir "
                "ausência de mercado."
            ),
            "verificar_com": "python scripts/probe_upstream.py --modulo pesquisa_preco",
            "alternativas": [
                "`compras_arp_itens_listar(...)`: atas de registro de preço "
                "trazem `valorUnitario` homologado — base válida para ETP.",
                "`compras_contratacoes_14133_resultados_listar(...)`: "
                "resultados homologados por item, com valor e fornecedor.",
                "Painel de Preços (paineldeprecos.planejamento.gov.br) para "
                "conferência manual enquanto a rota não volta.",
            ],
        },
    }


def _quartis(valores: list[float]) -> tuple[float, float, float]:
    """Devolve (q1, mediana, q3) — método linear simples."""
    if not valores:
        return (0.0, 0.0, 0.0)
    ordenado = sorted(valores)
    n = len(ordenado)
    mediana = statistics.median(ordenado)
    metade = n // 2
    q1 = statistics.median(ordenado[:metade]) if metade > 0 else ordenado[0]
    q3 = (
        statistics.median(ordenado[metade + (n % 2):])
        if metade + (n % 2) < n
        else ordenado[-1]
    )
    return (q1, mediana, q3)


def _filtrar_outliers_iqr(valores: list[float]) -> tuple[list[float], list[float]]:
    """Filtra outliers por IQR (Tukey: q ± 1.5*IQR). Retorna (filtrados, descartados)."""
    if len(valores) < 4:
        return (valores, [])
    q1, _med, q3 = _quartis(valores)
    iqr = q3 - q1
    limite_inferior = q1 - 1.5 * iqr
    limite_superior = q3 + 1.5 * iqr
    filtrados: list[float] = []
    descartados: list[float] = []
    for v in valores:
        if limite_inferior <= v <= limite_superior:
            filtrados.append(v)
        else:
            descartados.append(v)
    return (filtrados, descartados)


def _clusterizar_por_gap(valores: list[float], max_clusters: int = 3) -> list[dict[str, Any]]:
    """Particiona valores em até `max_clusters` clusters usando os maiores
    gaps relativos como cortes.

    Para `n` valores ordenados, há `n-1` gaps entre vizinhos. Pegamos os
    `max_clusters - 1` maiores gaps **relativos** (`gap / valor_anterior`)
    desde que ultrapassem 30% — abaixo disso a amostra é considerada
    contínua e devolvemos 1 cluster só.

    Cada cluster sai com `min`, `max`, `mediana`, `media`, `n` e `valores`
    (truncado em 20 para não inflar o payload).
    """
    if len(valores) < 4:
        return [_resumir_cluster(sorted(valores))]
    s = sorted(valores)
    # Acha gaps relativos: lista de (gap_relativo, idx_corte_no_array).
    gaps_fortes: list[tuple[float, int]] = []
    for i in range(1, len(s)):
        anterior = s[i - 1]
        if anterior <= 0:
            continue
        gap_rel = (s[i] - anterior) / anterior
        if gap_rel >= 0.30:  # 30% — limiar de heterogeneidade
            gaps_fortes.append((gap_rel, i))
    if not gaps_fortes:
        return [_resumir_cluster(s)]
    # Pega até (max_clusters - 1) gaps mais fortes; ordena os cortes por posição.
    cortes = sorted(
        idx for _, idx in sorted(gaps_fortes, reverse=True)[: max_clusters - 1]
    )
    clusters: list[dict[str, Any]] = []
    inicio = 0
    for corte in cortes:
        clusters.append(_resumir_cluster(s[inicio:corte]))
        inicio = corte
    clusters.append(_resumir_cluster(s[inicio:]))
    return clusters


def _resumir_cluster(valores_ordenados: list[float]) -> dict[str, Any]:
    if not valores_ordenados:
        return {"n": 0}
    return {
        "n": len(valores_ordenados),
        "minimo": round(min(valores_ordenados), 4),
        "maximo": round(max(valores_ordenados), 4),
        "mediana": round(statistics.median(valores_ordenados), 4),
        "media": round(statistics.fmean(valores_ordenados), 4),
        "valores_amostra": [round(v, 4) for v in valores_ordenados[:20]],
    }


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_pesquisar_precos_para_etp(
    tipo: Annotated[
        Literal["material", "servico"],
        Field(description=desc(PesquisarPrecosParaETPInput, "tipo")),
    ],
    codigo_item_catalogo: Annotated[
        int,
        Field(description=desc(PesquisarPrecosParaETPInput, "codigo_item_catalogo")),
    ],
    periodo_meses: Annotated[
        int,
        Field(description=desc(PesquisarPrecosParaETPInput, "periodo_meses")),
    ] = 12,
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(PesquisarPrecosParaETPInput, "uf")),
    ] = None,
    max_paginas: Annotated[
        int, Field(description=desc(PesquisarPrecosParaETPInput, "max_paginas"))
    ] = 5,
) -> dict[str, Any]:
    """Agrega preços praticados aplicando metodologia IN SEGES/ME 65/2021.

    Composição: percorre `compras_pesquisar_preco_material` ou `_servico`
    em até `max_paginas`, agrega os valores unitários e calcula:
    mediana, média, desvio padrão, mínimo, máximo, quartis (Q1, Q3) e
    descarte de outliers por IQR (1.5×IQR — Tukey).

    Saída pronta para colagem em ETP: lista detalhada + sumário estatístico
    + amostra recomendada (sem outliers). Cache 10 min.
    """
    started = time.perf_counter()
    cache_key = _ck("precos_etp", tipo, codigo_item_catalogo, periodo_meses, uf, max_paginas)
    cached = await _compostas_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()
    hoje = date.today()
    data_inicio = hoje - timedelta(days=periodo_meses * 30)
    path = (
        "/modulo-pesquisa-preco/1_consultarMaterial"
        if tipo == "material"
        else "/modulo-pesquisa-preco/3_consultarServico"
    )
    # As duas rotas identificam o item de formas diferentes desde 2026-08:
    # material usa `tipo`+`codigo` (enum EnumPesquisaPreco), serviço segue
    # com `codigoItemCatalogo`. Ver tools/pesquisa_precos.py.
    if tipo == "material":
        params: dict[str, Any] = {
            "tipo": "codigoItemCatalogo",
            "codigo": str(codigo_item_catalogo),
        }
    else:
        params = {"codigoItemCatalogo": codigo_item_catalogo}
    params["dataCompraInicio"] = format_date(data_inicio, "dados_abertos")
    params["dataCompraFim"] = format_date(hoje, "dados_abertos")
    if uf:
        params["estado"] = uf.upper()

    registros: list[dict[str, Any]] = []
    async with make_dados_abertos(settings) as client:
        # Preflight: a 1ª página vale como teste de disponibilidade da rota.
        # Se ela falhar, devolvemos diagnóstico e nem entramos no loop — o
        # modo de falha a evitar é estourar na página 3, depois de 20s, com
        # o analista achando que a amostra estava sendo montada. Um preflight
        # separado custaria uma requisição extra em toda chamada; esta é a
        # mesma garantia sem esse custo.
        try:
            primeira = await client.list_resource(
                path, pagina=1, tamanho_pagina=500, **params
            )
        except ComprasNotFoundError:
            return with_latency(
                _resposta_etp_rota_indisponivel(
                    path, tipo=tipo, codigo_item_catalogo=codigo_item_catalogo
                ),
                started,
            )

        registros.extend(primeira.get("resultado") or [])
        total_paginas = int(primeira.get("totalPaginas") or 1)
        for pagina in range(2, max_paginas + 1):
            if pagina > total_paginas or not registros:
                break
            resp = await client.list_resource(
                path,
                pagina=pagina,
                tamanho_pagina=500,
                **params,
            )
            batch = resp.get("resultado") or []
            registros.extend(batch)
            if not batch:
                break

    # Extrai unitários do payload (campos podem variar: precoUnitario, valorUnitario etc.)
    valores: list[float] = []
    for r in registros:
        for k in ("precoUnitario", "valorUnitario", "precoUnitarioHomologado", "valor"):
            v = r.get(k)
            if v is not None:
                try:
                    valores.append(float(v))
                    break
                except (TypeError, ValueError):
                    continue

    if not valores:
        return with_latency(
            {
                "tipo": tipo,
                "codigo_item_catalogo": codigo_item_catalogo,
                "periodo_meses": periodo_meses,
                "uf": uf,
                "amostra_total": 0,
                "registros": [],
                "aviso": (
                    "Nenhum preço unitário encontrado no período. "
                    "Aumente periodo_meses ou retire o filtro UF."
                ),
                "_cache_hit": False,
            },
            started,
        )

    filtrados, descartados = _filtrar_outliers_iqr(valores)
    q1, med, q3 = _quartis(filtrados or valores)

    base = filtrados or valores
    media = statistics.fmean(base)
    desvio = statistics.pstdev(base) if len(base) > 1 else 0.0
    # Coeficiente de variação: dispersão relativa. CV > 0.5 indica amostra
    # heterogênea (clusters distintos no mesmo CATMAT/PDM).
    cv = (desvio / media) if media else 0.0

    payload: dict[str, Any] = {
        "tipo": tipo,
        "codigo_item_catalogo": codigo_item_catalogo,
        "periodo_meses": periodo_meses,
        "uf": uf,
        "data_inicio": data_inicio.isoformat(),
        "data_fim": hoje.isoformat(),
        "amostra_total": len(valores),
        "outliers_descartados": len(descartados),
        "amostra_efetiva": len(filtrados),
        "estatisticas": {
            "media": round(media, 4),
            "mediana": round(med, 4),
            "desvio_padrao": round(desvio, 4),
            "coeficiente_variacao": round(cv, 4),
            "minimo": round(min(base), 4),
            "maximo": round(max(base), 4),
            "q1": round(q1, 4),
            "q3": round(q3, 4),
        },
        "metodologia": (
            "IN SEGES/ME 65/2021 art. 5. Outliers descartados pelo critério "
            "de Tukey (Q1-1.5*IQR, Q3+1.5*IQR). Recomenda-se a mediana como "
            "estimador robusto a outliers; usar a média apenas após filtragem."
        ),
        "registros": registros[:200],
        "_registros_truncados_em": 200 if len(registros) > 200 else None,
        "_cache_hit": False,
    }

    # Detecção de amostra heterogênea (clusters dentro do mesmo CATMAT/PDM).
    # Achado da bateria A v0.3.5: PDM 8435 NOTEBOOK mistura chromebooks
    # educacionais (R$ 1.487) com notebooks corporativos (R$ 7.479). A
    # mediana matemática é correta mas operacionalmente enganosa quando
    # produtos muito diferentes coexistem no mesmo código. Quando CV > 0.5
    # **ou** max/min > 3, devolvemos clusters por maior gap relativo +
    # aviso explícito para o analista revisar o recorte amostral.
    razao_max_min = (max(base) / min(base)) if base and min(base) > 0 else 1.0
    if (cv > 0.5 or razao_max_min > 3.0) and len(base) >= 4:
        clusters = _clusterizar_por_gap(base)
        payload["clusters"] = clusters
        # Aviso v0.3.12: marcar clusters com n < 3 — abaixo do mínimo
        # legal da IN 65/2021 art. 7º. Se o analista escolher um cluster
        # com n=1 ou n=2 sem ver isso, o valor estimado é frágil.
        clusters_pequenos = [
            c for c in clusters if isinstance(c, dict) and c.get("n", 0) < 3
        ]
        if clusters_pequenos:
            faixas = ", ".join(
                f"R$ {c['minimo']}–{c['maximo']} (n={c['n']})"
                for c in clusters_pequenos
            )
            payload["aviso_amostra_minima_por_cluster"] = (
                f"{len(clusters_pequenos)} cluster(s) com amostra menor que "
                "3 contratações (mínimo IN SEGES/ME 65/2021 art. 7º): "
                f"{faixas}. Se escolher um destes como base do valor "
                "estimado, complemente com cotações diretas (3 fornecedores) "
                "ou consulte preços em outras fontes (Painel CGU, ComprasNet)."
            )
        payload["aviso_heterogeneidade"] = (
            f"Amostra heterogênea detectada (CV={cv:.2f}, "
            f"razão max/min={razao_max_min:.1f}x). "
            "O mesmo CATMAT/PDM pode estar agregando produtos com "
            "perfis técnicos distintos (caso típico: notebook corporativo "
            "vs chromebook educacional no PDM 8435). "
            "**Hipótese alternativa — CATMAT-balde**: heterogeneidade alta "
            "também é sinal forte de que o código escolhido tem descrição "
            "oficial restrita (ex.: 'até 4GB RAM, sem SSD') mas órgãos "
            "estão cotando produtos diversos sob o mesmo código. "
            f"Confirme chamando `compras_catmat_consultar({codigo_item_catalogo})` "
            "e comparando `descricaoItem` com a spec do TR. Se houver "
            "divergência, o CATMAT correto provavelmente é outro item do "
            "mesmo PDM. "
            "Próximo passo: inspecione `clusters[]` e considere ajuste "
            "motivado do recorte amostral (IN 65/2021 art. 9º §1º) escolhendo "
            "apenas o cluster compatível com a spec, ou troque de CATMAT."
        )
    await _compostas_cache.set(cache_key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_checar_sancoes_fornecedor(
    cnpj: Annotated[
        str, Field(description=desc(CheckarSancoesFornecedorInput, "cnpj"))
    ],
) -> dict[str, Any]:
    """Consolida sanções de um fornecedor (CEIS + CNEP + CEPIM + leniência + impedimentos).

    Composição: chama em paralelo as listas do Portal da Transparência e os
    impedimentos do Comprasnet. Retorna um veredito booleano + lista
    consolidada de sanções ativas.

    Levanta `ComprasAuthError` se `TRANSPARENCIA_API_KEY` não estiver configurada.
    Sempre use antes de homologar pregões/contratos. Cache 10 min.
    """
    started = time.perf_counter()
    cnpj_clean_pre = _so_digitos(cnpj) or ""
    cache_key = _ck("sancoes_check", cnpj_clean_pre)
    cached = await _compostas_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()
    if not settings.transparencia_api_key:
        raise ComprasAuthError(
            "TRANSPARENCIA_API_KEY não configurada. Esta tool depende do "
            "Portal da Transparência. Cadastre em "
            "https://api.portaldatransparencia.gov.br/api-de-dados/cadastrar-email"
        )

    cnpj_clean = _so_digitos(cnpj) or ""

    async def _ceis() -> list[dict[str, Any]]:
        async with make_transparencia(settings) as c:
            resp = await c.list_ceis(cnpj_sancionado=cnpj_clean, pagina=1)
        return resp if isinstance(resp, list) else (resp.get("data") or [])

    async def _cnep() -> list[dict[str, Any]]:
        async with make_transparencia(settings) as c:
            resp = await c.list_cnep(cnpj_sancionado=cnpj_clean, pagina=1)
        return resp if isinstance(resp, list) else (resp.get("data") or [])

    async def _cepim() -> list[dict[str, Any]]:
        async with make_transparencia(settings) as c:
            resp = await c.list_cepim(cnpj_entidade=cnpj_clean, pagina=1)
        return resp if isinstance(resp, list) else (resp.get("data") or [])

    async def _leniencia() -> list[dict[str, Any]]:
        async with make_transparencia(settings) as c:
            resp = await c.list_acordos_leniencia(
                cnpj_sancionado=cnpj_clean, pagina=1
            )
        return resp if isinstance(resp, list) else (resp.get("data") or [])

    ceis, cnep, cepim, leniencia = await asyncio.gather(
        _ceis(), _cnep(), _cepim(), _leniencia(), return_exceptions=True
    )

    resultados: dict[str, Any] = {}
    erros: dict[str, str] = {}
    for nome, valor in (
        ("ceis", ceis),
        ("cnep", cnep),
        ("cepim", cepim),
        ("acordos_leniencia", leniencia),
    ):
        if isinstance(valor, Exception):
            erros[nome] = f"{type(valor).__name__}: {valor}"
            resultados[nome] = []
        else:
            resultados[nome] = valor

    total_sancoes = sum(len(v) for v in resultados.values() if isinstance(v, list))

    payload: dict[str, Any] = {
        "cnpj_consultado": cnpj_clean,
        "tem_sancao_ativa": total_sancoes > 0,
        "quantidade_sancoes_total": total_sancoes,
        "sancoes_por_cadastro": {k: len(v) for k, v in resultados.items()},
        "detalhamento": resultados,
        "fontes_com_erro": erros,
        "_cache_hit": False,
    }
    await _compostas_cache.set(cache_key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_montar_dossie_arp(
    numero_controle_pncp_ata: Annotated[
        str,
        Field(
            description=(
                "Identificador PNCP completo da **ata** (formato "
                "`cnpj14-1-sequencial/ano-NNNNNN`, com sufixo numerando a "
                "ata SRP dentro da compra). Ex.: "
                "`00394452000103-1-004729/2024-000006`. NÃO confundir com "
                "ID de compra (sem o sufixo). Retornado em "
                "`compras_arp_por_fim_vigencia` como `numeroControlePncpAta`."
            ),
        ),
    ],
    numero_ata: Annotated[
        str,
        Field(
            description=(
                "Número simples da ata (ex.: '00001/2024'). Usado nos "
                "endpoints de saldo, adesões e unidades participantes."
            ),
        ),
    ],
    unidade_gerenciadora: Annotated[
        int,
        Field(description="Código UASG da unidade gerenciadora da ata."),
    ],
    numero_item: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Número do item dentro da ata. Se informado, traz também "
                "saldo, adesões e unidades participantes daquele item. "
                "Se omitido, apenas o cabeçalho é consultado."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Dossiê completo de uma ARP em uma chamada.

    Composição: cabeçalho via `/modulo-arp/1.1` (id PNCP) e — se `numero_item`
    informado — saldo (4), adesões (5) e unidades participantes (3) em
    paralelo. Os 3 últimos endpoints usam a chave composta
    `numeroAta + unidadeGerenciadora`.

    Os 3 IDs vêm naturalmente do retorno de `compras_arp_listar` ou
    `compras_arp_itens_listar` (campos: `numeroControlePncpAta`,
    `numeroAta`, `unidadeGerenciadora`, `numeroItem`). Cache 10 min.

    Quando `numero_controle_pncp_ata` vem no formato de **compra** (sem
    sufixo `-NNNNNN`), devolvemos diagnóstico explícito antes de bater
    no upstream — caminho que retornava `cabecalho: null` silencioso.
    """
    started = time.perf_counter()

    # Mesma validação aplicada em `compras_arp_consultar` v0.3.7.
    import re as _re

    _rx_id_ata = _re.compile(r"^\d{14}-\d+-\d+/\d{4}-\d{6}$")
    _rx_id_compra = _re.compile(r"^\d{14}-\d+-\d+/\d{4}$")
    nca = (numero_controle_pncp_ata or "").strip()
    if not _rx_id_ata.fullmatch(nca):
        erro = {
            "encontrado": False,
            "numero_controle_pncp_ata": nca,
            "numero_ata": numero_ata,
            "unidade_gerenciadora": unidade_gerenciadora,
            "numero_item": numero_item,
            "_erro_upstream": {
                "tipo": "formato_id_invalido",
                "diagnostico": (
                    "Formato esperado: `cnpj14-1-sequencial/ano-NNNNNN` "
                    "(ID de ATA). Compras SRP multi-fornecedor produzem "
                    "várias atas dentro da mesma compra; o sufixo "
                    "`-NNNNNN` numera a ata específica."
                ),
                "id_recebido": nca,
            },
            "_cache_hit": False,
        }
        if _rx_id_compra.fullmatch(nca):
            erro["_erro_upstream"]["diagnostico_especifico"] = (
                "Você passou um ID de COMPRA. Liste as atas dessa compra "
                "via `compras_arp_listar` e use os `numeroControlePncpAta` "
                "que vêm com o sufixo `-NNNNNN`."
            )
        return with_latency(erro, started)

    cache_key = _ck(
        "dossie_arp",
        nca,
        numero_ata,
        unidade_gerenciadora,
        numero_item,
    )
    cached = await _compostas_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()

    async def _cabecalho() -> dict[str, Any] | None:
        async with make_dados_abertos(settings) as c:
            resp = await c.list_resource(
                "/modulo-arp/1.1_consultarARP_Id",
                pagina=1,
                tamanho_pagina=10,
                numeroControlePncpAta=nca,
            )
        items = resp.get("resultado") or []
        return items[0] if items else None

    async def _saldo() -> list[dict[str, Any]]:
        async with make_dados_abertos(settings) as c:
            resp = await c.list_resource(
                "/modulo-arp/4_consultarEmpenhosSaldoItem",
                pagina=1,
                tamanho_pagina=500,
                numeroAta=numero_ata,
                unidadeGerenciadora=unidade_gerenciadora,
            )
        return resp.get("resultado") or []

    async def _adesoes() -> list[dict[str, Any]]:
        async with make_dados_abertos(settings) as c:
            resp = await c.list_resource(
                "/modulo-arp/5_consultarAdesoesItem",
                pagina=1,
                tamanho_pagina=500,
                numeroAta=numero_ata,
                unidadeGerenciadora=unidade_gerenciadora,
                numeroItem=numero_item,
            )
        return resp.get("resultado") or []

    async def _unidades() -> list[dict[str, Any]]:
        async with make_dados_abertos(settings) as c:
            resp = await c.list_resource(
                "/modulo-arp/3_consultarUnidadesItem",
                pagina=1,
                tamanho_pagina=500,
                numeroAta=numero_ata,
                unidadeGerenciadora=unidade_gerenciadora,
                numeroItem=numero_item,
            )
        return resp.get("resultado") or []

    if numero_item is None:
        cabecalho = await _cabecalho()
        saldo: list[dict[str, Any]] = []
        adesoes: list[dict[str, Any]] = []
        unidades: list[dict[str, Any]] = []
    else:
        cabecalho, saldo, adesoes, unidades = await asyncio.gather(
            _cabecalho(), _saldo(), _adesoes(), _unidades()
        )

    payload: dict[str, Any] = {
        "numero_controle_pncp_ata": nca,
        "numero_ata": numero_ata,
        "unidade_gerenciadora": unidade_gerenciadora,
        "numero_item": numero_item,
        "encontrada": cabecalho is not None,
        "ata": cabecalho,
        "saldo_por_empenho": saldo,
        "adesoes_realizadas": adesoes,
        "unidades_participantes": unidades,
        "totalizadores": {
            "qtd_adesoes": len(adesoes),
            "qtd_unidades_participantes": len(unidades),
        },
        "aviso_se_omitiu_item": (
            "Para obter saldo, adesões e unidades, informe `numero_item`. "
            "Use `compras_arp_itens_listar` para listar itens da ata."
            if numero_item is None
            else None
        ),
        "_cache_hit": False,
    }

    # Diagnóstico para o caso "cabeçalho ok, mas saldo/adesões/unidades
    # vazios" — achado bateria A v0.3.5. Hipóteses (em ordem de
    # probabilidade): (1) ata recém-assinada, upstream ainda não populou
    # os endpoints auxiliares; (2) `numero_item` não corresponde a item
    # real da ata; (3) `unidade_gerenciadora` errada.
    if (
        numero_item is not None
        and cabecalho is not None
        and not saldo
        and not adesoes
        and not unidades
    ):
        # Tenta múltiplas fontes de data para calcular idade da ata.
        # Achado bateria A v0.3.8: ata UFPR 90290/2025 não trouxe dias
        # calculados porque algumas fontes têm `dataAssinatura` em formato
        # diferente ou ausente; outras só têm `dataVigenciaInicial`.
        dias_desde_assinatura: int | None = None
        campo_data_usado: str | None = None
        if isinstance(cabecalho, dict):
            from datetime import date as _date

            for campo in ("dataAssinatura", "dataVigenciaInicial", "dataInicialVigencia"):
                valor = cabecalho.get(campo)
                if not valor:
                    continue
                texto = str(valor)
                try:
                    if len(texto) >= 10 and texto[4] == "-":
                        # ISO YYYY-MM-DD
                        ano, mes, dia = int(texto[:4]), int(texto[5:7]), int(texto[8:10])
                    elif len(texto) >= 10 and texto[2] == "/":
                        # BR DD/MM/YYYY
                        dia, mes, ano = int(texto[:2]), int(texto[3:5]), int(texto[6:10])
                    else:
                        continue
                    dias_desde_assinatura = (_date.today() - _date(ano, mes, dia)).days
                    campo_data_usado = campo
                    break
                except (ValueError, IndexError):
                    continue

        # Frase de idade: sempre presente quando temos a data, independente
        # de threshold. Antes só anexava se dias < 60.
        if dias_desde_assinatura is not None:
            sinal = (
                "provavelmente upstream ainda não populou empenhos/adesões/"
                "unidades para esta ata"
                if dias_desde_assinatura < 60
                else "ata já com idade suficiente para ter dados populados — "
                "provavelmente saldo, adesões e unidades realmente estão vazios "
                "(item sem rateio ou ata sem caronas até agora)"
            )
            idade_txt = (
                f" Ata assinada há {dias_desde_assinatura} dias "
                f"(via campo `{campo_data_usado}`) — {sinal}."
            )
        else:
            idade_txt = (
                " Não foi possível extrair `dataAssinatura` nem "
                "`dataVigenciaInicial` do cabeçalho para estimar a idade."
            )

        payload["aviso_dados_auxiliares_indisponiveis"] = (
            "Cabeçalho da ata foi encontrado mas saldo, adesões e unidades "
            f"participantes vieram vazios.{idade_txt} "
            "Hipóteses (em ordem de probabilidade): "
            "(1) ata recém-assinada — Dados Abertos demora dias/semanas "
            "para popular `/modulo-arp/3/4/5` após assinatura; "
            "(2) `numero_item` informado não corresponde a item real desta "
            "ata — confirme com `compras_arp_itens_listar`; "
            "(3) `unidade_gerenciadora` divergente — use o "
            "`codigoUnidadeGerenciadora` que veio no cabeçalho da ata, "
            f"que foi `{(cabecalho or {}).get('codigoUnidadeGerenciadora', '?')}`."
        )
        payload["_dias_desde_assinatura"] = dias_desde_assinatura

    await _compostas_cache.set(cache_key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_buscar_contratacoes_similares(
    codigo_catmat: Annotated[
        int | None,
        Field(
            default=None,
            description=desc(BuscarContratacoesSimilaresInput, "codigo_catmat"),
        ),
    ] = None,
    codigo_catser: Annotated[
        int | None,
        Field(
            default=None,
            description=desc(BuscarContratacoesSimilaresInput, "codigo_catser"),
        ),
    ] = None,
    periodo_meses: Annotated[
        int,
        Field(description=desc(BuscarContratacoesSimilaresInput, "periodo_meses")),
    ] = 12,
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(BuscarContratacoesSimilaresInput, "uf")),
    ] = None,
    max_resultados: Annotated[
        int,
        Field(description=desc(BuscarContratacoesSimilaresInput, "max_resultados")),
    ] = 20,
) -> dict[str, Any]:
    """Federa Dados Abertos + PNCP buscando contratações similares.

    Composição: consulta resultados homologados (Dados Abertos 14.133) +
    publicações PNCP filtrando pelo CATMAT/CATSER do item alvo, deduplica
    por CNPJ órgão + ano + sequencial e devolve os `max_resultados` mais
    recentes. Insumo para mapear benchmarks de outros órgãos.

    **Atenção latência**: chama o PNCP em 3 modalidades (Pregão, Dispensa,
    Concorrência) em paralelo. Cada chamada PNCP costuma levar 30-60s — o
    tempo total da composta tende a 60-90s quando o cache está frio. Com
    Redis configurado as chamadas seguintes voltam em <1s.
    """
    started = time.perf_counter()
    if not codigo_catmat and not codigo_catser:
        raise ValueError("Informe pelo menos um codigo_catmat ou codigo_catser.")

    cache_key = _ck(
        "similares",
        codigo_catmat,
        codigo_catser,
        periodo_meses,
        uf,
        max_resultados,
    )
    cached = await _compostas_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()
    hoje = date.today()
    data_inicio = hoje - timedelta(days=periodo_meses * 30)

    async def _dados_abertos_resultados() -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "dataResultadoPncpInicial": format_date(data_inicio, "dados_abertos"),
            "dataResultadoPncpFinal": format_date(hoje, "dados_abertos"),
        }
        if codigo_catmat is not None:
            params["codigoItemCatalogo"] = codigo_catmat
        if codigo_catser is not None:
            params["codigoItemCatalogo"] = codigo_catser
        async with make_dados_abertos(settings) as c:
            resp = await c.list_resource(
                "/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133",
                pagina=1,
                tamanho_pagina=max(max_resultados * 2, 100),
                **params,
            )
        return resp.get("resultado") or []

    # Tentativa best-effort no PNCP — codigoModalidade é obrigatório, então
    # disparamos as 3 modalidades mais comuns EM PARALELO (cada chamada PNCP
    # pode demorar 30-60s sozinha; em sequência o p99 ficava em ~3 min).
    async def _pncp_uma_modalidade(mod: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "dataInicial": format_date(data_inicio, "pncp"),
            "dataFinal": format_date(hoje, "pncp"),
            "codigoModalidadeContratacao": mod,
        }
        if uf:
            params["uf"] = uf.upper()
        try:
            async with make_pncp(settings) as c:
                resp = await c.list_resource(
                    "/v1/contratacoes/publicacao",
                    pagina=1,
                    tamanho_pagina=max(max_resultados * 2, 100),
                    **params,
                )
            return resp.get("data") or []
        except Exception:
            return []

    async def _pncp_publicacoes() -> list[dict[str, Any]]:
        modalidades_comuns = [6, 8, 4]  # Pregão, Dispensa, Concorrência
        resultados = await asyncio.gather(
            *(_pncp_uma_modalidade(m) for m in modalidades_comuns),
            return_exceptions=True,
        )
        items: list[dict[str, Any]] = []
        for r in resultados:
            if isinstance(r, list):
                items.extend(r)
        return items

    da_items, pncp_items = await asyncio.gather(
        _dados_abertos_resultados(), _pncp_publicacoes(), return_exceptions=True
    )
    da_items = da_items if isinstance(da_items, list) else []
    pncp_items = pncp_items if isinstance(pncp_items, list) else []

    # Deduplica por chave composta (cnpj + ano + sequencial quando disponível)
    chaves_vistas: set[str] = set()
    consolidado: list[dict[str, Any]] = []
    for item in da_items + pncp_items:
        chave = (
            f"{item.get('cnpjOrgao') or item.get('orgaoEntidade', {}).get('cnpj') or ''}"
            f"-{item.get('anoCompra') or item.get('ano') or ''}"
            f"-{item.get('sequencialCompra') or item.get('sequencial') or ''}"
        )
        if chave in chaves_vistas:
            continue
        chaves_vistas.add(chave)
        consolidado.append(item)
        if len(consolidado) >= max_resultados:
            break

    payload: dict[str, Any] = {
        "codigo_catmat": codigo_catmat,
        "codigo_catser": codigo_catser,
        "periodo_meses": periodo_meses,
        "uf": uf,
        "amostra_dados_abertos": len(da_items),
        "amostra_pncp": len(pncp_items),
        "consolidado_unico": len(consolidado),
        "resultado": consolidado,
        "_cache_hit": False,
    }
    # **Política deliberada**: NÃO cachear resultado vazio porque o
    # upstream pode demorar a indexar contratações novas e cachear "0
    # similares" por 10 min serviria informação obsoleta em janelas
    # críticas de homologação. Resultados não-vazios seguem cache normal.
    if len(consolidado) > 0:
        await _compostas_cache.set(cache_key, json.loads(json.dumps(payload, default=str)))
    else:
        payload["_aviso_no_cache_vazio"] = (
            "Resultado vazio não foi cacheado — refazer a consulta em "
            "alguns minutos pode trazer dados recém-publicados."
        )
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_perfil_fornecedor_completo(
    cnpj: Annotated[
        str,
        Field(
            description="CNPJ do fornecedor (14 dígitos, com ou sem pontuação).",
            min_length=11,
            max_length=20,
        ),
    ],
) -> dict[str, Any]:
    """Perfil consolidado do fornecedor (cadastro + Receita + sanções + impedimentos).

    Composição em paralelo:
    - **cadastro**: Dados Abertos `/modulo-fornecedor/1_consultarFornecedor`
      pelo CNPJ (razão social, CNAE, porte, natureza jurídica);
    - **receita_federal**: BrasilAPI / MinhaReceita — QSA, capital social,
      atividades secundárias, data de início, situação cadastral (RF).
      Provider configurável via `CNPJ_PROVIDER` (default `brasilapi`);
    - **sanções**: Portal da Transparência (CEIS+CNEP+CEPIM) pelo CNPJ;
    - **impedimentos Comprasnet**: `/api/comprasnet/compras/impedimentos`.

    **Não inclui lista de contratos** porque os endpoints upstream
    `/modulo-contratos/1` (Dados Abertos) e `/v1/contratos` (PNCP) exigem
    `codigoOrgao` como filtro obrigatório — não é possível listar contratos
    de um fornecedor sem saber em qual órgão ele tem contrato. Se você já
    souber o órgão, use `compras_contratos_listar(codigo_orgao=X, ni_fornecedor=Y, ...)`.

    Sanções dependem de `TRANSPARENCIA_API_KEY` — se não configurada ou se
    o WAF da CGU bloquear, o bloco retorna aviso e o restante segue.

    Cache 10 min.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj) or ""
    cache_key = _ck("perfil_fornecedor", cnpj_clean)
    cached = await _compostas_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()

    async def _cadastro() -> dict[str, Any] | None:
        async with make_dados_abertos(settings) as c:
            resp = await c.list_resource(
                "/modulo-fornecedor/1_consultarFornecedor",
                pagina=1,
                tamanho_pagina=10,  # upstream exige >=10
                cnpj=cnpj_clean,
                ativo="true",
            )
        items = resp.get("resultado") or []
        return items[0] if items else None

    async def _sancoes() -> dict[str, Any]:
        if not settings.transparencia_api_key:
            return {
                "habilitado": False,
                "aviso": (
                    "TRANSPARENCIA_API_KEY não configurada — bloco de sanções omitido."
                ),
            }
        # Reaproveita a composta abaixo? Não — para evitar overhead de tool. Inline:
        async def _ceis() -> list[dict[str, Any]]:
            async with make_transparencia(settings) as c:
                r = await c.list_ceis(cnpj_sancionado=cnpj_clean, pagina=1)
            return r if isinstance(r, list) else (r.get("data") or [])

        async def _cnep() -> list[dict[str, Any]]:
            async with make_transparencia(settings) as c:
                r = await c.list_cnep(cnpj_sancionado=cnpj_clean, pagina=1)
            return r if isinstance(r, list) else (r.get("data") or [])

        async def _cepim() -> list[dict[str, Any]]:
            async with make_transparencia(settings) as c:
                r = await c.list_cepim(cnpj_entidade=cnpj_clean, pagina=1)
            return r if isinstance(r, list) else (r.get("data") or [])

        ceis, cnep, cepim = await asyncio.gather(
            _ceis(), _cnep(), _cepim(), return_exceptions=True
        )
        return {
            "habilitado": True,
            "ceis": ceis if isinstance(ceis, list) else [],
            "cnep": cnep if isinstance(cnep, list) else [],
            "cepim": cepim if isinstance(cepim, list) else [],
            "total": sum(
                len(x) for x in (ceis, cnep, cepim) if isinstance(x, list)
            ),
        }

    async def _impedimentos_comprasnet() -> list[dict[str, Any]] | dict[str, Any]:
        try:
            async with make_comprasnet(settings) as c:
                return await c.post_json(
                    "/comprasnet/compras/impedimentos", {"cnpj": cnpj_clean}
                )
        except Exception as e:
            return {"erro": f"{type(e).__name__}: {e}"}

    async def _receita() -> dict[str, Any]:
        """Enriquecimento via BrasilAPI/MinhaReceita (QSA, capital, CNAEs, situação RF)."""
        try:
            async with make_cnpj_client(settings) as c:
                return await c.consultar(cnpj_clean)
        except Exception as e:
            return {"erro": f"{type(e).__name__}: {e}"}

    cadastro, sancoes, impedimentos, receita = await asyncio.gather(
        _cadastro(),
        _sancoes(),
        _impedimentos_comprasnet(),
        _receita(),
        return_exceptions=True,
    )

    def _safe(v: Any) -> Any:
        return None if isinstance(v, Exception) else v

    payload: dict[str, Any] = {
        "cnpj_consultado": cnpj_clean,
        "cadastro": _safe(cadastro),
        "receita_federal": _safe(receita),
        "sancoes": _safe(sancoes),
        "impedimentos_comprasnet": _safe(impedimentos),
        "tem_alguma_restricao": bool(
            (isinstance(sancoes, dict) and sancoes.get("total", 0) > 0)
            or (isinstance(impedimentos, list) and len(impedimentos) > 0)
        ),
        "_aviso_contratos": (
            "Lista de contratos NÃO incluída — os endpoints upstream exigem "
            "codigo_orgao como filtro obrigatório. Para listar contratos deste "
            "fornecedor em um órgão específico, use compras_contratos_listar."
        ),
        "_cache_hit": False,
    }
    await _compostas_cache.set(cache_key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
