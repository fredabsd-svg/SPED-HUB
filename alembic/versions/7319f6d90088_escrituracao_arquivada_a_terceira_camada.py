"""escrituração arquivada: a terceira camada

Guarda o arquivo SPED que efetivamente saiu, ao lado do documento original
(`documentos_fiscais.xml_original`) e do tratamento fiscal (`ajustes_fiscais`).

O conteúdo é gravado, não reconstruído: um ajuste feito depois da entrega faz
uma geração nova divergir da que foi transmitida, e é aí que a diferença
importa.  `escrituracoes_documentos` responde em que arquivo cada nota entrou.

Revision ID: 7319f6d90088
Revises: d260426468a6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7319f6d90088"
down_revision: str | Sequence[str] | None = "d260426468a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "escrituracoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("escritorio_id", sa.Integer(), nullable=True),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("data_fim", sa.Date(), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("hash_conteudo", sa.String(length=64), nullable=False),
        sa.Column("total_linhas", sa.Integer(), nullable=False),
        sa.Column("avisos", sa.Text(), nullable=False),
        # Não-nula, como no modelo: o default é do lado do Python, e preenche
        # antes do INSERT.
        sa.Column("gerada_em", sa.DateTime(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["escritorio_id"], ["escritorios.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_escrituracoes_empresa_id", "escrituracoes", ["empresa_id"])
    op.create_index("ix_escrituracoes_escritorio_id", "escrituracoes", ["escritorio_id"])
    op.create_index("ix_escrituracoes_tipo", "escrituracoes", ["tipo"])
    op.create_index("ix_escrituracoes_data_inicio", "escrituracoes", ["data_inicio"])
    # Conferir o arquivo que o contribuinte tem em mãos é busca por hash.
    op.create_index("ix_escrituracoes_hash_conteudo", "escrituracoes", ["hash_conteudo"])

    op.create_table(
        "escrituracoes_documentos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("escrituracao_id", sa.Integer(), nullable=False),
        sa.Column("documento_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["documento_id"], ["documentos_fiscais.id"]),
        sa.ForeignKeyConstraint(["escrituracao_id"], ["escrituracoes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("escrituracao_id", "documento_id", name="uq_escrituracao_documento"),
    )
    op.create_index(
        "ix_escrituracoes_documentos_escrituracao_id",
        "escrituracoes_documentos",
        ["escrituracao_id"],
    )
    # "esta nota foi escriturada onde?" é a busca que a intimação faz.
    op.create_index(
        "ix_escrituracoes_documentos_documento_id",
        "escrituracoes_documentos",
        ["documento_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_escrituracoes_documentos_documento_id", table_name="escrituracoes_documentos")
    op.drop_index(
        "ix_escrituracoes_documentos_escrituracao_id",
        table_name="escrituracoes_documentos",
    )
    op.drop_table("escrituracoes_documentos")

    op.drop_index("ix_escrituracoes_hash_conteudo", table_name="escrituracoes")
    op.drop_index("ix_escrituracoes_data_inicio", table_name="escrituracoes")
    op.drop_index("ix_escrituracoes_tipo", table_name="escrituracoes")
    op.drop_index("ix_escrituracoes_escritorio_id", table_name="escrituracoes")
    op.drop_index("ix_escrituracoes_empresa_id", table_name="escrituracoes")
    op.drop_table("escrituracoes")
