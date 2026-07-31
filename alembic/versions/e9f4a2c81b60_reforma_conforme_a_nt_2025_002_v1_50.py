"""reforma: os grupos da NT 2025.002 v1.50, cada um onde a NT o põe

O leitor procurava redução, diferimento, devolução, crédito presumido e
monofásico como filhos diretos de `gIBSCBS`. A NT nunca os pôs ali: os três
primeiros existem uma vez dentro de cada destinação (`gIBSUF`, `gIBSMun`,
`gCBS`), o crédito presumido fica em `gCredPresOper` — irmão de `gIBSCBS` —, e
o monofásico está a dois níveis de profundidade desde a v1.50, que separou ad
rem de ad valorem. Procurar no lugar errado não levanta erro: devolve zero.

Por isso as colunas antigas são **removidas** e não renomeadas. Renomear
sugeriria que o conteúdo migra; não migra, porque nunca houve conteúdo — toda
linha gravada por essas colunas é zero, escrita por uma leitura que não
encontrava nada. Quem quiser o valor real reimporta o XML, que a primeira
camada guardou intacto exatamente para isto.

`municipio_fg_ibs` muda de tabela pelo mesmo motivo: a NT o põe no `ide`
(campo B12a), não no imposto do item.

Revision ID: e9f4a2c81b60
Revises: b7d3f1e620ca
"""

import sqlalchemy as sa

from alembic import op

revision = "e9f4a2c81b60"
down_revision = "b7d3f1e620ca"
branch_labels = None
depends_on = None


# Por destinação: a NT repete `gRed`, `gDif` e `gDevTrib` dentro de `gIBSUF`,
# `gIBSMun` e `gCBS`, com as mesmas tags em cada um.
POR_DESTINACAO = ("ibs_uf", "ibs_mun", "cbs")

NOVAS_DO_ITEM = [
    *(f"percentual_reducao_{d}" for d in POR_DESTINACAO),
    *(f"aliquota_efetiva_{d}" for d in POR_DESTINACAO),
    *(f"valor_diferido_{d}" for d in POR_DESTINACAO),
    *(f"valor_devolucao_{d}" for d in POR_DESTINACAO),
    "percentual_credito_presumido_ibs",
    "valor_credito_presumido_ibs",
    "valor_credito_presumido_ibs_susp",
    "percentual_credito_presumido_cbs",
    "valor_credito_presumido_cbs",
    "valor_credito_presumido_cbs_susp",
    "valor_bc_mono",
    "valor_ibs_mono_reten",
    "valor_cbs_mono_reten",
]

ANTIGAS_DO_ITEM = [
    "percentual_reducao_aliquota",
    "aliquota_efetiva",
    "valor_diferido",
    "valor_devolucao_tributo",
    "valor_credito_presumido",
    "valor_credito_presumido_susp",
    "municipio_fg_ibs",
]


def upgrade() -> None:
    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        for nome in NOVAS_DO_ITEM:
            lote.add_column(sa.Column(nome, sa.Float(), nullable=False, server_default="0"))
        for nome in ANTIGAS_DO_ITEM:
            lote.drop_column(nome)

    with op.batch_alter_table("documentos_fiscais") as lote:
        lote.add_column(sa.Column("municipio_fg_ibs", sa.String(length=7), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documentos_fiscais") as lote:
        lote.drop_column("municipio_fg_ibs")

    with op.batch_alter_table("itens_documentos_fiscais") as lote:
        lote.add_column(sa.Column("municipio_fg_ibs", sa.String(length=7), nullable=True))
        for nome in ANTIGAS_DO_ITEM:
            if nome == "municipio_fg_ibs":
                continue
            lote.add_column(sa.Column(nome, sa.Float(), nullable=False, server_default="0"))
        for nome in NOVAS_DO_ITEM:
            lote.drop_column(nome)
