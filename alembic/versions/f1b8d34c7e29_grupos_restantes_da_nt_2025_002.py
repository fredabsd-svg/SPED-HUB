"""os grupos da NT 2025.002 v1.50 que ainda ficavam sem leitura

Transferência de crédito (UB106), ajuste de competência (UB112), estorno de
crédito (UB116), a diferença na mistura de biocombustível (`gpBioDiferenca`) e
a base do crédito presumido (`vBCCredPres`, UB121). São valores destacados no
próprio documento; não lê-los é jogar fora informação que já chegou.

Revision ID: f1b8d34c7e29
Revises: e9f4a2c81b60
"""

import sqlalchemy as sa

from alembic import op

revision = "f1b8d34c7e29"
down_revision = "e9f4a2c81b60"
branch_labels = None
depends_on = None

NUMERICAS = [
    "base_credito_presumido",
    "quantidade_bio_diferenca",
    "valor_ibs_bio_diferenca",
    "valor_cbs_bio_diferenca",
    "valor_transf_credito_ibs",
    "valor_transf_credito_cbs",
    "valor_ajuste_compet_ibs",
    "valor_ajuste_compet_cbs",
    "valor_estorno_credito_ibs",
    "valor_estorno_credito_cbs",
]


def upgrade() -> None:
    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        for nome in NUMERICAS:
            lote.add_column(sa.Column(nome, sa.Float(), nullable=False, server_default="0"))
        lote.add_column(sa.Column("competencia_ajuste", sa.String(length=7), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        lote.drop_column("competencia_ajuste")
        for nome in NUMERICAS:
            lote.drop_column(nome)
