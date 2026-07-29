"""Livro Razão (F11).

Por conta ou grupo de contas: data, número do lançamento, histórico,
contrapartida(s), débito, crédito e saldo corrente linha a linha.
"""

import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.db.models import Lancamento, Partida, PlanoConta
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    fmt_moeda,
    valor_sinalizado,
)


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

    def gerar(
        self,
        cod_cta: str,
        criterios: FilterCriteria | None = None,
    ) -> tuple[ReportContext, list[LinhaRazao]]:
        """Gera o razão de uma conta específica.

        Args:
            cod_cta: Código da conta a razonar
            criterios: Filtros F7 adicionais

        Returns:
            (contexto, linhas)
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Força o filtro da conta
        criterios.cod_cta_exato = [cod_cta]

        # Busca partidas
        resultados = self.engine.aplicar_lancamentos(criterios)

        # Busca saldo inicial do período
        from sqlalchemy import select

        saldo_inicial = 0.0
        if criterios.dt_ini:
            # Pega o saldo final do período anterior ao dt_ini
            stmt = (
                select(Partida.vl_dc, Partida.ind_dc, Lancamento.dt_lcto)
                .join(Lancamento, Partida.lancamento_id == Lancamento.id)
                .where(
                    Lancamento.ecd_id == self.ecd_id,
                    Partida.cod_cta == cod_cta,
                    Lancamento.dt_lcto < criterios.dt_ini,
                )
            )
            for row in self.session.execute(stmt):
                vl = valor_sinalizado(row.vl_dc, row.ind_dc)
                saldo_inicial += vl

        # Busca nome da conta
        pc = self.session.execute(
            select(PlanoConta).where(
                PlanoConta.ecd_id == self.ecd_id,
                PlanoConta.cod_cta == cod_cta,
            )
        ).scalar_one_or_none()

        nome_cta = pc.nome_cta if pc else cod_cta

        # Monta linhas do razão
        linhas = []
        saldo_corrente = saldo_inicial

        # Agrupa partidas por lançamento para montar contrapartidas
        from collections import defaultdict

        # Primeiro, agrupa todas as partidas por lançamento
        partidas_por_lcto = defaultdict(list)
        for partida, lancamento in resultados:
            partidas_por_lcto[(lancamento.num_lcto, lancamento.dt_lcto)].append(
                (partida, lancamento)
            )

        # Processa em ordem cronológica
        for (num_lcto, dt_lcto), items in sorted(partidas_por_lcto.items()):
            # Pega a partida da conta que estamos razonando
            partida_conta = None
            contrapartidas = []
            hist = ""
            ind_lcto = "N"

            for partida, lancamento in items:
                ind_lcto = lancamento.ind_lcto
                if partida.cod_cta == cod_cta:
                    partida_conta = partida
                    hist = partida.hist or ""
                else:
                    contrapartidas.append(partida.cod_cta)

            if partida_conta is None:
                continue

            vl = valor_sinalizado(partida_conta.vl_dc, partida_conta.ind_dc)
            saldo_corrente += vl

            linhas.append(
                LinhaRazao(
                    data=dt_lcto,
                    num_lcto=num_lcto,
                    historico=hist,
                    contrapartidas=", ".join(sorted(set(contrapartidas))),
                    debito=vl if vl > 0 else 0.0,
                    credito=abs(vl) if vl < 0 else 0.0,
                    saldo_corrente=saldo_corrente,
                    ind_lcto=ind_lcto,
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
        saldo_razao = linhas[-1].saldo_corrente if linhas else 0.0
        div = saldo_razao - saldo_i155
        return {
            "saldo_razao": round(saldo_razao, 2),
            "saldo_i155": round(saldo_i155, 2),
            "divergencia": round(div, 2),
            "status": "OK" if abs(div) < 0.005 else "DIVERGÊNCIA",
        }
