"""o FCP-ST de cada item (`vFCPST`)

O `VL_OPR` do C190 é, pelo Guia Prático da EFD ICMS/IPI 3.2.2 (C190, campo
05), "o valor das mercadorias somadas aos valores de fretes, seguros e outras
despesas acessórias e os valores de ICMS_ST, FCP_ST e IPI [...], subtraídos o
desconto incondicional e o abatimento não tributado". O C190 agrupa por CST,
CFOP e alíquota, e por isso cada parcela precisa existir no item: o total do
documento (`valor_fcp_st`, já guardado) não diz de qual item ele é.

Documento importado antes desta migração fica com zero no item; o gerador
avisa quando o total do documento tem FCP-ST que os itens não têm.

Revision ID: e3a91c7d5b28
Revises: d1e6b48a92c5
"""

import sqlalchemy as sa

from alembic import op

revision = "e3a91c7d5b28"
down_revision = "d1e6b48a92c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        lote.add_column(sa.Column("valor_fcp_st", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        lote.drop_column("valor_fcp_st")
