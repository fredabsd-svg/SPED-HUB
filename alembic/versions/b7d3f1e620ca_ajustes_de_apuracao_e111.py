"""ajustes de apuração do ICMS (registro E111)

A apuração do bloco E era a soma dos documentos e mais nada. Empresa com
benefício fiscal, crédito outorgado, estorno ou dedução tem valores que **não
estão em nota nenhuma**, e sem eles o imposto sai errado nos dois sentidos.

O código vem da tabela 5.1.1, que é de cada Secretaria da Fazenda. O sistema
não guarda essa tabela — ela muda por ato normativo e é diferente em cada
estado. O que ele lê é a estrutura, nacional (Ato COTEPE/ICMS 09/2008):
`PRBCDDDD`, onde a 3ª posição é a apuração (0 ICMS, 1 ST, 2 DIFAL, 3 FCP) e a
4ª é a utilização, que decide em que campo do E110 o valor entra.

Revision ID: b7d3f1e620ca
Revises: a2e5c99b7714
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3f1e620ca"
down_revision: str | Sequence[str] | None = "a2e5c99b7714"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ajustes_apuracao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("escritorio_id", sa.Integer(), nullable=True),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("data_fim", sa.Date(), nullable=False),
        sa.Column("cod_aj", sa.String(length=8), nullable=False),
        sa.Column("descricao", sa.String(length=255), nullable=True),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["escritorio_id"], ["escritorios.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ajustes_apuracao", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ajustes_apuracao_data_inicio"), ["data_inicio"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ajustes_apuracao_empresa_id"), ["empresa_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ajustes_apuracao_escritorio_id"), ["escritorio_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ajustes_apuracao_tipo"), ["tipo"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("ajustes_apuracao")
