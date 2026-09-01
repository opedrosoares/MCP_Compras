"""Tools de enriquecimento — fontes externas complementares ao Compras.gov.br.

Atualmente expõe consulta de CNPJ pela BrasilAPI / MinhaReceita (dados
públicos da Receita Federal): razão social, situação cadastral, CNAE,
QSA, capital social, atividades secundárias.

Útil quando o usuário precisa avaliar porte, sócios e atividade de um
fornecedor — informação que o `compras_perfil_fornecedor_completo` cobre
apenas parcialmente, pois usa só dados SISG/PNCP.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.cache import cache_from_env
from compras_mcp.clients.cnpj import make_cnpj_client
from compras_mcp.config import get_settings
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.tools._helpers import with_latency

_cnpj_cache = cache_from_env("CNPJ_RECEITA", default_ttl=86400, default_max_size=500)


def _so_digitos(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_fornecedor_cnpj_receita(
    cnpj: Annotated[
        str,
        Field(
            description=(
                "CNPJ a consultar (14 dígitos, com ou sem pontuação). "
                "Usa BrasilAPI por padrão; trocável via env "
                "`CNPJ_PROVIDER=minhareceita`."
            ),
            min_length=11,
            max_length=20,
        ),
    ],
) -> dict[str, Any]:
    """Dados públicos do CNPJ na Receita Federal (via BrasilAPI/MinhaReceita).

    Retorna razão social, nome fantasia, situação cadastral, CNAE primário e
    secundários, QSA (sócios), capital social, natureza jurídica, porte,
    endereço e datas de início de atividade e da situação cadastral.

    **Quando usar**: complemento do `compras_perfil_fornecedor_completo`
    para due diligence (avaliar porte, sócios, CNAEs vs objeto da licitação).
    Os dados são da Receita; este MCP **não** consulta sanções aqui — para
    isso use as tools de sanção (CEIS/CNEP/CEPIM/CEAF).

    Cache 24h. Em caso de 404 ou erro upstream, retorna `encontrado=false`
    com diagnóstico em `_erro` em vez de propagar exception.
    """
    started = time.perf_counter()
    cnpj_clean = _so_digitos(cnpj)
    if len(cnpj_clean) != 14:
        return with_latency(
            {
                "encontrado": False,
                "cnpj_consultado": cnpj_clean,
                "_erro": (
                    f"CNPJ inválido: esperado 14 dígitos, recebido {len(cnpj_clean)}."
                ),
                "_cache_hit": False,
            },
            started,
        )

    settings = get_settings()
    cache_key = f"cnpj|{settings.cnpj_provider}|{cnpj_clean}"
    cached = await _cnpj_cache.get(cache_key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_cnpj_client(settings) as client:
        dados = await client.consultar(cnpj_clean)

    payload: dict[str, Any] = {
        "cnpj_consultado": cnpj_clean,
        **dados,
        "_cache_hit": False,
    }
    # Só cacheia respostas positivas (404 pode ser transitório se o CNPJ
    # acabou de ser aberto).
    if dados.get("encontrado"):
        await _cnpj_cache.set(
            cache_key, json.loads(json.dumps(payload, default=str))
        )
    return with_latency(payload, started)
