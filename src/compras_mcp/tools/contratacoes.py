"""Tools de contratações: Lei 14.133/2021 + regimes legados (Lei 8.666 e RDC).

Endpoints cobertos (Dados Abertos):
- /modulo-contratacoes/1_consultarContratacoes_PNCP_14133       (+ 1.1 por id)
- /modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133  (+ 2.1 por id)
- /modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133 (+ 3.1)
- /modulo-legado/1_consultarLicitacao                            (+ 1.1 por id)
- /modulo-legado/2_consultarItemLicitacao                        (+ 2.1 por id)
- /modulo-legado/3_consultarPregoes                              (+ 3.1 por id)
- /modulo-legado/5_consultarComprasSemLicitacao                  (dispensa/inexigibilidade)
- /modulo-legado/7_consultarRdc                                  (RDC)

Cache TTL 15 min — contratações são publicadas continuamente, mas a janela
de filtro por data já estabiliza a maioria das consultas.
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
from compras_mcp.schemas import (
    ConsultarContratacao14133Input,
    ListarContratacoes14133Input,
    ListarItensContratacoes14133Input,
    ListarItensPregaoLegadoInput,
    ListarItensSemLicitacaoLegadoInput,
    ListarPaginadoInput,
    ListarResultadosContratacoes14133Input,
)
from compras_mcp.tools._helpers import (
    desc,
    envelope_dados_abertos,
    make_dados_abertos,
    with_latency,
)


_contratacoes_cache = cache_from_env(
    "CONTRATACOES", default_ttl=900, default_max_size=300
)


def _ck(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


# ============================================================================
# Lei 14.133/2021 (PNCP-aderente via Dados Abertos)
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratacoes_14133_listar(
    data_inicial_publicacao: Annotated[
        date | None,
        Field(
            default=None,
            description=desc(ListarContratacoes14133Input, "data_inicial_publicacao"),
        ),
    ] = None,
    data_final_publicacao: Annotated[
        date | None,
        Field(
            default=None,
            description=desc(ListarContratacoes14133Input, "data_final_publicacao"),
        ),
    ] = None,
    codigo_uasg: Annotated[
        int | None,
        Field(default=None, description=desc(ListarContratacoes14133Input, "codigo_uasg")),
    ] = None,
    cnpj_orgao: Annotated[
        str | None,
        Field(default=None, description=desc(ListarContratacoes14133Input, "cnpj_orgao")),
    ] = None,
    codigo_orgao_pncp: Annotated[
        int | None,
        Field(
            default=None,
            description=desc(ListarContratacoes14133Input, "codigo_orgao_pncp"),
        ),
    ] = None,
    uf: Annotated[
        str | None,
        Field(default=None, description=desc(ListarContratacoes14133Input, "uf")),
    ] = None,
    codigo_ibge_municipio: Annotated[
        int | None,
        Field(
            default=None,
            description=desc(ListarContratacoes14133Input, "codigo_ibge_municipio"),
        ),
    ] = None,
    amparo_legal: Annotated[
        int | None,
        Field(default=None, description=desc(ListarContratacoes14133Input, "amparo_legal")),
    ] = None,
    codigo_modalidade_dados_abertos: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Código de modalidade na tabela do **Dados Abertos / SIASG** "
                "(NÃO é o cheat sheet do PNCP). Equivalências confirmadas em "
                "2026-05 por sweep empírico do endpoint:\n"
                "  3 = Concorrência Eletrônica (PNCP=4)\n"
                "  5 = Pregão Eletrônico (PNCP=6)\n"
                "  6 = Dispensa (PNCP=8)\n"
                "  7 = Inexigibilidade (PNCP=9)\n"
                "Demais códigos (1,2,4,8-13) retornam vazio neste endpoint. "
                "Para consultar usando o cheat sheet PNCP nativo, use "
                "`compras_pncp_contratacoes_publicacao`."
            ),
        ),
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ListarContratacoes14133Input, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarContratacoes14133Input, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações da Lei 14.133 publicadas no PNCP (via Dados Abertos).

    Endpoint `/modulo-contratacoes/1_consultarContratacoes_PNCP_14133`.
    Cobre pregões eletrônicos, dispensas, inexigibilidades e demais
    modalidades da Nova Lei de Licitações no governo federal.

    **Atenção semântica**: o filtro `codigo_modalidade_dados_abertos` usa a
    tabela de modalidade do SIASG/Dados Abertos, NÃO o cheat sheet PNCP de
    `compras_pncp_modalidades`. Os payloads retornam ambos os campos
    (`codigoModalidade` do Dados Abertos e `modalidadeIdPncp` do PNCP) — use
    `modalidadeNome` para o nome amigável.

    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_limpo = (
        "".join(c for c in cnpj_orgao if c.isdigit()) if cnpj_orgao else None
    )
    key = _ck(
        "ct14133_listar",
        data_inicial_publicacao,
        data_final_publicacao,
        codigo_uasg,
        cnpj_limpo,
        codigo_orgao_pncp,
        uf,
        codigo_ibge_municipio,
        amparo_legal,
        codigo_modalidade_dados_abertos,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {}
    if data_inicial_publicacao is not None:
        filtros["dataPublicacaoPncpInicial"] = format_date(
            data_inicial_publicacao, "dados_abertos"
        )
    if data_final_publicacao is not None:
        filtros["dataPublicacaoPncpFinal"] = format_date(
            data_final_publicacao, "dados_abertos"
        )
    # Os nomes upstream são `unidadeOrgaoCodigoUnidade` e `orgaoEntidadeCnpj`.
    # Nomes fora do contrato são ignorados em silêncio (HTTP 200, resultado
    # idêntico ao de uma chamada sem filtro) — ver test_parametros_upstream.
    if codigo_uasg is not None:
        filtros["unidadeOrgaoCodigoUnidade"] = codigo_uasg
    if cnpj_limpo:
        filtros["orgaoEntidadeCnpj"] = cnpj_limpo
    # `codigoOrgao` aqui é o código do PNCP, não o do SIASG que o resto do MCP
    # usa — ver a description do parâmetro. O nome carrega o espaço de códigos
    # justamente porque o valor errado devolve zero em silêncio.
    if codigo_orgao_pncp is not None:
        filtros["codigoOrgao"] = codigo_orgao_pncp
    if uf:
        filtros["unidadeOrgaoUfSigla"] = uf.upper()
    if codigo_ibge_municipio is not None:
        filtros["unidadeOrgaoCodigoIbge"] = codigo_ibge_municipio
    if amparo_legal is not None:
        filtros["amparoLegalCodigoPncp"] = amparo_legal
    if codigo_modalidade_dados_abertos is not None:
        filtros["codigoModalidade"] = codigo_modalidade_dados_abertos

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/1_consultarContratacoes_PNCP_14133",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False

    # Zero registros com filtro de órgão é quase sempre o código errado: o
    # espaço do PNCP não é o do SIASG, e o erro não dá nenhum sinal (HTTP 200,
    # lista vazia). Sem este aviso o analista conclui "o órgão não contratou
    # nada no período" — falso negativo silencioso, que é o defeito que esta
    # família de tools mais produz.
    if codigo_orgao_pncp is not None and not payload.get("_total_registros"):
        payload["_aviso_filtro"] = (
            f"Nenhuma contratação para `codigo_orgao_pncp={codigo_orgao_pncp}` "
            "na janela pedida. Confirme que o valor veio do campo `codigoOrgao` "
            "do payload desta mesma tool: o código SIASG de "
            "`compras_orgao_listar`/`compras_orgao_consultar` é de outro espaço "
            "e não casa aqui. Recorte equivalente e mais seguro: `cnpj_orgao` "
            "(CNPJ do órgão) ou `codigo_uasg`."
        )

    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratacoes_14133_consultar(
    id_contratacao: Annotated[
        str,
        Field(description=desc(ConsultarContratacao14133Input, "id_contratacao")),
    ],
    tipo_identificador: Annotated[
        Literal["idCompra", "numeroControlePNCPCompra"],
        Field(
            description=desc(ConsultarContratacao14133Input, "tipo_identificador")
        ),
    ] = "idCompra",
) -> dict[str, Any]:
    """Consulta uma contratação 14.133 pelo identificador.

    Endpoint `/modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id`.
    Devolve detalhes completos: objeto, valor estimado, modalidade,
    instrumento convocatório, status no PNCP.

    Aceita os dois identificadores do PNCP. Use `tipo_identificador='idCompra'`
    com o campo `idCompra` das listagens, ou `'numeroControlePNCPCompra'` com o
    número de controle que aparece no edital (ex.: `10673078000120-1-000021/2025`).

    Cache 15 min.
    """
    started = time.perf_counter()
    key = _ck("ct14133_consultar", tipo_identificador, id_contratacao)
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/1.1_consultarContratacoes_PNCP_14133_Id",
            pagina=1,
            tamanho_pagina=1,
            tipo=tipo_identificador,
            codigo=id_contratacao,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": id_contratacao,
        "contratacao": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratacoes_14133_itens_listar(
    data_inicial_inclusao: Annotated[
        date, Field(description=desc(ListarItensContratacoes14133Input, "data_inicial_inclusao"))
    ],
    data_final_inclusao: Annotated[
        date, Field(description=desc(ListarItensContratacoes14133Input, "data_final_inclusao"))
    ],
    cod_item_catalogo: Annotated[
        int | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "cod_item_catalogo"))
    ] = None,
    material_ou_servico: Annotated[
        Literal["M", "S"] | None,
        Field(default=None, description=desc(ListarItensContratacoes14133Input, "material_ou_servico")),
    ] = None,
    codigo_grupo: Annotated[
        int | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "codigo_grupo"))
    ] = None,
    codigo_classe: Annotated[
        int | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "codigo_classe"))
    ] = None,
    tem_resultado: Annotated[
        bool | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "tem_resultado"))
    ] = None,
    situacao_item: Annotated[
        str | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "situacao_item"))
    ] = None,
    cnpj_orgao: Annotated[
        str | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "cnpj_orgao"))
    ] = None,
    codigo_uasg: Annotated[
        int | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "codigo_uasg"))
    ] = None,
    cnpj_cpf_fornecedor: Annotated[
        str | None, Field(default=None, description=desc(ListarItensContratacoes14133Input, "cnpj_cpf_fornecedor"))
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de contratações 14.133 incluídos no período.

    Endpoint `/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133`.

    **Uso principal — pesquisa de preço por item.** Com `cod_item_catalogo`
    (CATMAT/CATSER) cada linha traz, junto, `quantidade`,
    `valorUnitarioEstimado`, `valorUnitarioResultado`, `valorTotalResultado`,
    `nomeFornecedor` e `unidadeMedida` — ou seja, estimado *versus* homologado
    por item, insumo direto do mapa de preços do ETP.

    **Higiene da amostra**: passe `tem_resultado=True` (ou `situacao_item='2'`,
    Homologado) antes de calcular média ou mediana. Item deserto, fracassado ou
    cancelado não é preço praticado.

    Sem nenhum filtro além das datas, a resposta é "tudo que o Brasil incluiu no
    PNCP nessa janela" — quase sempre grande demais para ser útil.

    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_limpo = (
        "".join(c for c in cnpj_orgao if c.isdigit()) if cnpj_orgao else None
    )
    forn_limpo = (
        "".join(c for c in cnpj_cpf_fornecedor if c.isdigit())
        if cnpj_cpf_fornecedor
        else None
    )
    key = _ck(
        "ct14133_itens",
        data_inicial_inclusao,
        data_final_inclusao,
        cod_item_catalogo,
        material_ou_servico,
        codigo_grupo,
        codigo_classe,
        tem_resultado,
        situacao_item,
        cnpj_limpo,
        codigo_uasg,
        forn_limpo,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataInclusaoPncpInicial": format_date(data_inicial_inclusao, "dados_abertos"),
        "dataInclusaoPncpFinal": format_date(data_final_inclusao, "dados_abertos"),
    }
    # Atenção: o nome upstream é `codItemCatalogo` (sem o "igo" de "codigo").
    if cod_item_catalogo is not None:
        filtros["codItemCatalogo"] = cod_item_catalogo
    if material_ou_servico:
        filtros["materialOuServico"] = material_ou_servico
    if codigo_grupo is not None:
        filtros["codigoGrupo"] = codigo_grupo
    if codigo_classe is not None:
        filtros["codigoClasse"] = codigo_classe
    # Só o ramo `true` existe upstream: item sem vencedor grava
    # `temResultado: null`, não `false` — medido em 2026-09-07 na janela
    # 2025-01-06..07 (5018 itens no total, 4089 com `temResultado=true` e
    # ZERO com `temResultado=false`, embora ~929 estejam sem resultado).
    # Mandar `false` devolveria lista vazia com cara de "não há desertos".
    if tem_resultado is True:
        filtros["temResultado"] = "true"
    if situacao_item:
        filtros["situacaoCompraItem"] = situacao_item
    if cnpj_limpo:
        filtros["orgaoEntidadeCnpj"] = cnpj_limpo
    if codigo_uasg is not None:
        filtros["unidadeOrgaoCodigoUnidade"] = codigo_uasg
    if forn_limpo:
        filtros["codFornecedor"] = forn_limpo

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False

    # `tem_resultado=False` é atendido aqui, client-side, pelo motivo acima.
    if tem_resultado is False:
        itens = payload.get("resultado") or []
        sem_resultado = [item for item in itens if not item.get("temResultado")]
        payload["resultado"] = sem_resultado
        payload["_filtro_client_side"] = (
            f"{len(sem_resultado)} de {len(itens)} itens desta página estão sem "
            "resultado (deserto, fracassado ou ainda em andamento). O upstream "
            "não oferece esse recorte — ele grava `temResultado: null` e só "
            "sabe filtrar por `true` —, então o filtro é aplicado aqui e "
            "`_total_registros` continua sendo a contagem do servidor, sem o "
            "recorte. Para o estado exato do item, use `situacao_item`."
        )

    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratacoes_14133_itens_por_contratacao(
    id_contratacao: Annotated[
        str,
        Field(description=desc(ConsultarContratacao14133Input, "id_contratacao")),
    ],
    tipo_identificador: Annotated[
        Literal["idCompra", "numeroControlePNCPCompra"],
        Field(
            description=desc(ConsultarContratacao14133Input, "tipo_identificador")
        ),
    ] = "idCompra",
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de uma contratação 14.133 específica.

    Endpoint `/modulo-contratacoes/2.1_consultarItensContratacoes_PNCP_14133_Id`.
    Aceita `idCompra` ou número de controle PNCP, conforme `tipo_identificador`.
    """
    started = time.perf_counter()
    key = _ck(
        "ct14133_itens_por_ct",
        tipo_identificador,
        id_contratacao,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/2.1_consultarItensContratacoes_PNCP_14133_Id",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            tipo=tipo_identificador,
            codigo=id_contratacao,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratacoes_14133_resultados_listar(
    data_inicial_resultado: Annotated[
        date, Field(description=desc(ListarResultadosContratacoes14133Input, "data_inicial_resultado"))
    ],
    data_final_resultado: Annotated[
        date, Field(description=desc(ListarResultadosContratacoes14133Input, "data_final_resultado"))
    ],
    ni_fornecedor: Annotated[
        str | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "ni_fornecedor"))
    ] = None,
    porte_fornecedor: Annotated[
        int | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "porte_fornecedor"))
    ] = None,
    situacao_resultado: Annotated[
        int | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "situacao_resultado"))
    ] = None,
    valor_unitario_min: Annotated[
        float | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "valor_unitario_min"))
    ] = None,
    valor_unitario_max: Annotated[
        float | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "valor_unitario_max"))
    ] = None,
    valor_total_min: Annotated[
        float | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "valor_total_min"))
    ] = None,
    valor_total_max: Annotated[
        float | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "valor_total_max"))
    ] = None,
    cnpj_orgao: Annotated[
        str | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "cnpj_orgao"))
    ] = None,
    codigo_uasg: Annotated[
        int | None, Field(default=None, description=desc(ListarResultadosContratacoes14133Input, "codigo_uasg"))
    ] = None,
    pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "pagina"))
    ] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista resultados (homologações) de itens 14.133 no período.

    Endpoint `/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133`.
    Devolve fornecedor vencedor, valor adjudicado e quantitativo homologado —
    fonte primária de preço praticado para o ETP.

    **Due diligence de fornecedor**: `ni_fornecedor` (CNPJ/CPF) levanta tudo que
    um fornecedor ganhou na janela.

    **Auditoria por materialidade**: `valor_total_min` monta a fila de
    homologações acima de um patamar — combine com uma janela curta, já que o
    filtro de data é obrigatório.

    Para recortar por item de catálogo, use
    `compras_contratacoes_14133_itens_listar(cod_item_catalogo=...)`: esta rota
    **não** oferece filtro por CATMAT/CATSER.

    Cache 15 min.
    """
    started = time.perf_counter()
    cnpj_limpo = (
        "".join(c for c in cnpj_orgao if c.isdigit()) if cnpj_orgao else None
    )
    ni_limpo = (
        "".join(c for c in ni_fornecedor if c.isdigit()) if ni_fornecedor else None
    )
    key = _ck(
        "ct14133_resultados",
        data_inicial_resultado,
        data_final_resultado,
        ni_limpo,
        porte_fornecedor,
        situacao_resultado,
        valor_unitario_min,
        valor_unitario_max,
        valor_total_min,
        valor_total_max,
        cnpj_limpo,
        codigo_uasg,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dataResultadoPncpInicial": format_date(data_inicial_resultado, "dados_abertos"),
        "dataResultadoPncpFinal": format_date(data_final_resultado, "dados_abertos"),
    }
    if ni_limpo:
        filtros["niFornecedor"] = ni_limpo
    if porte_fornecedor is not None:
        filtros["porteFornecedorId"] = porte_fornecedor
    if situacao_resultado is not None:
        filtros["situacaoCompraItemResultadoId"] = situacao_resultado
    if valor_unitario_min is not None:
        filtros["valorUnitarioHomologadoInicial"] = valor_unitario_min
    if valor_unitario_max is not None:
        filtros["valorUnitarioHomologadoFinal"] = valor_unitario_max
    if valor_total_min is not None:
        filtros["valorTotalHomologadoInicial"] = valor_total_min
    if valor_total_max is not None:
        filtros["valorTotalHomologadoFinal"] = valor_total_max
    if cnpj_limpo:
        filtros["orgaoEntidadeCnpj"] = cnpj_limpo
    if codigo_uasg is not None:
        filtros["unidadeOrgaoCodigoUnidade"] = codigo_uasg

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_contratacoes_14133_resultados_por_contratacao(
    id_contratacao: Annotated[
        str,
        Field(description=desc(ConsultarContratacao14133Input, "id_contratacao")),
    ],
    tipo_identificador: Annotated[
        Literal["idCompra", "numeroControlePNCPCompra"],
        Field(
            description=desc(ConsultarContratacao14133Input, "tipo_identificador")
        ),
    ] = "idCompra",
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista resultados (homologações) de uma contratação 14.133 específica.

    Endpoint `/modulo-contratacoes/3.1_consultarResultadoItensContratacoes...`.
    Aceita `idCompra` ou número de controle PNCP, conforme `tipo_identificador`.
    """
    started = time.perf_counter()
    key = _ck(
        "ct14133_res_por_ct",
        tipo_identificador,
        id_contratacao,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-contratacoes/3.1_consultarResultadoItensContratacoes_PNCP_14133_Id",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            tipo=tipo_identificador,
            codigo=id_contratacao,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


# ============================================================================
# Regime Legado — Lei 8.666 e RDC
# ============================================================================


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_licitacoes_listar(
    data_publicacao_inicial: Annotated[
        date,
        Field(description="Data inicial de publicação (YYYY-MM-DD). Obrigatório no upstream."),
    ],
    data_publicacao_final: Annotated[
        date,
        Field(description="Data final de publicação (YYYY-MM-DD). Obrigatório no upstream."),
    ],
    modalidade: Annotated[
        int | None,
        Field(
            default=None,
            description="Código de modalidade SIASG (opcional).",
        ),
    ] = None,
    numero_aviso: Annotated[
        int | None, Field(default=None, description="Número do aviso (opcional).")
    ] = None,
    pertence14133: Annotated[
        bool | None,
        Field(default=None, description="Filtrar somente processos vinculados à Lei 14.133."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista licitações do regime legado (Lei 8.666/93).

    Endpoint `/modulo-legado/1_consultarLicitacao`. **Bug upstream
    confirmado**: o filtro `uasg`, embora documentado no swagger oficial,
    retorna HTTP 400 ("Erro ao efetuar a consulta") porque o atributo não
    existe no modelo Hibernate da view (`TbVwLicitacao`). Por isso este
    parâmetro foi removido da assinatura.

    Workaround se você precisar filtrar por UASG: liste sem filtro, depois
    filtre client-side pelo campo `uasg` do resultado.
    """
    started = time.perf_counter()
    key = _ck(
        "legado_lic",
        data_publicacao_inicial,
        data_publicacao_final,
        modalidade,
        numero_aviso,
        pertence14133,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "data_publicacao_inicial": format_date(data_publicacao_inicial, "dados_abertos"),
        "data_publicacao_final": format_date(data_publicacao_final, "dados_abertos"),
    }
    if modalidade is not None:
        filtros["modalidade"] = modalidade
    if numero_aviso is not None:
        filtros["numero_aviso"] = numero_aviso
    if pertence14133 is not None:
        filtros["pertence14133"] = str(pertence14133).lower()

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/1_consultarLicitacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_licitacao_consultar(
    id_compra: Annotated[
        str,
        Field(description="ID da compra no SIASG (string, retornado em `compras_legado_licitacoes_listar`)."),
    ],
) -> dict[str, Any]:
    """Consulta uma licitação legado pelo id_compra.

    Endpoint `/modulo-legado/1.1_consultarLicitacao_Id`. Upstream exige
    `id_compra` (string), não um `id` numérico.
    """
    started = time.perf_counter()
    key = _ck("legado_lic_consultar", id_compra)
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/1.1_consultarLicitacao_Id",
            pagina=1,
            tamanho_pagina=10,
            id_compra=id_compra,
        )
    resultados = resp.get("resultado") or []
    payload: dict[str, Any] = {
        "encontrado": bool(resultados),
        "codigo_consultado": id_compra,
        "licitacao": resultados[0] if resultados else None,
        "_cache_hit": False,
    }
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_itens_licitacao_listar(
    modalidade: Annotated[
        int,
        Field(description="Código de modalidade SIASG (obrigatório). Ex.: 5=Pregão, 6=Dispensa."),
    ],
    uasg: Annotated[
        int | None, Field(default=None, description="Código UASG (opcional).")
    ] = None,
    numero_aviso: Annotated[
        int | None, Field(default=None, description="Número do aviso (opcional).")
    ] = None,
    codigo_item_material: Annotated[
        int | None, Field(default=None, description="Código CATMAT (opcional).")
    ] = None,
    codigo_item_servico: Annotated[
        int | None, Field(default=None, description="Código CATSER (opcional).")
    ] = None,
    cnpj_fornecedor: Annotated[
        str | None, Field(default=None, description="CNPJ do fornecedor (opcional).")
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de licitações legado (`/modulo-legado/2_consultarItemLicitacao`).

    Upstream exige `modalidade` obrigatório. Filtros opcionais: `uasg`,
    `numero_aviso`, `codigo_item_material/servico`, `cnpj_fornecedor`.
    """
    started = time.perf_counter()
    cnpj_clean = (
        "".join(c for c in cnpj_fornecedor if c.isdigit()) if cnpj_fornecedor else None
    )
    filtros: dict[str, Any] = {"modalidade": modalidade}
    if uasg is not None:
        filtros["uasg"] = uasg
    if numero_aviso is not None:
        filtros["numero_aviso"] = numero_aviso
    if codigo_item_material is not None:
        filtros["codigo_item_material"] = codigo_item_material
    if codigo_item_servico is not None:
        filtros["codigo_item_servico"] = codigo_item_servico
    if cnpj_clean:
        filtros["cnpj_fornecedor"] = cnpj_clean

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/2_consultarItemLicitacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_pregoes_listar(
    dt_data_edital_inicial: Annotated[
        date,
        Field(description="Data inicial do edital (YYYY-MM-DD). Obrigatório."),
    ],
    dt_data_edital_final: Annotated[
        date,
        Field(description="Data final do edital (YYYY-MM-DD). Obrigatório."),
    ],
    numero: Annotated[
        int | None, Field(default=None, description="Número do pregão (opcional).")
    ] = None,
    ds_tipo_pregao_compra: Annotated[
        str | None,
        Field(default=None, description="Tipo do pregão de compra (string upstream)."),
    ] = None,
    pertence14133: Annotated[
        bool | None,
        Field(default=None, description="Filtrar pregões vinculados à Lei 14.133."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista pregões eletrônicos do regime legado.

    Endpoint `/modulo-legado/3_consultarPregoes`. **Bug upstream
    confirmado**: os filtros `co_uasg` e `co_orgao`, embora documentados
    no swagger, retornam HTTP 400 com erro Hibernate
    `Could not resolve attribute 'TbVwPregaoId.coUasg'` porque os atributos
    não existem no modelo da view. Por isso ambos foram removidos da
    assinatura.

    Workaround para filtrar por UASG: chame sem filtro e filtre client-side
    pelos campos `coUasg`/`coOrgao` do resultado.
    """
    started = time.perf_counter()
    key = _ck(
        "legado_preg",
        dt_data_edital_inicial,
        dt_data_edital_final,
        numero,
        ds_tipo_pregao_compra,
        pertence14133,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {
        "dt_data_edital_inicial": format_date(dt_data_edital_inicial, "dados_abertos"),
        "dt_data_edital_final": format_date(dt_data_edital_final, "dados_abertos"),
    }
    if numero is not None:
        filtros["numero"] = numero
    if ds_tipo_pregao_compra:
        filtros["ds_tipo_pregao_compra"] = ds_tipo_pregao_compra
    if pertence14133 is not None:
        filtros["pertence14133"] = str(pertence14133).lower()

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/3_consultarPregoes",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_compras_sem_licitacao(
    dt_ano_aviso: Annotated[
        int,
        Field(description="Ano do aviso (ex.: 2024). Obrigatório no upstream."),
    ],
    co_uasg: Annotated[
        int | None, Field(default=None, description="Código UASG (opcional).")
    ] = None,
    co_orgao: Annotated[
        int | None, Field(default=None, description="Código do órgão (opcional).")
    ] = None,
    co_orgao_superior: Annotated[
        int | None, Field(default=None, description="Código do órgão superior (opcional).")
    ] = None,
    nu_aviso_licitacao: Annotated[
        int | None, Field(default=None, description="Número do aviso de licitação.")
    ] = None,
    co_modalidade_licitacao: Annotated[
        int | None, Field(default=None, description="Código da modalidade SIASG.")
    ] = None,
    pertence14133: Annotated[
        bool | None,
        Field(default=None, description="Vincula à Lei 14.133."),
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista compras sem licitação (dispensa/inexigibilidade) do regime legado.

    Endpoint `/modulo-legado/5_consultarComprasSemLicitacao`. **Upstream
    exige `dt_ano_aviso`** (ano inteiro, ex.: 2024) — não janela de datas.
    """
    started = time.perf_counter()
    filtros: dict[str, Any] = {"dt_ano_aviso": dt_ano_aviso}
    if co_uasg is not None:
        filtros["co_uasg"] = co_uasg
    if co_orgao is not None:
        filtros["co_orgao"] = co_orgao
    if co_orgao_superior is not None:
        filtros["co_orgao_superior"] = co_orgao_superior
    if nu_aviso_licitacao is not None:
        filtros["nu_aviso_licitacao"] = nu_aviso_licitacao
    if co_modalidade_licitacao is not None:
        filtros["co_modalidade_licitacao"] = co_modalidade_licitacao
    if pertence14133 is not None:
        filtros["pertence14133"] = str(pertence14133).lower()

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/5_consultarComprasSemLicitacao",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_rdc_listar(
    data_publicacao_min: Annotated[
        date,
        Field(description="Data MÍNIMA de publicação (YYYY-MM-DD). Obrigatório."),
    ],
    data_publicacao_max: Annotated[
        date,
        Field(description="Data MÁXIMA de publicação (YYYY-MM-DD). Obrigatório."),
    ],
    uasg: Annotated[
        int | None, Field(default=None, description="Código UASG (opcional).")
    ] = None,
    orgao: Annotated[
        int | None, Field(default=None, description="Código do órgão (opcional).")
    ] = None,
    uf_uasg: Annotated[
        str | None, Field(default=None, description="UF da UASG (sigla, ex.: 'DF').")
    ] = None,
    modalidade: Annotated[
        int | None, Field(default=None, description="Código de modalidade.")
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista contratações pelo RDC (Regime Diferenciado de Contratações).

    Endpoint `/modulo-legado/7_consultarRdc`. **Upstream usa
    `data_publicacao_min/max`** (note `min`/`max`, não `inicial`/`final`).
    RDC foi usado principalmente para obras dos megaeventos e da Copa —
    relevância residual hoje.
    """
    started = time.perf_counter()
    filtros: dict[str, Any] = {
        "data_publicacao_min": format_date(data_publicacao_min, "dados_abertos"),
        "data_publicacao_max": format_date(data_publicacao_max, "dados_abertos"),
    }
    if uasg is not None:
        filtros["uasg"] = uasg
    if orgao is not None:
        filtros["orgao"] = orgao
    if uf_uasg:
        filtros["uf_uasg"] = uf_uasg.upper()
    if modalidade is not None:
        filtros["modalidade"] = modalidade

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            "/modulo-legado/7_consultarRdc",
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            **filtros,
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_itens_pregao_listar(
    data_homologacao_inicial: Annotated[
        date | None, Field(default=None, description=desc(ListarItensPregaoLegadoInput, "data_homologacao_inicial"))
    ] = None,
    data_homologacao_final: Annotated[
        date | None, Field(default=None, description=desc(ListarItensPregaoLegadoInput, "data_homologacao_final"))
    ] = None,
    id_compra: Annotated[
        str | None, Field(default=None, description=desc(ListarItensPregaoLegadoInput, "id_compra"))
    ] = None,
    id_compra_item: Annotated[
        str | None, Field(default=None, description=desc(ListarItensPregaoLegadoInput, "id_compra_item"))
    ] = None,
    codigo_uasg: Annotated[
        int | None, Field(default=None, description=desc(ListarItensPregaoLegadoInput, "codigo_uasg"))
    ] = None,
    decreto_7174: Annotated[
        str | None, Field(default=None, description=desc(ListarItensPregaoLegadoInput, "decreto_7174"))
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de pregões do regime legado (Lei 8.666), com a cadeia de preço.

    Endpoints `/modulo-legado/4_consultarItensPregoes` (por período de
    homologação) e `/modulo-legado/4.1_consultarItensPregoes_Id` (quando
    `id_compra` é informado).

    **É a única fonte, em todo o MCP, da cadeia completa de formação de preço
    por item**: `valor_estimado_item` → `menor_lance` → `valor_negociado` →
    `valor_homologado_item`. Serve para medir o desconto real obtido em certame
    e para instruir negociação.

    Traz também `situacao_item`, que revela itens desertos e fracassados —
    invisíveis para quem só olha preço homologado, e relevantes para justificar
    revisão de estimativa.

    Informe `id_compra` **ou** o par de datas de homologação. As duas datas
    precisam ser diferentes entre si (restrição do upstream).

    Série histórica: use para contratações anteriores à Lei 14.133. Para 2022 em
    diante, prefira `compras_contratacoes_14133_itens_listar`.

    Cache 15 min.
    """
    started = time.perf_counter()
    if not id_compra and not (data_homologacao_inicial and data_homologacao_final):
        return with_latency(
            {
                "resultado": [],
                "_erro": (
                    "Informe `id_compra` ou o par "
                    "`data_homologacao_inicial`/`data_homologacao_final`."
                ),
                "_cache_hit": False,
            },
            started,
        )

    key = _ck(
        "legado_itens_pregao",
        id_compra,
        id_compra_item,
        data_homologacao_inicial,
        data_homologacao_final,
        codigo_uasg,
        decreto_7174,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {}
    # A rota por id (`4.1`) só declara `id_compra`/`id_compra_item`: qualquer
    # outro recorte pedido junto seria descartado em silêncio, e lista completa
    # com cara de lista filtrada é o defeito que este módulo mais produz.
    ignorados_no_id: list[str] = []
    if id_compra:
        path = "/modulo-legado/4.1_consultarItensPregoes_Id"
        filtros["id_compra"] = id_compra
        if id_compra_item:
            filtros["id_compra_item"] = id_compra_item
        ignorados_no_id = [
            nome
            for nome, valor in (
                ("data_homologacao_inicial", data_homologacao_inicial),
                ("data_homologacao_final", data_homologacao_final),
                ("codigo_uasg", codigo_uasg),
                ("decreto_7174", decreto_7174),
            )
            if valor is not None
        ]
    else:
        path = "/modulo-legado/4_consultarItensPregoes"
        filtros["dt_hom_inicial"] = format_date(
            data_homologacao_inicial, "dados_abertos"
        )
        filtros["dt_hom_final"] = format_date(data_homologacao_final, "dados_abertos")
        if codigo_uasg is not None:
            filtros["co_uasg"] = codigo_uasg
        if decreto_7174:
            filtros["decreto_7174"] = decreto_7174

    async with make_dados_abertos(get_settings()) as client:
        resp = await client.list_resource(
            path, pagina=pagina, tamanho_pagina=tamanho_pagina, **filtros
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["_cache_hit"] = False
    if ignorados_no_id:
        payload["_aviso_filtro"] = (
            f"Com `id_compra` a consulta vai para a rota por id, que só aceita "
            f"`id_compra`/`id_compra_item`: {', '.join(ignorados_no_id)} "
            "não teve efeito nenhum sobre este resultado. Para combinar esses "
            "recortes, consulte por período de homologação, sem `id_compra`."
        )
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)


@mcp.tool(annotations=SOMENTE_LEITURA)
async def compras_legado_itens_sem_licitacao_listar(
    ano_aviso: Annotated[
        int | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "ano_aviso"))
    ] = None,
    id_compra: Annotated[
        str | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "id_compra"))
    ] = None,
    id_compra_item: Annotated[
        str | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "id_compra_item"))
    ] = None,
    codigo_uasg: Annotated[
        int | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "codigo_uasg"))
    ] = None,
    codigo_orgao: Annotated[
        str | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "codigo_orgao"))
    ] = None,
    codigo_modalidade: Annotated[
        int | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "codigo_modalidade"))
    ] = None,
    codigo_conjunto_materiais: Annotated[
        int | None,
        Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "codigo_conjunto_materiais")),
    ] = None,
    codigo_servico: Annotated[
        int | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "codigo_servico"))
    ] = None,
    cpf_cnpj_fornecedor: Annotated[
        str | None, Field(default=None, description=desc(ListarItensSemLicitacaoLegadoInput, "cpf_cnpj_fornecedor"))
    ] = None,
    pagina: Annotated[int, Field(description=desc(ListarPaginadoInput, "pagina"))] = 1,
    tamanho_pagina: Annotated[
        int, Field(description=desc(ListarPaginadoInput, "tamanho_pagina"))
    ] = 50,
) -> dict[str, Any]:
    """Lista itens de contratações diretas do regime legado (dispensa/inexigibilidade).

    Endpoints `/modulo-legado/6_consultarCompraItensSemLicitacao` (por ano do
    aviso) e `/modulo-legado/6.1_consultarItensComprasSemLicitacao_Id` (quando
    `id_compra` é informado).

    **É o único caminho para contratação direta em nível de item no período
    anterior ao PNCP (2019-2021)** — justamente a janela das dispensas
    emergenciais da pandemia, para a qual as rotas da Lei 14.133 retornam vazio.
    Traz `vr_estimado`, fornecedor vencedor e a descrição detalhada do item.

    Informe `id_compra` **ou** `ano_aviso`.

    CPF de fornecedor pessoa física vem mascarado por padrão (LGPD).

    Cache 15 min.
    """
    started = time.perf_counter()
    if not id_compra and ano_aviso is None:
        return with_latency(
            {
                "resultado": [],
                "_erro": "Informe `id_compra` ou `ano_aviso`.",
                "_cache_hit": False,
            },
            started,
        )

    forn_limpo = (
        "".join(c for c in cpf_cnpj_fornecedor if c.isdigit())
        if cpf_cnpj_fornecedor
        else None
    )
    key = _ck(
        "legado_itens_sem_lic",
        id_compra,
        id_compra_item,
        ano_aviso,
        codigo_uasg,
        codigo_orgao,
        codigo_modalidade,
        codigo_conjunto_materiais,
        codigo_servico,
        forn_limpo,
        pagina,
        tamanho_pagina,
    )
    cached = await _contratacoes_cache.get(key)
    if cached is not None:
        cached["_cache_hit"] = True
        return with_latency(cached, started)

    filtros: dict[str, Any] = {}
    ignorados_no_id: list[str] = []
    if id_compra:
        # Mesma armadilha da rota `4.1`: a rota por id ignora os demais
        # recortes, e sem aviso o payload passa por lista filtrada.
        path = "/modulo-legado/6.1_consultarItensComprasSemLicitacao_Id"
        filtros["id_compra"] = id_compra
        if id_compra_item:
            filtros["id_compra_item"] = id_compra_item
        ignorados_no_id = [
            nome
            for nome, valor in (
                ("ano_aviso", ano_aviso),
                ("codigo_uasg", codigo_uasg),
                ("codigo_orgao", codigo_orgao),
                ("codigo_modalidade", codigo_modalidade),
                ("codigo_conjunto_materiais", codigo_conjunto_materiais),
                ("codigo_servico", codigo_servico),
                ("cpf_cnpj_fornecedor", forn_limpo),
            )
            if valor is not None
        ]
    else:
        path = "/modulo-legado/6_consultarCompraItensSemLicitacao"
        filtros["dt_ano_aviso_licitacao"] = ano_aviso
        if codigo_uasg is not None:
            filtros["co_uasg"] = codigo_uasg
        if codigo_orgao:
            filtros["co_orgao"] = codigo_orgao
        if codigo_modalidade is not None:
            filtros["co_modalidade_licitacao"] = codigo_modalidade
        if codigo_conjunto_materiais is not None:
            filtros["co_conjunto_materiais"] = codigo_conjunto_materiais
        if codigo_servico is not None:
            filtros["co_servico"] = codigo_servico
        if forn_limpo:
            filtros["nu_cpf_cnpj_fornecedor"] = forn_limpo

    settings = get_settings()
    async with make_dados_abertos(settings) as client:
        resp = await client.list_resource(
            path, pagina=pagina, tamanho_pagina=tamanho_pagina, **filtros
        )
    payload = envelope_dados_abertos(resp, pagina_atual=pagina)
    payload["resultado"] = apply_lgpd(
        payload.get("resultado"), incluir_cpf_completo=settings.incluir_cpf_completo
    )
    payload["_aviso_lgpd"] = aviso_lgpd()
    payload["_cache_hit"] = False
    if ignorados_no_id:
        payload["_aviso_filtro"] = (
            f"Com `id_compra` a consulta vai para a rota por id, que só aceita "
            f"`id_compra`/`id_compra_item`: {', '.join(ignorados_no_id)} "
            "não teve efeito nenhum sobre este resultado. Para combinar esses "
            "recortes, consulte por `ano_aviso`, sem `id_compra`."
        )
    await _contratacoes_cache.set(key, json.loads(json.dumps(payload, default=str)))
    return with_latency(payload, started)
