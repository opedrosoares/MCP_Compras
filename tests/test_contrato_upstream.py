"""Testes de contrato upstream — o que faltava para pegar a quebra de 2026-08.

Contexto: em 04/08/2026 duas falhas conviviam sem que nenhum teste caísse.

1. `/modulo-pesquisa-preco/1_consultarMaterial` devolvia 404 havia semanas.
   A SEGES trocou `codigoItemCatalogo` por `tipo`+`codigo` e a API responde
   404 (não 400) a obrigatório ausente. Nenhum teste olhava a **query
   enviada**, então a troca passou.

2. `/modulo-pesquisa-preco/2_consultarMaterialDetalhe` respondia HTTP 200
   com zero campos de preço, enquanto a docstring prometia "valor unitário
   homologado". Nenhum teste olhava os **campos do payload**, só o status.

Daí as duas famílias de teste aqui:

- **Query enviada** (`test_query_*`): trava os parâmetros que saem do MCP.
  Se alguém "simplificar" o `tipo`+`codigo` de volta para
  `codigoItemCatalogo`, quebra aqui e não em produção.
- **Contrato de campos** (`test_contrato_campos_*`): trava as chaves que
  cada rota precisa devolver. HTTP 200 sem `precoUnitario` é falha.

Os testes de campo rodam em dois modos:

- offline (default): validam a *mecânica* de detecção — que o probe
  classifica 200-sem-campo como `degradado` e que o registro declara
  `precoUnitario` para toda rota de preço.
- online (`COMPRAS_LIVE_TESTS=1`): batem no upstream real e conferem que os
  campos continuam lá. É o que dá o alarme quando a SEGES mexer de novo.

    COMPRAS_LIVE_TESTS=1 pytest tests/test_contrato_upstream.py -v
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pytest_httpx import HTTPXMock

# Side effect: importar registra todas as tools
import compras_mcp.server  # noqa: F401
from compras_mcp.upstream_probe import (
    STATUS_DEGRADADO,
    STATUS_FORA,
    STATUS_OK,
    executar_probe,
)
from compras_mcp.upstream_registry import ROTAS, rota_por_id

_RE_MATERIAL = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-pesquisa-preco/1_consultarMaterial.*"
)
_RE_SERVICO = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-pesquisa-preco/3_consultarServico.*"
)
_RE_UASG = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-uasg/1_consultarUasg.*"
)

# Rotas cuja razão de existir é devolver preço. Se uma delas parar de
# trazer o campo, o dado vira inútil para ETP mesmo com HTTP 200.
IDS_ROTAS_DE_PRECO = ("preco_material", "preco_servico", "arp_itens")

live = pytest.mark.skipif(
    os.environ.get("COMPRAS_LIVE_TESTS") != "1",
    reason="teste de contrato ao vivo; habilite com COMPRAS_LIVE_TESTS=1",
)


def _structured(result: Any) -> Any:
    return result.structured_content if hasattr(result, "structured_content") else result


async def _run(nome: str, args: dict[str, Any]) -> Any:
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    return _structured(await tools[nome].run(args))


def _query(request: Any) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(request.url)).query)


def _resposta_material(**extra: Any) -> dict[str, Any]:
    """Amostra fiel da rota 1 (campos conforme upstream em 2026-08-05)."""
    item = {
        "idCompra": "16024005900112026",
        "dataCompra": "2026-05-19",
        "codigoItemCatalogo": 630237,
        "precoUnitario": 104.0,
        "quantidade": 12.0,
        "niFornecedor": "36522055000109",
        "nomeFornecedor": "H&A VENDAS E SERVICOS LTDA",
        "codigoUasg": "160240",
        **extra,
    }
    return {"resultado": [item], "totalRegistros": 1, "totalPaginas": 1}


# ===========================================================================
# 1. Query enviada — o que teria pego a quebra da rota 1
# ===========================================================================


@pytest.mark.asyncio
async def test_query_preco_material_usa_tipo_e_codigo(httpx_mock: HTTPXMock) -> None:
    """Rota 1 exige `tipo`+`codigo`; `codigoItemCatalogo` devolve 404."""
    httpx_mock.add_response(url=_RE_MATERIAL, json=_resposta_material())

    await _run("compras_pesquisar_preco_material", {"codigo_item_catalogo": 630237})

    q = _query(httpx_mock.get_requests()[0])
    assert q["tipo"] == ["codigoItemCatalogo"], "faltou o discriminador `tipo`"
    assert q["codigo"] == ["630237"], "faltou `codigo` (o valor do item)"
    assert "codigoItemCatalogo" not in q, (
        "parâmetro pré-2026-08 voltou: o upstream responde 404 a ele"
    )


@pytest.mark.asyncio
async def test_query_preco_servico_mantem_codigo_item_catalogo(
    httpx_mock: HTTPXMock,
) -> None:
    """Rota 3 NÃO mudou — não pode ser 'corrigida' junto com a rota 1."""
    httpx_mock.add_response(url=_RE_SERVICO, json=_resposta_material())

    await _run("compras_pesquisar_preco_servico", {"codigo_item_catalogo": 25089})

    q = _query(httpx_mock.get_requests()[0])
    assert q["codigoItemCatalogo"] == ["25089"]
    assert "tipo" not in q, "serviço não usa o par tipo/codigo"


@pytest.mark.asyncio
async def test_query_etp_material_usa_tipo_e_codigo(httpx_mock: HTTPXMock) -> None:
    """A composta de ETP monta a query por conta própria — trava também."""
    httpx_mock.add_response(url=_RE_MATERIAL, json=_resposta_material())

    await _run(
        "compras_pesquisar_precos_para_etp",
        {"tipo": "material", "codigo_item_catalogo": 630237, "max_paginas": 1},
    )

    q = _query(httpx_mock.get_requests()[0])
    assert q["tipo"] == ["codigoItemCatalogo"]
    assert q["codigo"] == ["630237"]
    assert "codigoItemCatalogo" not in q


@pytest.mark.asyncio
async def test_query_uasg_envia_status_obrigatorio(httpx_mock: HTTPXMock) -> None:
    """`statusUasg` é obrigatório: sem ele o upstream devolve 404."""
    httpx_mock.add_response(
        url=_RE_UASG,
        json={
            "resultado": [{"codigoUasg": "160240", "nomeUasg": "UASG TESTE"}],
            "totalRegistros": 1,
            "totalPaginas": 1,
        },
    )

    await _run("compras_uasg_listar", {"tamanho_pagina": 10})

    assert _query(httpx_mock.get_requests()[0])["statusUasg"] == ["true"]


# ===========================================================================
# 2. Contrato de campos — o que teria pego a rota 2 silenciosamente vazia
# ===========================================================================


@pytest.mark.parametrize("rota_id", IDS_ROTAS_DE_PRECO)
def test_contrato_campos_rota_de_preco_exige_preco_unitario(rota_id: str) -> None:
    """Toda rota de preço declara o campo de valor no contrato.

    Sem isto, o probe daria `ok` para uma rota que devolve 200 e nenhum
    preço — exatamente o estado em que a rota 2 viveu por meses.
    """
    rota = rota_por_id(rota_id)
    assert rota is not None, f"rota '{rota_id}' sumiu do registro"
    campos_de_preco = {"precoUnitario", "valorUnitario"}
    assert campos_de_preco & set(rota.campos_esperados), (
        f"{rota.path} é rota de preço mas não exige campo de valor no "
        f"contrato; declarados: {rota.campos_esperados}"
    )


def test_contrato_campos_rotas_detalhe_nao_prometem_preco() -> None:
    """Rotas 2 e 4 não têm preço no DTO upstream — o registro não pode mentir.

    Verificado em 2026-08-05 contra o contrato OpenAPI e contra o upstream
    cru: `FtPesqPrecoCompraMaterialDetalheDTO` tem 7 campos, nenhum de valor.
    """
    for rota_id in ("preco_material_detalhe", "preco_servico_detalhe"):
        rota = rota_por_id(rota_id)
        assert rota is not None
        assert not ({"precoUnitario", "valorUnitario"} & set(rota.campos_esperados)), (
            f"{rota.path} não devolve preço; declarar o campo criaria um "
            "falso 'degradado' permanente"
        )


@pytest.mark.asyncio
async def test_probe_marca_degradado_quando_falta_campo_com_http_200(
    httpx_mock: HTTPXMock,
) -> None:
    """HTTP 200 sem o campo do contrato tem de virar `degradado`.

    Este é o teste que representa a falha da rota 2: status perfeito,
    payload inútil. Se o probe classificasse isso como `ok`, o healthcheck
    daria "pronto para uso" com a pesquisa de preço vazia.
    """
    rota = rota_por_id("preco_material")
    assert rota is not None

    # Payload plausível, porém sem nenhum campo de preço — como a rota 2.
    httpx_mock.add_response(
        url=_RE_MATERIAL,
        json={
            "resultado": [
                {
                    "idCompra": "16024005900112026",
                    "codigoItemCatalogo": 630237,
                    "descricaoDetalhadaItem": "CAMISA UNIFORME",
                }
            ],
            "totalRegistros": 1,
            "totalPaginas": 1,
        },
    )

    (resultado,) = await executar_probe([rota], timeout=5.0)

    assert resultado.http_status == 200, "o cenário é justamente 200 + payload ruim"
    assert resultado.status == STATUS_DEGRADADO
    assert "precoUnitario" in resultado.campos_faltando


@pytest.mark.asyncio
async def test_probe_marca_ok_quando_campos_estao_presentes(
    httpx_mock: HTTPXMock,
) -> None:
    """Contraprova do teste acima: com os campos, o probe aprova."""
    rota = rota_por_id("preco_material")
    assert rota is not None
    httpx_mock.add_response(url=_RE_MATERIAL, json=_resposta_material())

    (resultado,) = await executar_probe([rota], timeout=5.0)

    assert resultado.status == STATUS_OK
    assert resultado.campos_faltando == []


@pytest.mark.asyncio
async def test_probe_reconfirma_timeout_antes_de_dar_rota_como_fora(
    httpx_mock: HTTPXMock,
) -> None:
    """Rota lenta sob carga não pode ser reportada como quebrada.

    Verificado em produção 2026-08-05: `compras_arp_itens_listar` responde
    em 2,5s isolada e estourou 12s com 59 rotas concorrentes. O healthcheck
    chegou a marcar o módulo `atas` como degradado sem nada estar quebrado.
    """
    rota = rota_por_id("preco_material")
    assert rota is not None
    httpx_mock.add_exception(httpx.TimeoutException("estourou sob carga"), url=_RE_MATERIAL)
    httpx_mock.add_response(url=_RE_MATERIAL, json=_resposta_material())

    (resultado,) = await executar_probe([rota], timeout=5.0, reconfirmar_timeouts=True)

    assert resultado.status == STATUS_OK, "2ª tentativa respondeu — não é rota fora"
    assert "lenta sob carga" in resultado.detalhe, (
        "a lentidão tem de aparecer no diagnóstico, não ser varrida para baixo do tapete"
    )
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_probe_nao_reconfirma_erro_http(httpx_mock: HTTPXMock) -> None:
    """404/500 é resposta do servidor, não pressão do probe: não repetir.

    Contraprova do teste acima — sem isto, `reconfirmar_timeouts` viraria
    um retry genérico e dobraria o custo de todo probe com upstream fora.
    """
    rota = rota_por_id("preco_material")
    assert rota is not None
    httpx_mock.add_response(url=_RE_MATERIAL, status_code=404, text="Not Found")

    (resultado,) = await executar_probe([rota], timeout=5.0, reconfirmar_timeouts=True)

    assert resultado.status == STATUS_FORA
    assert resultado.http_status == 404
    assert len(httpx_mock.get_requests()) == 1, "erro HTTP não deve ser reexecutado"


# ===========================================================================
# 3. Degradação graciosa — 404 não pode virar exception nem conclusão falsa
# ===========================================================================


@pytest.mark.asyncio
async def test_preco_material_404_devolve_erro_upstream_sem_excecao(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_RE_MATERIAL, status_code=404, json={})

    payload = await _run(
        "compras_pesquisar_preco_material", {"codigo_item_catalogo": 630237}
    )

    assert "_erro_upstream" in payload
    assert payload["_erro_upstream"]["status"] == 404
    assert payload["resultado"] == []
    assert payload["_erro_upstream"]["alternativas"], "diagnóstico sem alternativa não ajuda"


@pytest.mark.asyncio
async def test_etp_detecta_404_antes_de_paginar(httpx_mock: HTTPXMock) -> None:
    """O ETP não pode estourar no meio da paginação nem tentar página 2.

    Uma única requisição deve acontecer: a falha é detectada no preflight,
    antes de qualquer agregação.
    """
    httpx_mock.add_response(url=_RE_MATERIAL, status_code=404, json={})

    payload = await _run(
        "compras_pesquisar_precos_para_etp",
        {"tipo": "material", "codigo_item_catalogo": 630237, "max_paginas": 5},
    )

    assert "_erro_upstream" in payload
    assert payload["amostra_total"] == 0
    assert payload["estatisticas"] is None, (
        "estatística sobre amostra inexistente induziria o analista a erro"
    )
    assert len(httpx_mock.get_requests()) == 1, (
        "paginação começou apesar da rota estar fora"
    )


@pytest.mark.asyncio
async def test_uasg_listar_404_degrada_com_diagnostico(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_RE_UASG, status_code=404, json={})

    payload = await _run("compras_uasg_listar", {"tamanho_pagina": 10})

    assert payload["_erro_upstream"]["status"] == 404
    assert "verificar_com" in payload["_erro_upstream"]


# ===========================================================================
# 4. Integridade do registro de rotas
# ===========================================================================


def test_registro_sem_ids_duplicados() -> None:
    ids = [r.id for r in ROTAS]
    assert len(ids) == len(set(ids)), "ids duplicados quebram o mecanismo de seed"


def test_registro_seeds_apontam_para_rotas_existentes() -> None:
    ids = {r.id for r in ROTAS}
    for rota in ROTAS:
        if rota.seed is not None:
            pai, _mapa = rota.seed
            assert pai in ids, f"{rota.id} semeia de '{pai}', que não existe"


def test_registro_path_params_tem_placeholder_no_path() -> None:
    for rota in ROTAS:
        for param in rota.path_params:
            assert "{" + param + "}" in rota.path, (
                f"{rota.id} declara path_param '{param}' ausente do path"
            )


# ===========================================================================
# 5. Contrato ao vivo (opt-in) — o alarme de verdade
# ===========================================================================


@live
@pytest.mark.asyncio
@pytest.mark.parametrize("rota_id", IDS_ROTAS_DE_PRECO)
async def test_live_rota_de_preco_ainda_devolve_preco(rota_id: str) -> None:
    """Bate no upstream real e exige o campo de preço. HTTP 200 não basta."""
    rota = rota_por_id(rota_id)
    assert rota is not None

    (resultado,) = await executar_probe([rota], timeout=30.0)

    assert resultado.http_status == 200, f"{rota.path} respondeu {resultado.http_status}"
    assert not resultado.campos_faltando, (
        f"{rota.path} respondeu 200 mas sem {resultado.campos_faltando} — "
        "contrato de campos quebrado no upstream"
    )


@live
@pytest.mark.asyncio
async def test_live_pesquisa_preco_material_ponta_a_ponta() -> None:
    """A tool que quebrou em 2026-08: preço de verdade, ponta a ponta."""
    payload = await _run(
        "compras_pesquisar_preco_material",
        {"codigo_item_catalogo": 630237, "tamanho_pagina": 10},
    )

    assert "_erro_upstream" not in payload, payload.get("_erro_upstream")
    assert payload["_total_registros"] > 0
    primeiro = payload["resultado"][0]
    assert primeiro.get("precoUnitario") is not None
    assert primeiro.get("nomeFornecedor")
