"""Ambiente do Alembic — a URL vem de ``src.settings``, nunca do alembic.ini.

Manter a URL no ``alembic.ini`` criaria um segundo lugar para configurar o
banco, exatamente o problema que a Fase 17 resolveu.  Aqui ela é sempre a
mesma que a aplicação usa, com ``-x url=...`` disponível para casos pontuais
(``alembic -x url=postgresql+psycopg://... upgrade head``).
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.models import Base  # noqa: E402
from src.settings import database_reference  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("url") or database_reference()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # O SQLite não tem ALTER COLUMN: sem o modo batch, o Alembic não
        # consegue alterar coluna nenhuma nesse backend.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _executar(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # `src.db.migrations.upgrade_head` injeta a conexão aqui para poder
    # segurar o advisory lock do Postgres durante a migração inteira.
    externa = config.attributes.get("connection")
    if externa is not None:
        _executar(externa)
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _executar(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
