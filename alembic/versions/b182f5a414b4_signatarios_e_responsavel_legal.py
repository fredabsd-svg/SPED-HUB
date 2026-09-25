"""signatários da ECD (J930) e responsável legal da empresa

O J930 diz quem assinou a escrituração — o contador, com o CRC, e o
responsável legal.  É o que as demonstrações precisam para sair com a linha
de assinatura preenchida.  E-mail e telefone do J930 não são guardados.

A ECD assinada com e-CNPJ traz a própria empresa como responsável legal, sem
o nome do sócio; por isso a empresa ganha três campos para quem assina por
ela.

ECD importada antes desta migração fica sem signatários até ser importada de
novo: a linha de assinatura do contador sai em branco, com a qualificação.

Revision ID: b182f5a414b4
Revises: e3a91c7d5b28
"""

import sqlalchemy as sa

from alembic import op

revision = "b182f5a414b4"
down_revision = "e3a91c7d5b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signatarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ecd_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("cpf_cnpj", sa.String(length=14), nullable=True),
        sa.Column("qualificacao", sa.String(length=255), nullable=True),
        sa.Column("cod_assin", sa.String(length=3), nullable=True),
        sa.Column("crc", sa.String(length=11), nullable=True),
        sa.Column("uf_crc", sa.String(length=2), nullable=True),
        sa.Column("ind_resp_legal", sa.String(length=1), nullable=True),
        sa.ForeignKeyConstraint(["ecd_id"], ["ecds.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("signatarios", schema=None) as lote:
        lote.create_index(lote.f("ix_signatarios_ecd_id"), ["ecd_id"], unique=False)

    with op.batch_alter_table("empresas", schema=None) as lote:
        lote.add_column(sa.Column("responsavel_nome", sa.String(length=150), nullable=True))
        lote.add_column(sa.Column("responsavel_cpf", sa.String(length=11), nullable=True))
        lote.add_column(sa.Column("responsavel_qualificacao", sa.String(length=60), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("empresas", schema=None) as lote:
        lote.drop_column("responsavel_qualificacao")
        lote.drop_column("responsavel_cpf")
        lote.drop_column("responsavel_nome")

    with op.batch_alter_table("signatarios", schema=None) as lote:
        lote.drop_index(lote.f("ix_signatarios_ecd_id"))
    op.drop_table("signatarios")
