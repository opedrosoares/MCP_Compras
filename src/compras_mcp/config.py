"""Configurações carregadas do ambiente (.env ou variáveis do sistema)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _find_dotenv() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return Path(".env")


@dataclass(frozen=True, slots=True)
class Settings:
    # APIs upstream
    dados_abertos_base_url: str
    pncp_base_url: str
    pncp_api_base_url: str
    transparencia_base_url: str
    transparencia_api_key: str
    comprasnet_contratos_base_url: str
    comprasnet_bearer_token: str

    # HTTP
    http_timeout: float
    http_max_retries: int

    # Cache
    redis_url: str  # vazio => usa cache em memória

    # LGPD
    incluir_cpf_completo: bool

    # Enriquecimento CNPJ (Receita Federal via agregadores públicos)
    cnpj_provider: str  # 'brasilapi' (default) ou 'minhareceita'
    brasilapi_base_url: str
    minhareceita_base_url: str

    # Logging
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(_find_dotenv(), override=False)

        return cls(
            dados_abertos_base_url=os.environ.get(
                "DADOS_ABERTOS_BASE_URL",
                "https://dadosabertos.compras.gov.br",
            ).rstrip("/"),
            pncp_base_url=os.environ.get(
                "PNCP_BASE_URL",
                "https://pncp.gov.br/api/consulta",
            ).rstrip("/"),
            pncp_api_base_url=os.environ.get(
                "PNCP_API_BASE_URL",
                "https://pncp.gov.br/api/pncp",
            ).rstrip("/"),
            transparencia_base_url=os.environ.get(
                "TRANSPARENCIA_BASE_URL",
                "https://api.portaldatransparencia.gov.br",
            ).rstrip("/"),
            transparencia_api_key=os.environ.get("TRANSPARENCIA_API_KEY", "").strip(),
            comprasnet_contratos_base_url=os.environ.get(
                "COMPRASNET_CONTRATOS_BASE_URL",
                "https://contratos.comprasnet.gov.br/api",
            ).rstrip("/"),
            comprasnet_bearer_token=os.environ.get("COMPRASNET_BEARER_TOKEN", "").strip(),
            http_timeout=float(os.environ.get("HTTP_TIMEOUT", "60")),
            http_max_retries=int(os.environ.get("HTTP_MAX_RETRIES", "2")),
            redis_url=os.environ.get("REDIS_URL", "").strip(),
            incluir_cpf_completo=os.environ.get("INCLUIR_CPF_COMPLETO", "false").lower()
            in ("1", "true", "yes"),
            cnpj_provider=os.environ.get("CNPJ_PROVIDER", "brasilapi").strip().lower(),
            brasilapi_base_url=os.environ.get(
                "BRASILAPI_BASE_URL",
                "https://brasilapi.com.br",
            ).rstrip("/"),
            minhareceita_base_url=os.environ.get(
                "MINHARECEITA_BASE_URL",
                "https://minhareceita.org",
            ).rstrip("/"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )


_singleton: Settings | None = None


def get_settings() -> Settings:
    """Singleton lazy de Settings — usado pelo server e pelas tools.

    Vive em config.py (e não em server.py) para que as tools possam
    importá-lo sem criar ciclo com server.py.
    """
    global _singleton
    if _singleton is None:
        _singleton = Settings.from_env()
    return _singleton
