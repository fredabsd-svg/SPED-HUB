"""Índices de habilitação econômico-financeira — licitação (Lei 14.133/2021, art. 69).

O art. 69 pede o balanço e a DRE dos **dois últimos exercícios** (inciso I) e
deixa o edital fixar índices para medir a capacidade de o licitante cumprir o
contrato — vedados os de rentabilidade ou lucratividade (§ 2º) e os "não
usualmente adotados" (§ 5º). Os usuais, os que o SICAF calcula e que o guia
de licitações do TCU cita, são três, com referência "maior que 1":

* Liquidez Geral (LG) = (AC + RLP) / (PC + PNC)
* Solvência Geral (SG) = AT / (PC + PNC)
* Liquidez Corrente (LC) = AC / PC

Vêm junto os que costumam aparecer em edital ou em análise de crédito —
liquidez seca e imediata, endividamento, capital circulante líquido — e o
patrimônio líquido, que o § 4º permite exigir em até 10% do valor estimado
da contratação.

O § 1º permite exigir "declaração, assinada por profissional habilitado da
área contábil, que ateste o atendimento pelo licitante dos índices
econômicos previstos no edital": por isso o PDF sai com a assinatura do
contador e do responsável legal.

Os grupos (AC, RLP, PC...) são os do `Classificador` — o nome dos grupos do
plano, com o referencial como apoio —, e o exercício anterior é o saldo
inicial do atual (ou a ECD anterior, quando importada): o balanço de
abertura de um exercício é o de encerramento do outro.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ECD
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import ReportContext, saldo_por_natureza
from src.reports.classificacao import Classificador
from src.reports.saldos import consolidar

GRUPOS = ("ac", "rlp", "investimentos", "imobilizado", "intangivel", "anc", "pc", "pnc", "pl")

REFERENCIA_USUAL = 1.0


@dataclass
class Grupos:
    """Totais do balanço que os índices usam, com o sinal de exibição."""

    ac: float = 0.0
    rlp: float = 0.0
    anc: float = 0.0  # não circulante inteiro (RLP incluído)
    at: float = 0.0
    pc: float = 0.0
    pnc: float = 0.0
    pl: float = 0.0
    disponivel: float = 0.0
    estoques: float = 0.0
    nao_classificado: float = 0.0
    contas_nao_classificadas: list[str] = field(default_factory=list)

    @property
    def exigivel(self) -> float:
        return self.pc + self.pnc


@dataclass
class Indice:
    chave: str
    nome: str
    formula: str
    valor: float | None
    valor_anterior: float | None
    # "maior que 1" nos três usuais; vazio nos demais.
    referencia: str = ""
    usual: bool = False
    # Moeda (CCL, PL) ou índice.
    moeda: bool = False
    interpretacao: str = ""

    @property
    def atende(self) -> bool | None:
        return _atende(self.valor) if self.usual else None

    @property
    def atende_anterior(self) -> bool | None:
        return _atende(self.valor_anterior) if self.usual else None


def _atende(valor: float | None) -> bool | None:
    return None if valor is None else valor > REFERENCIA_USUAL


def _dividir(numerador: float, denominador: float) -> float | None:
    if abs(denominador) < 0.005:
        return None
    return round(numerador / denominador, 4)


class IndicesFinanceiros:
    """Calcula os índices do exercício e do anterior."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _ecd_anterior(self) -> int | None:
        ecd = self.session.get(ECD, self.ecd_id)
        if ecd is None:
            return None
        anterior = self.session.execute(
            select(ECD)
            .where(
                ECD.empresa_id == ecd.empresa_id,
                ECD.dt_ini < ecd.dt_ini,
                ECD.id != self.ecd_id,
            )
            .order_by(ECD.dt_ini.desc())
            .limit(1)
        ).scalar_one_or_none()
        return anterior.id if anterior else None

    @staticmethod
    def grupos(engine: FilterEngine, criterios: FilterCriteria, campo: str = "sf") -> Grupos:
        """Soma as contas-base por grupo do balanço, no saldo final (ou inicial)."""
        classificador = Classificador(engine)
        saldos = consolidar(engine, criterios)
        resultado = Grupos()
        for cod in saldos.hierarquia.contas_base(saldos.proprios):
            natureza = classificador.natureza(cod)
            if natureza not in ("01", "02", "03"):
                continue
            valor = saldo_por_natureza(getattr(saldos.proprios[cod], campo), natureza)
            grupo = classificador.grupo_balanco(cod)
            if natureza == "01":
                resultado.at += valor
                if classificador.eh_caixa(cod):
                    resultado.disponivel += valor
                if grupo == "ac":
                    resultado.ac += valor
                    if classificador.categoria_dfc(cod) == "estoques":
                        resultado.estoques += valor
                elif grupo in ("rlp", "investimentos", "imobilizado", "intangivel", "anc"):
                    resultado.anc += valor
                    if grupo == "rlp":
                        resultado.rlp += valor
                else:
                    resultado.nao_classificado += valor
                    resultado.contas_nao_classificadas.append(cod)
            elif grupo == "pl":
                resultado.pl += valor
            elif grupo == "pnc":
                resultado.pnc += valor
            elif grupo == "pc":
                resultado.pc += valor
            else:
                # Passivo sem grupo identificado: circulante, o lado prudente
                # (piora LC e LG em vez de melhorar) — e a conta é listada.
                resultado.pc += valor
                resultado.nao_classificado += valor
                resultado.contas_nao_classificadas.append(cod)
        return resultado

    def gerar(
        self, criterios: FilterCriteria | None = None
    ) -> tuple[ReportContext, list[Indice], dict]:
        criterios = criterios or FilterCriteria()
        criterios = FilterCriteria(dt_ini=criterios.dt_ini, dt_fin=criterios.dt_fin)

        atual = self.grupos(self.engine, criterios)
        ecd_anterior = self._ecd_anterior()
        if ecd_anterior:
            anterior = self.grupos(FilterEngine(self.session, ecd_anterior), FilterCriteria())
            origem_anterior = "ecd_anterior"
        else:
            anterior = self.grupos(self.engine, criterios, campo="si")
            origem_anterior = "saldo_inicial"
        tem_anterior = abs(anterior.at) > 0.005 or abs(anterior.exigivel) > 0.005

        def par(funcao) -> tuple[float | None, float | None]:
            return funcao(atual), (funcao(anterior) if tem_anterior else None)

        indices: list[Indice] = []

        def indice(chave, nome, formula, funcao, **extra):
            valor, valor_anterior = par(funcao)
            indices.append(Indice(chave, nome, formula, valor, valor_anterior, **extra))

        indice(
            "lg",
            "Liquidez Geral (LG)",
            "(Ativo Circulante + Realizável a Longo Prazo) ÷ (Passivo Circulante + "
            "Passivo Não Circulante)",
            lambda g: _dividir(g.ac + g.rlp, g.exigivel),
            referencia="> 1,00",
            usual=True,
            interpretacao="Quanto a empresa tem a receber, no curto e no longo prazo, "
            "para cada real que deve.",
        )
        indice(
            "sg",
            "Solvência Geral (SG)",
            "Ativo Total ÷ (Passivo Circulante + Passivo Não Circulante)",
            lambda g: _dividir(g.at, g.exigivel),
            referencia="> 1,00",
            usual=True,
            interpretacao="Quantas vezes o ativo cobre todas as dívidas.",
        )
        indice(
            "lc",
            "Liquidez Corrente (LC)",
            "Ativo Circulante ÷ Passivo Circulante",
            lambda g: _dividir(g.ac, g.pc),
            referencia="> 1,00",
            usual=True,
            interpretacao="Quanto a empresa tem no curto prazo para cada real que "
            "vence no curto prazo.",
        )
        indice(
            "ls",
            "Liquidez Seca (LS)",
            "(Ativo Circulante − Estoques) ÷ Passivo Circulante",
            lambda g: _dividir(g.ac - g.estoques, g.pc),
            interpretacao="A liquidez corrente sem depender da venda dos estoques.",
        )
        indice(
            "li",
            "Liquidez Imediata (LI)",
            "Disponível ÷ Passivo Circulante",
            lambda g: _dividir(g.disponivel, g.pc),
            interpretacao="Quanto do passivo circulante o caixa e os equivalentes pagam hoje.",
        )
        indice(
            "eg",
            "Endividamento Geral (EG)",
            "(Passivo Circulante + Passivo Não Circulante) ÷ Ativo Total",
            lambda g: _dividir(g.exigivel, g.at),
            interpretacao="Parte do ativo financiada por terceiros — quanto menor, melhor.",
        )
        indice(
            "ce",
            "Composição do Endividamento (CE)",
            "Passivo Circulante ÷ (Passivo Circulante + Passivo Não Circulante)",
            lambda g: _dividir(g.pc, g.exigivel),
            interpretacao="Parte das dívidas que vence no curto prazo.",
        )
        indice(
            "ccl",
            "Capital Circulante Líquido (CCL)",
            "Ativo Circulante − Passivo Circulante",
            lambda g: round(g.ac - g.pc, 2),
            moeda=True,
            interpretacao="Folga (ou falta) de recursos de curto prazo, em reais.",
        )
        indice(
            "pl",
            "Patrimônio Líquido (PL)",
            "Total do patrimônio líquido",
            lambda g: round(g.pl, 2),
            moeda=True,
            interpretacao="O edital pode exigir PL mínimo de até 10% do valor estimado da "
            "contratação (art. 69, § 4º).",
        )

        usuais = [i for i in indices if i.usual]
        ecd = self.session.get(ECD, self.ecd_id)
        data_atual = criterios.dt_fin or (ecd.dt_fin if ecd else None)
        inicio = criterios.dt_ini or (ecd.dt_ini if ecd else None)
        totais = {
            "grupos": atual,
            "grupos_anterior": anterior if tem_anterior else None,
            "tem_anterior": tem_anterior,
            "origem_anterior": origem_anterior if tem_anterior else None,
            "data_atual": data_atual,
            "data_anterior": inicio - datetime.timedelta(days=1) if inicio else None,
            "atende_usuais": all(i.atende for i in usuais),
            "atende_usuais_anterior": (
                all(i.atende_anterior for i in usuais) if tem_anterior else None
            ),
            # O maior contrato para o qual o PL atende à exigência máxima de
            # PL mínimo de 10% do valor estimado (art. 69, § 4º).
            "contrato_maximo_pl_10": round(atual.pl * 10, 2) if atual.pl > 0 else 0.0,
            "contas_nao_classificadas": atual.contas_nao_classificadas,
        }
        ctx = ReportContext(
            titulo="Índices de Habilitação Econômico-Financeira",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )
        return ctx, indices, totais
