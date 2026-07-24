"""Balancete de Verificação (F8).

A partir dos saldos periódicos (I155), gera balancete com:
- Colunas: Saldo Inicial, Débitos, Créditos, Saldo Final
- Profundidade configurável
- Conferência automática contra I155 (SI + D − C = SF)
"""

import datetime
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from src.db.models import PlanoConta, SaldoPeriodico
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    fmt_moeda,
    saldo_por_natureza,
    valor_sinalizado,
)


@dataclass
class LinhaBalancete:
    """Uma linha do balancete."""

    cod_cta: str
    nome_cta: str
    nivel: int
    cod_nat: str
    ind_cta: str
    saldo_inicial: float = 0.0
    debitos: float = 0.0
    creditos: float = 0.0
    saldo_final: float = 0.0
    # Conferência
    saldo_calculado: float = 0.0
    divergencia: float = 0.0
    tem_divergencia: bool = False


class Balancete:
    """Gerador de Balancete de Verificação."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        nivel_max: int | None = None,
        apenas_sinteticas: bool = False,
    ) -> tuple[ReportContext, list[LinhaBalancete]]:
        """Gera o balancete.

        Args:
            criterios: Filtros F7 (None = sem filtro)
            nivel_max: Profundidade máxima (None = todas)
            apenas_sinteticas: Se True, apenas contas sintéticas

        Returns:
            (contexto, linhas)
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Busca saldos com filtros
        saldos = self.engine.aplicar_saldos(criterios)

        # Agrupa por conta (soma todos os períodos)
        from collections import defaultdict

        por_conta: dict[str, dict] = defaultdict(
            lambda: {"si": 0.0, "d": 0.0, "c": 0.0, "sf": 0.0}
        )

        for s in saldos:
            acc = por_conta[s.cod_cta]
            acc["si"] += valor_sinalizado(s.vl_sld_ini, s.ind_dc_ini)
            acc["d"] += s.vl_deb
            acc["c"] += s.vl_cred
            acc["sf"] += valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)

        # Busca plano de contas
        plano = {
            c.cod_cta: c
            for c in self.session.query(PlanoConta)
            .filter(PlanoConta.ecd_id == self.ecd_id)
            .all()
        }

        # Monta linhas
        linhas = []
        for cod_cta in sorted(por_conta.keys()):
            pc = plano.get(cod_cta)
            if pc is None:
                continue

            # Filtro de nível
            if nivel_max is not None and pc.nivel > nivel_max:
                continue
            if apenas_sinteticas and pc.ind_cta != "S":
                continue

            acc = por_conta[cod_cta]
            si = acc["si"]
            d = acc["d"]
            c = acc["c"]
            sf = acc["sf"]
            sc = si + d - c  # saldo calculado
            div = sf - sc

            linhas.append(
                LinhaBalancete(
                    cod_cta=cod_cta,
                    nome_cta=pc.nome_cta,
                    nivel=pc.nivel,
                    cod_nat=pc.cod_nat,
                    ind_cta=pc.ind_cta,
                    saldo_inicial=si,
                    debitos=d,
                    creditos=c,
                    saldo_final=sf,
                    saldo_calculado=sc,
                    divergencia=div,
                    tem_divergencia=abs(div) > 0.005,
                )
            )

        # Contexto
        ctx = ReportContext(
            titulo="Balancete de Verificação",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas

    def to_dict(self, linhas: list[LinhaBalancete]) -> list[dict]:
        """Converte linhas para dicionários (exportação)."""
        return [
            {
                "cod_cta": l.cod_cta,
                "nome_cta": l.nome_cta,
                "nivel": l.nivel,
                "cod_nat": l.cod_nat,
                "ind_cta": l.ind_cta,
                "saldo_inicial": fmt_moeda(saldo_por_natureza(l.saldo_inicial, l.cod_nat)),
                "debitos": fmt_moeda(l.debitos),
                "creditos": fmt_moeda(l.creditos),
                "saldo_final": fmt_moeda(saldo_por_natureza(l.saldo_final, l.cod_nat)),
                "divergencia": fmt_moeda(l.divergencia) if l.tem_divergencia else "–",
            }
            for l in linhas
        ]

    def conferir(self, linhas: list[LinhaBalancete]) -> dict:
        """Conferência contra I155: SI + D − C = SF."""
        total_divergencias = sum(1 for l in linhas if l.tem_divergencia)
        soma_divergencias = sum(abs(l.divergencia) for l in linhas if l.tem_divergencia)

        return {
            "total_contas": len(linhas),
            "contas_com_divergencia": total_divergencias,
            "soma_divergencias": round(soma_divergencias, 2),
            "status": "OK" if total_divergencias == 0 else "DIVERGÊNCIAS",
        }