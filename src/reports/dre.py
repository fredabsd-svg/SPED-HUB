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

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Mapeamento, PlanoConta
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import ReportContext
from src.reports.saldos import somar_resultado


@dataclass
class LinhaDRE:
    tipo: str  # step, detail, subtotal, total
    descricao: str
    valor_atual: float = 0.0
    valor_anterior: float = 0.0
    ordem: int = 0


# ── Estrutura default da DRE ──
DRE_DEFAULT = [
    {
        "tipo": "step",
        "descricao": "Receita Operacional Bruta",
        "categoria": "receita_bruta",
        "sinal": 1,
    },
    {"tipo": "step", "descricao": "(-) Deduções da Receita", "categoria": "deducoes", "sinal": -1},
    {
        "tipo": "subtotal",
        "descricao": "= Receita Operacional Líquida",
        "categoria": None,
        "sinal": 0,
    },
    {"tipo": "step", "descricao": "(-) Custos", "categoria": "custos", "sinal": -1},
    {"tipo": "subtotal", "descricao": "= Lucro Bruto", "categoria": None, "sinal": 0},
    {
        "tipo": "step",
        "descricao": "(-) Despesas Operacionais",
        "categoria": "despesas_operacionais",
        "sinal": -1,
    },
    {
        "tipo": "step",
        "descricao": "(+) Receitas Financeiras",
        "categoria": "receitas_financeiras",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(-) Despesas Financeiras",
        "categoria": "despesas_financeiras",
        "sinal": -1,
    },
    {"tipo": "subtotal", "descricao": "= Resultado Antes IRPJ/CSLL", "categoria": None, "sinal": 0},
    {"tipo": "step", "descricao": "(-) IRPJ / CSLL", "categoria": "irpj_csll", "sinal": -1},
    {
        "tipo": "total",
        "descricao": "= Resultado Líquido do Exercício",
        "categoria": None,
        "sinal": 0,
    },
]


class DRE:
    """Gerador de DRE."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_mapeamentos(self, empresa_id: int) -> dict[str, list[str]]:
        """Carrega mapeamentos DRE da empresa ou usa defaults."""
        maps = list(
            self.session.execute(
                select(Mapeamento)
                .where(
                    Mapeamento.empresa_id == empresa_id,
                    Mapeamento.tipo == "dre",
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
            elif any(
                t in nome
                for t in [
                    "DESPESAS FINANC",
                    "DESPESA FINANC",
                    "JUROS P",
                    "JURO P",
                    "VARIAÇÃO MONETÁRIA PASSIVA",
                    "DESCONTO CONCED",
                ]
            ):
                result["despesas_financeiras"].append(cod_cta)
            elif any(
                t in nome
                for t in [
                    "RECEITAS FINANC",
                    "RECEITA FINANC",
                    "JUROS A",
                    "JURO A",
                    "DESCONTO OBT",
                    "RENDIMENTO",
                    "VARIAÇÃO MONETÁRIA ATIVA",
                ]
            ):
                result["receitas_financeiras"].append(cod_cta)
            elif any(
                t in nome
                for t in ["DEVOLU", "CANCEL", "ABATIMENTO", "IMPOSTO S/", "PIS", "COFINS", "ICMS"]
            ):
                result["deducoes"].append(cod_cta)
            elif any(t in nome for t in ["CMV", "CUSTO", "CSP"]):
                result["custos"].append(cod_cta)
            elif any(
                t in nome
                for t in [
                    "DESPESAS ADMIN",
                    "DESPESA ADMIN",
                    "DESPESAS GERAL",
                    "DESPESA GERAL",
                    "DESPESAS COM",
                    "DESPESA COM",
                    "ADMINISTRATIVA",
                    "SALÁRIO",
                    "VENCIMENTO",
                ]
            ):
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

    @staticmethod
    def _valores_por_categoria(
        engine: FilterEngine,
        criterios: FilterCriteria,
        mapeamentos: dict[str, list[str]],
    ) -> dict[str, float]:
        """Soma do I355 por categoria da DRE, cada conta uma única vez.

        1. O I355 de cada conta soma os centros de custo e os encerramentos
           (antes ficava a última linha lida: o CC2 apagava o CC1).
        2. Só entram as contas com I355 próprio sem superior que também
           tenha — numa ECD conforme o manual, as analíticas.
        3. Cada uma vai para a categoria dela ou, se não for mapeada, do
           superior mapeado mais próximo: mapear a sintética "DESPESAS"
           leva todas as filhas, que não têm I355 na sintética.
        """
        proprios = somar_resultado(engine.aplicar_saldos_resultado(criterios))
        hierarquia = engine.hierarquia()
        categoria_da_conta: dict[str, str] = {}
        for categoria, contas in mapeamentos.items():
            for cod in contas:
                categoria_da_conta.setdefault(cod, categoria)

        valores: dict[str, float] = {cat: 0.0 for cat in mapeamentos}
        for cod in hierarquia.contas_base(proprios):
            categoria = hierarquia.atribuir(cod, categoria_da_conta)
            if categoria is not None:
                valores[categoria] = valores.get(categoria, 0.0) + proprios[cod]
        return valores

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

        # Valores por categoria (atual e anterior), pelo I355.
        cat_valores = self._valores_por_categoria(self.engine, criterios, mapeamentos)
        ecd_ant_id = self._get_ecd_anterior()
        cat_valores_ant: dict[str, float] = {}
        if ecd_ant_id:
            cat_valores_ant = self._valores_por_categoria(
                FilterEngine(self.session, ecd_ant_id), FilterCriteria(), mapeamentos
            )

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
                # Na DRE, crédito soma e débito subtrai: o valor é o saldo
                # interno (débito +, crédito −) com o sinal trocado, para
                # receita e para despesa.  O `abs()` de antes mostrava uma
                # despesa com saldo credor (recuperação maior que o gasto)
                # como despesa, e o resultado não batia com o I355.
                vl_dre = -vl
                vl_dre_ant = -vl_ant

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
            # Com o sinal da DRE (receita positiva). Era o saldo interno,
            # negativo, e o painel nunca mostrava a margem líquida.
            "receita_bruta": -cat_valores.get("receita_bruta", 0.0),
            "lucro_bruto": next(
                (ln.valor_atual for ln in linhas if "Lucro Bruto" in ln.descricao), 0.0
            ),
        }

        ctx = ReportContext(
            titulo="Demonstração do Resultado do Exercício",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas, totais
