"""natureza jurídica da empresa (IND_NAT_PJ)

`ind_nat_pj` é o campo 13 do registro 0000 da EFD-Contribuições. Vinha saindo
fixo como `00` (sociedade empresária em geral) com aviso no resultado, o que
declarava errado toda cooperativa e toda entidade que apura o PIS/Pasep sobre
a folha de salários — e o validador aceita, porque não tem como saber.

A tabela: 00 sociedade empresária em geral, 01 sociedade cooperativa, 02
entidade que apura sobre a folha de salários, 03 PJ em geral sócia ostensiva
de SCP, 04 cooperativa sócia ostensiva de SCP, 05 SCP.

Nasce nula, e nula continua valendo `00` com aviso: aqui existe um default
razoável para a imensa maioria, e exigir a resposta de todo mundo por causa da
minoria travaria quem não tem o que declarar. É a diferença para `ind_perfil`,
`ind_ativ`, `ind_ativ_contribuicoes` e `cod_inc_trib`, que não têm default
possível e por isso fazem o gerador parar.

Revision ID: a2e5c99b7714
Revises: f8c2d1a45b90
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2e5c99b7714"
down_revision: str | Sequence[str] | None = "f8c2d1a45b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ind_nat_pj", sa.String(length=2), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_column("ind_nat_pj")
