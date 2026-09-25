"""Balancete de Verificação (F8).

A partir dos saldos periódicos (I155), gera balancete com:
- Colunas: Saldo Inicial, Débitos, Créditos, Saldo Final
- Profundidade configurável
- Conferência automática contra I155 (SI + D − C = SF)

O saldo de cada conta vem de `src.reports.saldos.consolidar`: SI do primeiro
I150, SF do último, centros de custo somados, sintéticas agregando as filhas
(o I155 só existe para analítica).
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    fmt_moeda,
    saldo_por_natureza,
)
from src.reports.saldos import TOLERANCIA, consolidar


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
    # Superiores da conta no plano, do mais próximo ao topo. `None` quando a
    # linha foi montada fora do `gerar` e a hierarquia é desconhecida.
    ancestrais: tuple[str, ...] | None = None
    # A conta tem I155 próprio (analítica). Sintética agregada não tem: a
    # divergência dela é a soma das divergências das filhas.
    saldo_proprio: bool = True


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
            (contexto, linhas) — na ordem do plano de contas, cada sintética
            antes das filhas; só contas com saldo ou movimento na subárvore.
        """
        if criterios is None:
            criterios = FilterCriteria()

        saldos = consolidar(self.engine, criterios)
        plano = self.engine.plano()

        linhas = []
        for cod_cta in saldos.visiveis:
            if not saldos.tem_dado(cod_cta):
                continue
            pc = plano[cod_cta]
            if nivel_max is not None and pc.nivel > nivel_max:
                continue
            if apenas_sinteticas and pc.ind_cta != "S":
                continue

            saldo = saldos.saldo(cod_cta)
            div = saldo.divergencia
            linhas.append(
                LinhaBalancete(
                    cod_cta=cod_cta,
                    nome_cta=pc.nome_cta,
                    nivel=pc.nivel,
                    cod_nat=pc.cod_nat,
                    ind_cta=pc.ind_cta,
                    saldo_inicial=saldo.si,
                    debitos=saldo.d,
                    creditos=saldo.c,
                    saldo_final=saldo.sf,
                    saldo_calculado=saldo.calculado,
                    divergencia=div,
                    tem_divergencia=abs(div) > TOLERANCIA,
                    ancestrais=saldos.hierarquia.ancestrais(cod_cta),
                    saldo_proprio=cod_cta in saldos.proprios,
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
        """Totais da listagem, contando cada valor uma única vez.

        Entram as linhas que não têm superior **listado**: a sintética já
        agrega as filhas, e somar as duas dobraria o valor. Uma analítica
        cuja sintética não está na listagem (filtro por nível, só
        analíticas) entra por si — somar só o menor nível presente, como era
        antes, deixava de fora as analíticas mais fundas: 820.000 de débitos
        na amostra, contra 2.980.000.

        Linha montada fora do `gerar` (sem `ancestrais`) herda a superior da
        indentação: a linha anterior de nível menor.
        """
        listadas = {ln.cod_cta for ln in linhas}
        pilha: list[int] = []
        base: list[LinhaBalancete] = []
        for ln in linhas:
            while pilha and pilha[-1] >= ln.nivel:
                pilha.pop()
            if ln.ancestrais is not None:
                coberta = any(a in listadas for a in ln.ancestrais)
            else:
                coberta = bool(pilha)
            pilha.append(ln.nivel)
            if not coberta:
                base.append(ln)
        return {
            "saldo_inicial": round(sum(ln.saldo_inicial for ln in base), 2),
            "debitos": round(sum(ln.debitos for ln in base), 2),
            "creditos": round(sum(ln.creditos for ln in base), 2),
            "saldo_final": round(sum(ln.saldo_final for ln in base), 2),
        }

    def conferir(self, linhas: list[LinhaBalancete]) -> dict:
        """Conferência contra I155: SI + D − C = SF, conta a conta.

        Conta só as linhas com saldo próprio: a divergência de uma sintética
        agregada é a das filhas, e contá-la de novo multiplicaria um erro pelo
        número de níveis acima dele. A conferência é do intervalo inteiro
        (SI do primeiro mês, SF do último); a de cada mês é da validação (b).
        """
        proprias = [ln for ln in linhas if ln.saldo_proprio]
        total_divergencias = sum(1 for ln in proprias if ln.tem_divergencia)
        soma_divergencias = sum(abs(ln.divergencia) for ln in proprias if ln.tem_divergencia)

        return {
            "total_contas": len(linhas),
            "contas_com_divergencia": total_divergencias,
            "soma_divergencias": round(soma_divergencias, 2),
            "status": "OK" if total_divergencias == 0 else "DIVERGÊNCIAS",
        }
