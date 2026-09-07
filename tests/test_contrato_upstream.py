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

import contextlib
import json
import os
import pathlib
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


# ===========================================================================
# 5. API de arquivos do PNCP (host /api/pncp) — PR #1
# ===========================================================================
#
# Esta família existe porque a rota de arquivos mora num host diferente do
# resto do PNCP. `/api/consulta` exige `chave-api-dadosabertos` e não expõe
# anexo nenhum; `/api/pncp` é aberto e é o único lugar onde o Edital/TR vive.
# Uma "simplificação" que trocasse `make_pncp_api` por `make_pncp` devolveria
# 404 em produção sem quebrar nenhum outro teste — daí travarmos o host.

_RE_PNCP_ARQ_COMPRA = re.compile(
    r"https://pncp\.gov\.br/api/pncp/v1/orgaos/\d+/compras/\d+/\d+/arquivos.*"
)
_RE_PNCP_ARQ_ATA = re.compile(
    r"https://pncp\.gov\.br/api/pncp/v1/orgaos/\d+/compras/\d+/\d+/atas/\d+/arquivos.*"
)

# Amostra fiel da rota (campos conforme upstream em 2026-09-07).
_ARQUIVO_EDITAL = {
    "uri": "https://pncp.gov.br/pncp-api/v1/orgaos/00509018000113/compras/2025/2101/arquivos/2",
    "url": "https://pncp.gov.br/pncp-api/v1/orgaos/00509018000113/compras/2025/2101/arquivos/2",
    "tipoDocumentoNome": "Edital",
    "tipoDocumentoDescricao": "Edital",
    "statusAtivo": True,
    "dataPublicacaoPncp": "2025-08-26T08:17:46",
    "cnpj": "00509018000113",
    "anoCompra": 2025,
    "sequencialCompra": 2101,
    "sequencialDocumento": 2,
    "titulo": "07000805900422025001",
    "tipoDocumentoId": 2,
}


@pytest.mark.asyncio
async def test_arquivos_contratacao_usa_host_api_pncp(httpx_mock: HTTPXMock) -> None:
    """Trava o host: tem que ser /api/pncp, não /api/consulta."""
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, json=[_ARQUIVO_EDITAL])

    await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 2101},
    )

    url = str(httpx_mock.get_requests()[0].url)
    assert "/api/pncp/" in url, f"rota de arquivos saiu do host errado: {url}"
    assert "/api/consulta/" not in url


@pytest.mark.asyncio
async def test_arquivos_contratacao_sequencial_sem_zeros_a_esquerda(
    httpx_mock: HTTPXMock,
) -> None:
    """O path usa o sequencial cru; 002101 no lugar de 2101 devolve 404."""
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, json=[_ARQUIVO_EDITAL])

    await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00.509.018/0001-13", "ano": 2025, "sequencial": 2101},
    )

    url = str(httpx_mock.get_requests()[0].url)
    # CNPJ pontuado tem que chegar só com dígitos no path.
    assert "/orgaos/00509018000113/compras/2025/2101/arquivos" in url


@pytest.mark.asyncio
async def test_arquivos_contratacao_normaliza_lista_crua(httpx_mock: HTTPXMock) -> None:
    """Upstream devolve array cru (sem envelope `data`); a tool empacota."""
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, json=[_ARQUIVO_EDITAL])

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 2101},
    )

    assert payload["encontrado"] is True
    assert payload["_total_registros"] == 1
    assert payload["resultado"][0]["url"], "sem `url` a tool não serve para nada"
    assert payload["resultado"][0]["tipoDocumentoNome"] == "Edital"
    assert "_latency_ms" in payload


@pytest.mark.asyncio
async def test_arquivos_contratacao_lista_vazia_nao_finge_achado(
    httpx_mock: HTTPXMock,
) -> None:
    """Contratação sem anexo tem que dizer `encontrado: False`, não `[None]`."""
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, json=[])

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 2101},
    )

    assert payload["encontrado"] is False
    assert payload["resultado"] == []
    assert payload["_total_registros"] == 0


@pytest.mark.asyncio
async def test_arquivos_contratacao_404_degrada_com_diagnostico(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, status_code=404, json={})

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 999999},
    )

    assert payload["encontrado"] is False
    assert "_erro_upstream" in payload
    assert payload["_erro_upstream"]["status"] == 404
    assert payload["_erro_upstream"]["alternativas"]


@pytest.mark.asyncio
async def test_arquivos_ata_monta_path_aninhado(httpx_mock: HTTPXMock) -> None:
    """A ata é sub-recurso da compra: compras/{ano}/{seq}/atas/{seqAta}."""
    httpx_mock.add_response(
        url=_RE_PNCP_ARQ_ATA,
        json=[
            {
                "url": "https://pncp.gov.br/pncp-api/v1/orgaos/00509018000113"
                "/compras/2025/2101/atas/1/arquivos/1",
                "dataPublicacaoPncp": "2025-09-30T10:26:19",
                "sequencialDocumento": 1,
                "titulo": "Ata de Registro de Preços nº 00103",
                "tipoDocumentoNome": "Ata de Registro de Preços",
                "tipoDocumentoId": 11,
            },
            {
                "url": "https://pncp.gov.br/pncp-api/v1/orgaos/00509018000113"
                "/compras/2025/2101/atas/1/arquivos/2",
                "dataPublicacaoPncp": "2026-08-19T09:48:12",
                "sequencialDocumento": 2,
                "titulo": "Termo aditivo: reequilíbrio dos Itens 2, 3 e 5.",
                "tipoDocumentoNome": "Ata de Registro de Preços",
                "tipoDocumentoId": 11,
            },
        ],
    )

    payload = await _run(
        "compras_pncp_ata_arquivos",
        {
            "cnpj": "00509018000113",
            "ano_compra": 2025,
            "sequencial_compra": 2101,
            "sequencial_ata": 1,
        },
    )

    url = str(httpx_mock.get_requests()[0].url)
    assert "/api/pncp/v1/orgaos/00509018000113/compras/2025/2101/atas/1/arquivos" in url
    # Aditivo vem como documento extra do MESMO tipo da ata original: se a
    # tool filtrasse por `tipoDocumentoNome`, o aditivo sumiria.
    assert payload["_total_registros"] == 2
    assert {d["sequencialDocumento"] for d in payload["resultado"]} == {1, 2}


def test_registro_rotas_de_arquivo_exigem_url() -> None:
    """Sem `url` no item, a tool devolve metadado sem serventia — HTTP 200
    inútil, exatamente o caso que o registro de rotas existe para pegar.
    """
    for rota_id in ("pncp_compra_arquivos", "pncp_ata_arquivos"):
        rota = rota_por_id(rota_id)
        assert rota is not None, f"rota '{rota_id}' sumiu do registro"
        assert "url" in rota.campos_esperados, (
            f"'{rota_id}' não trava o campo `url` — quebra passaria como 200 OK"
        )
        assert rota.api == "pncp_api", "rota de arquivos aponta para o host errado"


# ===========================================================================
# 6. Classificação de erro do PNCP — timeout/5xx não são erro de parâmetro
# ===========================================================================
#
# `ComprasTimeoutError` e `ComprasServerError` herdam de `ComprasHTTPError`,
# então o antigo `else: 400` varria os dois para "requisição malformada". Na
# prática isso mandava o analista revisar argumentos corretos enquanto o PNCP
# estava só lento — observado ao vivo em 2026-09-07, com /api/consulta
# devolvendo 503/timeout em todas as rotas ao mesmo tempo.


@pytest.mark.asyncio
async def test_pncp_timeout_nao_vira_erro_de_parametro(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("upstream lento"), url=_RE_PNCP_ARQ_COMPRA)

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 2101},
    )

    erro = payload["_erro_upstream"]
    assert erro["status"] == 504, "timeout classificado como erro de parâmetro"
    assert "parâmetro" not in erro["diagnostico"] or "Não é erro de parâmetro" in (
        erro["diagnostico"]
    )
    assert "rejeitou os parâmetros" not in erro["diagnostico"]
    assert erro["alternativas"], "timeout sem alternativa não ajuda o analista"


@pytest.mark.asyncio
async def test_pncp_503_nao_vira_erro_de_parametro(httpx_mock: HTTPXMock) -> None:
    """O PNCP devolve 503 em janelas de indisponibilidade — culpar os
    argumentos do analista nesse caso custa uma investigação inteira à toa.
    """
    httpx_mock.add_response(
        url=_RE_PNCP_ARQ_COMPRA, status_code=503, text="Service Unavailable"
    )

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 2101},
    )

    erro = payload["_erro_upstream"]
    assert erro["status"] == 502
    assert "rejeitou os parâmetros" not in erro["diagnostico"]
    assert erro["alternativas"]


@pytest.mark.asyncio
async def test_pncp_404_continua_sendo_404(httpx_mock: HTTPXMock) -> None:
    """Regressão da correção acima: 404 não pode ter virado 502/504."""
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, status_code=404, json={})

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 999999},
    )

    assert payload["_erro_upstream"]["status"] == 404


@pytest.mark.asyncio
async def test_pncp_400_continua_sendo_400(httpx_mock: HTTPXMock) -> None:
    """E um 400 de verdade continua acusando parâmetro malformado."""
    httpx_mock.add_response(url=_RE_PNCP_ARQ_COMPRA, status_code=400, text="bad request")

    payload = await _run(
        "compras_pncp_contratacao_arquivos",
        {"cnpj": "00509018000113", "ano": 2025, "sequencial": 1},
    )

    erro = payload["_erro_upstream"]
    assert erro["status"] == 400
    assert "rejeitou os parâmetros" in erro["diagnostico"]


# ===========================================================================
# 5. Contrato contra o OpenAPI oficial — o que teria pego os 4 defeitos de
#    2026-09 (parâmetro fora do contrato ignorado em silêncio)
# ===========================================================================
#
# `dadosabertos.compras.gov.br` responde HTTP 200 a QUALQUER chave de query
# desconhecida — e devolve o resultado como se nenhum filtro tivesse sido
# pedido. Não há 400, não há aviso: `codigoUasg=158132` (nome inexistente)
# devolve a janela inteira do Brasil, exatamente igual a `parametroInventado=1`.
#
# Foi assim que quatro tools passaram meses filtrando nada:
#   - `..._14133_listar` mandava `codigoUasg`/`cnpjOrgao`; o contrato declara
#     `unidadeOrgaoCodigoUnidade`/`orgaoEntidadeCnpj`.
#   - `compras_buscar_contratacoes_similares` mandava `codigoItemCatalogo` para
#     a rota de resultados, que não declara filtro por item de catálogo.
#   - `compras_catmat_buscar` mandava `descricao`; o contrato declara
#     `descricaoItem`.
#   - as três tools de consulta por id mandavam `tipo=C`, fora do enum
#     `[idCompra, numeroControlePNCPCompra]` (essa dava HTTP 500, não 200).
#
# O teste abaixo exercita cada tool com argumentos sintéticos preenchendo TODOS
# os parâmetros, intercepta o que sai no fio e confere contra o contrato:
# chave declarada, valor dentro do enum, obrigatório presente.

_URL_DADOS_ABERTOS = "https://dadosabertos.compras.gov.br"
_FIXTURE_OPENAPI = (
    pathlib.Path(__file__).parent / "fixtures" / "dadosabertos_openapi_params.json"
)

# Tools cuja quebra de contrato é o motivo deste teste existir. Se alguma
# parar de ser exercitada (renomeada, sem chamada HTTP), falha aqui em vez de
# passar a coberta em silêncio.
TOOLS_CRITICAS_DADOS_ABERTOS = (
    "compras_contratacoes_14133_listar",
    "compras_contratacoes_14133_consultar",
    "compras_contratacoes_14133_itens_listar",
    "compras_contratacoes_14133_itens_por_contratacao",
    "compras_contratacoes_14133_resultados_listar",
    "compras_contratacoes_14133_resultados_por_contratacao",
    "compras_catmat_buscar",
    "compras_catmat_listar_pdms",
    "compras_legado_itens_pregao_listar",
    "compras_legado_itens_sem_licitacao_listar",
    "compras_contratos_item_consultar",
    "compras_buscar_contratacoes_similares",
    "compras_pgc_por_catalogo",
    "compras_uasg_listar",
    "compras_orgao_listar",
)

# Rotas que a varredura precisa ter batido de fato. Sem isto, uma tool que
# despacha para duas rotas (por id x por período) passava como "exercitada"
# tendo visitado só uma delas — e a outra, que é a registrada no healthcheck,
# nunca era conferida.
PATHS_QUE_PRECISAM_SER_VISITADOS = (
    "/modulo-legado/4_consultarItensPregoes",
    "/modulo-legado/6_consultarCompraItensSemLicitacao",
    "/modulo-contratacoes/1_consultarContratacoes_PNCP_14133",
    "/modulo-contratacoes/2_consultarItensContratacoes_PNCP_14133",
    "/modulo-contratacoes/3_consultarResultadoItensContratacoes_PNCP_14133",
    "/modulo-material/3_consultarPdmMaterial",
    "/modulo-material/4_consultarItemMaterial",
    "/modulo-contratos/2.1_consultarContratosItem_Id",
    "/modulo-pgc/2_consultarPgcDetalheCatalogo",
    "/modulo-uasg/1_consultarUasg",
)

# Tools que escolhem a rota conforme o argumento informado. Preenchendo TODOS
# os parâmetros, a varredura só veria o ramo por id; estes conjuntos removem os
# argumentos do ramo dominante para que o outro também chegue ao fio.
RAMOS_EXCLUSIVOS: dict[str, tuple[tuple[str, ...], ...]] = {
    "compras_legado_itens_pregao_listar": (("id_compra", "id_compra_item"),),
    "compras_legado_itens_sem_licitacao_listar": (("id_compra", "id_compra_item"),),
}

# Valores sintéticos por nome de parâmetro da tool (não do upstream). O que
# importa é a chave que sai no fio, não o valor — mas o valor precisa passar
# pela validação do Pydantic e pelas normalizações das tools (CNPJ, UF, datas).
_VALORES_SINTETICOS: dict[str, Any] = {
    "termo": "cadeira",
    "uf": "SP",
    "sigla_uf": "SP",
    "cnpj": "10673078000120",
    "cnpj_orgao": "10673078000120",
    "cnpj_fornecedor": "10673078000120",
    "cnpj_cpf_fornecedor": "10673078000120",
    "cpf_cnpj_fornecedor": "10673078000120",
    "ni_fornecedor": "10673078000120",
    "codigo_ibge_municipio": 3550308,
    "id_compra": "15813206001272025",
    "id_compra_item": "158132060012720251",
    "id_contratacao": "15813206001272025",
    "codigo": "15813206001272025",
    "numero_controle_pncp": "10673078000120-1-000021/2025",
    "sequencial": 2101,
    "ano": 2024,
    "ano_aviso": 2020,
    "ano_compra": 2024,
    "max_paginas": 1,
    "tamanho_pagina": 10,
}


def _valores_de_data(nome: str) -> str:
    """Janela curta e válida; `final` depois de `inicial` para não tropeçar
    em validação de intervalo."""
    return "2025-03-05" if ("final" in nome or "fim" in nome) else "2025-03-03"


def _ramo_util(schema: dict[str, Any]) -> dict[str, Any]:
    """Descarta o ramo `null` do `anyOf` que o Optional gera."""
    if "anyOf" in schema:
        for ramo in schema["anyOf"]:
            if ramo.get("type") != "null":
                return ramo
    return schema


def _valor_sintetico(nome: str, schema: dict[str, Any]) -> Any:
    ramo = _ramo_util(schema)
    enum = schema.get("enum") or ramo.get("enum")
    if enum:
        return enum[0]
    if nome in _VALORES_SINTETICOS:
        return _VALORES_SINTETICOS[nome]
    tipo = ramo.get("type")
    if tipo == "string":
        if ramo.get("format") == "date":
            return _valores_de_data(nome)
        return "1"
    if tipo == "integer":
        return 1
    if tipo == "number":
        return 1.0
    if tipo == "boolean":
        return True
    if tipo == "array":
        return []
    return "1"


def _args_sinteticos(parametros: Any) -> dict[str, Any]:
    if not isinstance(parametros, dict):
        return {}
    props = parametros.get("properties") or {}
    return {
        nome: _valor_sintetico(nome, schema)
        for nome, schema in props.items()
        if isinstance(schema, dict)
    }


def _conjuntos_de_args(nome_tool: str, parametros: Any) -> list[dict[str, Any]]:
    """Argumentos a exercitar para uma tool: o caso cheio, um por valor de enum
    e um por ramo mutuamente exclusivo.

    Testar só `enum[0]` deixa passar tradução quebrada no outro ramo — é
    exatamente o caso de `tipo='M'|'S'` em `compras_pgc_por_catalogo`, onde só
    o ramo de material chegaria ao upstream.
    """
    base = _args_sinteticos(parametros)
    conjuntos = [base]
    props = (parametros or {}).get("properties") or {}
    for campo, schema in props.items():
        if not isinstance(schema, dict):
            continue
        enum = schema.get("enum") or _ramo_util(schema).get("enum") or []
        for valor in enum[1:]:
            conjuntos.append({**base, campo: valor})
    for remover in RAMOS_EXCLUSIVOS.get(nome_tool, ()):
        conjuntos.append({k: v for k, v in base.items() if k not in remover})
    return conjuntos


def _contrato_openapi() -> dict[str, dict[str, dict[str, Any]]]:
    doc = json.loads(_FIXTURE_OPENAPI.read_text(encoding="utf-8"))
    return doc["paths"]


# `pagina` e `tamanhoPagina` saem em toda chamada do cliente, e o contrato os
# declara de forma irregular: `/modulo-material/1` não declara nenhum dos dois,
# `/modulo-uasg/1` declara só `pagina`, as rotas `_Id` não declaram nenhum. Onde
# não são declarados, são de fato ignorados (medido em 2026-09-07:
# `/modulo-material/1` devolve os 78 grupos com `tamanhoPagina=10`, e
# `/modulo-uasg/1` devolve 500 registros com `tamanhoPagina=10`) — o que custa
# uma paginação inútil, nunca um filtro perdido. Ficam de fora da checagem
# porque o alvo aqui é o filtro que desaparece em silêncio.
_CHAVES_DE_PAGINACAO = frozenset({"pagina", "tamanhoPagina"})


def _violacoes_de_contrato(
    caminho: str, query: dict[str, list[str]], contrato: dict[str, Any]
) -> list[str]:
    declarados = contrato.get(caminho)
    if declarados is None:
        return [f"{caminho}: path não existe no OpenAPI oficial"]

    problemas: list[str] = []
    for chave, valores in sorted(query.items()):
        if chave in _CHAVES_DE_PAGINACAO:
            continue
        spec = declarados.get(chave)
        if spec is None:
            problemas.append(
                f"{caminho}: parâmetro `{chave}` não existe no contrato "
                f"(declarados: {', '.join(sorted(declarados))}) — o upstream "
                f"devolve 200 ignorando o filtro"
            )
            continue
        enum = spec.get("enum")
        if enum:
            fora = [v for v in valores if v not in {str(e) for e in enum}]
            if fora:
                problemas.append(
                    f"{caminho}: `{chave}={fora[0]}` fora do enum {enum}"
                )
    faltando = [
        nome
        for nome, spec in declarados.items()
        if spec.get("obrigatorio")
        and nome not in query
        and nome not in _CHAVES_DE_PAGINACAO
    ]
    if faltando:
        problemas.append(
            f"{caminho}: obrigatório(s) ausente(s) {faltando} — esta API "
            f"responde 404 'Resource not found' a obrigatório faltando"
        )
    return problemas


@pytest.mark.asyncio
async def test_openapi_toda_chave_enviada_consta_do_contrato(
    httpx_mock: HTTPXMock,
) -> None:
    """Nenhuma tool pode mandar chave/valor fora do contrato do Dados Abertos.

    Exercita cada tool com todos os parâmetros preenchidos e valida o que sai
    no fio. É o teste que faltava: sem ele, nome errado de parâmetro é
    indistinguível de filtro funcionando, porque o upstream responde 200 aos
    dois.
    """
    from compras_mcp.mcp_instance import mcp

    contrato = _contrato_openapi()
    httpx_mock.add_response(
        url=re.compile(r"https?://.*"),
        json={"resultado": [], "totalRegistros": 0, "totalPaginas": 0},
        is_reusable=True,
    )

    tools = await mcp.get_tools()
    problemas: list[str] = []
    exercitadas: set[str] = set()
    paths_visitados: set[str] = set()
    vistas = 0

    for nome in sorted(tools):
        tool = tools[nome]
        for args in _conjuntos_de_args(nome, tool.parameters):
            # Tool que recusa o argumento sintético não chega a chamar o
            # upstream: não há contrato a violar, e a cobertura é conferida
            # no fim do teste.
            with contextlib.suppress(Exception):
                await tool.run(args)
        requisicoes = httpx_mock.get_requests()[vistas:]
        vistas += len(requisicoes)
        for req in requisicoes:
            url = urlparse(str(req.url))
            if f"{url.scheme}://{url.netloc}" != _URL_DADOS_ABERTOS:
                continue
            exercitadas.add(nome)
            paths_visitados.add(url.path)
            problemas += [
                f"{nome} -> {p}"
                for p in _violacoes_de_contrato(
                    url.path, parse_qs(url.query), contrato
                )
            ]

    assert not problemas, "Chamadas fora do contrato do OpenAPI:\n" + "\n".join(
        sorted(set(problemas))
    )

    nao_exercitadas = sorted(set(TOOLS_CRITICAS_DADOS_ABERTOS) - exercitadas)
    assert not nao_exercitadas, (
        "tools críticas não chegaram a chamar o Dados Abertos — o teste passou "
        f"sem checar nada nelas: {nao_exercitadas}"
    )

    nao_visitados = sorted(set(PATHS_QUE_PRECISAM_SER_VISITADOS) - paths_visitados)
    assert not nao_visitados, (
        "rotas que ninguém exercitou nesta varredura — a query que o MCP manda "
        f"para elas não foi conferida contra o contrato: {nao_visitados}"
    )


def test_openapi_registro_upstream_bate_com_o_contrato() -> None:
    """O healthcheck usa `upstream_registry`; ele também tem que bater.

    Um probe que manda parâmetro fora do contrato dá verde para uma rota que
    na prática ignora o filtro — foi o que aconteceu com `tipo=C`: o registro
    estava certo e as tools erradas, e ninguém comparou os dois.
    """
    contrato = _contrato_openapi()
    problemas: list[str] = []

    for rota in ROTAS:
        if rota.api != "dados_abertos":
            continue
        query = {k: [str(v)] for k, v in (rota.params or {}).items()}
        problemas += [
            f"{rota.id} -> {p}"
            for p in _violacoes_de_contrato(rota.path, query, contrato)
            # o probe só precisa das chaves que ele manda; obrigatório
            # ausente é checado ao vivo pelo próprio healthcheck
            if "obrigatório" not in p
        ]

    assert not problemas, "Registro upstream fora do contrato:\n" + "\n".join(problemas)


@live
def test_openapi_snapshot_continua_igual_ao_upstream() -> None:
    """Alarme de drift: a SEGES mexeu no contrato desde o snapshot?

    Roda só com COMPRAS_LIVE_TESTS=1. Quando falhar, regenerar o fixture e
    conferir tool por tool o que mudou — foi uma mudança dessas (`tipo`+`codigo`
    na rota de preço) que derrubou a pesquisa de material em 2026-08.
    """
    spec = httpx.get(f"{_URL_DADOS_ABERTOS}/v3/api-docs", timeout=60).json()
    atual: dict[str, dict[str, Any]] = {}
    for path, item in spec["paths"].items():
        params: dict[str, Any] = {}
        for metodo, op in item.items():
            if metodo not in ("get", "post", "put", "delete", "patch"):
                continue
            for p in op.get("parameters", []):
                if p.get("in") != "query":
                    continue
                sch = p.get("schema") or {}
                enum = sch.get("enum") or (sch.get("items") or {}).get("enum")
                params[p["name"]] = {
                    "obrigatorio": bool(p.get("required")),
                    "enum": enum,
                }
        atual[path] = dict(sorted(params.items()))

    snapshot = _contrato_openapi()
    usados = {rota.path for rota in ROTAS if rota.api == "dados_abertos"}
    divergentes = [
        path
        for path in sorted(usados)
        if atual.get(path) != snapshot.get(path)
    ]
    assert not divergentes, (
        "contrato upstream mudou nas rotas que o MCP usa: "
        f"{divergentes}\nRegenerar tests/fixtures/dadosabertos_openapi_params.json"
    )
