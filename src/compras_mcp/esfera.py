"""Filtro de esfera federativa para resultados do PNCP.

PNCP retorna em cada registro um campo `orgaoEntidade.esferaId` com os valores:

- `"F"` Federal
- `"E"` Estadual
- `"M"` Municipal
- `"D"` Distrital

O filtro é aplicado **client-side** sobre a página retornada — o endpoint
PNCP `/v1/contratacoes/publicacao` (e similares) não aceita `esfera` como
parâmetro de consulta. Isso significa que filtrar por esfera reduz o
`resultado` mas o `_total_registros` ainda reflete o total upstream.
"""

from __future__ import annotations

from typing import Any, Literal

EsferaValue = Literal["federal", "estadual", "municipal", "distrital"]

ESFERA_VALORES: tuple[str, ...] = ("federal", "estadual", "municipal", "distrital")

_LETRA_PARA_NOME: dict[str, str] = {
    "F": "federal",
    "E": "estadual",
    "M": "municipal",
    "D": "distrital",
}


def matches_esfera(registro: dict[str, Any], esfera: str | None) -> bool:
    """True quando o registro PNCP pertence à esfera pedida (ou se `esfera` é None)."""
    if not esfera:
        return True
    alvo = esfera.lower()
    orgao = registro.get("orgaoEntidade") or registro.get("orgao") or {}
    letra = (orgao.get("esferaId") or "").upper()
    nome = _LETRA_PARA_NOME.get(letra)
    return nome == alvo


def filtrar_por_esfera(
    registros: list[dict[str, Any]], esfera: str | None
) -> list[dict[str, Any]]:
    """Aplica o filtro de esfera a uma lista de registros PNCP. Idempotente."""
    if not esfera:
        return registros
    return [r for r in registros if matches_esfera(r, esfera)]


def aplicar_filtro_esfera_no_envelope(
    payload: dict[str, Any], esfera: str | None
) -> dict[str, Any]:
    """Aplica o filtro `esfera` em-place no envelope padrão das listagens PNCP.

    Substitui `resultado` pelo filtrado e anexa metadado explicando que o
    `_total_registros` original do upstream não foi filtrado.
    """
    if not esfera:
        return payload
    original = payload.get("resultado") or []
    filtrado = filtrar_por_esfera(original, esfera)
    payload["resultado"] = filtrado
    payload["_filtro_esfera"] = {
        "esfera": esfera,
        "total_apos_filtro": len(filtrado),
        "total_antes_filtro": len(original),
        "aviso": (
            "Filtro `esfera` é aplicado client-side sobre a página retornada "
            "pelo PNCP. `_total_registros` reflete o total **sem** o filtro de "
            "esfera. Pode ser necessário paginar mais para obter os resultados "
            "esperados."
        ),
    }
    return payload
