"""Cliente CNPJ — enriquecimento de dados via Receita Federal Open Data.

Suporta dois agregadores públicos sem autenticação:

- **BrasilAPI** (default): `https://brasilapi.com.br/api/cnpj/v1/{cnpj}`
  Forma estável, cache curto.
- **MinhaReceita**: `https://minhareceita.org/{cnpj}`
  Dados mais granulares (QSA detalhado), pode ser mais lento.

Escolha via `CNPJ_PROVIDER=brasilapi|minhareceita`.

Os dois retornam estruturas levemente diferentes; esta classe normaliza
para um shape único antes de devolver.
"""

from __future__ import annotations

from typing import Any, Literal

from compras_mcp.clients.base import BaseAsyncClient
from compras_mcp.errors import ComprasHTTPError, ComprasNotFoundError

CnpjProvider = Literal["brasilapi", "minhareceita"]


def _so_digitos(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _normalizar_brasilapi(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza payload BrasilAPI para o shape comum."""
    qsa = raw.get("qsa") or []
    return {
        "cnpj": raw.get("cnpj") or "",
        "razao_social": raw.get("razao_social") or raw.get("nome_fantasia") or "",
        "nome_fantasia": raw.get("nome_fantasia") or "",
        "situacao_cadastral": raw.get("descricao_situacao_cadastral")
        or raw.get("situacao_cadastral"),
        "data_situacao_cadastral": raw.get("data_situacao_cadastral"),
        "data_inicio_atividade": raw.get("data_inicio_atividade"),
        "natureza_juridica": raw.get("natureza_juridica"),
        "porte": raw.get("porte")
        or raw.get("descricao_porte")
        or raw.get("codigo_porte"),
        "capital_social": raw.get("capital_social"),
        "cnae_principal": {
            "codigo": raw.get("cnae_fiscal") or raw.get("cnae_fiscal_principal"),
            "descricao": raw.get("cnae_fiscal_descricao")
            or raw.get("descricao_cnae_fiscal_principal"),
        },
        "cnae_secundarios": [
            {
                "codigo": c.get("codigo") or c.get("cnae"),
                "descricao": c.get("descricao"),
            }
            for c in (raw.get("cnaes_secundarios") or [])
        ],
        "endereco": {
            "logradouro": raw.get("logradouro"),
            "numero": raw.get("numero"),
            "bairro": raw.get("bairro"),
            "municipio": raw.get("municipio"),
            "uf": raw.get("uf"),
            "cep": raw.get("cep"),
        },
        "email": raw.get("email"),
        "ddd_telefone_1": raw.get("ddd_telefone_1"),
        "qsa": [
            {
                "nome": s.get("nome_socio") or s.get("nome"),
                "qualificacao": s.get("qualificacao_socio")
                or s.get("codigo_qualificacao_socio"),
                "data_entrada": s.get("data_entrada_sociedade"),
            }
            for s in qsa
        ],
        "_fonte": "brasilapi",
    }


def _normalizar_minhareceita(raw: dict[str, Any]) -> dict[str, Any]:
    qsa = raw.get("qsa") or []
    return {
        "cnpj": raw.get("cnpj") or "",
        "razao_social": raw.get("razao_social") or "",
        "nome_fantasia": raw.get("nome_fantasia") or "",
        "situacao_cadastral": raw.get("descricao_situacao_cadastral"),
        "data_situacao_cadastral": raw.get("data_situacao_cadastral"),
        "data_inicio_atividade": raw.get("data_inicio_atividade"),
        "natureza_juridica": raw.get("natureza_juridica"),
        "porte": raw.get("porte"),
        "capital_social": raw.get("capital_social"),
        "cnae_principal": {
            "codigo": raw.get("cnae_fiscal"),
            "descricao": raw.get("cnae_fiscal_descricao"),
        },
        "cnae_secundarios": [
            {"codigo": c.get("codigo"), "descricao": c.get("descricao")}
            for c in (raw.get("cnaes_secundarios") or [])
        ],
        "endereco": {
            "logradouro": raw.get("logradouro"),
            "numero": raw.get("numero"),
            "bairro": raw.get("bairro"),
            "municipio": raw.get("municipio"),
            "uf": raw.get("uf"),
            "cep": raw.get("cep"),
        },
        "email": raw.get("email"),
        "ddd_telefone_1": raw.get("ddd_telefone_1"),
        "qsa": [
            {
                "nome": s.get("nome_socio"),
                "qualificacao": s.get("qualificacao_socio"),
                "data_entrada": s.get("data_entrada_sociedade"),
            }
            for s in qsa
        ],
        "_fonte": "minhareceita",
    }


class CNPJClient(BaseAsyncClient):
    """Wrapper sobre BrasilAPI ou MinhaReceita para enriquecer dados de CNPJ.

    Não é cacheado aqui — o cache fica na tool que chama (TTL 1h).
    """

    def __init__(
        self,
        *,
        provider: CnpjProvider,
        base_url: str,
        timeout: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_name=f"cnpj-{provider}",
            timeout=timeout,
            max_retries=max_retries,
        )
        self.provider: CnpjProvider = provider

    async def consultar(self, cnpj: str) -> dict[str, Any]:
        digitos = _so_digitos(cnpj)
        if len(digitos) != 14:
            raise ValueError(
                f"CNPJ inválido: esperado 14 dígitos, recebido {len(digitos)} ({cnpj!r})."
            )

        if self.provider == "brasilapi":
            path = f"/api/cnpj/v1/{digitos}"
        elif self.provider == "minhareceita":
            path = f"/{digitos}"
        else:
            raise ValueError(f"provider desconhecido: {self.provider!r}")

        try:
            raw = await self.get_json(path, max_retries=1, timeout=15.0)
        except ComprasNotFoundError:
            return {
                "cnpj": digitos,
                "encontrado": False,
                "_fonte": self.provider,
                "_erro": "CNPJ não localizado na base da Receita Federal.",
            }
        except ComprasHTTPError as e:
            return {
                "cnpj": digitos,
                "encontrado": False,
                "_fonte": self.provider,
                "_erro": f"Falha upstream: {e}",
            }

        if not isinstance(raw, dict):
            return {
                "cnpj": digitos,
                "encontrado": False,
                "_fonte": self.provider,
                "_erro": "Resposta upstream não-objeto.",
            }

        if self.provider == "brasilapi":
            normalizado = _normalizar_brasilapi(raw)
        else:
            normalizado = _normalizar_minhareceita(raw)

        normalizado["encontrado"] = True
        return normalizado


def make_cnpj_client(settings: Any) -> CNPJClient:
    """Factory que respeita `CNPJ_PROVIDER` do ambiente."""
    provider: CnpjProvider = (
        "minhareceita" if settings.cnpj_provider == "minhareceita" else "brasilapi"
    )
    base_url = (
        settings.minhareceita_base_url
        if provider == "minhareceita"
        else settings.brasilapi_base_url
    )
    return CNPJClient(
        provider=provider,
        base_url=base_url,
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
    )
