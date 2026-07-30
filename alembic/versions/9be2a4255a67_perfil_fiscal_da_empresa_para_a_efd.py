"""perfil fiscal da empresa para a EFD

Os dois campos do registro 0000 que não dá para derivar de documento nenhum:
`ind_perfil` (A, B ou C) e `ind_ativ` (0=industrial, 1=outros).  Dependem do
enquadramento da empresa junto à SEFAZ.

Nascem NULOS de propósito.  Um default faria o arquivo sair com enquadramento
errado — e o validador do Fisco aceita, porque não tem como saber qual é o
certo; o erro só apareceria meses depois, em intimação.  O gerador recusa
gerar sem eles.

Revision ID: 9be2a4255a67
Revises: 55e3c6ce2365
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9be2a4255a67"
down_revision: str | Sequence[str] | None = "55e3c6ce2365"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ind_perfil", sa.String(length=1), nullable=True))
        batch_op.add_column(sa.Column("ind_ativ", sa.String(length=1), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_column("ind_ativ")
        batch_op.drop_column("ind_perfil")
