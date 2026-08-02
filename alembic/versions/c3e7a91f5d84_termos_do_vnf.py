"""os termos que faltavam para fechar o vNF pela regra W16-10

O total do documento não é soma de parcela: é a fórmula do MOC 7.0 (Anexo I,
regra W16-10), com doze termos. Cinco deles o modelo não carregava — ICMS
desonerado, FCP-ST, imposto de importação, IPI devolvido e serviços —, e sem
eles o recálculo do §12.5 só podia avisar que deixava o total para trás.

`tipo_operacao_veiculo` entra por causa da exceção 1 da mesma regra:
faturamento direto de veículo novo (`tpOp = 2`) não soma ST, FCP-ST nem IPI
devolvido. Sem conseguir reconhecer o caso, o recálculo o trataria como comum.

Revision ID: c3e7a91f5d84
Revises: f1b8d34c7e29
"""

import sqlalchemy as sa

from alembic import op

revision = "c3e7a91f5d84"
down_revision = "f1b8d34c7e29"
branch_labels = None
depends_on = None

DO_DOCUMENTO = [
    "valor_icms_desonerado",
    "valor_fcp_st",
    "valor_imposto_importacao",
    "valor_ipi_devolvido",
    "valor_servicos",
]


def upgrade() -> None:
    with op.batch_alter_table("documentos_fiscais") as lote:
        for nome in DO_DOCUMENTO:
            lote.add_column(sa.Column(nome, sa.Float(), nullable=False, server_default="0"))

    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        lote.add_column(sa.Column("tipo_operacao_veiculo", sa.String(length=1), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        lote.drop_column("tipo_operacao_veiculo")

    with op.batch_alter_table("documentos_fiscais") as lote:
        for nome in DO_DOCUMENTO:
            lote.drop_column(nome)
