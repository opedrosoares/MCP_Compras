"""Mascaramento LGPD e avisos de privacidade.

Por padrão, mascara CPFs em respostas que possam conter dados de servidores
(fiscais de contrato, signatários, responsáveis). CNPJs ficam visíveis
porque são públicos.

Pode-se desabilitar a máscara via env `INCLUIR_CPF_COMPLETO=true`. Tools
que retornam dados pessoais incluem o campo `_aviso_lgpd` informativo.
"""

from __future__ import annotations

import re
from typing import Any

_CPF_RE = re.compile(r"\b(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})\b")

_AVISO_PADRAO = (
    "Resposta pode conter dados pessoais (CPFs de servidores/fiscais). "
    "CPFs estão mascarados por padrão; configure INCLUIR_CPF_COMPLETO=true "
    "para recebê-los integrais quando estritamente necessário."
)


def mask_cpf(cpf: str | None) -> str | None:
    """Mascara um CPF para o formato `123.***.***-45`.

    Aceita CPF com ou sem pontuação. Devolve None se entrada for None ou vazia.
    Devolve a string original se não bater no formato CPF.
    """
    if not cpf:
        return cpf
    m = _CPF_RE.search(cpf)
    if not m:
        return cpf
    return f"{m.group(1)}.***.***-{m.group(4)}"


def mask_cpfs_in_text(text: str) -> str:
    """Mascara todas as ocorrências de CPF em uma string longa."""
    return _CPF_RE.sub(r"\1.***.***-\4", text)


def apply_lgpd(payload: Any, *, incluir_cpf_completo: bool) -> Any:
    """Caminha recursivamente em `payload` mascarando campos sensíveis.

    Reconhece chaves comuns: `cpf`, `cpfFiscal`, `cpfResponsavel`,
    `cpfSignatario`, `cpfServidor`, `documento` (quando 11 dígitos).
    """
    if incluir_cpf_completo:
        return payload
    return _walk(payload)


_CPF_KEY_HINTS = ("cpf", "documento_pessoa", "doc_pessoa_fisica")


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _maybe_mask(k, v) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(item) for item in node]
    if isinstance(node, str):
        return mask_cpfs_in_text(node)
    return node


def _maybe_mask(key: str, value: Any) -> Any:
    key_lower = key.lower()
    if any(hint in key_lower for hint in _CPF_KEY_HINTS) and isinstance(value, str):
        masked = mask_cpf(value)
        return masked if masked is not None else value
    return _walk(value)


def aviso_lgpd(custom: str | None = None) -> str:
    """Texto padrão a colocar no campo `_aviso_lgpd` da resposta."""
    return custom or _AVISO_PADRAO
