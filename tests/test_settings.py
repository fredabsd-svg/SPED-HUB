"""Testes da Fase 17 — configuração por ambiente e banco configurável."""

from __future__ import annotations

import importlib

import pytest

from src.db.models import (
    _normalizar_database_url,
    criar_engine,
    get_session,
    init_db,
    obter_engine,
)
from src.settings import (
    database_reference,
    get_settings,
    reset_settings_cache,
    with_overrides,
)
from src.version import APP_VERSION


@pytest.fixture(autouse=True)
def _limpar_ambiente_para_settings(monkeypatch):
    """Garante que cada teste começa limpo das variáveis lidas em settings."""
    chaves = [
        "SPED_HUB_ENV",
        "SPED_HUB_DEBUG",
        "SPED_HUB_SECRET_KEY",
        "DATABASE_URL",
        "SPED_HUB_DB",
        "SPED_HUB_DB_ECHO",
        "SPED_HUB_LOG_LEVEL",
        "SPED_HUB_ALLOWED_HOSTS",
        "SPED_HUB_MAX_UPLOAD_MB",
        "SPED_HUB_ECD_CHUNK_ROWS",
        "SPED_HUB_ECD_CHUNK_BYTES",
        "SPED_HUB_MONITORING_RETENTION_HOURS",
        "SPED_HUB_METRICS_WINDOW_MINUTES",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_USE_TLS",
        "EMAIL_FROM",
        "EMAIL_ENABLED",
        "REDIS_URL",
        "SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES",
        "SPED_HUB_WEBHOOK_TIMEOUT",
        "SPED_HUB_RATE_LIMIT_DEFAULT",
        "SPED_HUB_RATE_LIMIT_WINDOW",
    ]
    for chave in chaves:
        monkeypatch.delenv(chave, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


class TestSettingsDefaults:
    def test_defaults_resepeitam_contrato_atual(self):
        cfg = get_settings()
        assert cfg.env == "dev"
        assert cfg.database_url.startswith("sqlite")
        assert cfg.max_upload_mb == 200
        assert cfg.monitoring_retention_hours == 24
        assert cfg.rate_limit_default == 100
        assert cfg.app_version == APP_VERSION

    def test_helpers_reconhecem_sqlite_e_postgres(self):
        cfg = with_overrides(database_url="sqlite:///./foo.db")
        assert cfg.is_sqlite is True
        assert cfg.is_postgres is False
        assert cfg.database_file_path is not None

        cfg2 = with_overrides(database_url="postgresql+psycopg://u:p@h:5432/db")
        assert cfg2.is_postgres is True
        assert cfg2.is_sqlite is False
        assert cfg2.database_file_path is None

    def test_database_file_path_resolve_relativo(self):
        cfg = with_overrides(database_url="sqlite:///./sped_hub.db")
        assert cfg.database_file_path is not None
        assert cfg.database_file_path.endswith("sped_hub.db")
        assert cfg.database_file_path != "sped_hub.db"

    def test_database_file_path_in_memory(self):
        cfg = with_overrides(database_url="sqlite:///:memory:")
        assert cfg.database_file_path == ":memory:"


class TestSettingsEnvOverrides:
    def test_database_url_direto_tem_precedencia(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
        cfg = get_settings()
        assert cfg.database_url == "postgresql+psycopg://u:p@h/db"

    def test_sped_hub_db_como_alias_de_caminho(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", "outro.db")
        cfg = get_settings()
        assert cfg.database_url == "sqlite:///./outro.db"

    def test_sped_hub_db_alias_e_database_url(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", "legacy.db")
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
        cfg = get_settings()
        assert cfg.database_url == "postgresql+psycopg://u:p@h/db"

    def test_sped_hub_db_memory(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", ":memory:")
        cfg = get_settings()
        assert cfg.database_url == "sqlite:///:memory:"

    def test_sped_hub_db_nao_eh_url_valida(self, monkeypatch):
        # Valores que não contenham scheme permanecem como caminho
        monkeypatch.setenv("SPED_HUB_DB", "banco.db")
        cfg = get_settings()
        assert cfg.database_url == "sqlite:///./banco.db"

    def test_database_url_explicita_com_scheme_sobrepoe_caminho(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")
        monkeypatch.setenv("SPED_HUB_DB", "ignorado.db")
        cfg = get_settings()
        assert cfg.database_url == "postgresql+psycopg://u@h/db"

    def test_booleans_e_inteiros(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DEBUG", "true")
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_MB", "300")
        monkeypatch.setenv("SPED_HUB_MONITORING_RETENTION_HOURS", "12")
        cfg = get_settings()
        assert cfg.debug is True
        assert cfg.max_upload_mb == 300
        assert cfg.monitoring_retention_hours == 12

    def test_coerce_int_invalido_cai_no_default(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_MB", "abc")
        cfg = get_settings()
        assert cfg.max_upload_mb == 200

    def test_allowed_hosts_csv(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "a.example, b.example ,c.example")
        cfg = get_settings()
        assert cfg.allowed_hosts == ("a.example", "b.example", "c.example")
        assert cfg.cors_origins == ("a.example", "b.example", "c.example")

    def test_database_url_legado_com_scheme_preservado(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", "sqlite:///:memory:")
        cfg = get_settings()
        assert cfg.database_url == "sqlite:///:memory:"


class TestSettingsWithOverrides:
    def test_with_overrides_substitui_e_preserva_ouros(self):
        cfg = with_overrides(database_url="postgresql+psycopg://x@y/z", max_upload_mb=64)
        assert cfg.database_url == "postgresql+psycopg://x@y/z"
        assert cfg.max_upload_mb == 64
        # Defaults preservados
        assert cfg.monitoring_retention_hours == 24

    def test_instance_e_imutavel(self):
        cfg = get_settings()
        with pytest.raises(Exception):
            cfg.max_upload_mb = 999  # type: ignore[misc]


class TestDatabaseEngineConfig:
    def test_caminho_legado_gera_engine_sqlite_memory(self):
        # Retrocompatibilidade com a Fase 16 e anteriores.
        engine = criar_engine(":memory:")
        try:
            assert engine.url.get_backend_name() == "sqlite"
            init_db(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
        finally:
            engine.dispose()

    def test_caminho_arquivo_relativo_normaliza_para_sqlite(self, tmp_path, monkeypatch):
        # Garante que arquivos SQLite sejam criados a partir de cwd conhecido
        monkeypatch.chdir(tmp_path)
        arquivo_local = "legado.db"
        engine = criar_engine(arquivo_local)
        try:
            assert engine.url.get_backend_name() == "sqlite"
            assert "legado.db" in str(engine.url)
            init_db(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
        finally:
            engine.dispose()

    def test_url_sqlite_explicita(self):
        engine = criar_engine(url="sqlite:///:memory:")
        try:
            assert engine.url.get_backend_name() == "sqlite"
        finally:
            engine.dispose()

    def test_url_postgres_preservada_sem_driver(self):
        """URL Postgres passa intacta e é reconhecida — sem exigir psycopg.

        Não constrói ``Engine`` de propósito: o SQLAlchemy 2.x importa o DBAPI
        já dentro de ``create_engine``, então instanciar exigiria ``psycopg``
        instalado.  O que importa aqui é a camada do projeto — a URL não pode
        ser convertida em caminho SQLite pelo caminho legado.
        """
        url = "postgresql+psycopg://u:p@h:5432/db"
        assert _normalizar_database_url(url) == url
        assert with_overrides(database_url=url).is_postgres is True
        assert with_overrides(database_url=url).is_sqlite is False
        assert with_overrides(database_url=url).database_file_path is None

    def test_url_postgres_constroi_engine(self):
        """Constrói a Engine de fato — só roda onde o driver existe."""
        pytest.importorskip("psycopg", reason="driver Postgres não instalado")
        engine = criar_engine(url="postgresql+psycopg://u:p@h:5432/db")
        try:
            assert engine.url.get_backend_name() == "postgresql"
        finally:
            engine.dispose()

    def test_echo_explicito_sobrepoe_default(self):
        engine = criar_engine(":memory:", echo=False)
        try:
            assert engine.echo is False
        finally:
            engine.dispose()

    def test_env_definindo_database_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        engine = criar_engine()
        try:
            assert engine.url.get_backend_name() == "sqlite"
        finally:
            engine.dispose()

    def test_sqlite_pragma_wal_aplicado_em_arquivo(self, tmp_path, monkeypatch):
        """Verifica que WAL e foreign_keys são ativados em SQLite de arquivo.

        Em ``:memory:`` os PRAGMAs não se aplicam (WAL exige arquivo em
        disco); por isso usamos um arquivo temporário.
        """
        monkeypatch.chdir(tmp_path)
        arquivo = "pragma.db"
        engine = criar_engine(arquivo)
        try:
            with engine.connect() as conn:
                wal = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
                fk = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
                # journal_mode é reportado em maiúsculas (WAL/memory)
                assert str(wal).lower() == "wal"
                assert fk == 1
        finally:
            engine.dispose()

    def test_init_db_sem_engine_cria_a_partir_de_settings(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        reset_settings_cache()
        init_db()
        # Chamar de novo também é tolerante (no-op)
        init_db()

    def test_get_session_sem_engine_usa_settings(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        reset_settings_cache()
        session = get_session()
        try:
            from sqlalchemy import text

            session.execute(text("SELECT 1"))
        finally:
            session.close()


class TestSettingsImports:
    def test_settings_module_nao_quebra_em_import(self):
        mod = importlib.import_module("src.settings")
        assert hasattr(mod, "Settings")
        assert hasattr(mod, "get_settings")
        assert hasattr(mod, "with_overrides")
        assert hasattr(mod, "reset_settings_cache")


class TestCoercaoBooleana:
    """Todo campo booleano precisa de coerção, não só ``debug``.

    Sem ela, ``SPED_HUB_DB_ECHO=false`` virava a *string* ``"false"`` — que é
    verdadeira em Python.  Como esse campo alimenta o ``echo`` do SQLAlchemy,
    desligar explicitamente o echo o ligava, despejando todo SQL no log.
    """

    @pytest.mark.parametrize(
        "env_key,campo",
        [
            ("SPED_HUB_DEBUG", "debug"),
            ("SPED_HUB_DB_ECHO", "database_echo"),
            ("SMTP_USE_TLS", "smtp_use_tls"),
            ("EMAIL_ENABLED", "email_enabled"),
            ("SPED_HUB_WEBHOOK_ALLOW_HTTP", "webhook_allow_http"),
            ("SPED_HUB_RELOAD", "reload"),
        ],
    )
    @pytest.mark.parametrize(
        "valor,esperado",
        [("false", False), ("0", False), ("no", False), ("true", True), ("1", True)],
    )
    def test_valores_viram_bool_de_verdade(self, monkeypatch, env_key, campo, valor, esperado):
        monkeypatch.setenv(env_key, valor)
        cfg = get_settings()
        assert getattr(cfg, campo) is esperado


class TestCaminhoSqlite:
    """Conversão caminho → URL, em especial o caso absoluto."""

    def test_caminho_absoluto_permanece_absoluto(self, monkeypatch):
        # `lstrip("./")` comia a barra inicial e devolvia um caminho relativo,
        # apontando para um banco diferente do pedido.
        monkeypatch.setenv("SPED_HUB_DB", "/tmp/sped_hub_abs.db")
        assert get_settings().database_url == "sqlite:////tmp/sped_hub_abs.db"

    def test_caminho_relativo(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", "rel.db")
        assert get_settings().database_url == "sqlite:///./rel.db"

    def test_memory_e_url_pronta(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", ":memory:")
        assert get_settings().database_url == "sqlite:///:memory:"
        monkeypatch.setenv("SPED_HUB_DB", "postgresql+psycopg://u@h/db")
        assert get_settings().database_url == "postgresql+psycopg://u@h/db"

    def test_engine_abre_o_arquivo_pedido(self, tmp_path, monkeypatch):
        alvo = tmp_path / "destino.db"
        monkeypatch.setenv("SPED_HUB_DB", str(alvo))
        engine = criar_engine()
        try:
            init_db(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
        finally:
            engine.dispose()
        assert alvo.exists()


class TestAliasesLegados:
    """SMTP_PASS/SMTP_FROM continuam válidos; o nome documentado vence."""

    def test_alias_legado_preenche(self, monkeypatch):
        monkeypatch.setenv("SMTP_PASS", "senha-legada")
        monkeypatch.setenv("SMTP_FROM", "legado@example.com")
        cfg = get_settings()
        assert cfg.smtp_password == "senha-legada"
        assert cfg.email_from == "legado@example.com"

    def test_nome_documentado_tem_precedencia(self, monkeypatch):
        monkeypatch.setenv("SMTP_PASS", "legada")
        monkeypatch.setenv("SMTP_PASSWORD", "oficial")
        monkeypatch.setenv("SMTP_FROM", "legado@example.com")
        monkeypatch.setenv("EMAIL_FROM", "oficial@example.com")
        cfg = get_settings()
        assert cfg.smtp_password == "oficial"
        assert cfg.email_from == "oficial@example.com"


class TestLimiteDeUpload:
    def test_default_deriva_de_mb(self):
        assert get_settings().max_upload_bytes == 200 * 1024 * 1024

    def test_mb_configuravel(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_MB", "50")
        assert get_settings().max_upload_bytes == 50 * 1024 * 1024

    def test_override_legado_em_bytes_vence(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_MB", "50")
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_BYTES", "4")
        assert get_settings().max_upload_bytes == 4

    def test_override_invalido_cai_no_mb(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_BYTES", "-1")
        assert get_settings().max_upload_bytes == 200 * 1024 * 1024


class TestDatabaseReferenceNosServicos:
    """DATABASE_URL precisa chegar em todos os serviços, não só em criar_engine.

    Antes, dashboard, API REST e GraphQL liam ``SPED_HUB_DB`` direto do
    ambiente: quem configurasse ``DATABASE_URL`` (inclusive o exemplo de
    PostgreSQL do README) subia silenciosamente em SQLite.
    """

    def test_database_reference_segue_database_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/referencia.db")
        assert database_reference() == "sqlite:////tmp/referencia.db"

    def test_api_rest_e_graphql_usam_a_mesma_referencia(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/referencia.db")
        from src.api.graphql import _get_db_path as graphql_db
        from src.api.routes import _get_db_path as rest_db

        assert rest_db() == "sqlite:////tmp/referencia.db"
        assert graphql_db() == "sqlite:////tmp/referencia.db"

    def test_sped_hub_db_continua_funcionando(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_DB", "/tmp/legado.db")
        assert database_reference() == "sqlite:////tmp/legado.db"


class TestCacheDeEngine:
    """``obter_engine`` reaproveita o pool; ``criar_engine`` continua criando novo.

    O caminho quente (validar sessão, resolver tenant, cada request do
    dashboard) criava uma engine e rodava ``create_all`` — reflexão das 24
    tabelas — a cada chamada.
    """

    def test_mesma_url_devolve_a_mesma_engine(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'cache.db'}"
        assert obter_engine(url=url) is obter_engine(url=url)

    def test_urls_distintas_nao_se_misturam(self, tmp_path):
        a = obter_engine(url=f"sqlite:///{tmp_path / 'a.db'}")
        b = obter_engine(url=f"sqlite:///{tmp_path / 'b.db'}")
        assert a is not b

    def test_criar_engine_continua_sem_cache(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'sem_cache.db'}"
        primeira = criar_engine(url=url)
        try:
            assert primeira is not criar_engine(url=url)
        finally:
            primeira.dispose()

    def test_memory_nunca_e_compartilhada(self):
        # Cada engine `:memory:` é um banco próprio; compartilhá-las faria
        # bancos de teste independentes enxergarem os dados uns dos outros.
        assert obter_engine(":memory:") is not obter_engine(":memory:")

    def test_init_db_once_roda_uma_vez_por_engine(self, tmp_path, monkeypatch):
        from src.db import models

        chamadas = []
        original = models.Base.metadata.create_all
        monkeypatch.setattr(
            models.Base.metadata,
            "create_all",
            lambda *a, **k: (chamadas.append(1), original(*a, **k))[1],
        )
        engine = obter_engine(url=f"sqlite:///{tmp_path / 'once.db'}")
        for _ in range(5):
            models.init_db_once(engine)
        assert len(chamadas) == 1

    def test_init_db_once_roda_de_novo_para_engine_nova(self, tmp_path):
        from src.db import models

        engine = criar_engine(url=f"sqlite:///{tmp_path / 'nova.db'}")
        try:
            models.init_db_once(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1 FROM usuarios LIMIT 1")
        finally:
            engine.dispose()

    def test_cache_e_zerado_no_processo_filho(self, tmp_path):
        """Pools do SQLAlchemy não sobrevivem a ``fork`` — o worker usa multiprocessing."""
        import multiprocessing
        import os

        if not hasattr(os, "fork"):
            pytest.skip("plataforma sem fork")

        from src.db import models

        obter_engine(url=f"sqlite:///{tmp_path / 'fork.db'}")
        assert models._ENGINES, "pré-condição: cache populado no pai"

        ctx = multiprocessing.get_context("fork")
        fila = ctx.Queue()
        processo = ctx.Process(target=_reportar_tamanho_do_cache, args=(fila,))
        processo.start()
        processo.join(timeout=30)
        assert fila.get(timeout=5) == 0, "o filho herdou engines do pai"


def _reportar_tamanho_do_cache(fila):
    from src.db import models

    fila.put(len(models._ENGINES))
