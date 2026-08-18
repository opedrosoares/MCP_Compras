"""Tools de sanções (Portal da Transparência / CGU).

Endpoints cobertos:
- /api-de-dados/ceis              — Cadastro de Empresas Inidôneas e Suspensas
- /api-de-dados/cnep              — Cadastro Nacional de Empresas Punidas (Lei Anticorrupção)
- /api-de-dados/ceaf              — Cadastro de Expulsões da Administração Federal (servidores)
- /api-de-dados/cepim             — Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas
- /api-de-dados/acordos-leniencia — Acordos de leniência firmados com a CGU

**Pré-requisito:** TRANSPARENCIA_API_KEY no ambiente (cadastro gratuito em
api.portaldatransparencia.gov.br/api-de-dados/cadastrar-email). Sem ela,
estas tools levantam ComprasAuthError.

Rate limit do Portal: 30 req/min. Cache TTL 1h para reduzir consultas
repetidas (LGPD de servidores aplicado em CEAF).
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.access_control import apply_lgpd, aviso_lgpd
from compras_mcp.cache import cache_from_env
from compras_mcp.config import get_settings
from compras_mcp.errors import ComprasWafBlockError
from compras_mcp.mcp_instance import mcp
from compras_mcp.schemas import (
    ConsultarSancaoCNPJInput,
    ConsultarSancaoCPFInput,
)
from compras_mcp.tools._helpers import desc, make_transparencia, with_latency


_sancoes_cache = cache_from_env("SANCOES", default_ttl=3600, default_max_size=500)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _so_digitos(s: str | None) -> str | None:
    if s is None:
        return None
    return "".join(c for c in s if c.isdigit())


def _resposta_waf_block(cadastro: str, filtros: dict[str, Any]) -> dict[str, Any]:
    """Payload graceful quando o AWS WAF da CGU bloqueia a chamada.

    O Portal da Transparência (CGU) intermitentemente retorna HTTP 405 +
    HTML "Human Verification" mesmo com chave de API válida. Bug upstream
    confirmado, sem fix possível pelo cliente. Em vez de propagar
    `ComprasWafBlockError`, devolvemos payload informativo similar ao das
    tools `compras_uasg_*` (modulo morto) — mesmo padrão v0.2.8/v0.2.9.
    """
    return {
        "resultado": [],
        "_total_registros": 0,
        "_cache_hit": False,
        "_erro_upstream": {
            "endpoint": f"transparencia/api-de-dados/{cadastro}",
            "status": 405,
            "tipo": "waf_block",
            "diagnostico": (
                "Portal da Transparência (CGU) bloqueou a chamada via AWS WAF "
                "(HTTP 405 + HTML 'Human Verification'). Bug upstream "
                "intermitente — pode passar em retries espaçados ou nunca. "
                "Sem fix do lado do cliente: o WAF detecta padrões "
                "automatizados independente de chave de API válida."
            ),
            "filtros_tentados": filtros,
            "alternativas": [
                "Consulta manual via webapp: https://portaldatransparencia.gov.br/sancoes/ceis",
                "Aguardar 10-30 minutos e retentar (WAF tem janelas variáveis)",
                "Para verificação pontual de CNPJ específico, costuma passar; "
                "para listagens amplas o WAF bloqueia com mais frequência.",
                "Reportar à CGU se persistente: api.dadosabertos@cgu.gov.br",
            ],
        },
    }


@mcp.tool
async def compras_sancao_ceis(
    cnpj: Annotated[
        str | None,
        Field(default=None, description=desc(ConsultarSancaoCNPJInput, "cnpj")),
    ] = None,
    nome: Annotated[
        str | None,
        Field(
            default=None,
            description="Nome (razão social/fantasia) do sancionado para busca textual.",
        ),
    ] = None,
    orgao_sancionador: Annotated[
        str | None,
        Field(default=None, description="Sigla do órgão sancionador (ex.: 'TCU')."),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ConsultarSancaoCNPJInput, "pagina"))
    ] = 1,
) -> dict[str, Any]:
    """Consulta CEIS — Cadastro de Empresas Inidôneas e Suspensas.

    Endpoint `/api-de-dados/ceis`. Empresas com sanção ativa não podem
    contratar com a administração pública. Use **sempre** antes de
    homologar pregões e contratos.

    Cache 1h.
    """
    started = time.perf_counter()
    cnpj_limpo = _so_digitos(cnpj)
    key = _ck("ceis", cnpj_limpo, nome, orgao_sancionador, pagina)
    cached = await _sancoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()
    try:
        async with make_transparencia(settings) as client:
            resp = await client.list_ceis(
                cnpj_sancionado=cnpj_limpo,
                nome_sancionado=nome,
                orgao_sancionador=orgao_sancionador,
                pagina=pagina,
            )
    except ComprasWafBlockError:
        return with_latency(
            _resposta_waf_block("ceis", {"cnpj": cnpj_limpo, "nome": nome,
                                          "orgao_sancionador": orgao_sancionador,
                                          "pagina": pagina}),
            started,
        )
    items = resp if isinstance(resp, list) else (resp.get("data") or [])
    payload: dict[str, Any] = {
        "resultado": items,
        "_total_registros": len(items),
        "_pagina_atual": pagina,
        "_cache_hit": False,
    }
    await _sancoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_sancao_cnep(
    cnpj: Annotated[
        str | None,
        Field(default=None, description=desc(ConsultarSancaoCNPJInput, "cnpj")),
    ] = None,
    nome: Annotated[
        str | None,
        Field(default=None, description="Nome do sancionado."),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ConsultarSancaoCNPJInput, "pagina"))
    ] = 1,
) -> dict[str, Any]:
    """Consulta CNEP — Cadastro Nacional de Empresas Punidas (Lei Anticorrupção).

    Endpoint `/api-de-dados/cnep`. Empresas punidas pela Lei 12.846/2013
    (Lei Anticorrupção). Indicador de risco de integridade.

    Cache 1h.
    """
    started = time.perf_counter()
    cnpj_limpo = _so_digitos(cnpj)
    key = _ck("cnep", cnpj_limpo, nome, pagina)
    cached = await _sancoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_transparencia(get_settings()) as client:
            resp = await client.list_cnep(
                cnpj_sancionado=cnpj_limpo,
                nome_sancionado=nome,
                pagina=pagina,
            )
    except ComprasWafBlockError:
        return with_latency(
            _resposta_waf_block("cnep", {"cnpj": cnpj_limpo, "nome": nome, "pagina": pagina}),
            started,
        )
    items = resp if isinstance(resp, list) else (resp.get("data") or [])
    payload: dict[str, Any] = {
        "resultado": items,
        "_total_registros": len(items),
        "_pagina_atual": pagina,
        "_cache_hit": False,
    }
    await _sancoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_sancao_ceaf(
    cpf: Annotated[
        str | None,
        Field(default=None, description=desc(ConsultarSancaoCPFInput, "cpf")),
    ] = None,
    nome: Annotated[
        str | None,
        Field(default=None, description="Nome do servidor expulso (busca textual)."),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ConsultarSancaoCPFInput, "pagina"))
    ] = 1,
) -> dict[str, Any]:
    """Consulta CEAF — Cadastro de Expulsões da Administração Federal.

    Endpoint `/api-de-dados/ceaf`. Servidores expulsos do serviço público
    federal. Útil quando se identifica responsável/preposto suspeito.

    CPFs mascarados por LGPD (`123.***.***-45`). Cache 1h.
    """
    started = time.perf_counter()
    cpf_limpo = _so_digitos(cpf)
    key = _ck("ceaf", cpf_limpo, nome, pagina)
    cached = await _sancoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    settings = get_settings()
    try:
        async with make_transparencia(settings) as client:
            resp = await client.list_ceaf(
                cpf_sancionado=cpf_limpo,
                nome_sancionado=nome,
                pagina=pagina,
            )
    except ComprasWafBlockError:
        return with_latency(
            _resposta_waf_block("ceaf", {"cpf": cpf_limpo, "nome": nome, "pagina": pagina}),
            started,
        )
    items = resp if isinstance(resp, list) else (resp.get("data") or [])
    masked = apply_lgpd(items, incluir_cpf_completo=settings.incluir_cpf_completo)
    payload: dict[str, Any] = {
        "resultado": masked,
        "_total_registros": len(items),
        "_pagina_atual": pagina,
        "_aviso_lgpd": aviso_lgpd(),
        "_cache_hit": False,
    }
    await _sancoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_sancao_cepim(
    cnpj: Annotated[
        str | None,
        Field(default=None, description="CNPJ da entidade (14 dígitos)."),
    ] = None,
    nome: Annotated[
        str | None,
        Field(default=None, description="Nome da entidade (busca textual)."),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ConsultarSancaoCNPJInput, "pagina"))
    ] = 1,
) -> dict[str, Any]:
    """Consulta CEPIM — Entidades Privadas Sem Fins Lucrativos Impedidas.

    Endpoint `/api-de-dados/cepim`. Aplicável a contratações via convênios
    e termos de fomento com OSCs.

    Cache 1h.
    """
    started = time.perf_counter()
    cnpj_limpo = _so_digitos(cnpj)
    key = _ck("cepim", cnpj_limpo, nome, pagina)
    cached = await _sancoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_transparencia(get_settings()) as client:
            resp = await client.list_cepim(
                cnpj_entidade=cnpj_limpo,
                nome_entidade=nome,
                pagina=pagina,
            )
    except ComprasWafBlockError:
        return with_latency(
            _resposta_waf_block("cepim", {"cnpj": cnpj_limpo, "nome": nome, "pagina": pagina}),
            started,
        )
    items = resp if isinstance(resp, list) else (resp.get("data") or [])
    payload: dict[str, Any] = {
        "resultado": items,
        "_total_registros": len(items),
        "_pagina_atual": pagina,
        "_cache_hit": False,
    }
    await _sancoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool
async def compras_sancao_acordos_leniencia(
    cnpj: Annotated[
        str | None,
        Field(default=None, description="CNPJ do sancionado (14 dígitos)."),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ConsultarSancaoCNPJInput, "pagina"))
    ] = 1,
) -> dict[str, Any]:
    """Lista acordos de leniência firmados com a CGU.

    Endpoint `/api-de-dados/acordos-leniencia`. Empresas com acordo ativo
    estão sob compromisso de compliance reforçado — informação útil para
    análise de risco em contratações de alto valor.

    Cache 1h.
    """
    started = time.perf_counter()
    cnpj_limpo = _so_digitos(cnpj)
    key = _ck("leniencia", cnpj_limpo, pagina)
    cached = await _sancoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    try:
        async with make_transparencia(get_settings()) as client:
            resp = await client.list_acordos_leniencia(
                cnpj_sancionado=cnpj_limpo,
                pagina=pagina,
            )
    except ComprasWafBlockError:
        return with_latency(
            _resposta_waf_block("acordos-leniencia",
                                {"cnpj": cnpj_limpo, "pagina": pagina}),
            started,
        )
    items = resp if isinstance(resp, list) else (resp.get("data") or [])
    payload: dict[str, Any] = {
        "resultado": items,
        "_total_registros": len(items),
        "_pagina_atual": pagina,
        "_cache_hit": False,
    }
    await _sancoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
