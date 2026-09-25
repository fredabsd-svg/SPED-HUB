"""Demonstração dos Fluxos de Caixa — DFC (método indireto).

Estrutura conforme NBC TG 03 / CPC 03:
  1. Fluxo das Atividades Operacionais
  2. Fluxo das Atividades de Investimento
  3. Fluxo das Atividades de Financiamento
  4. Variação Líquida de Caixa e Equivalentes
  5. Conciliação com os saldos de caixa e equivalentes

Método indireto a partir dos saldos (I155) e do resultado (I355):

* lucro líquido do exercício = resultado do I355 (crédito − débito);
* cada conta patrimonial (ativo, passivo, PL), exceto caixa e equivalentes,
  contribui com **menos a variação do saldo sinalizado** (SF do último I150
  − SI do primeiro): aumento de ativo é saída de caixa; aumento de passivo,
  PL ou depreciação acumulada é entrada;
* as contas do PL que recebem o resultado (lucros acumulados, reservas) dão
  a distribuição de lucros: a variação delas menos o lucro do exercício;
* a soma das três seções tem de ser a variação de caixa e equivalentes
  (SF − SI). Quando o balanço de abertura ou de encerramento não fecha, não
  é — e a diferença sai numa linha própria, em vez de sumir.

A classificação das contas segue a do módulo: mapeamento da empresa
(`Mapeamento`, tipo "dfc") pela conta ou pelo superior mapeado mais
próximo; o que não estiver mapeado, pelo nome da conta ou de um superior,
dentro da natureza.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session
from unidecode import unidecode

from src.db.models import Mapeamento
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import ReportContext
from src.reports.saldos import consolidar, somar_resultado

TOLERANCIA_CONCILIACAO = 0.01


@dataclass
class LinhaDFC:
    tipo: str  # section, step, subtotal, total, conciliacao
    descricao: str
    valor: float = 0.0
    valor_anterior: float = 0.0
    ordem: int = 0


# (tipo, descrição, categoria). Subtotal e total não têm categoria.
DFC_DEFAULT = [
    {"tipo": "section", "descricao": "Fluxo das Atividades Operacionais", "categoria": None},
    {"tipo": "step", "descricao": "Lucro Líquido do Exercício", "categoria": "lucro_liquido"},
    {"tipo": "step", "descricao": "(+) Depreciação e Amortização", "categoria": "depreciacao"},
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Contas a Receber",
        "categoria": "var_contas_receber",
    },
    {"tipo": "step", "descricao": "(+/-) Variação em Estoques", "categoria": "var_estoques"},
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Fornecedores",
        "categoria": "var_fornecedores",
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Variação em Obrigações Fiscais",
        "categoria": "var_obrig_fiscais",
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Outros Ajustes Operacionais",
        "categoria": "outros_operacionais",
    },
    {"tipo": "subtotal", "descricao": "= Caixa Gerado nas Operações", "categoria": None},
    {"tipo": "section", "descricao": "Fluxo das Atividades de Investimento", "categoria": None},
    {
        "tipo": "step",
        "descricao": "(-) Aquisição de Imobilizado",
        "categoria": "aquisicao_imobilizado",
    },
    {"tipo": "step", "descricao": "(+) Venda de Imobilizado", "categoria": "venda_imobilizado"},
    {
        "tipo": "step",
        "descricao": "(-) Aquisição de Intangível",
        "categoria": "aquisicao_intangivel",
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Outros Investimentos",
        "categoria": "outros_investimentos",
    },
    {
        "tipo": "subtotal",
        "descricao": "= Caixa das Atividades de Investimento",
        "categoria": None,
    },
    {"tipo": "section", "descricao": "Fluxo das Atividades de Financiamento", "categoria": None},
    {"tipo": "step", "descricao": "(+) Aumento de Capital", "categoria": "aumento_capital"},
    {
        "tipo": "step",
        "descricao": "(+/-) Empréstimos e Financiamentos",
        "categoria": "emprestimos",
    },
    {
        "tipo": "step",
        "descricao": "(-) Distribuição de Lucros",
        "categoria": "distribuicao_lucros",
    },
    {
        "tipo": "subtotal",
        "descricao": "= Caixa das Atividades de Financiamento",
        "categoria": None,
    },
    {"tipo": "total", "descricao": "= Variação Líquida de Caixa", "categoria": None},
]

# Categorias que uma conta patrimonial pode receber. "caixa" não é linha:
# é o que a DFC explica.
CATEGORIAS = {
    "caixa",
    "depreciacao",
    "var_contas_receber",
    "var_estoques",
    "var_fornecedores",
    "var_obrig_fiscais",
    "outros_operacionais",
    "aquisicao_imobilizado",
    "venda_imobilizado",
    "aquisicao_intangivel",
    "outros_investimentos",
    "aumento_capital",
    "emprestimos",
    "distribuicao_lucros",
}

_TRIBUTOS = r"IMPOSTO|TRIBUT|OBRIGACOES FISC|\bPIS\b|COFINS|\bICMS\b|\bIPI\b|\bISS\b|IRPJ|CSLL"

# Regras por natureza, em ordem: a primeira que casa decide. O nome é
# comparado sem acento e em maiúsculas; a conta sem regra que case herda a
# da superior mais próxima que case.
_REGRAS: dict[str, list[tuple[str, str]]] = {
    "01": [
        ("depreciacao", r"DEPRECIA|AMORTIZA|EXAUST"),
        (
            "caixa",
            r"\bCAIXA\b|\bBANCOS?\b|DISPONIBILIDADE|DISPONIVE|EQUIVALENTES? DE CAIXA"
            r"|NUMERARIO|LIQUIDEZ IMEDIATA",
        ),
        ("var_contas_receber", r"CLIENTE|CONTAS A RECEBER|DUPLICATAS A RECEBER|RECEBIVE"),
        ("var_estoques", r"ESTOQUE|MERCADORIA|MATERIA.?PRIMA|PRODUTOS ACABADOS"),
        ("var_obrig_fiscais", _TRIBUTOS),
        ("var_fornecedores", r"FORNECEDOR"),
        (
            "aquisicao_imobilizado",
            r"IMOBILIZADO|MAQUINA|EQUIPAMENTO|VEICULO|MOVEIS|IMOVE|EDIFIC|TERRENO"
            r"|BENFEITORIA|INSTALAC|COMPUTADOR",
        ),
        ("aquisicao_intangivel", r"INTANGIVE|SOFTWARE|MARCA|PATENTE|LICENCA|DIREITO DE USO"),
        ("outros_investimentos", r"INVESTIMENTO|PARTICIPAC|COLIGADA|CONTROLADA"),
    ],
    "02": [
        ("var_fornecedores", r"FORNECEDOR"),
        ("emprestimos", r"EMPRESTIMO|FINANCIAMENTO|DEBENTURE"),
        ("distribuicao_lucros", r"DIVIDENDO|LUCROS A DISTRIBUIR|JUROS SOBRE (O )?CAPITAL"),
        ("var_obrig_fiscais", _TRIBUTOS),
    ],
    "03": [
        ("aumento_capital", r"CAPITAL"),
    ],
}

# Sem regra que case em nenhum nível: ativo e passivo são capital de giro
# (outros operacionais); PL que não é capital é lucro retido ou reserva.
_PADRAO_DA_NATUREZA = {
    "01": "outros_operacionais",
    "02": "outros_operacionais",
    "03": "distribuicao_lucros",
}


def _normalizar(nome: str) -> str:
    return unidecode(nome or "").upper()


def _r(valor: float) -> float:
    """Centavos, sem o -0.0 que a troca de sinal de uma variação nula produz."""
    return round(valor, 2) + 0.0


@dataclass
class _Fluxos:
    """O resultado do método indireto para uma ECD."""

    categorias: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    lucro: float = 0.0
    caixa_inicial: float = 0.0
    caixa_final: float = 0.0

    @property
    def variacao_saldos(self) -> float:
        return self.caixa_final - self.caixa_inicial


class DFC:
    """Gerador de DFC — Demonstração dos Fluxos de Caixa."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_mapeamentos(self, empresa_id: int) -> dict[str, str]:
        """Mapeamento da empresa, `{cod_cta: categoria}` (primeiro por ordem)."""
        mapeamento: dict[str, str] = {}
        for m in self.session.execute(
            select(Mapeamento)
            .where(Mapeamento.empresa_id == empresa_id, Mapeamento.tipo == "dfc")
            .order_by(Mapeamento.ordem)
        ).scalars():
            if m.categoria in CATEGORIAS:
                mapeamento.setdefault(m.cod_cta, m.categoria)
        return mapeamento

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
    def _classificar(cod: str, engine: FilterEngine, mapeamento: dict[str, str]) -> str | None:
        """Categoria da conta patrimonial; `None` para natureza fora do balanço."""
        plano = engine.plano()
        natureza = plano[cod].cod_nat
        if natureza not in _REGRAS:
            return None
        mapeada = engine.hierarquia().atribuir(cod, mapeamento)
        if mapeada is not None:
            return mapeada
        for candidata in (cod, *engine.hierarquia().ancestrais(cod)):
            nome = _normalizar(plano[candidata].nome_cta)
            for categoria, padrao in _REGRAS[natureza]:
                if re.search(padrao, nome):
                    return categoria
        return _PADRAO_DA_NATUREZA[natureza]

    @classmethod
    def _calcular(
        cls, engine: FilterEngine, criterios: FilterCriteria, mapeamento: dict[str, str]
    ) -> _Fluxos:
        """Fluxos de uma ECD: cada conta patrimonial uma vez, pela variação do saldo."""
        fluxos = _Fluxos()
        fluxos.lucro = -sum(somar_resultado(engine.aplicar_saldos_resultado(criterios)).values())

        saldos = consolidar(engine, criterios)
        for cod in saldos.hierarquia.contas_base(saldos.proprios):
            categoria = cls._classificar(cod, engine, mapeamento)
            if categoria is None:
                continue
            saldo = saldos.proprios[cod]
            if categoria == "caixa":
                fluxos.caixa_inicial += saldo.si
                fluxos.caixa_final += saldo.sf
                continue
            # Menos a variação: ativo que sobe consome caixa; passivo, PL e
            # depreciação acumulada que sobem (mais crédito) liberam caixa.
            fluxos.categorias[categoria] += -(saldo.sf - saldo.si)

        # O lucro entra no PL pelos lucros acumulados/reservas: o que a
        # variação dessas contas não explica pelo lucro foi distribuído.
        fluxos.categorias["distribuicao_lucros"] -= fluxos.lucro
        fluxos.categorias["lucro_liquido"] = fluxos.lucro
        return fluxos

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        empresa_id: int | None = None,
    ) -> tuple[ReportContext, list[LinhaDFC], dict[str, float]]:
        """Gera a DFC com comparativo de período anterior.

        Dos critérios, valem o período e o centro de custo: uma DFC de parte
        das contas não fecha com o caixa, então filtro de conta não se aplica
        (e não aparece no cabeçalho).
        """
        criterios = criterios or FilterCriteria()
        criterios = FilterCriteria(
            dt_ini=criterios.dt_ini, dt_fin=criterios.dt_fin, cod_ccus=list(criterios.cod_ccus)
        )

        if empresa_id is None:
            from src.db.models import ECD

            ecd = self.session.get(ECD, self.ecd_id)
            empresa_id = ecd.empresa_id if ecd else 0

        mapeamento = self._get_mapeamentos(empresa_id)
        atual = self._calcular(self.engine, criterios, mapeamento)

        ecd_ant_id = self._get_ecd_anterior()
        anterior = _Fluxos()
        if ecd_ant_id:
            anterior = self._calcular(
                FilterEngine(self.session, ecd_ant_id), FilterCriteria(), mapeamento
            )

        linhas: list[LinhaDFC] = []
        secao = secao_ant = 0.0
        total = total_ant = 0.0
        subtotais: list[tuple[float, float]] = []
        for i, degrau in enumerate(DFC_DEFAULT):
            if degrau["tipo"] == "section":
                secao = secao_ant = 0.0
                valor = valor_ant = 0.0
            elif degrau["tipo"] == "subtotal":
                # Subtotal da própria seção: antes acumulava as anteriores.
                valor, valor_ant = secao, secao_ant
                subtotais.append((valor, valor_ant))
                total += valor
                total_ant += valor_ant
            elif degrau["tipo"] == "total":
                valor, valor_ant = total, total_ant
            else:
                valor = _r(atual.categorias.get(degrau["categoria"], 0.0))
                valor_ant = _r(anterior.categorias.get(degrau["categoria"], 0.0))
                secao += valor
                secao_ant += valor_ant
            linhas.append(
                LinhaDFC(
                    tipo=degrau["tipo"],
                    descricao=degrau["descricao"],
                    valor=_r(valor),
                    valor_anterior=_r(valor_ant),
                    ordem=i,
                )
            )

        diferenca = _r(total - atual.variacao_saldos)
        diferenca_ant = _r(total_ant - anterior.variacao_saldos)
        conciliacao = [
            ("section", "Conciliação com caixa e equivalentes", 0.0, 0.0),
            (
                "conciliacao",
                "Caixa e equivalentes no início do período",
                atual.caixa_inicial,
                anterior.caixa_inicial,
            ),
            (
                "conciliacao",
                "Caixa e equivalentes no fim do período",
                atual.caixa_final,
                anterior.caixa_final,
            ),
            (
                "conciliacao",
                "Variação de caixa nos saldos (fim − início)",
                atual.variacao_saldos,
                anterior.variacao_saldos,
            ),
            ("conciliacao", "Diferença não conciliada (fluxos − saldos)", diferenca, diferenca_ant),
        ]
        for tipo, descricao, valor, valor_ant in conciliacao:
            linhas.append(
                LinhaDFC(
                    tipo=tipo,
                    descricao=descricao,
                    valor=_r(valor),
                    valor_anterior=_r(valor_ant),
                    ordem=len(linhas),
                )
            )

        (fco, fco_ant), (fci, fci_ant), (fcf, fcf_ant) = subtotais
        totais = {
            "variacao_caixa": _r(total),
            "variacao_caixa_anterior": _r(total_ant),
            "operacional": _r(fco),
            "operacional_anterior": _r(fco_ant),
            "investimento": _r(fci),
            "investimento_anterior": _r(fci_ant),
            "financiamento": _r(fcf),
            "financiamento_anterior": _r(fcf_ant),
            "lucro_liquido": _r(atual.lucro),
            "lucro_liquido_anterior": _r(anterior.lucro),
            "caixa_inicial": _r(atual.caixa_inicial),
            "caixa_final": _r(atual.caixa_final),
            "variacao_caixa_saldos": _r(atual.variacao_saldos),
            "diferenca_conciliacao": diferenca,
            "conciliado": abs(diferenca) <= TOLERANCIA_CONCILIACAO,
            "caixa_inicial_anterior": _r(anterior.caixa_inicial),
            "caixa_final_anterior": _r(anterior.caixa_final),
            "variacao_caixa_saldos_anterior": _r(anterior.variacao_saldos),
            "diferenca_conciliacao_anterior": diferenca_ant,
            "tem_anterior": ecd_ant_id is not None,
        }

        ctx = ReportContext(
            titulo="Demonstração dos Fluxos de Caixa",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas, totais
