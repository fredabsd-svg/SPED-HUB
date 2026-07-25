"""Demonstração do Resultado do Exercício — DRE (F10).

Estrutura de degraus com mapeamento configurável por empresa.
Default: estrutura analítica da ITG 1000 / NBC TG 26.

Degraus:
  1. Receita Operacional Bruta
  2. (-) Deduções da Receita
  3. = Receita Operacional Líquida
  4. (-) Custos (CMV/CSP)
  5. = Lucro Bruto
  6. (-) Despesas Operacionais
  7. (+) Receitas Financeiras
  8. (-) Despesas Financeiras
  9. = Resultado Antes IRPJ/CSLL
  10. (-) IRPJ/CSLL
  11. = Resultado Líquido do Exercício
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Mapeamento, PlanoConta, SaldoPeriodico, SaldoResultado
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    fmt_moeda,
    valor_sinalizado,
)


@dataclass
class LinhaDRE:
    tipo: str  # step, detail, subtotal, total
    descricao: str
    valor_atual: float = 0.0
    valor_anterior: float = 0.0
    ordem: int = 0


# ── Estrutura default da DRE ──
DRE_DEFAULT = [
    {"tipo": "step", "descricao": "Receita Operacional Bruta", "categoria": "receita_bruta", "sinal": 1},
    {"tipo": "step", "descricao": "(-) Deduções da Receita", "categoria": "deducoes", "sinal": -1},
    {"tipo": "subtotal", "descricao": "= Receita Operacional Líquida", "categoria": None, "sinal": 0},
    {"tipo": "step", "descricao": "(-) Custos", "categoria": "custos", "sinal": -1},
    {"tipo": "subtotal", "descricao": "= Lucro Bruto", "categoria": None, "sinal": 0},
    {"tipo": "step", "descricao": "(-) Despesas Operacionais", "categoria": "despesas_operacionais", "sinal": -1},
    {"tipo": "step", "descricao": "(+) Receitas Financeiras", "categoria": "receitas_financeiras", "sinal": 1},
    {"tipo": "step", "descricao": "(-) Despesas Financeiras", "categoria": "despesas_financeiras", "sinal": -1},
    {"tipo": "subtotal", "descricao": "= Resultado Antes IRPJ/CSLL", "categoria": None, "sinal": 0},
    {"tipo": "step", "descricao": "(-) IRPJ / CSLL", "categoria": "irpj_csll", "sinal": -1},
    {"tipo": "total", "descricao": "= Resultado Líquido do Exercício", "categoria": None, "sinal": 0},
]


class DRE:
    """Gerador de DRE."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_mapeamentos(self, empresa_id: int) -> dict[str, list[str]]:
        """Carrega mapeamentos DRE da empresa ou usa defaults."""
        maps = list(self.session.execute(
            select(Mapeamento)
            .where(
                Mapeamento.empresa_id == empresa_id,
                Mapeamento.tipo == "dre",
            )
            .order_by(Mapeamento.ordem)
        ).scalars())

        if maps:
            result: dict[str, list[str]] = {}
            for m in maps:
                result.setdefault(m.categoria, []).append(m.cod_cta)
            return result

        # Default: classifica por nome da conta
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        result: dict[str, list[str]] = {
            "receita_bruta": [],
            "deducoes": [],
            "custos": [],
            "despesas_operacionais": [],
            "receitas_financeiras": [],
            "despesas_financeiras": [],
            "irpj_csll": [],
        }

        for cod_cta, pc in plano.items():
            if pc.cod_nat != "04":
                continue
            nome = pc.nome_cta.upper()

            # Ordem importa: mais específico primeiro
            if any(t in nome for t in ["IRPJ", "CSLL", "IMPOSTO DE RENDA", "CONTRIBUIÇÃO SOCIAL"]):
                result["irpj_csll"].append(cod_cta)
            elif any(t in nome for t in ["DESPESAS FINANC", "DESPESA FINANC", "JUROS P", "JURO P", "VARIAÇÃO MONETÁRIA PASSIVA", "DESCONTO CONCED"]):
                result["despesas_financeiras"].append(cod_cta)
            elif any(t in nome for t in ["RECEITAS FINANC", "RECEITA FINANC", "JUROS A", "JURO A", "DESCONTO OBT", "RENDIMENTO", "VARIAÇÃO MONETÁRIA ATIVA"]):
                result["receitas_financeiras"].append(cod_cta)
            elif any(t in nome for t in ["DEVOLU", "CANCEL", "ABATIMENTO", "IMPOSTO S/", "PIS", "COFINS", "ICMS"]):
                result["deducoes"].append(cod_cta)
            elif any(t in nome for t in ["CMV", "CUSTO", "CSP"]):
                result["custos"].append(cod_cta)
            elif any(t in nome for t in ["DESPESAS ADMIN", "DESPESA ADMIN", "DESPESAS GERAL", "DESPESA GERAL", "DESPESAS COM", "DESPESA COM", "ADMINISTRATIVA", "SALÁRIO", "VENCIMENTO"]):
                result["despesas_operacionais"].append(cod_cta)
            elif any(t in nome for t in ["RECEITAS", "RECEITA", "VENDA", "SERVIÇO", "FATURAMENTO"]):
                result["receita_bruta"].append(cod_cta)
            else:
                result["despesas_operacionais"].append(cod_cta)

        return result


    def _get_ecd_anterior(self) -> int | None:
        """Encontra o ID da ECD do período anterior para a mesma empresa."""
        from src.db.models import ECD
        ecd_atual = self.session.get(ECD, self.ecd_id)
        if not ecd_atual:
            return None
        ecd_ant = self.session.execute(
            select(ECD)
            .where(
                ECD.empresa_id == ecd_atual.empresa_id,
                ECD.dt_ini < ecd_atual.dt_ini,
                ECD.id != self.ecd_id,
            )
            .order_by(ECD.dt_ini.desc())
            .limit(1)
        ).scalar_one_or_none()
        return ecd_ant.id if ecd_ant else None

    def _get_saldos_resultado_anteriores(self, ecd_anterior_id: int) -> dict[str, float]:
        """Carrega saldos de resultado do período anterior."""
        saldos_ant = self.session.execute(
            select(SaldoResultado).where(SaldoResultado.ecd_id == ecd_anterior_id)
        ).scalars().all()
        result: dict[str, float] = {}
        for s in saldos_ant:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            result[s.cod_cta] = vl
        return result

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        empresa_id: int | None = None,
    ) -> tuple[ReportContext, list[LinhaDRE], dict[str, float]]:
        """Gera a DRE.

        Args:
            criterios: Filtros F7
            empresa_id: ID da empresa para mapeamentos (None = usa default)

        Returns:
            (contexto, linhas, totais)
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Determina empresa_id
        if empresa_id is None:
            from src.db.models import ECD
            ecd = self.session.get(ECD, self.ecd_id)
            empresa_id = ecd.empresa_id if ecd else 0

        mapeamentos = self._get_mapeamentos(empresa_id)

        # Busca saldos de resultado (I355)
        saldos_res = self.engine.aplicar_saldos_resultado(criterios)
        saldo_por_conta: dict[str, float] = {}
        for s in saldos_res:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            saldo_por_conta[s.cod_cta] = vl

        # Busca saldos do período anterior
        ecd_ant_id = self._get_ecd_anterior()
        saldo_anterior_por_conta: dict[str, float] = {}
        if ecd_ant_id:
            saldo_anterior_por_conta = self._get_saldos_resultado_anteriores(ecd_ant_id)

        # Calcula valores por categoria (atual e anterior)
        cat_valores: dict[str, float] = {}
        cat_valores_ant: dict[str, float] = {}
        for cat, contas in mapeamentos.items():
            total = sum(saldo_por_conta.get(c, 0.0) for c in contas)
            cat_valores[cat] = total
            total_ant = sum(saldo_anterior_por_conta.get(c, 0.0) for c in contas)
            cat_valores_ant[cat] = total_ant

        # Monta linhas da DRE
        linhas: list[LinhaDRE] = []
        running = 0.0
        running_ant = 0.0

        for i, degrau in enumerate(DRE_DEFAULT):
            if degrau["categoria"] is None:
                # Subtotal ou total
                linhas.append(
                    LinhaDRE(
                        tipo=degrau["tipo"],
                        descricao=degrau["descricao"],
                        valor_atual=running,
                        valor_anterior=running_ant,
                        ordem=i,
                    )
                )
            else:
                vl = cat_valores.get(degrau["categoria"], 0.0)
                vl_ant = cat_valores_ant.get(degrau["categoria"], 0.0)
                # Aplica sinal: receitas são crédito (negativo interno → positivo na DRE)
                # despesas são débito (positivo interno → negativo na DRE)
                if degrau["sinal"] == 1:
                    # Receitas: crédito interno (negativo) → positivo na DRE
                    vl_dre = abs(vl)
                    vl_dre_ant = abs(vl_ant)
                elif degrau["sinal"] == -1:
                    # Despesas: débito interno (positivo) → negativo na DRE
                    vl_dre = -abs(vl)
                    vl_dre_ant = -abs(vl_ant)
                else:
                    vl_dre = vl
                    vl_dre_ant = vl_ant

                running += vl_dre
                running_ant += vl_dre_ant

                linhas.append(
                    LinhaDRE(
                        tipo=degrau["tipo"],
                        descricao=degrau["descricao"],
                        valor_atual=vl_dre,
                        valor_anterior=vl_dre_ant,
                        ordem=i,
                    )
                )

        resultado_liquido = running

        totais = {
            "resultado_liquido": resultado_liquido,
            "receita_bruta": cat_valores.get("receita_bruta", 0.0),
            "lucro_bruto": next(
                (l.valor_atual for l in linhas if "Lucro Bruto" in l.descricao), 0.0
            ),
        }

        ctx = ReportContext(
            titulo="Demonstração do Resultado do Exercício",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas, totais