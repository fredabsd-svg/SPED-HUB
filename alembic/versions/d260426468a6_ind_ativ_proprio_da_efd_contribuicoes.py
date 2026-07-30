"""IND_ATIV próprio da EFD-Contribuições

`ind_ativ_contribuicoes` é o IND_ATIV do registro 0000 da EFD-Contribuições.

É coluna separada de `ind_ativ` — o IND_ATIV da EFD ICMS/IPI — de propósito.
As duas escriturações fazem perguntas diferentes com o mesmo nome de campo:
lá a resposta é binária (0=industrial, 1=outros), aqui são 0=industrial ou
equiparado, 1=prestador de serviços, 2=comércio, 3=PJ dos §§ 6º, 8º e 9º do
art. 3º da Lei 9.718/98, 4=atividade imobiliária, 9=outros.  Copiar o valor de
uma para a outra declararia como prestador de serviços toda empresa de
comércio que respondeu "1 = outros" pensando na EFD ICMS/IPI — e o validador
aceitaria, porque não tem como saber qual é o certo.

Por isso nasce NULA e não é preenchida a partir de `ind_ativ`: não há
conversão correta entre as duas tabelas.  O gerador recusa gerar sem o campo.

Revision ID: d260426468a6
Revises: e5bbaf229c7c
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d260426468a6"
down_revision: str | Sequence[str] | None = "e5bbaf229c7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ind_ativ_contribuicoes", sa.String(length=1), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_column("ind_ativ_contribuicoes")
