"""marcar qual escrituração foi transmitida

Todas as gerações já ficavam guardadas — a terceira camada existe desde a
fase 45. O que faltava era saber **qual delas foi entregue**. Sem isso a
camada guarda candidatos, não o registro efetivamente enviado ao SPED, e a
pergunta da intimação continua sem resposta exata.

Três colunas, todas nulas:

  * `transmitida_em` — quando foi entregue. Nulo enquanto ninguém disser: o
    sistema não transmite, e deduzir pela geração mais recente diria que foi
    entregue justamente a que se acabou de gerar para conferir;
  * `recibo` — o número devolvido pelo Fisco, que liga o arquivo daqui ao que
    está lá. Sem ele, "transmitida" é palavra de quem marcou;
  * `transmitida_por_id` — quem marcou, que não é necessariamente quem gerou.

A linha continua imutável no que diz respeito ao conteúdo: marcar não toca no
texto nem no hash.

Revision ID: f8c2d1a45b90
Revises: c4a1f7b8e2d3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8c2d1a45b90"
down_revision: str | Sequence[str] | None = "c4a1f7b8e2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("escrituracoes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("transmitida_em", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("recibo", sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column("transmitida_por_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_escrituracoes_transmitida_por", "usuarios", ["transmitida_por_id"], ["id"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("escrituracoes", schema=None) as batch_op:
        batch_op.drop_constraint("fk_escrituracoes_transmitida_por", type_="foreignkey")
        batch_op.drop_column("transmitida_por_id")
        batch_op.drop_column("recibo")
        batch_op.drop_column("transmitida_em")
