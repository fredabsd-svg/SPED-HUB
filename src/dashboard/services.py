"""Serviços de dados para o Dashboard Web.

Fornece dados agregados para KPIs, gráficos e visualizações interativas.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    ECD,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.reports.base import valor_sinalizado, saldo_por_natureza


@dataclass
class KPICard:
    titulo: str
    valor: float
    formato: str = "moeda"  # moeda, percentual, inteiro
    tendencia: str = "neutro"  # up, down, neutro
    descricao: str = ""
    icone: str = ""


@dataclass
class DashboardData:
    empresa_nome: str = ""
    empresa_cnpj: str = ""
    periodo: str = ""
    ecd_id: int = 0
    kpis: list[KPICard] = field(default_factory=list)
    ativo_total: float = 0.0
    passivo_total: float = 0.0
    pl_total: float = 0.0
    receita_liquida: float = 0.0
    resultado_liquido: float = 0.0
    num_lancamentos: int = 0
    num_contas: int = 0


class DashboardService:
    """Serviço que agrega dados de múltiplos relatórios para o dashboard."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def get_dashboard_data(self) -> DashboardData:
        """Coleta todos os dados para o dashboard principal."""
        ecd = self.session.get(ECD, self.ecd_id)
        if not ecd:
            return DashboardData()

        empresa = self.session.get(Empresa, ecd.empresa_id)

        # ── Balanço Patrimonial ──
        balanco = BalancoPatrimonial(self.session, self.ecd_id)
        _, grupos, totais = balanco.gerar()

        # ── DRE ──
        dre = DRE(self.session, self.ecd_id)
        _, linhas_dre, totais_dre = dre.gerar()

        # ── Contagens ──
        num_lancs = self.session.execute(
            select(func.count(Lancamento.id)).where(Lancamento.ecd_id == self.ecd_id)
        ).scalar() or 0

        num_contas = self.session.execute(
            select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == self.ecd_id)
        ).scalar() or 0

        # ── KPIs ──
        kpis = self._calcular_kpis(totais, totais_dre, num_lancs, num_contas)

        return DashboardData(
            empresa_nome=empresa.nome if empresa else "",
            empresa_cnpj=empresa.cnpj if empresa else "",
            periodo=f"{ecd.dt_ini} a {ecd.dt_fin}",
            ecd_id=self.ecd_id,
            kpis=kpis,
            ativo_total=totais["ativo"],
            passivo_total=totais["passivo"],
            pl_total=totais["pl"],
            receita_liquida=totais_dre.get("receita_bruta", 0.0),
            resultado_liquido=totais_dre.get("resultado_liquido", 0.0),
            num_lancamentos=num_lancs,
            num_contas=num_contas,
        )

    def _calcular_kpis(
        self,
        totais: dict,
        totais_dre: dict,
        num_lancs: int,
        num_contas: int,
    ) -> list[KPICard]:
        """Calcula indicadores financeiros para os cards do dashboard."""
        kpis = []

        ativo = totais["ativo"]
        passivo = totais["passivo"]
        pl = totais["pl"]
        passivo_pl = passivo + pl
        receita_bruta = totais_dre.get("receita_bruta", 0.0)
        resultado = totais_dre.get("resultado_liquido", 0.0)

        # 1. Ativo Total
        kpis.append(KPICard(
            titulo="Ativo Total",
            valor=ativo,
            formato="moeda",
            tendencia="neutro",
            descricao="Total de bens e direitos",
            icone="💰",
        ))

        # 2. Patrimônio Líquido
        kpis.append(KPICard(
            titulo="Patrimônio Líquido",
            valor=pl,
            formato="moeda",
            tendencia="up" if pl > 0 else "down",
            descricao="Capital próprio da empresa",
            icone="🏛️",
        ))

        # 3. Endividamento (Passivo/Ativo)
        if ativo > 0:
            endividamento = (passivo / ativo) * 100
            kpis.append(KPICard(
                titulo="Endividamento",
                valor=endividamento,
                formato="percentual",
                tendencia="down" if endividamento < 60 else "up",
                descricao="Passivo ÷ Ativo Total",
                icone="📊",
            ))

        # 4. Resultado Líquido
        kpis.append(KPICard(
            titulo="Resultado Líquido",
            valor=resultado,
            formato="moeda",
            tendencia="up" if resultado > 0 else "down",
            descricao="Lucro ou prejuízo do período",
            icone="📈",
        ))

        # 5. Margem Líquida
        if receita_bruta > 0:
            margem = (resultado / receita_bruta) * 100
            kpis.append(KPICard(
                titulo="Margem Líquida",
                valor=margem,
                formato="percentual",
                tendencia="up" if margem > 10 else "neutro",
                descricao="Resultado ÷ Receita Bruta",
                icone="🎯",
            ))

        # 6. Lançamentos
        kpis.append(KPICard(
            titulo="Lançamentos",
            valor=num_lancs,
            formato="inteiro",
            tendencia="neutro",
            descricao="Total de lançamentos contábeis",
            icone="📝",
        ))

        return kpis

    def get_evolucao_patrimonial(self) -> dict:
        """Dados para gráfico de evolução patrimonial (Ativo vs Passivo+PL)."""
        # Busca saldos agregados por período
        saldos = self.session.execute(
            select(SaldoPeriodico)
            .where(SaldoPeriodico.ecd_id == self.ecd_id)
            .order_by(SaldoPeriodico.dt_ini)
        ).scalars().all()

        # Agrupa por período
        periodos: dict[str, dict[str, float]] = {}
        for s in saldos:
            chave = s.dt_ini.isoformat()
            if chave not in periodos:
                periodos[chave] = {"ativo": 0.0, "passivo": 0.0, "pl": 0.0}

            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)

            # Busca natureza da conta
            conta = self.session.execute(
                select(PlanoConta).where(
                    PlanoConta.ecd_id == self.ecd_id,
                    PlanoConta.cod_cta == s.cod_cta,
                )
            ).scalar_one_or_none()

            if conta:
                if conta.cod_nat == "01":
                    periodos[chave]["ativo"] += saldo_por_natureza(vl, "01")
                elif conta.cod_nat == "02":
                    periodos[chave]["passivo"] += saldo_por_natureza(vl, "02")
                elif conta.cod_nat == "03":
                    periodos[chave]["pl"] += saldo_por_natureza(vl, "03")

        labels = sorted(periodos.keys())
        ativo_series = [periodos[p]["ativo"] for p in labels]
        passivo_pl_series = [periodos[p]["passivo"] + periodos[p]["pl"] for p in labels]

        return {
            "labels": labels,
            "ativo": ativo_series,
            "passivo_pl": passivo_pl_series,
        }

    def get_composicao_ativo(self) -> dict:
        """Dados para gráfico de pizza da composição do ativo."""
        balanco = BalancoPatrimonial(self.session, self.ecd_id)
        _, grupos, _ = balanco.gerar()

        # Agrupa contas de ativo por nível 2 (subgrupos), somando contas filhas
        # Primeiro mapeia hierarquia: cod_cta -> cod_cta_sup
        from sqlalchemy import select
        from src.db.models import PlanoConta
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        # Para cada conta de ativo, sobe até o nível 2
        categorias: dict[str, float] = {}
        for linha in grupos["ativo"]:
            if linha.nivel <= 2:
                nome = linha.nome_cta[:30]
                categorias[nome] = categorias.get(nome, 0.0) + linha.saldo_atual
            else:
                # Sobe até o nível 2
                cod = linha.cod_cta
                pc = plano.get(cod)
                while pc and pc.nivel > 2 and pc.cod_cta_sup:
                    pc = plano.get(pc.cod_cta_sup)
                if pc:
                    nome = pc.nome_cta[:30]
                    categorias[nome] = categorias.get(nome, 0.0) + linha.saldo_atual

        # Ordena por valor decrescente
        sorted_cats = sorted(categorias.items(), key=lambda x: abs(x[1]), reverse=True)

        return {
            "labels": [c[0] for c in sorted_cats],
            "valores": [c[1] for c in sorted_cats],
        }

    def get_dre_waterfall(self) -> dict:
        """Dados para gráfico waterfall da DRE."""
        dre = DRE(self.session, self.ecd_id)
        _, linhas, _ = dre.gerar()

        labels = []
        valores = []
        for l in linhas:
            if l.tipo in ("step", "subtotal", "total"):
                labels.append(l.descricao)
                valores.append(l.valor_atual)

        return {
            "labels": labels,
            "valores": valores,
        }

    def get_ecds_disponiveis(self) -> list[dict]:
        """Lista ECDs disponíveis para seleção."""
        ecds = self.session.execute(
            select(ECD, Empresa.nome)
            .join(Empresa)
            .order_by(ECD.importado_em.desc())
        ).all()

        return [
            {
                "id": ecd.id,
                "empresa": nome,
                "periodo": f"{ecd.dt_ini} a {ecd.dt_fin}",
                "importado_em": ecd.importado_em.isoformat() if ecd.importado_em else "",
                "leiaute": ecd.leiaute,
            }
            for ecd, nome in ecds
        ]