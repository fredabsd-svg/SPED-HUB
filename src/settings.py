"""Configuração centralizada por variáveis de ambiente (Fase 17).

A aplicação inteira (CLI, dashboard, API, workers, watchdog) lê configuração
deste módulo em vez de espalhar ``os.environ.get`` por todo o código.

Uso básico::

    from src.settings import get_settings

    settings = get_settings()
    engine = criar_engine(url=settings.database_url)
    ...

Para sobrescrever em testes, basta ajustar o ambiente ou a função
:func:`reset_settings_cache` (usada por fixtures).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE: dict[tuple, "Settings"] = {}


def _coerce_bool(value: str | bool | None, default: bool = False) -> bool:
    """Converte string de env para booleano de forma tolerante."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _coerce_int(value: str | int | None, default: int) -> int:
    if isinstance(value, int):
        return value
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Snapshot imutável de toda a configuração da aplicação.

    Instâncias são obtidas por :func:`get_settings` (cacheadas) e podem ser
    livremente substituídas em testes usando :func:`reset_settings_cache` ou
    sobrescrevendo o ambiente antes da primeira leitura.
    """

    # Ambiente
    env: str = "dev"  # dev | test | prod
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # Banco de dados
    database_url: str = "sqlite:///./sped_hub.db"
    database_echo: bool = False

    # Aplicação
    app_name: str = "SPED-HUB"
    app_version: str = "0.15.0"
    default_db_path: str = "sped_hub.db"
    log_level: str = "INFO"
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: ("*",))

    # Uploads e processamento
    max_upload_mb: int = 200
    ecd_import_chunk_rows: int = 5_000
    ecd_import_chunk_bytes: int = 8 * 1024 * 1024  # 8MB de arquivo lido por vez

    # Observabilidade
    monitoring_retention_hours: int = 24
    metrics_window_minutes: int = 60
    enable_health_check_db_probe: bool = True

    # Email (Fase 8 / Fase 16)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str = "noreply@sped-hub.local"
    email_enabled: bool = False

    # Cache (Redis é opcional)
    redis_url: str | None = None

    # Webhooks
    webhook_default_max_retries: int = 3
    webhook_timeout_seconds: int = 10

    # Rate limiting
    rate_limit_default: int = 100
    rate_limit_window_seconds: int = 60

    # Documentação
    extra_docs_path: Path | None = None

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres", "postgresql"))

    @property
    def database_file_path(self) -> str | None:
        """Caminho de arquivo SQLite (``None`` para bancos não-SQLite)."""
        if not self.is_sqlite:
            return None
        prefix = "sqlite:///"
        url = self.database_url[len(prefix):]
        if url == ":memory:":
            return ":memory:"
        # Caminhos relativos passam a ser resolvidos a partir da raiz do projeto.
        path = Path(url)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return str(path)

    @property
    def cors_origins(self) -> tuple[str, ...]:
        return self.allowed_hosts


# Mapeamento das variáveis de ambiente suportadas.  Strings vazias são
# ignoradas (mantém o default) para evitar ``DEBUG=''`` forçar ``False``.
# Observação: ``SPED_HUB_DB`` NÃO aparece aqui — ele é tratado em
# :func:`_read_env` com regra de precedência em relação a ``DATABASE_URL``.
_ENV_TO_FIELD: Mapping[str, str] = {
    "SPED_HUB_ENV": "env",
    "SPED_HUB_DEBUG": "debug",
    "SPED_HUB_SECRET_KEY": "secret_key",
    "DATABASE_URL": "database_url",
    "SPED_HUB_DB_ECHO": "database_echo",
    "SPED_HUB_LOG_LEVEL": "log_level",
    "SPED_HUB_ALLOWED_HOSTS": "allowed_hosts",
    "SPED_HUB_MAX_UPLOAD_MB": "max_upload_mb",
    "SPED_HUB_ECD_CHUNK_ROWS": "ecd_import_chunk_rows",
    "SPED_HUB_ECD_CHUNK_BYTES": "ecd_import_chunk_bytes",
    "SPED_HUB_MONITORING_RETENTION_HOURS": "monitoring_retention_hours",
    "SPED_HUB_METRICS_WINDOW_MINUTES": "metrics_window_minutes",
    "SMTP_HOST": "smtp_host",
    "SMTP_PORT": "smtp_port",
    "SMTP_USER": "smtp_user",
    "SMTP_PASSWORD": "smtp_password",
    "SMTP_USE_TLS": "smtp_use_tls",
    "EMAIL_FROM": "email_from",
    "EMAIL_ENABLED": "email_enabled",
    "REDIS_URL": "redis_url",
    "SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES": "webhook_default_max_retries",
    "SPED_HUB_WEBHOOK_TIMEOUT": "webhook_timeout_seconds",
    "SPED_HUB_RATE_LIMIT_DEFAULT": "rate_limit_default",
    "SPED_HUB_RATE_LIMIT_WINDOW": "rate_limit_window_seconds",
}

_INT_FIELDS = {
    "max_upload_mb",
    "ecd_import_chunk_rows",
    "ecd_import_chunk_bytes",
    "monitoring_retention_hours",
    "metrics_window_minutes",
    "smtp_port",
    "webhook_default_max_retries",
    "webhook_timeout_seconds",
    "rate_limit_default",
    "rate_limit_window_seconds",
}


def _read_env(environ: Mapping[str, str] | None = None) -> dict:
    """Lê o ambiente (ou um mapping fornecido em teste) e produz kwargs."""
    env = environ if environ is not None else os.environ
    overrides: dict = {}

    for env_key, field_name in _ENV_TO_FIELD.items():
        raw = env.get(env_key)
        if raw is None or raw == "":
            continue
        if field_name == "debug":
            overrides[field_name] = _coerce_bool(raw)
        elif field_name in _INT_FIELDS:
            default = Settings.__dataclass_fields__[field_name].default
            overrides[field_name] = _coerce_int(raw, default)
        elif field_name == "allowed_hosts":
            overrides[field_name] = tuple(
                h.strip() for h in raw.split(",") if h.strip()
            )
        else:
            overrides[field_name] = raw

    # Regra de precedência do banco:
    #   1) DATABASE_URL sempre vence (já lido acima).
    #   2) SPED_HUB_DB (legado) só é usado para preencher DATABASE_URL
    #      quando ``DATABASE_URL`` não estiver definido.  Aceita caminho
    #      relativo puro ou ":memory:".
    db_url = env.get("DATABASE_URL")
    db_legacy = env.get("SPED_HUB_DB")

    if db_url is None or db_url == "":
        if db_legacy:
            if db_legacy == ":memory:":
                overrides["database_url"] = "sqlite:///:memory:"
            elif "://" in db_legacy:
                overrides["database_url"] = db_legacy
            else:
                overrides["database_url"] = f"sqlite:///./{db_legacy.lstrip('./')}"
    return overrides


def _cache_key(environ: Mapping[str, str] | None) -> tuple:
    """Gera chave estável baseada nas variáveis lidas em :func:`_read_env`."""
    if environ is None:
        env = os.environ
    else:
        env = environ
    keys = sorted(set(_ENV_TO_FIELD.keys()) | {"SPED_HUB_DB"})
    return tuple((k, env.get(k, "")) for k in keys)


def get_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Retorna a configuração corrente, com cache por snapshot do ambiente."""
    key = _cache_key(environ)
    if environ is None and key in _CACHE:
        return _CACHE[key]
    overrides = _read_env(environ)
    settings = Settings(**overrides) if overrides else Settings()
    if environ is None:
        _CACHE[key] = settings
    return settings


def reset_settings_cache() -> None:
    """Limpa o cache in-process de :func:`get_settings` (usado em testes)."""
    _CACHE.clear()


def with_overrides(**kwargs) -> Settings:
    """Cria um clone de ``Settings`` aplicando overrides pontuais.

    Útil para componentes que precisam configurar seu próprio subset::

        cfg = with_overrides(database_url="postgresql://...")
    """

    base = get_settings()
    return replace(base, **kwargs)
