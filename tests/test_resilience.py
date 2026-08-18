"""Testes de resiliência — não validam *correção* (o que `test_server.py`
já cobre), mas sim como o servidor reage a entradas ruins e a falhas do
ecossistema upstream (CGU/PNCP/Dados Abertos).

Cada teste cobre um corner case que a bateria E2E pode não bater no
caminho feliz mas que aparece em produção. Tudo mockado via
`pytest-httpx` (sem rede).
"""

from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

# Side effect: importar registra todas as tools
import compras_mcp.server  # noqa: F401, E402


# pytest-httpx 0.36+ não aceita mais o sufixo `url__regex`; passamos um
# `re.Pattern` direto via `url=`.
_RE_DADOS_ABERTOS_FORNECEDOR = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-fornecedor/1_consultarFornecedor.*"
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _structured(result):
    """Desempacota ToolResult do FastMCP."""
    return result.structured_content if hasattr(result, "structured_content") else result


# ----------------------------------------------------------------------------
# Teste 1 — CNPJ inválido
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cnpj_invalido_recebe_erro_graceful_nao_crash() -> None:
    """CNPJs malformados devem voltar com `_erro` informativo, nunca crash.

    Casos cobertos: 13 dígitos (curto), alfanumérico, vazio.
    """
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    inputs_ruins = [
        "1234567890123",  # 13 dígitos
        "abcdefghijklmn",  # alfanumérico
        "12345",  # muito curto
    ]
    for cnpj_ruim in inputs_ruins:
        # FastMCP valida min_length antes de chamar a função.
        # Para os casos que passam validação Pydantic (len 13), o handler
        # tem que devolver `_erro` ou `encontrado=false`.
        try:
            result = await tools["compras_fornecedor_cnpj_receita"].run({"cnpj": cnpj_ruim})
        except Exception as e:
            # Validation error do Pydantic é OK — também não crashou silenciosa.
            assert "cnpj" in str(e).lower() or "length" in str(e).lower(), (
                f"Erro de validação opaco para CNPJ {cnpj_ruim!r}: {e}"
            )
            continue
        payload = _structured(result)
        assert payload.get("encontrado") is False, (
            f"CNPJ inválido {cnpj_ruim!r} foi tratado como válido: {payload}"
        )
        # Deve ter algum diagnóstico
        assert "_erro" in payload or "_erro_upstream" in payload, (
            f"CNPJ inválido {cnpj_ruim!r} sem diagnóstico: {payload}"
        )


# ----------------------------------------------------------------------------
# Teste 2 — CNPJ em múltiplos cadastros simultâneos
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cnpj_em_multiplos_cadastros_acumula_corretamente(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Um CNPJ que está em CEIS **e** CNEP **e** CEPIM ao mesmo tempo deve
    aparecer nos três blocos do `perfil_fornecedor_completo` sem perda nem
    dedup errado, com `tem_alguma_restricao=true` e `sancoes.total == 3`.
    """
    monkeypatch.setenv("TRANSPARENCIA_API_KEY", "test-key")
    cnpj = "11222333000144"
    cnpj_fmt = "11.222.333/0001-44"

    # CEIS responde com 1 item para o CNPJ
    httpx_mock.add_response(
        url="https://api.portaldatransparencia.gov.br/api-de-dados/ceis?codigoSancionado=11222333000144&pagina=1",
        json=[{"id": 1, "sancionado": {"codigoFormatado": cnpj_fmt, "nome": "EMPRESA TESTE"}}],
    )
    # CNEP idem
    httpx_mock.add_response(
        url="https://api.portaldatransparencia.gov.br/api-de-dados/cnep?codigoSancionado=11222333000144&pagina=1",
        json=[{"id": 2, "sancionado": {"codigoFormatado": cnpj_fmt, "nome": "EMPRESA TESTE"}}],
    )
    # CEPIM com filtragem client-side: upstream retorna lista global, o cliente filtra
    httpx_mock.add_response(
        url="https://api.portaldatransparencia.gov.br/api-de-dados/cepim?cnpjEntidade=11222333000144&pagina=1",
        json=[
            {"id": 3, "pessoaJuridica": {"cnpjFormatado": cnpj_fmt, "nome": "EMPRESA TESTE"}},
            {"id": 999, "pessoaJuridica": {"cnpjFormatado": "00.000.000/0001-00", "nome": "OUTRA"}},
        ],
    )
    # Cadastro Dados Abertos
    httpx_mock.add_response(
        url=re.compile(r"https://dadosabertos\.compras\.gov\.br/modulo-fornecedor/1_consultarFornecedor.*"),
        json={"resultado": [{"cnpj": cnpj, "nomeRazaoSocialFornecedor": "EMPRESA TESTE"}]},
    )
    # BrasilAPI
    httpx_mock.add_response(
        url=f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
        json={"cnpj": cnpj, "razao_social": "EMPRESA TESTE", "qsa": []},
    )
    # Comprasnet impedimentos
    httpx_mock.add_response(
        url="https://contratos.comprasnet.gov.br/api/comprasnet/compras/impedimentos",
        json=[],
        method="POST",
    )

    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    result = await tools["compras_perfil_fornecedor_completo"].run({"cnpj": cnpj})
    payload = _structured(result)

    sancoes = payload["sancoes"]
    assert sancoes["habilitado"] is True
    assert len(sancoes["ceis"]) == 1, f"CEIS deveria ter 1 item: {sancoes['ceis']}"
    assert len(sancoes["cnep"]) == 1, f"CNEP deveria ter 1 item: {sancoes['cnep']}"
    assert len(sancoes["cepim"]) == 1, (
        f"CEPIM deveria ter 1 item (filtrado client-side de 2): {sancoes['cepim']}"
    )
    assert sancoes["total"] == 3
    assert payload["tem_alguma_restricao"] is True


# ----------------------------------------------------------------------------
# Teste 3 — Aggregate boundaries
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_rejeita_data_invertida() -> None:
    """data_final < data_inicial → ValueError claro, sem chegar no upstream."""
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    with pytest.raises(Exception) as exc_info:
        await tools["compras_aggregate_contratacoes_por_periodo"].run(
            {
                "data_inicial": "2026-05-10",
                "data_final": "2026-05-01",
                "codigo_modalidade": 6,
            }
        )
    assert "data_inicial" in str(exc_info.value).lower() or ">=" in str(exc_info.value)


@pytest.mark.asyncio
async def test_aggregate_rejeita_janela_acima_5_anos() -> None:
    """Janela total > MAX_AGGREGATION_DAYS (1825) → ValueError com diagnóstico."""
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    with pytest.raises(Exception) as exc_info:
        await tools["compras_aggregate_contratacoes_por_periodo"].run(
            {
                "data_inicial": "2018-01-01",
                "data_final": "2026-05-17",
                "codigo_modalidade": 6,
            }
        )
    msg = str(exc_info.value).lower()
    assert "limite" in msg or "5 anos" in msg or "1825" in msg


@pytest.mark.asyncio
async def test_aggregate_rejeita_granularidade_invalida() -> None:
    """granularidade fora do enum → Pydantic ValidationError."""
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    with pytest.raises(Exception):
        await tools["compras_aggregate_contratacoes_por_periodo"].run(
            {
                "data_inicial": "2026-05-01",
                "data_final": "2026-05-07",
                "codigo_modalidade": 6,
                "granularidade": "decada",
            }
        )


# ----------------------------------------------------------------------------
# Teste 4 — TRANSPARENCIA_API_KEY ausente
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perfil_fornecedor_sem_chave_transparencia_degrada_gracioso(
    httpx_mock: HTTPXMock,
) -> None:
    """Sem TRANSPARENCIA_API_KEY, o bloco `sancoes` vem com `habilitado=false`
    mas cadastro + receita + impedimentos seguem normais. Não pode crashar.
    """
    cnpj = "33000167000101"
    httpx_mock.add_response(
        url=re.compile(r"https://dadosabertos\.compras\.gov\.br/modulo-fornecedor/1_consultarFornecedor.*"),
        json={"resultado": [{"cnpj": cnpj, "nomeRazaoSocialFornecedor": "PETROBRAS"}]},
    )
    httpx_mock.add_response(
        url=f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
        json={"cnpj": cnpj, "razao_social": "PETROLEO BRASILEIRO S A PETROBRAS", "qsa": []},
    )
    httpx_mock.add_response(
        url="https://contratos.comprasnet.gov.br/api/comprasnet/compras/impedimentos",
        json=[],
        method="POST",
    )

    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    result = await tools["compras_perfil_fornecedor_completo"].run({"cnpj": cnpj})
    payload = _structured(result)

    assert payload["sancoes"]["habilitado"] is False, (
        "sem API key, sanções deveriam vir como habilitado=false"
    )
    assert "aviso" in payload["sancoes"]
    # Cadastro e receita seguem
    assert payload["cadastro"] is not None
    assert payload["receita_federal"]["encontrado"] is True
    assert payload["receita_federal"]["razao_social"].upper().startswith("PETRO")
    # Como sancoes.habilitado=false, sem total — não acumula restrição
    assert payload["tem_alguma_restricao"] is False


@pytest.mark.asyncio
async def test_sancao_ceis_sem_chave_falha_com_mensagem_clara(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compras_sancao_ceis` sem API key deve produzir erro com mensagem
    diagnóstica clara (mencionando 'chave', 'auth' ou 401) — não crash
    silencioso. Aceita dois caminhos: `_erro_upstream` no payload OU
    exception com mensagem informativa.

    Fix futuro desejável: early return graceful como já existe em
    `compras_perfil_fornecedor_completo`. Hoje propaga exception, mas a
    mensagem é clara o suficiente para o LLM reagir.
    """
    # Garante que não há chave em os.environ (auth.py lê direto de os.environ,
    # não passa por Settings — o conftest pode não cobrir esse caminho).
    monkeypatch.delenv("TRANSPARENCIA_API_KEY", raising=False)

    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    try:
        result = await tools["compras_sancao_ceis"].run({"cnpj": "33000167000101"})
        payload = _structured(result)
        assert (
            "_erro_upstream" in payload
            or payload.get("_erro")
            or payload.get("resultado") == []
        ), f"Sem API key, esperava degradação graceful: {payload}"
    except Exception as e:
        msg = str(e).lower()
        assert any(
            k in msg for k in ("chave", "401", "auth", "api key", "api_key")
        ), f"Sem API key, exception sem mensagem clara: {e!r}"


# ----------------------------------------------------------------------------
# Teste 5 — Composta com sub-call quebrada
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perfil_fornecedor_segue_quando_receita_federal_falha(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se BrasilAPI cair (500 persistente), os outros 3 blocos do perfil
    devem seguir intactos graças a `return_exceptions=True` no gather.
    """
    monkeypatch.setenv("TRANSPARENCIA_API_KEY", "test-key")
    cnpj = "33000167000101"

    # BrasilAPI quebrada
    httpx_mock.add_response(
        url=f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
        status_code=500,
        text="Internal Server Error",
        is_reusable=True,
    )
    # Sanções vazias
    for cadastro, param in (("ceis", "codigoSancionado"), ("cnep", "codigoSancionado"), ("cepim", "cnpjEntidade")):
        httpx_mock.add_response(
            url=f"https://api.portaldatransparencia.gov.br/api-de-dados/{cadastro}?{param}={cnpj}&pagina=1",
            json=[],
        )
    # Cadastro Dados Abertos
    httpx_mock.add_response(
        url=re.compile(r"https://dadosabertos\.compras\.gov\.br/modulo-fornecedor/1_consultarFornecedor.*"),
        json={"resultado": [{"cnpj": cnpj, "nomeRazaoSocialFornecedor": "PETROBRAS"}]},
    )
    # Comprasnet
    httpx_mock.add_response(
        url="https://contratos.comprasnet.gov.br/api/comprasnet/compras/impedimentos",
        json=[],
        method="POST",
    )

    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    result = await tools["compras_perfil_fornecedor_completo"].run({"cnpj": cnpj})
    payload = _structured(result)

    # Cadastro/sanções/impedimentos OK
    assert payload["cadastro"] is not None
    assert payload["sancoes"]["habilitado"] is True
    assert payload["sancoes"]["total"] == 0
    assert isinstance(payload["impedimentos_comprasnet"], list)
    # Receita falhou, mas o servidor não crashou — devolveu marca de erro
    receita = payload["receita_federal"]
    assert receita is None or receita.get("encontrado") is False, (
        "BrasilAPI quebrada deveria virar receita_federal.encontrado=false"
    )
    if receita and "_erro" in receita:
        msg = receita["_erro"]
        assert any(
            marca in msg
            for marca in ("500", "Server", "HTTP", "retries", "Esgotou")
        ), f"erro não menciona origem da falha upstream: {msg!r}"


# ----------------------------------------------------------------------------
# Teste 7 — ARP ID-de-compra-sem-sufixo
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arp_consultar_rejeita_id_de_compra_com_diagnostico_explicito() -> None:
    """`compras_arp_consultar` exige ID de ATA (com sufixo `-NNNNNN`).
    Quando o agente passa o ID de COMPRA (sem o sufixo), antes da v0.3.7
    a tool batia no upstream e devolvia `encontrada: false` silencioso —
    indistinguível de "ata não existe", causando travamento na Etapa 4 da
    bateria A v0.3.5.

    Após o fix, a tool detecta o formato antes da chamada e devolve
    `_erro_upstream.tipo == "formato_id_invalido"` com diagnóstico
    específico orientando o uso correto.
    """
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    # ID de COMPRA (sem sufixo)
    r = await tools["compras_arp_consultar"].run(
        {"numero_controle_pncp_ata": "00394452000103-1-004729/2024"}
    )
    payload = _structured(r)
    assert payload["encontrada"] is False
    erro = payload.get("_erro_upstream") or {}
    assert erro.get("tipo") == "formato_id_invalido"
    # Diagnóstico específico — quando ID combina com pattern de compra
    assert "diagnostico_especifico" in erro
    assert "COMPRA" in erro["diagnostico_especifico"]


@pytest.mark.asyncio
async def test_arp_consultar_rejeita_id_totalmente_invalido() -> None:
    """ID que nem é de compra nem de ata deve receber diagnóstico de
    formato, sem `diagnostico_especifico` (porque não combina com nenhum
    pattern conhecido)."""
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    r = await tools["compras_arp_consultar"].run(
        {"numero_controle_pncp_ata": "lixo123"}
    )
    payload = _structured(r)
    assert payload["encontrada"] is False
    erro = payload.get("_erro_upstream") or {}
    assert erro.get("tipo") == "formato_id_invalido"
    assert "diagnostico_especifico" not in erro


@pytest.mark.asyncio
async def test_montar_dossie_arp_rejeita_id_de_compra() -> None:
    """Mesma proteção em `compras_montar_dossie_arp`."""
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    r = await tools["compras_montar_dossie_arp"].run(
        {
            "numero_controle_pncp_ata": "00394452000103-1-004729/2024",
            "numero_ata": "00006/2024",
            "unidade_gerenciadora": 160444,
        }
    )
    payload = _structured(r)
    erro = payload.get("_erro_upstream") or {}
    assert erro.get("tipo") == "formato_id_invalido"


# ----------------------------------------------------------------------------
# Teste 6 — WAF block simulado
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sancao_ceis_waf_block_retorna_erro_upstream_classificado(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quando a CGU retorna 405 + HTML 'Human Verification' (WAF block),
    `compras_sancao_ceis` deve devolver `_erro_upstream.tipo == "waf_block"`
    em vez de propagar exception. Guarda o fix da v0.2.12.
    """
    monkeypatch.setenv("TRANSPARENCIA_API_KEY", "test-key")

    httpx_mock.add_response(
        url=re.compile(r"https://api\.portaldatransparencia\.gov\.br/api-de-dados/ceis.*"),
        status_code=405,
        headers={"Content-Type": "text/html; charset=utf-8"},
        text="<html><body>Human Verification</body></html>",
        is_reusable=True,
    )

    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    result = await tools["compras_sancao_ceis"].run({"cnpj": "33000167000101"})
    payload = _structured(result)

    assert "_erro_upstream" in payload, f"Esperava _erro_upstream: {payload}"
    erro = payload["_erro_upstream"]
    assert erro.get("tipo") == "waf_block", (
        f"WAF block deveria ser classificado como tipo=waf_block: {erro}"
    )
    assert erro.get("status") == 405
    assert "alternativas" in erro and len(erro["alternativas"]) >= 1
