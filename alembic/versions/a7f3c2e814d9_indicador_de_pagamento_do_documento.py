"""o indicador de pagamento do documento (`indPag`)

O `IND_PGTO` do C100 é obrigatório na entrada e na saída — o Guia Prático da
EFD ICMS/IPI 3.2.2 marca "O" nas duas colunas — e saía vazio, o que faz o
validador recusar o arquivo. O dado existe no XML desde sempre, no grupo
`pag/detPag/indPag`; só não estava sendo lido.

A coluna é anulável de propósito: documento importado antes desta migração
não tem como saber o que o XML dizia. Para esses o gerador escreve `2`
(outros), o código que menos afirma, e avisa com o número de cada documento —
a mesma decisão já tomada para o `IND_FRT`.

Revision ID: a7f3c2e814d9
Revises: d4a8b2f60e15
"""

import sqlalchemy as sa

from alembic import op

revision = "a7f3c2e814d9"
down_revision = "d4a8b2f60e15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documentos_fiscais") as lote:
        lote.add_column(sa.Column("indicador_pagamento", sa.String(length=1), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documentos_fiscais") as lote:
        lote.drop_column("indicador_pagamento")
