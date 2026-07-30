"""central de documentos fiscais, com os tributos da reforma

Cria as três tabelas que sustentam a Central de Documentos Fiscais, com as
camadas que o resto da suíte depende de manter separadas:

  * `documentos_fiscais`        cabeçalho normalizado + o XML original
                                preservado em `xml_original`;
  * `itens_documentos_fiscais`  o item normalizado, com ICMS/IPI/PIS/Cofins
                                E IBS/CBS/IS lado a lado;
  * `ajustes_fiscais`           a camada de tratamento, aditiva: cada linha é
                                um campo alterado, com valor anterior, origem
                                (regra ou usuário) e lote, para reverter.

Os campos da Reforma Tributária do Consumo convivem com os antigos em vez de
substituí-los porque os dois regimes coexistem de 2026 a 2032 — modelar como
substituição obrigaria a alterar o schema na virada de cada ano da transição.
Ver `docs/reforma-tributaria.md`.

Revision ID: d5969a68dba0
Revises: b3e91d4a7c22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5969a68dba0"
down_revision: str | Sequence[str] | None = "b3e91d4a7c22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "documentos_fiscais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("escritorio_id", sa.Integer(), nullable=True),
        sa.Column("empresa_id", sa.Integer(), nullable=True),
        sa.Column("chave", sa.String(length=64), nullable=False),
        sa.Column("modelo", sa.String(length=2), nullable=False),
        sa.Column("especie", sa.String(length=12), nullable=False),
        sa.Column("numero", sa.String(length=20), nullable=False),
        sa.Column("serie", sa.String(length=5), nullable=True),
        sa.Column("sentido", sa.String(length=7), nullable=False),
        sa.Column("situacao", sa.String(length=12), nullable=False),
        sa.Column("finalidade", sa.String(length=20), nullable=True),
        sa.Column("natureza_operacao", sa.String(length=120), nullable=True),
        sa.Column("emitente_cnpj", sa.String(length=14), nullable=True),
        sa.Column("emitente_nome", sa.String(length=120), nullable=True),
        sa.Column("emitente_ie", sa.String(length=20), nullable=True),
        sa.Column("emitente_uf", sa.String(length=2), nullable=True),
        sa.Column("destinatario_cnpj", sa.String(length=14), nullable=True),
        sa.Column("destinatario_nome", sa.String(length=120), nullable=True),
        sa.Column("destinatario_ie", sa.String(length=20), nullable=True),
        sa.Column("destinatario_uf", sa.String(length=2), nullable=True),
        sa.Column("municipio_codigo", sa.String(length=7), nullable=True),
        sa.Column("data_emissao", sa.Date(), nullable=True),
        sa.Column("data_entrada_saida", sa.Date(), nullable=True),
        sa.Column("valor_total", sa.Float(), nullable=False),
        sa.Column("valor_produtos", sa.Float(), nullable=False),
        sa.Column("valor_desconto", sa.Float(), nullable=False),
        sa.Column("valor_frete", sa.Float(), nullable=False),
        sa.Column("valor_seguro", sa.Float(), nullable=False),
        sa.Column("valor_outras", sa.Float(), nullable=False),
        sa.Column("base_icms", sa.Float(), nullable=False),
        sa.Column("valor_icms", sa.Float(), nullable=False),
        sa.Column("valor_icms_st", sa.Float(), nullable=False),
        sa.Column("valor_ipi", sa.Float(), nullable=False),
        sa.Column("valor_pis", sa.Float(), nullable=False),
        sa.Column("valor_cofins", sa.Float(), nullable=False),
        sa.Column("valor_ibs", sa.Float(), nullable=False),
        sa.Column("valor_cbs", sa.Float(), nullable=False),
        sa.Column("valor_is", sa.Float(), nullable=False),
        sa.Column("xml_original", sa.Text(), nullable=True),
        sa.Column("hash_original", sa.String(length=64), nullable=False),
        sa.Column("origem", sa.String(length=40), nullable=True),
        sa.Column("nome_arquivo", sa.String(length=255), nullable=True),
        sa.Column("adaptador", sa.String(length=40), nullable=False),
        sa.Column("importado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["escritorio_id"],
            ["escritorios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("escritorio_id", "chave", name="uq_documento_chave"),
    )
    with op.batch_alter_table("documentos_fiscais", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_documentos_fiscais_chave"), ["chave"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_documentos_fiscais_data_emissao"), ["data_emissao"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_documentos_fiscais_destinatario_cnpj"),
            ["destinatario_cnpj"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_documentos_fiscais_emitente_cnpj"), ["emitente_cnpj"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_documentos_fiscais_empresa_id"), ["empresa_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_documentos_fiscais_escritorio_id"), ["escritorio_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_documentos_fiscais_hash_original"), ["hash_original"], unique=False
        )

    op.create_table(
        "itens_documentos_fiscais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("documento_id", sa.Integer(), nullable=False),
        sa.Column("numero_item", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=60), nullable=True),
        sa.Column("descricao", sa.String(length=255), nullable=True),
        sa.Column("ncm", sa.String(length=8), nullable=True),
        sa.Column("cest", sa.String(length=7), nullable=True),
        sa.Column("codigo_servico", sa.String(length=20), nullable=True),
        sa.Column("unidade", sa.String(length=6), nullable=True),
        sa.Column("quantidade", sa.Float(), nullable=False),
        sa.Column("valor_unitario", sa.Float(), nullable=False),
        sa.Column("valor_total", sa.Float(), nullable=False),
        sa.Column("valor_desconto", sa.Float(), nullable=False),
        sa.Column("valor_frete", sa.Float(), nullable=False),
        sa.Column("valor_seguro", sa.Float(), nullable=False),
        sa.Column("valor_outras", sa.Float(), nullable=False),
        sa.Column("cfop", sa.String(length=4), nullable=True),
        sa.Column("origem_mercadoria", sa.String(length=1), nullable=True),
        sa.Column("cst_icms", sa.String(length=3), nullable=True),
        sa.Column("csosn", sa.String(length=3), nullable=True),
        sa.Column("base_icms", sa.Float(), nullable=False),
        sa.Column("aliquota_icms", sa.Float(), nullable=False),
        sa.Column("valor_icms", sa.Float(), nullable=False),
        sa.Column("base_icms_st", sa.Float(), nullable=False),
        sa.Column("valor_icms_st", sa.Float(), nullable=False),
        sa.Column("valor_fcp", sa.Float(), nullable=False),
        sa.Column("cst_ipi", sa.String(length=2), nullable=True),
        sa.Column("valor_ipi", sa.Float(), nullable=False),
        sa.Column("cst_pis", sa.String(length=2), nullable=True),
        sa.Column("base_pis", sa.Float(), nullable=False),
        sa.Column("aliquota_pis", sa.Float(), nullable=False),
        sa.Column("valor_pis", sa.Float(), nullable=False),
        sa.Column("cst_cofins", sa.String(length=2), nullable=True),
        sa.Column("base_cofins", sa.Float(), nullable=False),
        sa.Column("aliquota_cofins", sa.Float(), nullable=False),
        sa.Column("valor_cofins", sa.Float(), nullable=False),
        sa.Column("valor_iss", sa.Float(), nullable=False),
        sa.Column("codigo_beneficio", sa.String(length=10), nullable=True),
        sa.Column("cst_ibscbs", sa.String(length=3), nullable=True),
        sa.Column("class_trib_ibscbs", sa.String(length=10), nullable=True),
        sa.Column("base_ibscbs", sa.Float(), nullable=False),
        sa.Column("aliquota_ibs_uf", sa.Float(), nullable=False),
        sa.Column("valor_ibs_uf", sa.Float(), nullable=False),
        sa.Column("aliquota_ibs_mun", sa.Float(), nullable=False),
        sa.Column("valor_ibs_mun", sa.Float(), nullable=False),
        sa.Column("municipio_fg_ibs", sa.String(length=7), nullable=True),
        sa.Column("aliquota_cbs", sa.Float(), nullable=False),
        sa.Column("valor_cbs", sa.Float(), nullable=False),
        sa.Column("percentual_reducao_aliquota", sa.Float(), nullable=False),
        sa.Column("aliquota_efetiva", sa.Float(), nullable=False),
        sa.Column("valor_diferido", sa.Float(), nullable=False),
        sa.Column("valor_devolucao_tributo", sa.Float(), nullable=False),
        sa.Column("codigo_credito_presumido", sa.String(length=10), nullable=True),
        sa.Column("valor_credito_presumido", sa.Float(), nullable=False),
        sa.Column("valor_credito_presumido_susp", sa.Float(), nullable=False),
        sa.Column("quantidade_bc_mono", sa.Float(), nullable=False),
        sa.Column("valor_ibs_mono", sa.Float(), nullable=False),
        sa.Column("valor_cbs_mono", sa.Float(), nullable=False),
        sa.Column("valor_ibs_mono_retido", sa.Float(), nullable=False),
        sa.Column("valor_cbs_mono_retido", sa.Float(), nullable=False),
        sa.Column("cst_is", sa.String(length=3), nullable=True),
        sa.Column("class_trib_is", sa.String(length=10), nullable=True),
        sa.Column("base_is", sa.Float(), nullable=False),
        sa.Column("aliquota_is", sa.Float(), nullable=False),
        sa.Column("aliquota_is_especifica", sa.Float(), nullable=False),
        sa.Column("unidade_tributavel_is", sa.String(length=6), nullable=True),
        sa.Column("quantidade_tributavel_is", sa.Float(), nullable=False),
        sa.Column("valor_is", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["documento_id"],
            ["documentos_fiscais.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("documento_id", "numero_item", name="uq_item_documento"),
    )
    with op.batch_alter_table("itens_documentos_fiscais", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_itens_documentos_fiscais_cfop"), ["cfop"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_itens_documentos_fiscais_cst_cofins"), ["cst_cofins"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_itens_documentos_fiscais_cst_ibscbs"), ["cst_ibscbs"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_itens_documentos_fiscais_cst_icms"), ["cst_icms"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_itens_documentos_fiscais_cst_pis"), ["cst_pis"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_itens_documentos_fiscais_documento_id"), ["documento_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_itens_documentos_fiscais_ncm"), ["ncm"], unique=False)

    op.create_table(
        "ajustes_fiscais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("documento_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("campo", sa.String(length=60), nullable=False),
        sa.Column("valor_anterior", sa.Text(), nullable=True),
        sa.Column("valor_novo", sa.Text(), nullable=True),
        sa.Column("origem", sa.String(length=12), nullable=False),
        sa.Column("regra", sa.String(length=120), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("lote", sa.String(length=32), nullable=True),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["documento_id"],
            ["documentos_fiscais.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["itens_documentos_fiscais.id"],
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ajustes_fiscais", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ajustes_fiscais_campo"), ["campo"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ajustes_fiscais_documento_id"), ["documento_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ajustes_fiscais_item_id"), ["item_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ajustes_fiscais_lote"), ["lote"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("ajustes_fiscais", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ajustes_fiscais_lote"))
        batch_op.drop_index(batch_op.f("ix_ajustes_fiscais_item_id"))
        batch_op.drop_index(batch_op.f("ix_ajustes_fiscais_documento_id"))
        batch_op.drop_index(batch_op.f("ix_ajustes_fiscais_campo"))

    op.drop_table("ajustes_fiscais")
    with op.batch_alter_table("itens_documentos_fiscais", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_ncm"))
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_documento_id"))
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_cst_pis"))
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_cst_icms"))
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_cst_ibscbs"))
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_cst_cofins"))
        batch_op.drop_index(batch_op.f("ix_itens_documentos_fiscais_cfop"))

    op.drop_table("itens_documentos_fiscais")
    with op.batch_alter_table("documentos_fiscais", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_hash_original"))
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_escritorio_id"))
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_empresa_id"))
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_emitente_cnpj"))
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_destinatario_cnpj"))
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_data_emissao"))
        batch_op.drop_index(batch_op.f("ix_documentos_fiscais_chave"))

    op.drop_table("documentos_fiscais")
