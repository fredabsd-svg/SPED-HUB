"""O que os geradores de SPED têm em comum.

Formatação do leiaute, estrutura de registro e — o que mais importa — as
contagens do bloco 9. Elas são idênticas em todas as escriturações, e são o
ponto onde gerador próprio erra: o validador recusa o arquivo inteiro sem
apontar a linha. Escrever essa lógica uma vez, num lugar só, é o que evita
acertá-la numa escrituração e errá-la na seguinte.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


class CampoObrigatorioAusente(ValueError):
    """Falta cadastro sem o qual o arquivo sairia errado — e aceito.

    O validador do Fisco não recusa um enquadramento errado: ele não tem como
    saber qual é o certo.  O erro só aparece meses depois, em intimação.  Por
    isso o gerador para em vez de assumir um padrão.
    """


def formatar_valor(valor: float | Decimal | None) -> str:
    """Duas casas, vírgula decimal, sem separador de milhar.

    Zero vira campo vazio: o leiaute trata valor ausente e valor zero como a
    mesma coisa na maioria dos campos, e escrever `0,00` onde o validador
    espera vazio gera advertência.

    O arredondamento é meio para cima, não para o par.  O padrão do
    `Decimal.quantize` — e do `round` do Python — arredondaria 2,665 para 2,66.
    """
    if valor is None:
        return ""
    numero = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if numero == 0:
        return ""
    return f"{numero:.2f}".replace(".", ",")


def formatar_data(data: datetime.date | None) -> str:
    """ddmmaaaa — o formato do leiaute, sem separador."""
    return data.strftime("%d%m%Y") if data else ""


def texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


@dataclass
class Registro:
    """Uma linha do arquivo.

    Guardar os campos em lista, e só juntá-los na hora de escrever, é o que
    permite contar e conferir antes de gerar o texto final.
    """

    tipo: str
    campos: list[str] = field(default_factory=list)

    def linha(self) -> str:
        return "|" + "|".join([self.tipo, *self.campos]) + "|"


@dataclass
class ResultadoGeracao:
    """O arquivo e o que se precisa saber sobre ele."""

    registros: list[Registro] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    # Os documentos que entraram no arquivo, na ordem em que foram escriturados.
    # Quem arquiva a escrituração precisa disto, e só o gerador sabe: o período
    # sozinho não basta, porque o recorte depende também da empresa e do que
    # estava importado na hora de gerar.
    documentos_ids: list[int] = field(default_factory=list)

    @property
    def total_linhas(self) -> int:
        return len(self.registros)

    def contagem_por_tipo(self) -> dict[str, int]:
        contagem: dict[str, int] = defaultdict(int)
        for registro in self.registros:
            contagem[registro.tipo] += 1
        return dict(contagem)

    def texto(self) -> str:
        """O arquivo, com quebra de linha CRLF.

        O leiaute do SPED pede CRLF; gerar com LF faz alguns validadores
        recusarem o arquivo inteiro sem dizer por quê.
        """
        return "\r\n".join(r.linha() for r in self.registros) + "\r\n"


class GeradorBase:
    """A mecânica de montar registros e fechar as contagens."""

    def __init__(self) -> None:
        self._resultado = ResultadoGeracao()

    def _add(self, tipo: str, *campos: Any) -> None:
        self._resultado.registros.append(Registro(tipo, [texto(c) for c in campos]))

    def _encerrar_bloco(self, bloco: str, tipo_encerramento: str) -> None:
        """`|X990|n|`, onde n conta o próprio encerramento.

        Contar antes de acrescentar a linha deixaria o total um a menos, e o
        validador recusa o arquivo inteiro por causa disso.
        """
        do_bloco = sum(1 for r in self._resultado.registros if r.tipo.startswith(bloco))
        self._add(tipo_encerramento, do_bloco + 1)

    def _bloco_9(self) -> None:
        """O bloco que conta os outros — e a si mesmo.

        É onde gerador próprio erra: o 9900 tem de contar também os registros
        do bloco 9, inclusive os 9900 que ainda vão ser escritos, o 9990 e o
        9999.  A ordem aqui existe para fechar essa conta sem chute.
        """
        self._add("9001", "0")

        contagem = self._resultado.contagem_por_tipo()
        tipos = sorted(contagem)
        # +1 pelo 9900 do próprio "9900", +1 pelo 9990, +1 pelo 9999.
        contagem["9900"] = len(tipos) + 3
        contagem["9990"] = 1
        contagem["9999"] = 1

        for tipo in sorted(contagem):
            self._add("9900", tipo, contagem[tipo])

        do_bloco_9 = sum(1 for r in self._resultado.registros if r.tipo.startswith("9"))
        self._add("9990", do_bloco_9 + 2)  # +9990 +9999
        self._add("9999", len(self._resultado.registros) + 1)
