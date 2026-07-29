"""Aplicação programática das migrações Alembic (Fase 17, Etapa 3).

Política de versionamento do schema — ver também ``docs/migrations.md``:

* **PostgreSQL** é versionado por migração.  ``create_all`` nunca deve tocar
  um banco Postgres de produção: ele cria o que falta, mas não altera nem
  remove nada, então o schema silenciosamente diverge dos modelos.
* **SQLite** (desenvolvimento e testes) continua com ``create_all``, que é
  mais rápido e não exige histórico.  As migrações são exercitadas contra os
  dois backends no CI mesmo assim, para não apodrecerem.

O ponto delicado é a concorrência: ``web`` e ``worker`` sobem ao mesmo tempo
no ``docker-compose``, e duas migrações simultâneas no mesmo banco se
atropelam.  Por isso o upgrade em Postgres roda dentro de um *advisory lock*
transacional — o segundo processo espera o primeiro terminar e então encontra
o schema já em ``head``, sem fazer nada.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from alembic import command
from src.db.models import criar_engine
from src.settings import caminho_para_url_sqlite

logger = logging.getLogger("sped-hub.migrations")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = PROJECT_ROOT / "alembic"

# Chave arbitrária mas fixa do advisory lock.  Precisa ser constante entre
# processos para que eles de fato disputem o mesmo lock.
_LOCK_KEY = 8_150_117


def _normalizar(url: str | None) -> str | None:
    """Aceita URL pronta ou caminho de arquivo, como o resto da aplicação.

    A CLI recebe `--db` que pode ser qualquer um dos dois.
    """
    return caminho_para_url_sqlite(url) if url else None


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    if url:
        # `set_main_option` grava no configparser, que trata `%` como início
        # de interpolação.  Sem escapar, uma senha com `%` — ou um parâmetro
        # percent-encoded na query string — derruba o upgrade com
        # "invalid interpolation syntax".
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def revisao_head() -> str | None:
    """Revisão mais recente disponível no diretório de migrações."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def revisao_atual(engine) -> str | None:
    """Revisão em que o banco está, ou ``None`` se nunca foi migrado."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def upgrade_head(url: str | None = None) -> str | None:
    """Leva o banco até a revisão mais recente.  Idempotente.

    Em PostgreSQL, segura um advisory lock transacional durante toda a
    migração: sem ele, dois containers subindo juntos executam o mesmo
    ``CREATE TABLE`` e um dos dois quebra.
    """
    url = _normalizar(url)
    engine = criar_engine(url=url) if url else criar_engine()
    cfg = alembic_config(url)
    try:
        with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_LOCK_KEY})")
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        atual = revisao_atual(engine)
        logger.info("Schema migrado para %s", atual)
        return atual
    finally:
        engine.dispose()


def stamp_head(url: str | None = None) -> None:
    """Marca o banco como estando em ``head`` sem executar as migrações.

    Serve para adotar o Alembic num banco que já tem o schema completo —
    o caso de qualquer instalação anterior à Etapa 3.  Ver
    ``docs/migrations.md``.
    """
    url = _normalizar(url)
    engine = criar_engine(url=url) if url else criar_engine()
    cfg = alembic_config(url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.stamp(cfg, "head")
        logger.info("Schema marcado como %s sem executar migrações", revisao_head())
    finally:
        engine.dispose()
