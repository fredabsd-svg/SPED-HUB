"""as demonstrações publicadas da ECD (bloco J)

O J100, o J150 e o J210 trazem o balanço, a DRE e a DLPA/DMPL **como a
empresa os declarou**. Eram lidos pelo parser e descartados: o programa
guardava só o que ele mesmo recalcula a partir dos saldos.

São coisas diferentes, e a diferença entre elas é o achado. Guardar só o
recalculado é guardar a nossa leitura no lugar do documento.

Revision ID: b9e4d17c30a2
Revises: a7f3c2e814d9
"""

import sqlalchemy as sa

from alembic import op

revision = "b9e4d17c30a2"
down_revision = "a7f3c2e814d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demonstracoes_contabeis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ecd_id", sa.Integer(), nullable=False),
        sa.Column("dt_ini", sa.Date(), nullable=False),
        sa.Column("dt_fin", sa.Date(), nullable=False),
        sa.Column("id_dem", sa.String(length=2), nullable=False),
        sa.Column("cab_dem", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["ecd_id"], ["ecds.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ecd_id", "dt_ini", "dt_fin", "id_dem", name="uq_demonstracao"),
    )
    op.create_index(
        "ix_demonstracoes_contabeis_ecd_id", "demonstracoes_contabeis", ["ecd_id"]
    )

    op.create_table(
        "linhas_demonstracao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("demonstracao_id", sa.Integer(), nullable=False),
        sa.Column("registro", sa.String(length=4), nullable=False),
        sa.Column("cod_agl", sa.String(length=255), nullable=False),
        sa.Column("ind_cod_agl", sa.String(length=1), nullable=True),
        sa.Column("nivel_agl", sa.Integer(), nullable=True),
        sa.Column("cod_agl_sup", sa.String(length=255), nullable=True),
        sa.Column("descricao", sa.String(length=255), nullable=True),
        sa.Column("vl_cta_ini", sa.Float(), nullable=False),
        sa.Column("ind_dc_cta_ini", sa.String(length=1), nullable=True),
        sa.Column("vl_cta_fin", sa.Float(), nullable=False),
        sa.Column("ind_dc_cta_fin", sa.String(length=1), nullable=True),
        sa.Column("ind_grp_bal", sa.String(length=1), nullable=True),
        sa.Column("nu_ordem", sa.Integer(), nullable=True),
        sa.Column("ind_grp_dre", sa.String(length=1), nullable=True),
        sa.Column("ind_tip", sa.String(length=1), nullable=True),
        sa.ForeignKeyConstraint(["demonstracao_id"], ["demonstracoes_contabeis.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_linhas_demonstracao_demonstracao_id", "linhas_demonstracao", ["demonstracao_id"]
    )
    op.create_index("ix_linhas_demonstracao_registro", "linhas_demonstracao", ["registro"])
    op.create_index("ix_linhas_demonstracao_cod_agl", "linhas_demonstracao", ["cod_agl"])


def downgrade() -> None:
    op.drop_index("ix_linhas_demonstracao_cod_agl", "linhas_demonstracao")
    op.drop_index("ix_linhas_demonstracao_registro", "linhas_demonstracao")
    op.drop_index("ix_linhas_demonstracao_demonstracao_id", "linhas_demonstracao")
    op.drop_table("linhas_demonstracao")
    op.drop_index("ix_demonstracoes_contabeis_ecd_id", "demonstracoes_contabeis")
    op.drop_table("demonstracoes_contabeis")
