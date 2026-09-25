"""Livro Razão (F11).

Por conta ou grupo de contas: data, número do lançamento, histórico,
contrapartida(s), débito, crédito e saldo corrente linha a linha.

O saldo corrente parte do saldo inicial do I155 (o do período em que o
razão começa) e soma uma linha por partida — duas partidas do mesmo
lançamento na mesma conta são duas linhas. A ordem é a da data e, no mesmo
dia, a do número do lançamento lido como número.
"""

import dataclasses
import datetime
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Lancamento, Partida, PlanoConta, SaldoPeriodico
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    chave_num_lcto,
    fmt_moeda,
    valor_sinalizado,
)
from src.reports.saldos import saldos_por_periodo

# Quantos ids por `IN` ao buscar as contrapartidas: abaixo do limite de
# parâmetros do SQLite e do que o Postgres aceita sem degradar o plano.
_LOTE_IN = 500


@dataclass
class LinhaRazao:
    """Uma linha do livro razão."""

    data: datetime.date
    num_lcto: str
    historico: str
    contrapartidas: str  # contas da contrapartida
    debito: float = 0.0
    credito: float = 0.0
    saldo_corrente: float = 0.0
    ind_lcto: str = "N"


class Razao:
    """Gerador de Livro Razão."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)
        # Saldo da conta antes da primeira linha do último `gerar`.
        self.saldo_inicial = 0.0

    def _contas_dos_lancamentos(self, ids: set[int]) -> dict[int, set[str]]:
        """As contas de todas as partidas de cada lançamento.

        A consulta do razão só traz as partidas da conta razonada; a
        contrapartida está nas outras partidas do mesmo lançamento, que é
        preciso buscar à parte — sem isso a coluna saía sempre vazia.
        """
        contas: dict[int, set[str]] = defaultdict(set)
        lista = sorted(ids)
        for inicio in range(0, len(lista), _LOTE_IN):
            lote = lista[inicio : inicio + _LOTE_IN]
            for lancamento_id, cod_cta in self.session.execute(
                select(Partida.lancamento_id, Partida.cod_cta).where(
                    Partida.lancamento_id.in_(lote)
                )
            ):
                contas[lancamento_id].add(cod_cta)
        return contas

    def _saldo_inicial(self, cod_cta: str, criterios: FilterCriteria) -> float:
        """Saldo da conta no início do razão, a partir do I155.

        Sem data inicial, é o SI do primeiro período. Com data inicial, é o
        SI do período em que ela cai, mais as partidas desse período
        anteriores a ela. Sem I155 para a conta, parte de zero e soma as
        partidas anteriores à data.
        """
        stmt = select(SaldoPeriodico).where(
            SaldoPeriodico.ecd_id == self.ecd_id, SaldoPeriodico.cod_cta == cod_cta
        )
        if criterios.cod_ccus:
            stmt = stmt.where(SaldoPeriodico.cod_ccus.in_(criterios.cod_ccus))
        periodos = sorted(
            (
                (periodo, saldo)
                for (_cod, periodo), saldo in saldos_por_periodo(
                    self.session.execute(stmt).scalars()
                ).items()
            ),
            key=lambda item: item[0],
        )

        if criterios.dt_ini is None:
            return periodos[0][1].si if periodos else 0.0

        base, desde = 0.0, None
        if periodos:
            anteriores = [(p, s) for p, s in periodos if p[0] <= criterios.dt_ini]
            (inicio, _fim), saldo = anteriores[-1] if anteriores else periodos[0]
            base, desde = saldo.si, inicio

        stmt = (
            select(Partida.vl_dc, Partida.ind_dc)
            .join(Lancamento, Partida.lancamento_id == Lancamento.id)
            .where(
                Lancamento.ecd_id == self.ecd_id,
                Partida.cod_cta == cod_cta,
                Lancamento.dt_lcto < criterios.dt_ini,
            )
        )
        if desde is not None:
            stmt = stmt.where(Lancamento.dt_lcto >= desde)
        if criterios.cod_ccus:
            stmt = stmt.where(Partida.cod_ccus.in_(criterios.cod_ccus))
        for vl_dc, ind_dc in self.session.execute(stmt):
            base += valor_sinalizado(vl_dc, ind_dc)
        return base

    def gerar(
        self,
        cod_cta: str,
        criterios: FilterCriteria | None = None,
    ) -> tuple[ReportContext, list[LinhaRazao]]:
        """Gera o razão de uma conta específica.

        Args:
            cod_cta: Código da conta a razonar
            criterios: Filtros F7 adicionais (não são alterados)

        Returns:
            (contexto, linhas)
        """
        # Cópia: gravar a conta nos critérios de quem chamou fazia o próximo
        # relatório com os mesmos critérios sair filtrado por ela.
        criterios = dataclasses.replace(criterios or FilterCriteria(), cod_cta_exato=[cod_cta])

        resultados = self.engine.aplicar_lancamentos(criterios)
        contas_por_lcto = self._contas_dos_lancamentos({lanc.id for _p, lanc in resultados})
        self.saldo_inicial = self._saldo_inicial(cod_cta, criterios)

        pc = self.session.execute(
            select(PlanoConta).where(
                PlanoConta.ecd_id == self.ecd_id,
                PlanoConta.cod_cta == cod_cta,
            )
        ).scalar_one_or_none()
        nome_cta = pc.nome_cta if pc else cod_cta

        # Uma linha por partida, em ordem de data e de número do lançamento.
        ordenados = sorted(
            resultados,
            key=lambda item: (
                item[1].dt_lcto,
                chave_num_lcto(item[1].num_lcto),
                item[1].id,
                item[0].id,
            ),
        )

        linhas = []
        saldo_corrente = self.saldo_inicial
        for partida, lancamento in ordenados:
            vl = valor_sinalizado(partida.vl_dc, partida.ind_dc)
            saldo_corrente += vl
            contrapartidas = sorted(contas_por_lcto.get(lancamento.id, set()) - {cod_cta})
            linhas.append(
                LinhaRazao(
                    data=lancamento.dt_lcto,
                    num_lcto=lancamento.num_lcto,
                    historico=partida.hist or "",
                    contrapartidas=", ".join(contrapartidas),
                    debito=vl if vl > 0 else 0.0,
                    credito=abs(vl) if vl < 0 else 0.0,
                    saldo_corrente=round(saldo_corrente, 2),
                    ind_lcto=lancamento.ind_lcto,
                )
            )

        # Contexto
        ctx = ReportContext(
            titulo=f"Livro Razão — {cod_cta} — {nome_cta}",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas

    def to_dict(self, linhas: list[LinhaRazao]) -> list[dict]:
        """Converte linhas para dicionários."""
        return [
            {
                "data": ln.data.isoformat(),
                "num_lcto": ln.num_lcto,
                "historico": ln.historico,
                "contrapartidas": ln.contrapartidas,
                "debito": fmt_moeda(ln.debito) if ln.debito else "–",
                "credito": fmt_moeda(ln.credito) if ln.credito else "–",
                "saldo_corrente": fmt_moeda(ln.saldo_corrente),
                "ind_lcto": ln.ind_lcto,
            }
            for ln in linhas
        ]

    def conferir_saldo_final(self, linhas: list[LinhaRazao], saldo_i155: float) -> dict:
        """Compara o saldo final do razão com o I155."""
        saldo_razao = linhas[-1].saldo_corrente if linhas else self.saldo_inicial
        div = saldo_razao - saldo_i155
        return {
            "saldo_razao": round(saldo_razao, 2),
            "saldo_i155": round(saldo_i155, 2),
            "divergencia": round(div, 2),
            "status": "OK" if abs(div) < 0.005 else "DIVERGÊNCIA",
        }
