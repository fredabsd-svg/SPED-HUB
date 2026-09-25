"""signatários da ECD (J930) e responsável legal da empresa

O J930 diz quem assinou a escrituração — o contador, com o CRC, e o
responsável legal.  É o que as demonstrações precisam para sair com a linha
de assinatura preenchida.  E-mail e telefone do J930 não são guardados.

A ECD assinada com e-CNPJ traz a própria empresa como responsável legal, sem
o nome do sócio; por isso a empresa ganha três campos para quem assina por
ela.

ECD importada antes desta migração fica sem signatários até ser importada de
novo: a linha de assinatura do contador sai em branco, com a qualificação.

Cada passo confere antes se já foi feito.  O painel que sobe sobre o banco
antes desta migração cria `signatarios` (`create_all`) e, no SQLite, as
colunas de `empresas` (`completar_colunas`); criar de novo derrubava a
migração com "already exists" (ADR 0013).

Revision ID: b182f5a414b4
Revises: e3a91c7d5b28
"""

import sqlalchemy as sa

from alembic import op

revision = "b182f5a414b4"
down_revision = "e3a91c7d5b28"
branch_labels = None
depends_on = None


def _tabelas() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indices(tabela: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(tabela)}


def _colunas(tabela: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(tabela)}


def upgrade() -> None:
    if "signatarios" not in _tabelas():
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
    if "ix_signatarios_ecd_id" not in _indices("signatarios"):
        with op.batch_alter_table("signatarios", schema=None) as lote:
            lote.create_index(lote.f("ix_signatarios_ecd_id"), ["ecd_id"], unique=False)

    novas = [
        sa.Column("responsavel_nome", sa.String(length=150), nullable=True),
        sa.Column("responsavel_cpf", sa.String(length=11), nullable=True),
        sa.Column("responsavel_qualificacao", sa.String(length=60), nullable=True),
    ]
    presentes = _colunas("empresas")
    faltando = [coluna for coluna in novas if coluna.name not in presentes]
    if faltando:
        with op.batch_alter_table("empresas", schema=None) as lote:
            for coluna in faltando:
                lote.add_column(coluna)


def downgrade() -> None:
    with op.batch_alter_table("empresas", schema=None) as lote:
        lote.drop_column("responsavel_qualificacao")
        lote.drop_column("responsavel_cpf")
        lote.drop_column("responsavel_nome")

    with op.batch_alter_table("signatarios", schema=None) as lote:
        lote.drop_index(lote.f("ix_signatarios_ecd_id"))
    op.drop_table("signatarios")
