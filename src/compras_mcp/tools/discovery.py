"""Tools-espelho para descoberta de prompts e resources via canal de tools.

Por que isto existe
-------------------
O protocolo MCP define três primitivos — `tools`, `prompts`, `resources` —
com semânticas diferentes:

- `tools` são "model-controlled": o LLM decide quando chamar.
- `prompts` e `resources` são "user-controlled": o usuário seleciona via
  UI do cliente (slash-command, attachment picker etc.).

Nem todo cliente MCP expõe UI para os user-controlled. O **Claude.ai web**,
por exemplo, hoje só passa `tools` para o modelo — prompts e resources
ficam mortos lá. Este módulo expõe os mesmos dados via tools normais para
que o LLM possa enumerar e ler sob demanda em qualquer cliente.

Em clientes que **suportam** o protocolo completo (Claude Desktop, Cursor,
MCP Inspector), o usuário continua tendo UI dedicada para invocar prompts
e anexar resources. As tools-espelho não atrapalham — apenas oferecem um
caminho alternativo via LLM.

Como funciona
-------------
- `compras_listar_prompts()` enumera todos os prompts registrados com
  nome, descrição e argumentos.
- `compras_obter_prompt(nome, argumentos)` renderiza um prompt e devolve
  o texto pronto para o LLM seguir.
- `compras_listar_resources()` enumera todas as URIs disponíveis.
- `compras_obter_resource(uri)` lê o conteúdo de um resource.

A fonte de verdade continua sendo `prompts.py` e `resources.py` — estas
tools só refletem o que está registrado no servidor.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from pydantic import Field

from compras_mcp.mcp_instance import mcp
from compras_mcp.tools._helpers import with_latency


_SCHEMA_LEAKAGE_MARKER = "\n\nProvide as a JSON string matching the following schema:"


def _limpar_descricao_argumento(desc: str | None) -> str | None:
    """Remove o sufixo verbose `"Provide as a JSON string matching..."` que o
    FastMCP/MCP SDK anexa em modo verbose. Para o usuário do cliente, esse
    trecho é ruído — a descrição útil é só o primeiro parágrafo.
    """
    if not desc:
        return desc
    idx = desc.find(_SCHEMA_LEAKAGE_MARKER)
    if idx >= 0:
        return desc[:idx].rstrip()
    return desc


def _argumento_to_dict(arg: Any) -> dict[str, Any]:
    return {
        "nome": getattr(arg, "name", None),
        "descricao": _limpar_descricao_argumento(getattr(arg, "description", None)),
        "obrigatorio": bool(getattr(arg, "required", False)),
    }


@mcp.tool
async def compras_listar_prompts() -> dict[str, Any]:
    """Lista os MCP Prompts disponíveis com nome, descrição e argumentos.

    Tools de descoberta para clientes (como o Claude.ai web) que ainda
    não expõem UI para prompts. Em Claude Desktop / Cursor / MCP Inspector,
    prompts aparecem em UI dedicada — esta tool é um caminho alternativo,
    não substituto.

    Use depois `compras_obter_prompt(nome, argumentos)` para renderizar
    um prompt específico.

    Retorno:
        {
          "total": int,
          "prompts": [
            {
              "nome": str,
              "descricao": str,
              "tags": [str, ...],
              "argumentos": [
                {"nome": str, "descricao": str | None, "obrigatorio": bool},
                ...
              ]
            },
            ...
          ]
        }
    """
    started = time.perf_counter()
    prompts = await mcp.get_prompts()
    out: list[dict[str, Any]] = []
    for nome in sorted(prompts):
        p = prompts[nome]
        tags = list(getattr(p, "tags", None) or [])
        out.append(
            {
                "nome": nome,
                "descricao": (getattr(p, "description", "") or "").strip(),
                "tags": sorted(tags),
                "argumentos": [
                    _argumento_to_dict(a) for a in (getattr(p, "arguments", None) or [])
                ],
            }
        )
    return with_latency({"total": len(out), "prompts": out}, started)


@mcp.tool
async def compras_obter_prompt(
    nome: Annotated[
        str,
        Field(
            description=(
                "Nome do prompt a renderizar. Use `compras_listar_prompts` "
                "para descobrir nomes disponíveis. Exemplos: "
                "`analisar_contratacao_pncp`, `dossie_due_diligence_fornecedor`, "
                "`oportunidades_carona_arp`."
            ),
            min_length=1,
        ),
    ],
    argumentos: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Mapa de argumentos exigidos pelo prompt. Os nomes e tipos "
                "vêm de `compras_listar_prompts`. Ex.: "
                '{"cnpj_orgao": "00394460000141", "ano": 2025, "sequencial": 12345}.'
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Renderiza um MCP Prompt e devolve o texto pronto.

    O texto retornado é o conteúdo da `PromptMessage[0]` — tipicamente um
    roteiro que orienta o LLM a executar um fluxo usando as tools deste
    servidor. Depois de obter o texto, o LLM normalmente segue as
    instruções dele, chamando outras tools conforme indicado.

    Retorno:
        {
          "nome": str,
          "texto": str,  # conteúdo renderizado pronto para usar
          "argumentos_usados": dict,
        }

    Se o prompt não existir ou faltar argumento obrigatório, retorna
    `_erro` com diagnóstico em vez de propagar exception.
    """
    started = time.perf_counter()
    prompts = await mcp.get_prompts()
    if nome not in prompts:
        return with_latency(
            {
                "nome": nome,
                "_erro": (
                    f"Prompt {nome!r} não registrado. "
                    f"Disponíveis: {sorted(prompts)}."
                ),
            },
            started,
        )
    p = prompts[nome]
    args = argumentos or {}
    try:
        msgs = await p.render(args)
    except Exception as e:
        return with_latency(
            {
                "nome": nome,
                "argumentos_usados": args,
                "_erro": f"{type(e).__name__}: {e}",
            },
            started,
        )

    # Concatena texto de todas as mensagens preservando role (raramente >1).
    partes: list[str] = []
    for m in msgs:
        content = getattr(m, "content", None)
        if content is None:
            continue
        if isinstance(content, list):
            for c in content:
                t = getattr(c, "text", None)
                if t:
                    partes.append(t)
        else:
            t = getattr(content, "text", None)
            if t:
                partes.append(t)
    texto = "\n\n".join(partes)

    return with_latency(
        {"nome": nome, "texto": texto, "argumentos_usados": args},
        started,
    )


@mcp.tool
async def compras_listar_resources() -> dict[str, Any]:
    """Lista os MCP Resources disponíveis com URI, nome e mime-type.

    Tools de descoberta para clientes que não expõem UI de attachment de
    resources (como o Claude.ai web). Em Claude Desktop / Cursor / MCP
    Inspector, resources aparecem em picker dedicado.

    Resources contêm dados de referência estáticos (tabelas de domínio,
    glossário, metadados do servidor). Use `compras_obter_resource(uri)`
    para ler o conteúdo.

    Retorno:
        {
          "total": int,
          "resources": [
            {"uri": str, "nome": str, "descricao": str, "mime_type": str, "tags": [str,...]},
            ...
          ]
        }
    """
    started = time.perf_counter()
    resources = await mcp.get_resources()
    out: list[dict[str, Any]] = []
    for uri in sorted(resources):
        r = resources[uri]
        tags = list(getattr(r, "tags", None) or [])
        out.append(
            {
                "uri": uri,
                "nome": getattr(r, "name", None),
                "descricao": (getattr(r, "description", "") or "").strip(),
                "mime_type": getattr(r, "mime_type", None),
                "tags": sorted(tags),
            }
        )
    return with_latency({"total": len(out), "resources": out}, started)


@mcp.tool
async def compras_obter_resource(
    uri: Annotated[
        str,
        Field(
            description=(
                "URI do resource. Use `compras_listar_resources` para "
                "descobrir URIs disponíveis. Exemplos: "
                "`compras://referencia/modalidades-pncp`, "
                "`compras://glossario/lei-14133`, `compras://meta/escopo`."
            ),
            min_length=5,
        ),
    ],
) -> dict[str, Any]:
    """Lê o conteúdo de um MCP Resource pela URI.

    Retorna o conteúdo bruto (texto/JSON-string conforme o mime-type
    registrado) e os metadados do resource.

    Retorno:
        {
          "uri": str,
          "nome": str,
          "mime_type": str,
          "conteudo": str,
        }

    Se a URI não existir, retorna `_erro` em vez de propagar exception.
    """
    started = time.perf_counter()
    resources = await mcp.get_resources()
    if uri not in resources:
        return with_latency(
            {
                "uri": uri,
                "_erro": (
                    f"Resource {uri!r} não registrado. "
                    f"Disponíveis: {sorted(resources)}."
                ),
            },
            started,
        )
    r = resources[uri]
    try:
        conteudo = await r.read()
    except Exception as e:
        return with_latency(
            {"uri": uri, "_erro": f"{type(e).__name__}: {e}"},
            started,
        )

    # `read()` pode retornar str direto ou bytes; normalizamos para str.
    if isinstance(conteudo, bytes):
        conteudo = conteudo.decode("utf-8", errors="replace")
    elif not isinstance(conteudo, str):
        conteudo = str(conteudo)

    return with_latency(
        {
            "uri": uri,
            "nome": getattr(r, "name", None),
            "mime_type": getattr(r, "mime_type", None),
            "conteudo": conteudo,
        },
        started,
    )
