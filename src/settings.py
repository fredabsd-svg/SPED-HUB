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
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from src.version import APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE: dict[tuple, Settings] = {}


def _coerce_bool(value: str | bool | None, default: bool = False) -> bool:
    """Converte string de env para booleano de forma tolerante."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


_SQLITE_INLINE_URLS = {
    ":memory:": "sqlite:///:memory:",
    "": "sqlite:///:memory:",
}


def caminho_para_url_sqlite(caminho: str) -> str:
    """Converte um caminho de arquivo SQLite (ou URL pronta) em URL SQLAlchemy.

    Regras:
        * URL já formada (contém ``://``) passa intacta.
        * ``:memory:`` e vazio → ``sqlite:///:memory:``.
        * Caminho absoluto → ``sqlite:///{caminho}`` — a barra do próprio
          caminho compõe as quatro barras que o SQLAlchemy espera.
        * Caminho relativo → ``sqlite:///./{caminho}``.

    O tratamento do caminho absoluto é o ponto delicado: um ``lstrip("./")``
    ingênuo remove a barra inicial e transforma ``/tmp/x.db`` no *relativo*
    ``sqlite:///./tmp/x.db``, apontando para outro banco.
    """
    if "://" in caminho:
        return caminho
    if caminho in _SQLITE_INLINE_URLS:
        return _SQLITE_INLINE_URLS[caminho]
    if caminho.startswith("/"):
        return f"sqlite:///{caminho}"
    return f"sqlite:///./{caminho}"


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
    # RESERVADO: nenhum componente consome esta chave hoje.  Sessões e tokens
    # usam ``secrets.token_hex`` (CSPRNG, dispensa chave) e a assinatura de
    # webhook usa o segredo por registro.  Mantida para quando surgir uma
    # necessidade real de assinatura global — não confie nela como proteção.
    secret_key: str = "change-me-in-production"

    # Banco de dados
    database_url: str = "sqlite:///./sped_hub.db"
    database_echo: bool = False

    # Aplicação
    app_name: str = "SPED-HUB"
    app_version: str = APP_VERSION
    default_db_path: str = "sped_hub.db"
    log_level: str = "INFO"
    # Uma linha JSON por evento, para coletor de logs.  PII é
    # mascarada nos dois formatos (ver src/logging_config.py).
    log_json: bool = False
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: ("*",))
    # Auto-serviço no `/register`.  Fechado por padrão: com ele aberto,
    # qualquer um que alcance o servidor cria conta e cai no mesmo grupo do
    # contador — e enxerga a escrituração dos clientes.  O primeiro usuário
    # é sempre permitido, senão não há como criar o administrador inicial.
    registro_aberto: bool = False

    # Servidor (uvicorn)
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False

    # Uploads e processamento
    max_upload_mb: int = 200
    # Alias legado em bytes (``SPED_HUB_MAX_UPLOAD_BYTES``).  Quando definido
    # vence ``max_upload_mb`` — ver a propriedade ``max_upload_bytes``.
    max_upload_bytes_override: int | None = None
    upload_dir: str = str(PROJECT_ROOT / "uploads")
    ecd_import_chunk_rows: int = 5_000
    ecd_import_chunk_bytes: int = 8 * 1024 * 1024  # 8MB de arquivo lido por vez

    # Workers
    worker_count: int = 4

    # Observabilidade
    monitoring_retention_hours: int = 24
    #: Retenção do histórico de jobs de importação, em horas.  0 desliga.
    job_retention_hours: int = 24
    #: Intervalo entre execuções do expurgo periódico, em minutos.  0 desliga
    #: o expurgo por completo — e aí o histórico volta a crescer sem limite.
    maintenance_interval_minutes: int = 60
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
    #: Retenção do histórico de entregas de webhook, em dias.  Há uma linha por
    #: TENTATIVA, não por evento, então a tabela cresce rápido em integração
    #: instável.  0 desliga o expurgo.
    webhook_delivery_retention_days: int = 30
    #: Reenviar sozinho as entregas que o processo abandonou no meio.  Só as
    #: abandonadas: `failed` esgotou as tentativas e martelar sozinho um
    #: endpoint quebrado não resolve — ali falta alguém olhar.
    webhook_auto_retry: bool = True
    # Permite destino http:// (só para desenvolvimento — em produção o
    # webhook exige https e endereço público).
    webhook_allow_http: bool = False

    # Rate limiting
    rate_limit_default: int = 100
    rate_limit_window_seconds: int = 60
    # Por IP — protege o que não tem API Key (login, registro).
    rate_limit_ip_default: int = 300
    rate_limit_ip_window_seconds: int = 60
    rate_limit_login_default: int = 10
    rate_limit_login_window_seconds: int = 60
    # Só ligue com um proxy reverso confiável à frente: sem ele, o cliente
    # escreve o próprio X-Forwarded-For e escapa do limite por IP.
    trust_proxy: bool = False

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
        url = self.database_url[len(prefix) :]
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

    @property
    def max_upload_bytes(self) -> int:
        """Limite de upload em bytes.

        ``SPED_HUB_MAX_UPLOAD_BYTES`` (legado, em bytes) tem precedência sobre
        ``SPED_HUB_MAX_UPLOAD_MB``; valores não positivos caem no default.
        """
        if self.max_upload_bytes_override and self.max_upload_bytes_override > 0:
            return self.max_upload_bytes_override
        return self.max_upload_mb * 1024 * 1024

    @property
    def redis_url_or_local(self) -> str:
        """URL do Redis, caindo no localhost padrão quando não configurada."""
        return self.redis_url or "redis://localhost:6379/0"


# Mapeamento das variáveis de ambiente suportadas.  Strings vazias são
# ignoradas (mantém o default) para evitar ``DEBUG=''`` forçar ``False``.
# Observação: ``SPED_HUB_DB`` NÃO aparece aqui — ele é tratado em
# :func:`_read_env` com regra de precedência em relação a ``DATABASE_URL``.
_ENV_TO_FIELD: Mapping[str, str] = {
    "SPED_HUB_ENV": "env",
    "SPED_HUB_SECRET_KEY": "secret_key",
    "DATABASE_URL": "database_url",
    "SPED_HUB_DB_ECHO": "database_echo",
    "SPED_HUB_LOG_LEVEL": "log_level",
    "SPED_HUB_LOG_JSON": "log_json",
    "SPED_HUB_HOST": "host",
    "SPED_HUB_PORT": "port",
    "SPED_HUB_RELOAD": "reload",
    "SPED_HUB_ALLOWED_HOSTS": "allowed_hosts",
    "SPED_HUB_REGISTRO_ABERTO": "registro_aberto",
    "SPED_HUB_MAX_UPLOAD_MB": "max_upload_mb",
    "SPED_HUB_MAX_UPLOAD_BYTES": "max_upload_bytes_override",
    "SPED_HUB_UPLOAD_DIR": "upload_dir",
    "SPED_HUB_ECD_CHUNK_ROWS": "ecd_import_chunk_rows",
    "SPED_HUB_ECD_CHUNK_BYTES": "ecd_import_chunk_bytes",
    "WORKER_COUNT": "worker_count",
    "SPED_HUB_MONITORING_RETENTION_HOURS": "monitoring_retention_hours",
    "SPED_HUB_JOB_RETENTION_HOURS": "job_retention_hours",
    "SPED_HUB_MAINTENANCE_INTERVAL_MINUTES": "maintenance_interval_minutes",
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
    "SPED_HUB_WEBHOOK_RETENTION_DAYS": "webhook_delivery_retention_days",
    "SPED_HUB_WEBHOOK_AUTO_RETRY": "webhook_auto_retry",
    "SPED_HUB_WEBHOOK_ALLOW_HTTP": "webhook_allow_http",
    "SPED_HUB_RATE_LIMIT_DEFAULT": "rate_limit_default",
    "SPED_HUB_RATE_LIMIT_WINDOW": "rate_limit_window_seconds",
    "SPED_HUB_RATE_LIMIT_IP": "rate_limit_ip_default",
    "SPED_HUB_RATE_LIMIT_IP_WINDOW": "rate_limit_ip_window_seconds",
    "SPED_HUB_RATE_LIMIT_LOGIN": "rate_limit_login_default",
    "SPED_HUB_RATE_LIMIT_LOGIN_WINDOW": "rate_limit_login_window_seconds",
    "SPED_HUB_TRUST_PROXY": "trust_proxy",
}

# Nomes antigos ainda aceitos, para não quebrar deploys existentes (o
# docker-compose.yml, por exemplo, sempre passou SMTP_PASS/SMTP_FROM).
# O nome documentado tem precedência quando ambos estiverem definidos.
_LEGACY_ALIASES: Mapping[str, str] = {
    "SMTP_PASS": "SMTP_PASSWORD",
    "SMTP_FROM": "EMAIL_FROM",
}

_INT_FIELDS = {
    "max_upload_mb",
    "max_upload_bytes_override",
    "ecd_import_chunk_rows",
    "ecd_import_chunk_bytes",
    "monitoring_retention_hours",
    "maintenance_interval_minutes",
    "job_retention_hours",
    "webhook_delivery_retention_days",
    "metrics_window_minutes",
    "smtp_port",
    "webhook_default_max_retries",
    "webhook_timeout_seconds",
    "rate_limit_default",
    "rate_limit_window_seconds",
    "rate_limit_ip_default",
    "rate_limit_ip_window_seconds",
    "rate_limit_login_default",
    "rate_limit_login_window_seconds",
    "worker_count",
    "port",
}

# Campos booleanos precisam de coerção explícita: sem isto, ``EMAIL_ENABLED=false``
# vira a *string* ``"false"``, que é verdadeira em Python — desligar a opção a
# ligava.
_BOOL_FIELDS = {
    "database_echo",
    "smtp_use_tls",
    "email_enabled",
    "webhook_allow_http",
    "webhook_auto_retry",
    "reload",
    "trust_proxy",
    "log_json",
    "registro_aberto",
}


def _read_env(environ: Mapping[str, str] | None = None) -> dict:
    """Lê o ambiente (ou um mapping fornecido em teste) e produz kwargs."""
    env = environ if environ is not None else os.environ
    overrides: dict = {}

    for env_key, field_name in _ENV_TO_FIELD.items():
        raw = env.get(env_key)
        if raw is None or raw == "":
            # Nome documentado ausente: aceita o alias legado, se houver.
            for legado, oficial in _LEGACY_ALIASES.items():
                if oficial == env_key:
                    raw = env.get(legado)
                    break
        if raw is None or raw == "":
            continue
        if field_name in _BOOL_FIELDS:
            default = Settings.__dataclass_fields__[field_name].default
            overrides[field_name] = _coerce_bool(raw, default)
        elif field_name in _INT_FIELDS:
            default = Settings.__dataclass_fields__[field_name].default
            overrides[field_name] = _coerce_int(raw, default)
        elif field_name == "allowed_hosts":
            overrides[field_name] = tuple(h.strip() for h in raw.split(",") if h.strip())
        else:
            overrides[field_name] = raw

    # Regra de precedência do banco:
    #   1) DATABASE_URL sempre vence (já lido acima).
    #   2) SPED_HUB_DB (legado) só é usado para preencher DATABASE_URL
    #      quando ``DATABASE_URL`` não estiver definido.  Aceita caminho
    #      absoluto, relativo ou ":memory:".
    db_url = env.get("DATABASE_URL")
    db_legacy = env.get("SPED_HUB_DB")

    if (db_url is None or db_url == "") and db_legacy:
        overrides["database_url"] = caminho_para_url_sqlite(db_legacy)
    return overrides


def _cache_key(environ: Mapping[str, str] | None) -> tuple:
    """Gera chave estável baseada nas variáveis lidas em :func:`_read_env`."""
    if environ is None:
        env = os.environ
    else:
        env = environ
    keys = sorted(set(_ENV_TO_FIELD.keys()) | set(_LEGACY_ALIASES.keys()) | {"SPED_HUB_DB"})
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


def database_reference() -> str:
    """Referência de banco para componentes que recebem ``db_path: str``.

    Devolve sempre a URL SQLAlchemy corrente.  ``criar_engine`` aceita tanto
    URL quanto caminho de arquivo, então isto é compatível com todos os
    chamadores existentes — e, ao contrário de ler ``SPED_HUB_DB`` direto do
    ambiente, respeita ``DATABASE_URL``.

    É lido a cada chamada (não em tempo de import) porque as fixtures de teste
    trocam o banco em runtime.
    """
    return get_settings().database_url


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
