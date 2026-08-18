"""Fixtures globais para os testes do MCP Compras.gov.br.

Mantemos o ambiente determinístico em CI: sem .env, sem chave de Transparência,
sem Redis. Cada teste que precisar de credenciais usa monkeypatch para setar
apenas o que precisa.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Limpa env vars do MCP entre testes para evitar vazamento.

    Mantém PATH e variáveis de sistema; só zera o que afeta o Settings/Cache.
    """
    for var in (
        "TRANSPARENCIA_API_KEY",
        "REDIS_URL",
        "DADOS_ABERTOS_BASE_URL",
        "PNCP_BASE_URL",
        "TRANSPARENCIA_BASE_URL",
        "COMPRASNET_CONTRATOS_BASE_URL",
        "COMPRASNET_BEARER_TOKEN",
        "HTTP_TIMEOUT",
        "HTTP_MAX_RETRIES",
        "INCLUIR_CPF_COMPLETO",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    # Zera caches que leem env vars no init (CACHE_*)
    for key in list(os.environ):
        if key.startswith("CACHE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_settings_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garante que cada teste pegue um Settings fresco (singleton lazy em config.py).

    Também desliga `load_dotenv` para que o .env real do dev (que pode
    ter chaves de produção) não vaze nos asserts dos testes.
    """
    from compras_mcp import config as _config

    monkeypatch.setattr(_config, "load_dotenv", lambda *a, **kw: None)
    _config._singleton = None
    yield
    _config._singleton = None


@pytest.fixture(autouse=True)
async def _reset_tool_caches() -> None:
    """Limpa caches em-memória das tools entre testes.

    Cache de tool é var de módulo (`_compostas_cache`, `_pncp_cache`, etc.).
    Persiste entre testes e contamina: ex., teste A cacheia perfil de CNPJ X
    sem chave de API (`sancoes.habilitado=false`); teste B com a mesma chave
    de CNPJ recebe cache hit e vê `habilitado=false` indevidamente.

    Limpamos após o teste (yield primeiro) para que erros num teste não
    impeçam o reset do próximo.
    """
    yield
    import importlib

    for mod_name in (
        "compras_mcp.tools.analitica",
        "compras_mcp.tools.atas",
        "compras_mcp.tools.catalogo",
        "compras_mcp.tools.compostas",
        "compras_mcp.tools.contratacoes",
        "compras_mcp.tools.contratos",
        "compras_mcp.tools.enriquecimento",
        "compras_mcp.tools.fornecedores",
        "compras_mcp.tools.indicadores",
        "compras_mcp.tools.organizacoes",
        "compras_mcp.tools.pesquisa_precos",
        "compras_mcp.tools.planejamento",
        "compras_mcp.tools.pncp",
        "compras_mcp.tools.sancoes",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for attr in dir(mod):
            if not attr.startswith("_") or "cache" not in attr.lower():
                continue
            cache = getattr(mod, attr, None)
            clear = getattr(cache, "clear", None)
            if callable(clear):
                try:
                    result = clear()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    pass
