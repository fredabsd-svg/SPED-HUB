"""o cofre do certificado A1

Guarda o PFX e a senha em envelopes cifrados separados, com AES-256-GCM. A
chave mestra fica em `SPED_HUB_CERTIFICATE_MASTER_KEY`, fora do banco: cifrar
com uma chave guardada ao lado do texto cifrado protege contra quase nada.

As colunas abaixo dos envelopes são os campos públicos do certificado — os
mesmos que qualquer navegador exibe. Existem para responder "está vencendo?"
e "é da empresa certa?" sem abrir o cofre.

Revision ID: d1e6b48a92c5
Revises: c8f1a3d59e47
"""

import sqlalchemy as sa

from alembic import op

revision = "d1e6b48a92c5"
down_revision = "c8f1a3d59e47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "certificados_digitais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("envelope_pfx", sa.Text(), nullable=False),
        sa.Column("envelope_senha", sa.Text(), nullable=False),
        sa.Column("titular", sa.String(length=255), nullable=False),
        sa.Column("emissor", sa.String(length=255), nullable=False),
        sa.Column("valido_de", sa.Date(), nullable=False),
        sa.Column("valido_ate", sa.Date(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("cnpj", sa.String(length=14), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("substituido_em", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "fingerprint", name="uq_certificado_empresa"),
    )
    op.create_index("ix_certificados_digitais_empresa_id", "certificados_digitais", ["empresa_id"])
    op.create_index("ix_certificados_digitais_valido_ate", "certificados_digitais", ["valido_ate"])
    op.create_index("ix_certificados_digitais_cnpj", "certificados_digitais", ["cnpj"])


def downgrade() -> None:
    op.drop_index("ix_certificados_digitais_cnpj", "certificados_digitais")
    op.drop_index("ix_certificados_digitais_valido_ate", "certificados_digitais")
    op.drop_index("ix_certificados_digitais_empresa_id", "certificados_digitais")
    op.drop_table("certificados_digitais")
