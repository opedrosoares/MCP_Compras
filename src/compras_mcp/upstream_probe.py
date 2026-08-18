"""Motor de probe das rotas upstream — usado pelo script CLI e pelo healthcheck.

Diferente dos clients em `clients/`, aqui **não** há retry nem tradução de
erro para exceção: o probe precisa observar o status HTTP cru. Um 404 é o
dado mais importante que temos (foi como a rota 1 quebrou em 2026-08), e
um retry só mascararia latência.

Classificação de status:

- `ok`        — HTTP 2xx e todos os `campos_esperados` presentes no 1º item.
- `degradado` — HTTP 2xx mas faltam campos esperados, ou veio vazio numa
                rota que deveria ter dados. É o caso silencioso: a tool
                responde, o LLM formata bonito, e o analista leva para o
                processo um payload sem preço nenhum.
- `fora`      — erro HTTP, timeout ou resposta não-JSON.
- `pulado`    — falta credencial (ex.: TRANSPARENCIA_API_KEY).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from compras_mcp.config import Settings, get_settings
from compras_mcp.upstream_registry import ROTAS, RotaUpstream, rota_por_id

STATUS_OK = "ok"
STATUS_DEGRADADO = "degradado"
STATUS_FORA = "fora"
STATUS_PULADO = "pulado"


@dataclass
class ResultadoProbe:
    rota_id: str
    api: str
    modulo: str
    path: str
    status: str
    http_status: int | None
    latencia_ms: float
    registros: int | None
    chaves_primeiro_item: list[str]
    campos_faltando: list[str]
    tools: list[str]
    detalhe: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base_url(api: str, settings: Settings) -> str:
    return {
        "dados_abertos": settings.dados_abertos_base_url,
        "pncp": settings.pncp_base_url,
        "transparencia": settings.transparencia_base_url,
        "comprasnet": settings.comprasnet_contratos_base_url,
        "cnpj": settings.brasilapi_base_url,
    }[api]


def _headers(api: str, settings: Settings, accept: str = "application/json") -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": "compras-mcp-probe/1.0 (+https://github.com/opedrosoares/MCP_Compras)",
    }
    if api == "transparencia" and settings.transparencia_api_key:
        headers["chave-api-dados"] = settings.transparencia_api_key
    if api == "comprasnet" and settings.comprasnet_bearer_token:
        headers["Authorization"] = f"Bearer {settings.comprasnet_bearer_token}"
    return headers


def _credencial_ausente(rota: RotaUpstream, settings: Settings) -> bool:
    if rota.exige_credencial == "TRANSPARENCIA_API_KEY":
        return not settings.transparencia_api_key
    return False


def _extrair_itens(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Normaliza os 3 envelopes upstream para (itens, total_registros)."""
    if isinstance(payload, list):
        return ([x for x in payload if isinstance(x, dict)], len(payload))
    if isinstance(payload, dict):
        for chave in ("resultado", "data", "_embedded"):
            valor = payload.get(chave)
            if isinstance(valor, list):
                total = payload.get("totalRegistros")
                itens = [x for x in valor if isinstance(x, dict)]
                return (itens, int(total) if total is not None else len(valor))
        # Recurso singular (ex.: BrasilAPI CNPJ) conta como 1 item.
        return ([payload], 1)
    return ([], None)


def _ler_campo(item: dict[str, Any], caminho: str) -> Any:
    """Lê `campo` ou `objeto.campo` de um item de resposta.

    O PNCP aninha o CNPJ em `orgaoEntidade.cnpj`; sem caminho pontuado, as
    rotas por órgão precisariam de um CNPJ fixo no código.
    """
    atual: Any = item
    for parte in caminho.split("."):
        if not isinstance(atual, dict):
            return None
        atual = atual.get(parte)
    return atual


def _params_com_paginacao(rota: RotaUpstream) -> dict[str, Any]:
    params = dict(rota.params)
    # Dados Abertos e PNCP compartilham a convenção pagina/tamanhoPagina, e
    # ambos rejeitam tamanhoPagina < 10. Transparência e Comprasnet têm
    # convenção própria e não recebem default aqui.
    if rota.api in ("dados_abertos", "pncp"):
        params.setdefault("pagina", 1)
        params.setdefault("tamanhoPagina", 10)
    return params


async def _probe_uma(
    rota: RotaUpstream,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    timeout: float,
    seeds: dict[str, dict[str, Any]],
) -> ResultadoProbe:
    base = ResultadoProbe(
        rota_id=rota.id,
        api=rota.api,
        modulo=rota.modulo,
        path=rota.path,
        status=STATUS_FORA,
        http_status=None,
        latencia_ms=0.0,
        registros=None,
        chaves_primeiro_item=[],
        campos_faltando=[],
        tools=list(rota.tools),
    )

    if _credencial_ausente(rota, settings):
        base.status = STATUS_PULADO
        base.detalhe = f"{rota.exige_credencial} não configurada"
        return base

    params = _params_com_paginacao(rota)

    # Rotas dependentes de ID puxam o valor da rota-pai (evita ID volátil no código).
    if rota.seed is not None:
        pai_id, mapa = rota.seed
        item_pai = seeds.get(pai_id)
        if not item_pai:
            base.status = STATUS_PULADO
            base.detalhe = f"sem seed: rota-pai '{pai_id}' não devolveu item"
            return base
        for param, campo in mapa.items():
            valor = _ler_campo(item_pai, campo)
            if valor is None:
                base.status = STATUS_PULADO
                base.detalhe = f"seed incompleto: '{campo}' ausente em '{pai_id}'"
                return base
            params[param] = valor

    # Params de path saem da query e entram na URL.
    path = rota.path
    if rota.path_params:
        faltando_no_path = [p for p in rota.path_params if p not in params]
        if faltando_no_path:
            base.status = STATUS_PULADO
            base.detalhe = f"sem valor para path param: {', '.join(faltando_no_path)}"
            return base
        path = rota.path.format(**{p: params[p] for p in rota.path_params})
        base.path = path
        for p in rota.path_params:
            params.pop(p, None)

    timeout_efetivo = rota.timeout_s or timeout
    started = time.perf_counter()
    try:
        resp = await client.get(
            f"{_base_url(rota.api, settings)}{path}",
            params=params,
            headers=_headers(rota.api, settings, rota.accept),
            timeout=timeout_efetivo,
        )
    except httpx.TimeoutException:
        base.latencia_ms = round((time.perf_counter() - started) * 1000, 1)
        base.detalhe = f"timeout > {timeout_efetivo}s"
        return base
    except httpx.HTTPError as e:
        base.latencia_ms = round((time.perf_counter() - started) * 1000, 1)
        base.detalhe = f"erro de transporte: {type(e).__name__}"
        return base

    base.latencia_ms = round((time.perf_counter() - started) * 1000, 1)
    base.http_status = resp.status_code

    if not resp.is_success:
        base.detalhe = resp.text[:160].replace("\n", " ").strip()
        return base

    # Rotas CSV respondem 200 text/csv — sucesso, sem contrato de campos JSON.
    content_type = resp.headers.get("content-type", "").lower()
    if "json" not in content_type:
        base.status = STATUS_OK
        base.registros = None
        base.detalhe = f"content-type {content_type.split(';')[0] or 'desconhecido'}"
        return base

    try:
        payload = resp.json()
    except ValueError:
        base.detalhe = "resposta não-JSON apesar do content-type"
        return base

    itens, total = _extrair_itens(payload)
    base.registros = total
    if itens:
        base.chaves_primeiro_item = sorted(itens[0].keys())
        seeds[rota.id] = itens[0]

    if not itens:
        base.status = STATUS_OK if rota.aceita_vazio else STATUS_DEGRADADO
        base.detalhe = "amostra vazia" + ("" if rota.aceita_vazio else " (esperava dados)")
        return base

    faltando = [c for c in rota.campos_esperados if c not in itens[0]]
    base.campos_faltando = faltando
    base.status = STATUS_DEGRADADO if faltando else STATUS_OK
    if faltando:
        base.detalhe = f"campos ausentes no payload: {', '.join(faltando)}"
    elif rota.observacao:
        base.detalhe = rota.observacao
    return base


def _foi_timeout(resultado: ResultadoProbe) -> bool:
    """Distingue "estourou o relógio" de "o servidor respondeu erro"."""
    return (
        resultado.status == STATUS_FORA
        and resultado.http_status is None
        and resultado.detalhe.startswith("timeout")
    )


async def executar_probe(
    rotas: tuple[RotaUpstream, ...] | list[RotaUpstream] = ROTAS,
    *,
    timeout: float = 15.0,
    concorrencia: int = 8,
    settings: Settings | None = None,
    reconfirmar_timeouts: bool = False,
) -> list[ResultadoProbe]:
    """Roda o probe em paralelo e devolve um resultado por rota.

    Rotas com `seed` dependem da rota-pai, então rodam numa segunda onda —
    é o único acoplamento de ordem no probe.

    `reconfirmar_timeouts` reexecuta **em série** apenas as rotas que
    estouraram o relógio. Sob 59 rotas concorrentes uma rota apenas lenta
    (caso real: `compras_arp_itens_listar`, 2,5s isolada) passa dos 12s e
    seria reportada como fora — falso alarme que, numa checagem pré-uso,
    custa tanto quanto o alarme que não toca. Erro HTTP não é reexecutado:
    500 e 404 são resposta do servidor, não pressão do probe.
    """
    settings = settings or get_settings()
    seeds: dict[str, dict[str, Any]] = {}
    semaforo = asyncio.Semaphore(concorrencia)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:

        async def _com_limite(rota: RotaUpstream) -> ResultadoProbe:
            async with semaforo:
                return await _probe_uma(
                    rota, client, settings, timeout=timeout, seeds=seeds
                )

        primeira_onda = [r for r in rotas if r.seed is None]
        segunda_onda = [r for r in rotas if r.seed is not None]

        resultados = list(await asyncio.gather(*(_com_limite(r) for r in primeira_onda)))
        if segunda_onda:
            resultados += list(
                await asyncio.gather(*(_com_limite(r) for r in segunda_onda))
            )

        if reconfirmar_timeouts:
            por_id = {r.id: r for r in rotas}
            for i, anterior in enumerate(resultados):
                if not _foi_timeout(anterior) or anterior.rota_id not in por_id:
                    continue
                novo = await _probe_uma(
                    por_id[anterior.rota_id],
                    client,
                    settings,
                    timeout=timeout,
                    seeds=seeds,
                )
                if novo.status == STATUS_FORA:
                    novo.detalhe = f"{novo.detalhe} (2 tentativas)"
                else:
                    novo.detalhe = (
                        f"lenta sob carga: só respondeu na 2ª tentativa, "
                        f"em série ({novo.latencia_ms:.0f}ms). {novo.detalhe}".strip()
                    )
                resultados[i] = novo

    ordem = {r.id: i for i, r in enumerate(rotas)}
    resultados.sort(key=lambda r: ordem.get(r.rota_id, 999))
    return resultados


def resumir_por_modulo(resultados: list[ResultadoProbe]) -> dict[str, dict[str, Any]]:
    """Agrega os resultados por módulo funcional.

    Um módulo é `fora` se toda rota testável caiu, `degradado` se alguma
    caiu ou veio sem os campos do contrato, `ok` se todas passaram.
    """
    por_modulo: dict[str, dict[str, Any]] = {}
    for r in resultados:
        m = por_modulo.setdefault(
            r.modulo,
            {
                "situacao": STATUS_OK,
                "rotas_ok": 0,
                "rotas_degradadas": 0,
                "rotas_fora": 0,
                "rotas_puladas": 0,
                "latencia_media_ms": 0.0,
                "problemas": [],
            },
        )
        if r.status == STATUS_OK:
            m["rotas_ok"] += 1
        elif r.status == STATUS_DEGRADADO:
            m["rotas_degradadas"] += 1
            m["problemas"].append(f"{r.path}: {r.detalhe}")
        elif r.status == STATUS_FORA:
            m["rotas_fora"] += 1
            m["problemas"].append(f"{r.path}: HTTP {r.http_status} — {r.detalhe}")
        else:
            m["rotas_puladas"] += 1

    for modulo, m in por_modulo.items():
        latencias = [r.latencia_ms for r in resultados if r.modulo == modulo and r.latencia_ms]
        m["latencia_media_ms"] = round(sum(latencias) / len(latencias), 1) if latencias else 0.0
        testaveis = m["rotas_ok"] + m["rotas_degradadas"] + m["rotas_fora"]
        if testaveis == 0:
            m["situacao"] = STATUS_PULADO
        elif m["rotas_fora"] == testaveis:
            m["situacao"] = STATUS_FORA
        elif m["rotas_fora"] or m["rotas_degradadas"]:
            m["situacao"] = STATUS_DEGRADADO
        else:
            m["situacao"] = STATUS_OK
    return por_modulo


__all__ = [
    "STATUS_DEGRADADO",
    "STATUS_FORA",
    "STATUS_OK",
    "STATUS_PULADO",
    "ResultadoProbe",
    "executar_probe",
    "resumir_por_modulo",
    "rota_por_id",
]
