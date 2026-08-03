"""o total do documento com os tributos da Reforma (`vNFTot`)

A NT 2025.002 v1.51 mantém o `vNF` como sempre foi e acrescenta o `vNFTot`
(W60) ao lado: "Valor total da NF-e com IBS / CBS / IS". São dois campos, não
duas versões do mesmo — somar os novos tributos ao `vNF` produziria um
documento que a SEFAZ recusa, e é exatamente o engano que ter os dois lado a
lado evita.

O campo é opcional (0-1) e as regras W60-05/W60-10 estão marcadas
"implementação futura" na própria NT, então nota que não o traz fica com zero.

Revision ID: d4a8b2f60e15
Revises: c3e7a91f5d84
"""

import sqlalchemy as sa

from alembic import op

revision = "d4a8b2f60e15"
down_revision = "c3e7a91f5d84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documentos_fiscais") as lote:
        lote.add_column(
            sa.Column("valor_total_com_reforma", sa.Float(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("documentos_fiscais") as lote:
        lote.drop_column("valor_total_com_reforma")
