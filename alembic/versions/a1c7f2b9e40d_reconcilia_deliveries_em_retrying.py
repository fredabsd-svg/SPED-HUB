"""reconcilia webhook_deliveries presas em 'retrying'

Revision ID: a1c7f2b9e40d
Revises: 6e470dce13c0
Create Date: 2026-07-30 01:05:00.000000

Até esta versão, toda tentativa de webhook que falhava era marcada
`retrying` e nunca mais tocada.  Só a última tentativa era corrigida para
`failed`.  As anteriores ficavam presas: não eram desfecho do evento, não
eram reenviáveis e ninguém as resolvia.

Três efeitos num banco em uso:

1. A taxa de sucesso do painel contava linha por tentativa, então uma entrega
   que só funcionou na 3ª tentativa valia 1 sucesso em 3 — 33% para uma
   integração que estava entregando.
2. Entrega interrompida por restart/deploy/crash ficava sem desfecho nenhum,
   e o reenvio manual (que procura `failed`) não a via.
3. O histórico crescia com linhas que pareciam em andamento para sempre.

Esta migração não muda schema — `status` é texto livre.  Ela reconcilia os
dados: cada linha `retrying` passa a `superseded` (tentativa encerrada e
superada) quando a entrega lógica dela teve desfecho, e a `failed` quando não
teve — nesse caso o evento não chegou e a linha precisa ficar visível ao
reenvio, que é justamente o que faltava.

A identidade da entrega lógica é `(webhook_id, request_body)`: o
`request_body` carrega o timestamp do evento, então identifica a emissão.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c7f2b9e40d"
down_revision: str | Sequence[str] | None = "6e470dce13c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESFECHOS = ("success", "failed", "retried")


def upgrade() -> None:
    conexao = op.get_bind()

    # Entregas lógicas que já têm desfecho: as tentativas presas delas são
    # apenas histórico.  `request_body` pode ser NULL em linha antiga; o
    # agrupamento por NULL não é confiável em SQL, então essas ficam de fora
    # do "tem desfecho" e caem no ramo conservador (viram `failed`, visíveis
    # ao reenvio) em vez de serem silenciosamente enterradas.
    resolvidas = {
        (linha.webhook_id, linha.request_body)
        for linha in conexao.execute(
            sa.text(
                "SELECT DISTINCT webhook_id, request_body FROM webhook_deliveries "
                "WHERE status IN ('success', 'failed', 'retried') "
                "AND request_body IS NOT NULL"
            )
        )
    }

    presas = conexao.execute(
        sa.text("SELECT id, webhook_id, request_body FROM webhook_deliveries WHERE status = :s"),
        {"s": "retrying"},
    ).all()

    superadas, orfas = [], []
    for linha in presas:
        chave = (linha.webhook_id, linha.request_body)
        if linha.request_body is not None and chave in resolvidas:
            superadas.append(linha.id)
        else:
            orfas.append(linha.id)

    for ids, novo_status in ((superadas, "superseded"), (orfas, "failed")):
        # Em lotes: um `IN` com milhares de itens estoura o limite de
        # parâmetros do SQLite (999) e de drivers de Postgres.
        for inicio in range(0, len(ids), 500):
            lote = ids[inicio : inicio + 500]
            marcadores = ", ".join(f":id{i}" for i in range(len(lote)))
            parametros = {f"id{i}": valor for i, valor in enumerate(lote)}
            parametros["novo"] = novo_status
            conexao.execute(
                sa.text(f"UPDATE webhook_deliveries SET status = :novo WHERE id IN ({marcadores})"),
                parametros,
            )

    # Tentativa encerrada precisa de `concluido_em`, senão o histórico do
    # painel a mostra em andamento para sempre.  `criado_em` é a melhor
    # aproximação disponível: o instante real do fim não foi registrado.
    conexao.execute(
        sa.text(
            "UPDATE webhook_deliveries SET concluido_em = criado_em "
            "WHERE concluido_em IS NULL AND status IN ('superseded', 'failed', 'retried')"
        )
    )


def downgrade() -> None:
    """Devolve as linhas reconciliadas a `retrying`.

    Só as `superseded` voltam: elas existiam exclusivamente nesse estado antes
    da migração.  As que viraram `failed` ficam como estão — reverter
    esconderia do reenvio um evento que de fato não chegou, e perder entrega é
    pior que carregar uma linha a mais no histórico.
    """
    op.get_bind().execute(
        sa.text("UPDATE webhook_deliveries SET status = 'retrying' WHERE status = 'superseded'")
    )
