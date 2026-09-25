"""Saldo de conta e hierarquia do plano — a árvore `COD_CTA → COD_CTA_SUP`.

O `COD_CTA` do I155, do I250 e do I355 é, pelo manual do leiaute 9, o
"código da conta **analítica**". Quem precisa de uma sintética — a subárvore
de um filtro, o saldo de "ATIVO CIRCULANTE" — precisa descer ou subir pela
hierarquia do plano, nunca adivinhar pelo prefixo do código: o código é livre
no leiaute ("111001" pode ser filha de "11", e "1101" pode não ser).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

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
