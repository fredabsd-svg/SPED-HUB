"""regras fiscais

A biblioteca de classificações recorrentes do escritório: "para este
fornecedor e este NCM, use sempre este CFOP".

Condições e ações são JSON estruturado, não expressão avaliada — um campo de
texto que o sistema executasse transformaria a tabela em superfície de
execução de código no servidor, a troco de expressividade que o domínio não
pede.

Revision ID: 55e3c6ce2365
Revises: d5969a68dba0
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "55e3c6ce2365"
down_revision: str | Sequence[str] | None = "d5969a68dba0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regras_fiscais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("escritorio_id", sa.Integer(), nullable=True),
        sa.Column("empresa_id", sa.Integer(), nullable=True),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("prioridade", sa.Integer(), nullable=False),
        sa.Column("condicoes", sa.Text(), nullable=False),
        sa.Column("acoes", sa.Text(), nullable=False),
        sa.Column("obrigacao", sa.String(length=30), nullable=True),
        sa.Column("vigencia_inicio", sa.Date(), nullable=True),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("confianca", sa.Float(), nullable=False),
        sa.Column("ativa", sa.Boolean(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["escritorio_id"],
            ["escritorios.id"],
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("regras_fiscais", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_regras_fiscais_ativa"), ["ativa"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_regras_fiscais_empresa_id"), ["empresa_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_regras_fiscais_escritorio_id"), ["escritorio_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_regras_fiscais_prioridade"), ["prioridade"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("regras_fiscais", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_regras_fiscais_prioridade"))
        batch_op.drop_index(batch_op.f("ix_regras_fiscais_escritorio_id"))
        batch_op.drop_index(batch_op.f("ix_regras_fiscais_empresa_id"))
        batch_op.drop_index(batch_op.f("ix_regras_fiscais_ativa"))

    op.drop_table("regras_fiscais")
