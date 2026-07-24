"""Livro Diário (F12).

Formato formal com termos de abertura/encerramento, lançamentos numerados
sequencialmente com partidas, totais e conferência.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Lancamento, Partida, PlanoConta
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    fmt_data,
    fmt_moeda,
    valor_sinalizado,
)


@dataclass
class PartidaDiario:
    cod_cta: str
    nome_cta: str
    historico: str
    debito: float = 0.0
    credito: float = 0.0


@dataclass
class LancamentoDiario:
    num_lcto: str
    data: str
    ind_lcto: str
    partidas: list[PartidaDiario] = field(default_factory=list)
    total_debito: float = 0.0
    total_credito: float = 0.0


class LivroDiario:
    """Gerador de Livro Diário."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
    ) -> tuple[ReportContext, list[LancamentoDiario], dict[str, Any]]:
        """Gera o Livro Diário.

        Args:
            criterios: Filtros F7

        Returns:
            (contexto, lancamentos, totais)
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Busca plano de contas para nomes
        plano = {
            c.cod_cta: c.nome_cta
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        # Busca lançamentos com filtros
        resultados = self.engine.aplicar_lancamentos(criterios)

        # Agrupa partidas por lançamento
        from collections import defaultdict

        lcto_map: dict[tuple, dict] = defaultdict(
            lambda: {"partidas": [], "ind_lcto": "N", "dt_lcto": None}
        )

        for partida, lancamento in resultados:
            key = (lancamento.num_lcto, lancamento.dt_lcto)
            lcto_map[key]["partidas"].append(partida)
            lcto_map[key]["ind_lcto"] = lancamento.ind_lcto
            lcto_map[key]["dt_lcto"] = lancamento.dt_lcto

        # Monta lançamentos
        lancamentos: list[LancamentoDiario] = []
        total_debitos = 0.0
        total_creditos = 0.0

        for (num_lcto, dt_lcto), dados in sorted(lcto_map.items()):
            partidas_diario: list[PartidaDiario] = []
            deb_lanc = 0.0
            cred_lanc = 0.0

            for p in dados["partidas"]:
                vl = valor_sinalizado(p.vl_dc, p.ind_dc)
                deb = vl if vl > 0 else 0.0
                cred = abs(vl) if vl < 0 else 0.0

                partidas_diario.append(
                    PartidaDiario(
                        cod_cta=p.cod_cta,
                        nome_cta=plano.get(p.cod_cta, ""),
                        historico=p.hist or "",
                        debito=deb,
                        credito=cred,
                    )
                )
                deb_lanc += deb
                cred_lanc += cred

            lancamentos.append(
                LancamentoDiario(
                    num_lcto=num_lcto,
                    data=fmt_data(dt_lcto),
                    ind_lcto=dados["ind_lcto"],
                    partidas=partidas_diario,
                    total_debito=deb_lanc,
                    total_credito=cred_lanc,
                )
            )
            total_debitos += deb_lanc
            total_creditos += cred_lanc

        totais = {
            "num_lancamentos": len(lancamentos),
            "total_debitos": total_debitos,
            "total_creditos": total_creditos,
        }

        ctx = ReportContext(
            titulo="Livro Diário",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, lancamentos, totais