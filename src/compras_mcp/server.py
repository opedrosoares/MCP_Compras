"""FastMCP server expondo as tools do ecossistema Compras.gov.br.

Transportes:
- stdio (default, para Claude Desktop): `uv run compras-mcp`
- HTTP (para deploy no Railway): export `PORT=8080` e rode `uv run compras-mcp`

Logs vão para STDERR (STDOUT é reservado para protocolo MCP em stdio).

Cada domínio funcional fica em `compras_mcp/tools/<modulo>.py`. Os módulos
registram tools chamando `@mcp.tool` no `mcp` global importado de
`compras_mcp.mcp_instance`. server.py importa os módulos para forçar o
registro no carregamento.
"""

from __future__ import annotations

import os
import time
from typing import Annotated, Any, Literal

import structlog
from pydantic import Field

from compras_mcp.config import get_settings
from compras_mcp.logging_setup import configure_logging
from compras_mcp.mcp_instance import SOMENTE_LEITURA, mcp
from compras_mcp.schemas import HealthcheckInput, VersaoOutput
from compras_mcp.tools._helpers import desc
from compras_mcp.upstream_probe import (
    STATUS_DEGRADADO,
    STATUS_FORA,
    executar_probe,
    resumir_por_modulo,
)
from compras_mcp.upstream_registry import MODULOS, ROTAS

# IMPORTANT: importar os módulos de tools DEPOIS do mcp_instance para
# que os decorators @mcp.tool registrem nas instâncias certas.
# Ordem dos imports define a ordem de listagem (não a prioridade lógica).
# NÃO reordenar com `ruff --fix --select I001`: o autofix tira `pncp` do
# bloco e a listagem passa a sair fora de ordem.
from compras_mcp.tools import (  # noqa: F401, E402
    analitica,
    atas,
    catalogo,
    compostas,
    contratacoes,
    contratos,
    discovery,
    enriquecimento,
    fornecedores,
    indicadores,
    organizacoes,
    pesquisa_precos,
    planejamento,
    pncp as pncp_tools,
    sancoes,
)

# Prompts e Resources MCP — registrados via @mcp.prompt e @mcp.resource.
from compras_mcp import prompts as _prompts  # noqa: F401, E402
from compras_mcp import resources as _resources  # noqa: F401, E402

log = structlog.get_logger(__name__)


def measure_ms(started: float) -> float:
    """Latência em ms com 1 casa decimal."""
    return round((time.perf_counter() - started) * 1000, 1)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_versao() -> dict[str, Any]:
    """Healthcheck/diagnóstico do MCP. Retorna versão, fontes upstream e
    estado de configurações sensíveis (sem expor valores).

    Útil para confirmar que o servidor está respondendo, qual a versão
    instalada, quais APIs estão acessíveis e se a chave da Transparência
    foi configurada (necessária para tools de sanções).
    """
    started = time.perf_counter()
    from compras_mcp import __version__

    settings = get_settings()
    out = VersaoOutput(
        nome="compras-mcp",
        versao=__version__,
        fontes={
            "dados_abertos": settings.dados_abertos_base_url,
            "pncp": settings.pncp_base_url,
            "transparencia": settings.transparencia_base_url,
            "comprasnet_contratos": settings.comprasnet_contratos_base_url,
        },
        redis_configurado=bool(settings.redis_url),
        transparencia_configurada=bool(settings.transparencia_api_key),
    )
    result = out.model_dump()
    result["_latency_ms"] = measure_ms(started)
    return result


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_healthcheck(
    profundidade: Annotated[
        Literal["basico", "rotas"],
        Field(description=desc(HealthcheckInput, "profundidade")),
    ] = "rotas",
    modulo: Annotated[
        str | None,
        Field(default=None, description=desc(HealthcheckInput, "modulo")),
    ] = None,
) -> dict[str, Any]:
    """Diz, em ~30 segundos, o que está de pé neste servidor **agora**.

    Estende `compras_versao`: além de versão e configuração, dispara um
    probe paralelo (timeout curto) contra as rotas upstream reais e
    devolve a situação por módulo funcional.

    Por que existe: em 04/08/2026 a tool de pesquisa de preço de material
    estava quebrada havia semanas e ninguém sabia — a SEGES trocou a
    assinatura da rota sem versionar. A descoberta veio de um analista
    tentando usar a ferramenta. Antes de uma demonstração ou de instruir
    processo, rode isto: o objetivo é que a descoberta aconteça aqui, não
    no palco.

    Args:
        profundidade: `basico` responde só versão/config (instantâneo);
            `rotas` (padrão) executa o probe upstream.
        modulo: restringe o probe a um módulo (ex.: `pesquisa_preco`,
            `atas`, `pncp`). Sem isso, testa todos.

    Situação por módulo:
        - `ok`: todas as rotas responderam com os campos esperados.
        - `degradado`: alguma rota caiu, ou respondeu 200 **sem** os campos
          do contrato (ex.: rota de preço sem `precoUnitario`) — o modo de
          falha silencioso que só o contrato de campos pega.
        - `fora`: todas as rotas testáveis do módulo falharam.
        - `pulado`: faltou credencial (ex.: TRANSPARENCIA_API_KEY).

    Rota que estoura o relógio é reexecutada em série antes de virar
    `fora`: com dezenas de rotas em paralelo, uma rota apenas lenta seria
    reportada como quebrada. Quando passa na segunda tentativa, o campo
    `problemas` do módulo registra "lenta sob carga" em vez de escondê-lo.

    O campo `pronto_para_uso` é o resumo honesto: `False` quando existe
    qualquer módulo fora ou degradado.
    """
    started = time.perf_counter()
    from compras_mcp import __version__

    settings = get_settings()
    payload: dict[str, Any] = {
        "nome": "compras-mcp",
        "versao": __version__,
        "profundidade": profundidade,
        "fontes": {
            "dados_abertos": settings.dados_abertos_base_url,
            "pncp": settings.pncp_base_url,
            "transparencia": settings.transparencia_base_url,
            "comprasnet_contratos": settings.comprasnet_contratos_base_url,
        },
        "redis_configurado": bool(settings.redis_url),
        "transparencia_configurada": bool(settings.transparencia_api_key),
    }

    if profundidade == "basico":
        payload["pronto_para_uso"] = True
        payload["observacao"] = (
            "Profundidade 'basico' não testa upstream. Use profundidade='rotas' "
            "antes de demonstração ou uso em processo."
        )
        result = payload
        result["_latency_ms"] = measure_ms(started)
        return result

    rotas = tuple(r for r in ROTAS if not modulo or r.modulo == modulo)
    if not rotas:
        payload["erro"] = (
            f"Módulo '{modulo}' não existe. Disponíveis: {', '.join(MODULOS)}"
        )
        payload["_latency_ms"] = measure_ms(started)
        return payload

    # Timeout curto: healthcheck que demora não é healthcheck. Rotas
    # reconhecidamente lentas (PNCP) trazem timeout próprio no registro.
    # `reconfirmar_timeouts`: sob 59 rotas concorrentes, uma rota só lenta
    # estoura os 12s e viraria "fora" — falso alarme. Quem estourou o
    # relógio é reexecutado em série antes de ser dado como fora.
    resultados = await executar_probe(
        rotas, timeout=12.0, concorrencia=10, reconfirmar_timeouts=True
    )
    por_modulo = resumir_por_modulo(resultados)

    fora = [m for m, v in por_modulo.items() if v["situacao"] == STATUS_FORA]
    degradados = [m for m, v in por_modulo.items() if v["situacao"] == STATUS_DEGRADADO]

    payload["modulos"] = por_modulo
    payload["rotas_testadas"] = len(resultados)
    payload["modulos_fora"] = fora
    payload["modulos_degradados"] = degradados
    payload["pronto_para_uso"] = not fora and not degradados
    payload["tools_afetadas"] = sorted(
        {
            tool
            for r in resultados
            if r.status in (STATUS_FORA, STATUS_DEGRADADO)
            for tool in r.tools
        }
    )
    if not payload["pronto_para_uso"]:
        payload["proximo_passo"] = (
            "Rode `python scripts/probe_upstream.py"
            + (f" --modulo {modulo}" if modulo else "")
            + "` para ver rota a rota (status HTTP, latência, chaves do 1º item)."
        )
    payload["_latency_ms"] = measure_ms(started)
    return payload


def main() -> None:
    """Entry point usado pelo console script `compras-mcp`.

    Se a env var `PORT` existir (padrão de PaaS como Railway), sobe em
    HTTP escutando em `0.0.0.0:PORT`. Caso contrário, sobe em stdio
    (modo Claude Desktop).
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    port_env = os.environ.get("PORT", "").strip()
    if port_env:
        port = int(port_env)
        log.info(
            "compras_mcp.startup",
            transport="http",
            host="0.0.0.0",
            port=port,
            transparencia_configured=bool(settings.transparencia_api_key),
            redis_configured=bool(settings.redis_url),
        )
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        log.info(
            "compras_mcp.startup",
            transport="stdio",
            transparencia_configured=bool(settings.transparencia_api_key),
            redis_configured=bool(settings.redis_url),
        )
        mcp.run()


if __name__ == "__main__":
    main()
