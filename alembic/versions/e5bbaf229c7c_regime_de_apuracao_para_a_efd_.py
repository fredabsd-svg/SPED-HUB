"""regime de apuração para a EFD-Contribuições

`cod_inc_trib` é o COD_INC_TRIB do registro 0110: 1=não cumulativo,
2=cumulativo, 3=ambos.  É o campo que decide se a empresa desconta crédito das
aquisições.

Nasce NULO de propósito.  Errar o regime produz arquivo estruturalmente
válido com contribuição errada — no cumulativo não há crédito, e somar os
créditos das entradas ali geraria contribuição a menor.  O Fisco cobra a
diferença com multa, e a conferência não pega, porque o arquivo passa no
validador.  O gerador recusa gerar sem o campo.

Revision ID: e5bbaf229c7c
Revises: 9be2a4255a67
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5bbaf229c7c"
down_revision: str | Sequence[str] | None = "9be2a4255a67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cod_inc_trib", sa.String(length=1), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_column("cod_inc_trib")
