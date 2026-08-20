"""o NUM_ARQ da partida é texto, não número

O Manual do Leiaute 9 da ECD declara o campo 06 do I250 como
"Número, Código ou **caminho de localização** dos documentos arquivados",
tipo C. Estava sendo lido como número e guardado como inteiro: qualquer
conteúdo que não fosse só algarismos virava nulo, em silêncio.

Revision ID: c8f1a3d59e47
Revises: b9e4d17c30a2
"""

import sqlalchemy as sa

from alembic import op

revision = "c8f1a3d59e47"
down_revision = "b9e4d17c30a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partidas") as lote:
        lote.alter_column(
            "num_arq",
            existing_type=sa.Integer(),
            type_=sa.String(length=255),
            existing_nullable=True,
            postgresql_using="num_arq::varchar",
        )


def downgrade() -> None:
    """Só volta o que couber em inteiro; caminho de localização não cabe."""
    with op.batch_alter_table("partidas") as lote:
        lote.alter_column(
            "num_arq",
            existing_type=sa.String(length=255),
            type_=sa.Integer(),
            existing_nullable=True,
            postgresql_using="NULLIF(regexp_replace(num_arq, '\\D', '', 'g'), '')::integer",
        )
