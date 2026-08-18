"""Smoke tests do servidor FastMCP.

Inclui o teste SSoT que replica o padrão do mcp-inpi: as descriptions dos
parâmetros expostos no MCP têm que ser idênticas às descriptions dos campos
Pydantic em schemas.py. Isso evita drift entre validação e documentação.
"""

from __future__ import annotations

import pytest

# Side effect: importar `server` registra todas as tools no `mcp` global.
import compras_mcp.server  # noqa: F401, E402


@pytest.mark.asyncio
async def test_compras_versao_responde_com_metadados_basicos() -> None:
    """A tool de diagnóstico deve responder com nome/versão/fontes e flags."""
    from compras_mcp import __version__
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    assert "compras_versao" in tools, "Tool de healthcheck não foi registrada"

    payload = await tools["compras_versao"].run({})
    # FastMCP retorna ToolResult — payload bruto fica em .structured_content
    if hasattr(payload, "structured_content"):
        payload = payload.structured_content

    assert payload["nome"] == "compras-mcp"
    assert payload["versao"] == __version__
    fontes = payload["fontes"]
    assert "dados_abertos" in fontes and fontes["dados_abertos"].startswith("https://")
    assert "pncp" in fontes and fontes["pncp"].startswith("https://")
    assert "transparencia" in fontes and fontes["transparencia"].startswith("https://")
    # Sem env vars (vide conftest): flags devem ser False.
    assert payload["redis_configurado"] is False
    assert payload["transparencia_configurada"] is False
    assert "_latency_ms" in payload


@pytest.mark.asyncio
async def test_ssot_descriptions_pydantic_iguais_a_mcp_schema() -> None:
    """SSoT: a description vista pelo cliente MCP tem que ser idêntica à
    description do campo Pydantic correspondente em schemas.py.

    Cobre as tools atualmente implementadas. Quando novas tools forem
    adicionadas, estender o mapa abaixo.
    """
    from compras_mcp.mcp_instance import mcp
    from compras_mcp.schemas import (
        BuscarItemCatalogoInput,
        ConsultarCatmatInput,
        ConsultarCatserInput,
        ListarPaginadoInput,
        PesquisarPrecoMaterialInput,
        PesquisarPrecoServicoInput,
    )

    pairs: list[tuple[str, type, list[str]]] = [
        ("compras_catmat_listar_grupos", ListarPaginadoInput, ["pagina", "tamanho_pagina"]),
        # codigo_grupo de classes tem description inline (não em schema)
        ("compras_catmat_consultar", ConsultarCatmatInput, ["codigo_item"]),
        (
            "compras_catmat_buscar",
            BuscarItemCatalogoInput,
            ["termo", "pagina", "tamanho_pagina"],
        ),
        ("compras_catser_listar_secoes", ListarPaginadoInput, ["pagina", "tamanho_pagina"]),
        ("compras_catser_consultar", ConsultarCatserInput, ["codigo_item"]),
        (
            "compras_pesquisar_preco_material",
            PesquisarPrecoMaterialInput,
            [
                "codigo_item_catalogo",
                "data_inicio",
                "data_fim",
                "uf",
                "codigo_municipio",
                "codigo_uasg",
                "pagina",
                "tamanho_pagina",
            ],
        ),
        (
            "compras_detalhar_preco_material",
            PesquisarPrecoMaterialInput,
            [
                "codigo_item_catalogo",
                "data_inicio",
                "data_fim",
                "pagina",
                "tamanho_pagina",
            ],
        ),
        (
            "compras_pesquisar_preco_servico",
            PesquisarPrecoServicoInput,
            [
                "codigo_item_catalogo",
                "data_inicio",
                "data_fim",
                "uf",
                "codigo_municipio",
                "codigo_uasg",
                "pagina",
                "tamanho_pagina",
            ],
        ),
        (
            "compras_detalhar_preco_servico",
            PesquisarPrecoServicoInput,
            [
                "codigo_item_catalogo",
                "data_inicio",
                "data_fim",
                "pagina",
                "tamanho_pagina",
            ],
        ),
    ]

    tools = await mcp.get_tools()
    drift: list[str] = []
    for tool_name, model, fields in pairs:
        if tool_name not in tools:
            drift.append(f"{tool_name}: tool não registrada")
            continue
        tool = tools[tool_name]
        # FastMCP expõe parameters via .parameters (dict JSON Schema)
        props = tool.parameters.get("properties", {}) if isinstance(tool.parameters, dict) else {}
        for field in fields:
            mcp_desc = props.get(field, {}).get("description") if isinstance(props, dict) else None
            model_desc = model.model_fields[field].description
            if mcp_desc != model_desc:
                drift.append(
                    f"{tool_name}.{field}:\n  mcp     = {mcp_desc!r}\n  schemas = {model_desc!r}"
                )

    assert not drift, "Drift de descriptions detectado:\n" + "\n".join(drift)


@pytest.mark.asyncio
async def test_todas_as_tools_tem_description_nao_vazia() -> None:
    """Toda tool registrada tem que ter docstring/description não vazia.

    O cliente MCP usa essa string para o LLM entender o que a tool faz.
    """
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    sem_desc = [name for name, tool in tools.items() if not (tool.description or "").strip()]
    assert not sem_desc, f"Tools sem description: {sem_desc}"


@pytest.mark.asyncio
async def test_tools_seguem_convencao_de_naming() -> None:
    """Todas as tools começam com `compras_` (namespace consistente)."""
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    fora_do_padrao = [name for name in tools if not name.startswith("compras_")]
    assert not fora_do_padrao, f"Tools fora do padrão `compras_*`: {fora_do_padrao}"


@pytest.mark.asyncio
async def test_prompts_referenciam_apenas_tools_existentes() -> None:
    """Cada prompt renderiza um roteiro citando tools — todas devem existir.

    Bug recorrente: roteiros mencionam tool com nome desatualizado (ex.:
    `compras_brasilapi_cnpj` no dossiê de fornecedor v0.3.0, que na verdade
    se chama `compras_fornecedor_cnpj_receita`). Agente literal falha. Este
    teste varre o texto renderizado, extrai chamadas `compras_*` e exige que
    cada uma exista no servidor.
    """
    import re

    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    prompts = await mcp.get_prompts()

    nomes_validos = set(tools.keys())
    # Argumentos sintéticos para renderizar cada prompt — só precisam ser
    # type-correct, não precisam fazer sentido semântico.
    args_padrao: dict[str, dict[str, object]] = {
        "analisar_contratacao_pncp": {
            "cnpj_orgao": "00394460000141",
            "ano": 2025,
            "sequencial": 1,
        },
        "panorama_orgao_360": {"codigo_orgao": "00394460000141"},
        "dossie_due_diligence_fornecedor": {"cnpj": "33000167000101"},
        "oportunidades_carona_arp": {"palavra_chave": "notebook", "uf": "SP"},
        "montar_etp_pesquisa_precos": {
            "descricao_objeto": "Notebook 8GB",
            "codigo_catmat": 460789,
        },
        "tendencia_contratacoes_periodo": {
            "data_inicial": "2026-01-01",
            "data_final": "2026-03-31",
            "codigo_modalidade": 6,
        },
    }

    # Aceita `compras_xxx(`, `compras_xxx ` ou `compras_xxx"` como delimitadores
    # típicos em backticks e código embutido no roteiro.
    padrao = re.compile(r"compras_[a-z0-9_]+")

    drift: list[str] = []
    for nome in sorted(prompts):
        p = prompts[nome]
        args = args_padrao.get(nome, {})
        try:
            msgs = await p.render(args)
        except Exception as e:
            drift.append(f"prompt {nome!r} falhou ao renderizar: {e}")
            continue
        # concatena texto de todas as mensagens
        texto = ""
        for m in msgs:
            content = getattr(m, "content", None)
            if content is None:
                continue
            if isinstance(content, list):
                for c in content:
                    texto += getattr(c, "text", "") or ""
            else:
                texto += getattr(content, "text", "") or ""
        mencionadas = set(padrao.findall(texto))
        inexistentes = mencionadas - nomes_validos
        if inexistentes:
            drift.append(
                f"prompt {nome!r} cita tools inexistentes: {sorted(inexistentes)}"
            )

    assert not drift, "Tools fantasmas em prompts:\n" + "\n".join(drift)


@pytest.mark.asyncio
async def test_prompts_nao_usam_catmat_buscar_sem_codigo_grupo() -> None:
    """`compras_catmat_buscar` tem filtro textual quebrado upstream — devolve
    o universo CATMAT (~340k itens, começando por arma de fogo) quando
    chamado sem `codigo_grupo`. Qualquer prompt que oriente `catmat_buscar`
    sem mencionar `codigo_grupo` no mesmo bloco está conduzindo o agente
    para o bug.

    Heurística simples: para cada prompt renderizado, se o texto contém
    `compras_catmat_buscar(` então ele também deve mencionar `codigo_grupo`
    em algum lugar do texto. Achado da bateria E2E v0.3.3 que abriu o
    caminho para reincidência caso outro prompt seja adicionado no futuro.
    """
    from compras_mcp.mcp_instance import mcp

    prompts = await mcp.get_prompts()
    args_padrao: dict[str, dict[str, object]] = {
        "analisar_contratacao_pncp": {
            "cnpj_orgao": "00394460000141",
            "ano": 2025,
            "sequencial": 1,
        },
        "panorama_orgao_360": {"codigo_orgao": "00394460000141"},
        "dossie_due_diligence_fornecedor": {"cnpj": "33000167000101"},
        "oportunidades_carona_arp": {"palavra_chave": "notebook", "uf": "SP"},
        "montar_etp_pesquisa_precos": {
            "descricao_objeto": "Notebook 8GB",
            "codigo_catmat": 460789,
        },
        "tendencia_contratacoes_periodo": {
            "data_inicial": "2026-01-01",
            "data_final": "2026-03-31",
            "codigo_modalidade": 6,
        },
    }

    drift: list[str] = []
    for nome in sorted(prompts):
        p = prompts[nome]
        args = args_padrao.get(nome, {})
        msgs = await p.render(args)
        texto = ""
        for m in msgs:
            content = getattr(m, "content", None)
            if content is None:
                continue
            if isinstance(content, list):
                for c in content:
                    texto += getattr(c, "text", "") or ""
            else:
                texto += getattr(content, "text", "") or ""
        if "compras_catmat_buscar(" in texto and "codigo_grupo" not in texto:
            drift.append(
                f"prompt {nome!r} cita compras_catmat_buscar sem mencionar "
                "codigo_grupo — filtro textual upstream está quebrado, "
                "agente vai receber armas de fogo. Use workflow estrutural "
                "(listar_grupos → escolher → buscar com codigo_grupo)."
            )

    assert not drift, "\n".join(drift)


@pytest.mark.asyncio
async def test_modalidades_pncp_fonte_unica() -> None:
    """A tool `compras_pncp_modalidades` e o resource `modalidades-pncp` devem
    consumir a mesma fonte (`compras_mcp.dominio.MODALIDADES_PNCP`).

    Bug em v0.3.0: duas tabelas divergentes (ids 2/3/13 inconsistentes).
    Garantia: comparamos os ids+nomes das duas fontes.
    """
    import json

    from compras_mcp.dominio import MODALIDADES_PNCP as CANONICA
    from compras_mcp.mcp_instance import mcp

    tools = await mcp.get_tools()
    resources = await mcp.get_resources()

    # Da tool
    result = await tools["compras_pncp_modalidades"].run({})
    payload = (
        result.structured_content if hasattr(result, "structured_content") else result
    )
    da_tool = payload["resultado"]

    # Do resource
    r = resources["compras://referencia/modalidades-pncp"]
    conteudo = await r.read()
    do_resource = json.loads(conteudo)["modalidades"]

    pares_canonicos = {(m["codigo"], m["nome"]) for m in CANONICA}
    pares_tool = {(m["codigo"], m["nome"]) for m in da_tool}
    pares_resource = {(m["codigo"], m["nome"]) for m in do_resource}

    assert pares_tool == pares_canonicos, (
        f"tool diverge da fonte canônica: faltando {pares_canonicos - pares_tool}, "
        f"extra {pares_tool - pares_canonicos}"
    )
    assert pares_resource == pares_canonicos, (
        f"resource diverge da fonte canônica: faltando {pares_canonicos - pares_resource}, "
        f"extra {pares_resource - pares_canonicos}"
    )
