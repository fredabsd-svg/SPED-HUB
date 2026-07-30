"""api_key ganha escritório dono

Revision ID: b3e91d4a7c22
Revises: a1c7f2b9e40d
Create Date: 2026-07-30 12:00:00.000000

Antes desta coluna, `ApiKey` não tinha dono e nenhuma rota de `/api/v1`
filtrava por escritório: uma chave entregue ao integrador do escritório A lia a
escrituração do B.

A coluna é nullable e as chaves existentes ficam com `NULL`, que significa
"chave de instância" — lê tudo, o comportamento que já tinham. Preencher com um
escritório arbitrário quebraria integração em produção, e preencher com um
escritório errado seria pior que o buraco: a integração pararia de ver os dados
certos sem explicação.

O que a migração NÃO precisa consertar é o escalonamento de privilégio (chave
criando e revogando chaves, e elevando a própria cota). Esse fecha por código:
as rotas de administração passaram a exigir sessão de administrador.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e91d4a7c22"
down_revision: str | Sequence[str] | None = "a1c7f2b9e40d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `batch_alter_table` porque o SQLite não tem ALTER COLUMN: sem o modo
    # batch, o Alembic não consegue adicionar a chave estrangeira nesse backend.
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.add_column(sa.Column("escritorio_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_api_keys_escritorio_id"), ["escritorio_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_api_keys_escritorio_id", "escritorios", ["escritorio_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_constraint("fk_api_keys_escritorio_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_api_keys_escritorio_id"))
        batch_op.drop_column("escritorio_id")
