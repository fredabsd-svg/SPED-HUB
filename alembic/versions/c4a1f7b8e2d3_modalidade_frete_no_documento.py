"""modalidade do frete no documento fiscal

`modalidade_frete` é o `modFrete` do grupo `transp` da NF-e, guardado porque é
o `IND_FRT` do registro C100 — mesma tabela de códigos desde 01/01/2018, sem
conversão no meio: 0 e 3 por conta do remetente, 1 e 4 por conta do
destinatário, 2 de terceiros, 9 sem frete.

O C100 vinha saindo **sem** esse campo, e como ele é o campo 17, logo depois do
VL_MERC, todos os doze valores seguintes ocupavam a posição do vizinho: o valor
do frete ia para onde o leiaute espera o indicador do frete, a base do ICMS
para "outras despesas", e assim até o fim da linha.

Nasce nula: documento importado antes desta versão não tem de onde tirar o
código. O gerador emite 9 (sem frete) — o único código possível quando não há
valor de frete — e, quando há frete sem modalidade, emite 9 e avisa nomeando os
documentos, em vez de afirmar quem pagou.

Revision ID: c4a1f7b8e2d3
Revises: 7319f6d90088
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a1f7b8e2d3"
down_revision: str | Sequence[str] | None = "7319f6d90088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("documentos_fiscais", schema=None) as batch_op:
        batch_op.add_column(sa.Column("modalidade_frete", sa.String(length=1), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documentos_fiscais", schema=None) as batch_op:
        batch_op.drop_column("modalidade_frete")
