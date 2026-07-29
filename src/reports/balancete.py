"""Balancete de Verificação (F8).

A partir dos saldos periódicos (I155), gera balancete com:
- Colunas: Saldo Inicial, Débitos, Créditos, Saldo Final
- Profundidade configurável
- Conferência automática contra I155 (SI + D − C = SF)
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import PlanoConta
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

        por_conta: dict[str, dict] = defaultdict(lambda: {"si": 0.0, "d": 0.0, "c": 0.0, "sf": 0.0})

        for s in saldos:
            acc = por_conta[s.cod_cta]
            acc["si"] += valor_sinalizado(s.vl_sld_ini, s.ind_dc_ini)
            acc["d"] += s.vl_deb
            acc["c"] += s.vl_cred
            acc["sf"] += valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)

        # Busca plano de contas
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
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
                "cod_cta": ln.cod_cta,
                "nome_cta": ln.nome_cta,
                "nivel": ln.nivel,
                "cod_nat": ln.cod_nat,
                "ind_cta": ln.ind_cta,
                "saldo_inicial": fmt_moeda(saldo_por_natureza(ln.saldo_inicial, ln.cod_nat)),
                "debitos": fmt_moeda(ln.debitos),
                "creditos": fmt_moeda(ln.creditos),
                "saldo_final": fmt_moeda(saldo_por_natureza(ln.saldo_final, ln.cod_nat)),
                "divergencia": fmt_moeda(ln.divergencia) if ln.tem_divergencia else "–",
            }
            for ln in linhas
        ]

    def totais(self, linhas: list[LinhaBalancete]) -> dict:
        """Totais da listagem, somando só as linhas do menor nível presente.

        Somar todas as linhas dobraria cada valor: uma conta sintética já
        agrega as analíticas abaixo dela. As linhas do menor nível são
        subárvores disjuntas entre si e cobrem tudo o que está listado —
        na listagem completa, são as contas de nível 1, e o total bate com
        a soma das analíticas quando o arquivo é consistente (a divergência
        é assunto do :meth:`conferir`, linha a linha).
        """
        if not linhas:
            return {"saldo_inicial": 0.0, "debitos": 0.0, "creditos": 0.0, "saldo_final": 0.0}
        topo = min(ln.nivel for ln in linhas)
        base = [ln for ln in linhas if ln.nivel == topo]
        return {
            "saldo_inicial": round(sum(ln.saldo_inicial for ln in base), 2),
            "debitos": round(sum(ln.debitos for ln in base), 2),
            "creditos": round(sum(ln.creditos for ln in base), 2),
            "saldo_final": round(sum(ln.saldo_final for ln in base), 2),
        }

    def conferir(self, linhas: list[LinhaBalancete]) -> dict:
        """Conferência contra I155: SI + D − C = SF."""
        total_divergencias = sum(1 for ln in linhas if ln.tem_divergencia)
        soma_divergencias = sum(abs(ln.divergencia) for ln in linhas if ln.tem_divergencia)

        return {
            "total_contas": len(linhas),
            "contas_com_divergencia": total_divergencias,
            "soma_divergencias": round(soma_divergencias, 2),
            "status": "OK" if total_divergencias == 0 else "DIVERGÊNCIAS",
        }
