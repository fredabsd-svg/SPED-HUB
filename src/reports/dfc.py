"""Demonstração dos Fluxos de Caixa — DFC (método indireto).

Estrutura conforme NBC TG 03 / CPC 03:
  1. Fluxo das Atividades Operacionais
  2. Fluxo das Atividades de Investimento
  3. Fluxo das Atividades de Financiamento
  4. Variação Líquida de Caixa e Equivalentes

Fase 6: +período anterior comparativo.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Mapeamento, PlanoConta, SaldoPeriodico
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    valor_sinalizado,
)


@dataclass
class LinhaDFC:
    tipo: str  # step, detail, subtotal, total
    descricao: str
    valor: float = 0.0
    valor_anterior: float = 0.0
    ordem: int = 0


DFC_DEFAULT = [
    {
        "tipo": "section",
        "descricao": "Fluxo das Atividades Operacionais",
        "categoria": None,
        "sinal": 0,
    },
    {
        "tipo": "step",
        "descricao": "Lucro Líquido do Exercício",
        "categoria": "lucro_liquido",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+) Depreciação e Amortização",
        "categoria": "depreciacao",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Contas a Receber",
        "categoria": "var_contas_receber",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Estoques",
        "categoria": "var_estoques",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Fornecedores",
        "categoria": "var_fornecedores",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Obrigações Fiscais",
        "categoria": "var_obrig_fiscais",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Outros Ajustes Operacionais",
        "categoria": "outros_operacionais",
        "sinal": 1,
    },
    {
        "tipo": "subtotal",
        "descricao": "= Caixa Gerado nas Operações",
        "categoria": None,
        "sinal": 0,
    },
    {
        "tipo": "section",
        "descricao": "Fluxo das Atividades de Investimento",
        "categoria": None,
        "sinal": 0,
    },
    {
        "tipo": "step",
        "descricao": "(-) Aquisição de Imobilizado",
        "categoria": "aquisicao_imobilizado",
        "sinal": -1,
    },
    {
        "tipo": "step",
        "descricao": "(+) Venda de Imobilizado",
        "categoria": "venda_imobilizado",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(-) Aquisição de Intangível",
        "categoria": "aquisicao_intangivel",
        "sinal": -1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Outros Investimentos",
        "categoria": "outros_investimentos",
        "sinal": 1,
    },
    {
        "tipo": "subtotal",
        "descricao": "= Caixa das Atividades de Investimento",
        "categoria": None,
        "sinal": 0,
    },
    {
        "tipo": "section",
        "descricao": "Fluxo das Atividades de Financiamento",
        "categoria": None,
        "sinal": 0,
    },
    {
        "tipo": "step",
        "descricao": "(+) Aumento de Capital",
        "categoria": "aumento_capital",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Empréstimos e Financiamentos",
        "categoria": "emprestimos",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(-) Distribuição de Lucros",
        "categoria": "distribuicao_lucros",
        "sinal": -1,
    },
    {
        "tipo": "subtotal",
        "descricao": "= Caixa das Atividades de Financiamento",
        "categoria": None,
        "sinal": 0,
    },
    {"tipo": "total", "descricao": "= Variação Líquida de Caixa", "categoria": None, "sinal": 0},
]


class DFC:
    """Gerador de DFC — Demonstração dos Fluxos de Caixa."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_mapeamentos(self, empresa_id: int) -> dict[str, list[str]]:
        """Carrega mapeamentos DFC da empresa ou usa defaults."""
        maps = list(
            self.session.execute(
                select(Mapeamento)
                .where(
                    Mapeamento.empresa_id == empresa_id,
                    Mapeamento.tipo == "dfc",
                )
                .order_by(Mapeamento.ordem)
            ).scalars()
        )

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
            "lucro_liquido": [],
            "depreciacao": [],
            "var_contas_receber": [],
            "var_estoques": [],
            "var_fornecedores": [],
            "var_obrig_fiscais": [],
            "outros_operacionais": [],
            "aquisicao_imobilizado": [],
            "venda_imobilizado": [],
            "aquisicao_intangivel": [],
            "outros_investimentos": [],
            "aumento_capital": [],
            "emprestimos": [],
            "distribuicao_lucros": [],
        }

        for cod_cta, pc in plano.items():
            nome = pc.nome_cta.upper()

            if any(t in nome for t in ["DEPRECIA", "AMORTIZA", "EXAUSTÃO"]):
                result["depreciacao"].append(cod_cta)
            elif any(t in nome for t in ["CLIENTES", "CONTAS A RECEBER", "DUPLICATAS A RECEBER"]):
                result["var_contas_receber"].append(cod_cta)
            elif any(t in nome for t in ["ESTOQUE", "MERCADORIA"]):
                result["var_estoques"].append(cod_cta)
            elif any(t in nome for t in ["FORNECEDOR"]):
                result["var_fornecedores"].append(cod_cta)
            elif any(
                t in nome
                for t in [
                    "IMPOSTO",
                    "TRIBUTO",
                    "OBRIGAÇÕES FISC",
                    "PIS",
                    "COFINS",
                    "ICMS",
                    "IRPJ",
                    "CSLL",
                ]
            ):
                result["var_obrig_fiscais"].append(cod_cta)
            elif any(
                t in nome for t in ["IMOBILIZADO", "MÁQUINAS", "EQUIPAMENTO", "VEÍCULO", "MÓVEIS"]
            ):
                result["aquisicao_imobilizado"].append(cod_cta)
            elif any(t in nome for t in ["INTANGÍVEL", "SOFTWARE", "MARCA", "PATENTE"]):
                result["aquisicao_intangivel"].append(cod_cta)
            elif any(t in nome for t in ["CAPITAL SOCIAL", "CAPITAL SUBSCRITO"]):
                result["aumento_capital"].append(cod_cta)
            elif any(t in nome for t in ["EMPRÉSTIMO", "FINANCIAMENTO", "DEBÊNTURE"]):
                result["emprestimos"].append(cod_cta)
            elif any(t in nome for t in ["LUCRO", "DIVIDENDO", "DISTRIBUIÇÃO"]):
                result["distribuicao_lucros"].append(cod_cta)

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

    def _get_saldos_anteriores(self, ecd_anterior_id: int) -> dict[str, float]:
        """Carrega saldos do período anterior."""
        saldos_ant = (
            self.session.execute(
                select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == ecd_anterior_id)
            )
            .scalars()
            .all()
        )
        result: dict[str, float] = {}
        for s in saldos_ant:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            result[s.cod_cta] = vl
        return result

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        empresa_id: int | None = None,
    ) -> tuple[ReportContext, list[LinhaDFC], dict[str, float]]:
        """Gera a DFC com comparativo de período anterior."""
        if criterios is None:
            criterios = FilterCriteria()

        if empresa_id is None:
            from src.db.models import ECD

            ecd = self.session.get(ECD, self.ecd_id)
            empresa_id = ecd.empresa_id if ecd else 0

        mapeamentos = self._get_mapeamentos(empresa_id)

        # Busca saldos atuais
        saldos = self.engine.aplicar_saldos(criterios)
        saldo_por_conta: dict[str, float] = {}
        for s in saldos:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            saldo_por_conta[s.cod_cta] = vl

        # Busca saldos do período anterior
        ecd_ant_id = self._get_ecd_anterior()
        saldo_anterior_por_conta: dict[str, float] = {}
        if ecd_ant_id:
            saldo_anterior_por_conta = self._get_saldos_anteriores(ecd_ant_id)

        # Calcula valores por categoria (atual e anterior)
        cat_valores: dict[str, float] = {}
        cat_valores_ant: dict[str, float] = {}
        for cat, contas in mapeamentos.items():
            total = sum(saldo_por_conta.get(c, 0.0) for c in contas)
            cat_valores[cat] = total
            total_ant = sum(saldo_anterior_por_conta.get(c, 0.0) for c in contas)
            cat_valores_ant[cat] = total_ant

        # Monta linhas
        linhas: list[LinhaDFC] = []
        running = 0.0
        running_ant = 0.0

        for i, degrau in enumerate(DFC_DEFAULT):
            if degrau["categoria"] is None:
                if degrau["tipo"] in ("subtotal", "total"):
                    linhas.append(
                        LinhaDFC(
                            tipo=degrau["tipo"],
                            descricao=degrau["descricao"],
                            valor=running,
                            valor_anterior=running_ant,
                            ordem=i,
                        )
                    )
                else:
                    linhas.append(
                        LinhaDFC(
                            tipo=degrau["tipo"],
                            descricao=degrau["descricao"],
                            valor=0.0,
                            valor_anterior=0.0,
                            ordem=i,
                        )
                    )
            else:
                vl = cat_valores.get(degrau["categoria"], 0.0)
                vl_ant = cat_valores_ant.get(degrau["categoria"], 0.0)
                vl_dfc = vl * degrau["sinal"]
                vl_dfc_ant = vl_ant * degrau["sinal"]
                running += vl_dfc
                running_ant += vl_dfc_ant
                linhas.append(
                    LinhaDFC(
                        tipo=degrau["tipo"],
                        descricao=degrau["descricao"],
                        valor=vl_dfc,
                        valor_anterior=vl_dfc_ant,
                        ordem=i,
                    )
                )

        totais = {
            "variacao_caixa": running,
            "variacao_caixa_anterior": running_ant,
            "operacional": next(
                (
                    ln.valor
                    for ln in linhas
                    if "Operações" in ln.descricao and ln.tipo == "subtotal"
                ),
                0.0,
            ),
            "operacional_anterior": next(
                (
                    ln.valor_anterior
                    for ln in linhas
                    if "Operações" in ln.descricao and ln.tipo == "subtotal"
                ),
                0.0,
            ),
            "investimento": next(
                (
                    ln.valor
                    for ln in linhas
                    if "Investimento" in ln.descricao and ln.tipo == "subtotal"
                ),
                0.0,
            ),
            "investimento_anterior": next(
                (
                    ln.valor_anterior
                    for ln in linhas
                    if "Investimento" in ln.descricao and ln.tipo == "subtotal"
                ),
                0.0,
            ),
            "financiamento": next(
                (
                    ln.valor
                    for ln in linhas
                    if "Financiamento" in ln.descricao and ln.tipo == "subtotal"
                ),
                0.0,
            ),
            "financiamento_anterior": next(
                (
                    ln.valor_anterior
                    for ln in linhas
                    if "Financiamento" in ln.descricao and ln.tipo == "subtotal"
                ),
                0.0,
            ),
            "tem_anterior": ecd_ant_id is not None,
        }

        ctx = ReportContext(
            titulo="Demonstração dos Fluxos de Caixa",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas, totais
