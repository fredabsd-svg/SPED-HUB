"""Saldo de conta a partir do I155 e do I355 — um lugar só para os três passos.

O manual do leiaute 9 diz que o `COD_CTA` do I155 (saldos periódicos), do
I250 (partidas) e do I355 (resultado antes do encerramento) é o "código da
conta **analítica**". Uma ECD real também traz um I150 por mês, e, quando a
empresa usa centro de custo, um I155 por centro. Todo relatório que lê o
saldo de uma conta precisa, portanto, dos mesmos três passos:

1. somar os centros de custo do mesmo (conta, período);
2. tomar o saldo inicial do **primeiro** período e o final do **último**,
   somando débitos e créditos de todos — somar saldos de doze meses dá doze
   vezes o saldo;
3. subir pela hierarquia (`COD_CTA_SUP`) para dar saldo às sintéticas, que
   não têm I155 próprio.

Cada relatório fazia a sua versão — e cada versão errava num ponto
diferente: o balancete somava os saldos de todos os períodos, o balanço
mostrava as sintéticas zeradas, a DRE ficava com o último centro de custo.
Este módulo é a versão única; balancete, balanço, DRE, DFC, validações e o
painel passam por ele.

Sintética que traz I155 próprio (arquivo fora do manual, ou banco montado à
mão) usa o próprio saldo, e as filhas dela deixam de ser somadas acima dela:
nunca se conta o mesmo valor duas vezes.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.reports.base import valor_sinalizado

if TYPE_CHECKING:
    from src.filters.engine import FilterCriteria, FilterEngine

TOLERANCIA = 0.005


@dataclass
class Saldo:
    """Saldo sinalizado (devedor +, credor −) de uma conta num intervalo."""

    si: float = 0.0
    d: float = 0.0
    c: float = 0.0
    sf: float = 0.0

    def somar(self, outro: Saldo) -> None:
        self.si += outro.si
        self.d += outro.d
        self.c += outro.c
        self.sf += outro.sf

    def copia(self) -> Saldo:
        return Saldo(self.si, self.d, self.c, self.sf)

    @property
    def calculado(self) -> float:
        """SI + D − C: o saldo final que os movimentos explicam."""
        return self.si + self.d - self.c

    @property
    def divergencia(self) -> float:
        """SF declarado menos SF calculado."""
        return self.sf - self.calculado

    @property
    def tem_movimento(self) -> bool:
        return abs(self.d) > TOLERANCIA or abs(self.c) > TOLERANCIA


# ── Hierarquia do plano de contas ──────────────────────────────────────────


class Hierarquia:
    """A árvore `COD_CTA → COD_CTA_SUP` de uma ECD, à prova de ciclo.

    A importação recusa plano com ciclo (ADR 0006), mas bancos importados
    antes disso podem tê-lo: toda travessia aqui guarda o que já visitou.
    Conta cuja superior não existe no plano (órfã) é tratada como raiz — o
    saldo dela entra no total em vez de sumir.
    """

    def __init__(self, superiores: Mapping[str, str | None]):
        self._sup: dict[str, str | None] = {}
        self._auto_referentes = {cod for cod, sup in superiores.items() if sup == cod}
        for cod, sup in superiores.items():
            self._sup[cod] = sup if sup and sup != cod and sup in superiores else None
        self._filhos: dict[str, list[str]] = defaultdict(list)
        for cod, sup in self._sup.items():
            if sup is not None:
                self._filhos[sup].append(cod)
        for filhos in self._filhos.values():
            filhos.sort()
        self._ancestrais: dict[str, tuple[str, ...]] = {}
        self._ordem: list[str] | None = None

    @classmethod
    def do_plano(cls, plano: Mapping[str, Any]) -> Hierarquia:
        """A partir de `{cod_cta: PlanoConta}`."""
        return cls({cod: pc.cod_cta_sup for cod, pc in plano.items()})

    def __contains__(self, cod: object) -> bool:
        return cod in self._sup

    def superior(self, cod: str) -> str | None:
        return self._sup.get(cod)

    def filhos(self, cod: str) -> list[str]:
        return list(self._filhos.get(cod, ()))

    def ancestrais(self, cod: str) -> tuple[str, ...]:
        """Superiores de `cod`, do mais próximo ao topo."""
        if cod in self._ancestrais:
            return self._ancestrais[cod]
        cadeia: list[str] = []
        vistos = {cod}
        atual = self._sup.get(cod)
        while atual is not None and atual not in vistos:
            cadeia.append(atual)
            vistos.add(atual)
            atual = self._sup.get(atual)
        resultado = tuple(cadeia)
        self._ancestrais[cod] = resultado
        return resultado

    def descendentes(self, cod: str) -> set[str]:
        """Todas as contas abaixo de `cod`, em qualquer nível (sem ela)."""
        encontrados: set[str] = set()
        pilha = list(self._filhos.get(cod, ()))
        while pilha:
            atual = pilha.pop()
            if atual in encontrados or atual == cod:
                continue
            encontrados.add(atual)
            pilha.extend(self._filhos.get(atual, ()))
        return encontrados

    def raizes(self) -> list[str]:
        return sorted(cod for cod, sup in self._sup.items() if sup is None)

    def em_ordem(self) -> list[str]:
        """Pré-ordem: cada sintética seguida das filhas, irmãs por código.

        Contas presas num ciclo não descendem de raiz nenhuma; entram no fim,
        para que nada do plano suma da listagem.
        """
        if self._ordem is not None:
            return self._ordem
        ordem: list[str] = []
        vistos: set[str] = set()
        for inicio in self.raizes() + sorted(self._sup):
            pilha = [inicio]
            while pilha:
                atual = pilha.pop()
                if atual in vistos:
                    continue
                vistos.add(atual)
                ordem.append(atual)
                pilha.extend(reversed(self._filhos.get(atual, ())))
        self._ordem = ordem
        return ordem

    def contas_em_ciclo(self) -> set[str]:
        """Contas que são a própria sintética ou que nenhuma raiz alcança (A→B→A)."""
        alcancadas: set[str] = set()
        pilha = self.raizes()
        while pilha:
            atual = pilha.pop()
            if atual in alcancadas:
                continue
            alcancadas.add(atual)
            pilha.extend(self._filhos.get(atual, ()))
        return (set(self._sup) - alcancadas) | self._auto_referentes

    def maximais(self, contas: Iterable[str]) -> list[str]:
        """As contas do conjunto que não têm superior no mesmo conjunto.

        Somar só estas não conta duas vezes o valor que uma sintética já
        agregou das filhas.
        """
        conjunto = set(contas)
        return [
            c
            for c in _ordenar(conjunto, self)
            if not any(a in conjunto for a in self.ancestrais(c))
        ]

    def contas_base(self, proprios: Iterable[str]) -> list[str]:
        """Contas com dado próprio sem superior que também tenha dado próprio.

        Numa ECD conforme o manual são as analíticas com I155/I355. A soma
        delas é o total da escrituração, sem dobrar nada.
        """
        return self.maximais(c for c in proprios if c in self._sup)

    def consolidar(self, proprios: Mapping[str, Saldo]) -> dict[str, Saldo]:
        """Saldo de cada conta que tem dado na própria subárvore.

        O saldo de uma conta é o próprio, se ela tem I155; senão, a soma das
        filhas. Conta do I155 que não existe no plano fica de fora — não há
        onde pendurá-la.
        """
        resultado: dict[str, Saldo] = {}
        for cod in reversed(self.em_ordem()):  # filhas antes das superiores
            if cod in proprios:
                resultado[cod] = proprios[cod].copia()
                continue
            soma: Saldo | None = None
            for filho in self._filhos.get(cod, ()):
                parcial = resultado.get(filho)
                if parcial is None:
                    continue
                if soma is None:
                    soma = Saldo()
                soma.somar(parcial)
            if soma is not None:
                resultado[cod] = soma
        return resultado

    def atribuir(self, cod: str, destino: Mapping[str, Any]) -> Any | None:
        """O valor de `destino` para a conta ou para o superior mais próximo mapeado."""
        for candidata in (cod, *self.ancestrais(cod)):
            if candidata in destino:
                return destino[candidata]
        return None


def _ordenar(contas: set[str], hierarquia: Hierarquia) -> list[str]:
    na_arvore = [c for c in hierarquia.em_ordem() if c in contas]
    return na_arvore + sorted(c for c in contas if c not in hierarquia)


# ── Períodos e centros de custo ────────────────────────────────────────────


def saldo_da_linha(linha: Any) -> Saldo:
    """Um I155 (`SaldoPeriodico`) como `Saldo` sinalizado."""
    return Saldo(
        si=valor_sinalizado(linha.vl_sld_ini or 0.0, linha.ind_dc_ini),
        d=linha.vl_deb or 0.0,
        c=linha.vl_cred or 0.0,
        sf=valor_sinalizado(linha.vl_sld_fin or 0.0, linha.ind_dc_fin),
    )


Periodo = tuple[datetime.date, datetime.date]


def saldos_por_periodo(linhas: Iterable[Any]) -> dict[tuple[str, Periodo], Saldo]:
    """Saldo por (conta, período), somando os centros de custo.

    Dois I155 da mesma conta no mesmo período só diferem pelo centro de
    custo: são partes do mesmo saldo, não versões dele.
    """
    resultado: dict[tuple[str, Periodo], Saldo] = {}
    for linha in linhas:
        chave = (linha.cod_cta, (linha.dt_ini, linha.dt_fin))
        if chave not in resultado:
            resultado[chave] = Saldo()
        resultado[chave].somar(saldo_da_linha(linha))
    return resultado


def consolidar_periodos(linhas: Iterable[Any]) -> dict[str, Saldo]:
    """Saldo de cada conta no intervalo coberto pelas linhas.

    SI do primeiro período, SF do último, débitos e créditos de todos. Cada
    conta usa os próprios períodos: a que só aparece em março (sem saldo
    antes) começa com o SI de março, que é zero.
    """
    por_conta: dict[str, list[tuple[Periodo, Saldo]]] = defaultdict(list)
    for (cod, periodo), saldo in saldos_por_periodo(linhas).items():
        por_conta[cod].append((periodo, saldo))

    resultado: dict[str, Saldo] = {}
    for cod, periodos in por_conta.items():
        periodos.sort(key=lambda item: item[0])
        resultado[cod] = Saldo(
            si=periodos[0][1].si,
            d=sum(s.d for _p, s in periodos),
            c=sum(s.c for _p, s in periodos),
            sf=periodos[-1][1].sf,
        )
    return resultado


def somar_resultado(linhas: Iterable[Any]) -> dict[str, float]:
    """I355 por conta, sinalizado, somando centros de custo e encerramentos.

    Conta de resultado é fluxo: cada I350 (encerramento trimestral, por
    exemplo) traz o resultado daquele intervalo, e o do exercício é a soma.
    """
    resultado: dict[str, float] = defaultdict(float)
    for linha in linhas:
        resultado[linha.cod_cta] += valor_sinalizado(linha.vl_sld_fin or 0.0, linha.ind_dc_fin)
    return dict(resultado)


# ── Saldos de uma ECD sob um critério de filtro ────────────────────────────


@dataclass
class SaldosConsolidados:
    """Saldos de uma ECD prontos para relatório.

    `saldos` tem toda conta com dado na subárvore (analíticas e sintéticas);
    `visiveis` são as contas que o critério seleciona, na ordem do plano.
    """

    hierarquia: Hierarquia
    proprios: dict[str, Saldo]
    saldos: dict[str, Saldo]
    visiveis: list[str]

    def saldo(self, cod: str) -> Saldo:
        return self.saldos.get(cod) or Saldo()

    def tem_dado(self, cod: str) -> bool:
        return cod in self.saldos

    def maximais_visiveis(self) -> list[str]:
        return self.hierarquia.maximais(self.visiveis)


def _passa_valor(saldo: Saldo, criterios: FilterCriteria) -> bool:
    """Critérios de valor aplicados ao saldo **consolidado** da conta.

    Aplicados linha a linha do I155, eles descartavam um mês e deixavam os
    outros — o saldo inicial e o final passavam a vir de meses errados — e,
    numa sintética, tiravam da soma a filha que não passava.
    """
    sf = abs(saldo.sf)
    if criterios.vl_min is not None and sf < criterios.vl_min:
        return False
    if criterios.vl_max is not None and sf > criterios.vl_max:
        return False
    if criterios.somente_debitos and saldo.sf <= TOLERANCIA:
        return False
    if criterios.somente_creditos and saldo.sf >= -TOLERANCIA:
        return False
    if criterios.ocultar_saldo_zero and sf <= TOLERANCIA:
        return False
    if criterios.ocultar_sem_movimento and not saldo.tem_movimento:
        return False
    return True


def consolidar(engine: FilterEngine, criterios: FilterCriteria) -> SaldosConsolidados:
    """Saldos consolidados da ECD do `engine`, com os critérios aplicados.

    * Centro de custo e período restringem as **linhas** do I155 (o período
      pega os I150 inteiramente dentro do intervalo).
    * Critérios de conta escolhem as contas **exibidas** — o saldo de uma
      sintética continua vindo de todas as filhas, exibidas ou não.
    * Critérios de valor olham o saldo consolidado de cada conta.
    """
    hierarquia = engine.hierarquia()
    proprios = consolidar_periodos(engine.linhas_de_saldo(criterios))
    saldos = hierarquia.consolidar(proprios)
    selecionadas = engine.contas_selecionadas(criterios)
    visiveis = [
        cod
        for cod in hierarquia.em_ordem()
        if cod in selecionadas and _passa_valor(saldos.get(cod) or Saldo(), criterios)
    ]
    return SaldosConsolidados(hierarquia, proprios, saldos, visiveis)
