"""A estrutura do plano de contas conferida contra o balanço publicado (J100).

O I050 é o plano como a empresa o declarou, e o `COD_CTA_SUP` dele às vezes
está errado: uma ECD real, validada e transmitida pelo PVA, pendurava CLIENTES
A RECEBER e a PECLD em APLICAÇÕES FINANCEIRAS e os INVESTIMENTOS em
EMPRÉSTIMOS MÚTUOS (realizável a longo prazo). O PVA não confere o sentido do
superior, só que ele existe.  Seguido à risca, esse plano põe todo o saldo de
clientes no disponível — e daí o balanço, a DFC e a liquidez imediata erram
juntos.

O J100 da mesma ECD estava certo, porque é o balanço que a empresa assinou.
Este módulo acha as analíticas em que os dois divergem, no único caso sem
ambiguidade (ver `correcoes_pelo_balanco_publicado`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Aglutinacao, DemonstracaoContabil, LinhaDemonstracao, PlanoConta


def correcoes_pelo_balanco_publicado(
    session: Session, ecd_id: int, plano: Mapping[str, Any]
) -> dict[str, tuple[str | None, str]]:
    """`{conta: (superior no I050, superior no J100)}` das analíticas divergentes.

    Só corrige quando não há o que interpretar:

    * a conta é analítica e o I052 a aglutina **no próprio código** (é o
      caso de quem publica o balanço conta a conta);
    * o J100 tem uma linha de detalhe com esse código, e o superior dela é
      uma **sintética do próprio plano, da mesma natureza**;
    * o superior do I050 é outro.

    Duas linhas de detalhe da mesma conta com superiores diferentes (dois
    J005 que discordam) não corrigem nada.
    """
    linhas = session.execute(
        select(LinhaDemonstracao.cod_agl, LinhaDemonstracao.cod_agl_sup)
        .join(DemonstracaoContabil)
        .where(
            DemonstracaoContabil.ecd_id == ecd_id,
            LinhaDemonstracao.registro == "J100",
            LinhaDemonstracao.ind_cod_agl == "D",
        )
    ).all()
    if not linhas:
        return {}

    aglutinacoes: dict[str, set[str]] = {}
    for cod_cta, cod_agl in session.execute(
        select(PlanoConta.cod_cta, Aglutinacao.cod_agl)
        .join(Aglutinacao, Aglutinacao.plano_conta_id == PlanoConta.id)
        .where(PlanoConta.ecd_id == ecd_id)
    ):
        aglutinacoes.setdefault(cod_cta, set()).add(cod_agl)

    propostas: dict[str, set[str]] = {}
    for cod_agl, superior in linhas:
        conta = plano.get(cod_agl)
        destino = plano.get(superior) if superior else None
        if (
            conta is None
            or destino is None
            or conta.ind_cta != "A"
            or destino.ind_cta != "S"
            or destino.cod_nat != conta.cod_nat
            or aglutinacoes.get(cod_agl) != {cod_agl}
        ):
            continue
        propostas.setdefault(cod_agl, set()).add(superior)

    return {
        cod: (plano[cod].cod_cta_sup, next(iter(superiores)))
        for cod, superiores in propostas.items()
        if len(superiores) == 1 and plano[cod].cod_cta_sup not in superiores
    }
