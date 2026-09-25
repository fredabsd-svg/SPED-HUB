"""Plano de contas da ECD — o I050 com referencial (I051) e aglutinação (I052).

É a extração do plano **como a escrituração o declara**: a ordem e o recuo
seguem o `COD_CTA_SUP` do I050, sem a correção que os demonstrativos fazem
pelo balanço publicado. Onde o J100 agrupa a conta noutra sintética, a linha
diz qual — é justamente o que precisa ser acertado na origem.

Os critérios de conta do filtro (natureza, nível, código, nome) escolhem as
linhas; os de valor e período não se aplicam a um cadastro.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Aglutinacao, ContaReferencial, PlanoConta
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import ReportContext
from src.reports.saldos import Hierarquia

NATUREZAS = {
    "01": "Ativo",
    "02": "Passivo",
    "03": "Patrimônio líquido",
    "04": "Resultado",
    "05": "Compensação",
    "09": "Outras",
}


@dataclass
class LinhaPlano:
    cod_cta: str
    nome_cta: str
    nivel: int
    ind_cta: str  # S (sintética) ou A (analítica)
    cod_nat: str
    natureza: str
    cod_cta_sup: str
    referencial: str = ""
    aglutinacao: str = ""
    # Superior no balanço publicado (J100), quando diverge do I050.
    superior_publicado: str = ""


class PlanoDeContas:
    """Gerador do relatório do plano de contas."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _por_conta(self, modelo, coluna) -> dict[str, list[str]]:
        resultado: dict[str, list[str]] = {}
        for cod_cta, valor in self.session.execute(
            select(PlanoConta.cod_cta, coluna)
            .join(modelo, modelo.plano_conta_id == PlanoConta.id)
            .where(PlanoConta.ecd_id == self.ecd_id)
            .order_by(modelo.id)
        ):
            if valor and valor not in resultado.setdefault(cod_cta, []):
                resultado[cod_cta].append(valor)
        return resultado

    def gerar(
        self, criterios: FilterCriteria | None = None
    ) -> tuple[ReportContext, list[LinhaPlano], dict]:
        """Linhas do plano, na ordem do I050, e os totais do cadastro."""
        criterios = criterios or FilterCriteria()
        plano = self.engine.plano()
        declarada = Hierarquia.do_plano(plano)
        selecionadas = self.engine.contas_selecionadas(criterios)
        referenciais = self._por_conta(ContaReferencial, ContaReferencial.cod_cta_ref)
        aglutinacoes = self._por_conta(Aglutinacao, Aglutinacao.cod_agl)
        correcoes = self.engine.correcoes_de_superior()

        linhas = [
            LinhaPlano(
                cod_cta=cod,
                nome_cta=plano[cod].nome_cta,
                nivel=plano[cod].nivel,
                ind_cta=plano[cod].ind_cta,
                cod_nat=plano[cod].cod_nat,
                natureza=NATUREZAS.get(plano[cod].cod_nat, plano[cod].cod_nat),
                cod_cta_sup=plano[cod].cod_cta_sup or "",
                referencial=", ".join(referenciais.get(cod, [])),
                aglutinacao=", ".join(aglutinacoes.get(cod, [])),
                superior_publicado=correcoes[cod][1] if cod in correcoes else "",
            )
            for cod in declarada.em_ordem()
            if cod in selecionadas
        ]

        analiticas = [ln for ln in linhas if ln.ind_cta == "A"]
        totais = {
            "total": len(linhas),
            "sinteticas": sum(1 for ln in linhas if ln.ind_cta == "S"),
            "analiticas": len(analiticas),
            "por_natureza": {
                NATUREZAS.get(cod, cod): quantidade
                for cod, quantidade in sorted(Counter(ln.cod_nat for ln in linhas).items())
            },
            "analiticas_sem_referencial": [
                ln.cod_cta
                for ln in analiticas
                if not ln.referencial and ln.cod_nat in ("01", "02", "03", "04")
            ],
            "nivel_maximo": max((ln.nivel for ln in linhas), default=0),
            "divergencias_publicado": sum(1 for ln in linhas if ln.superior_publicado),
        }
        ctx = ReportContext(
            titulo="Plano de Contas",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )
        return ctx, linhas, totais
