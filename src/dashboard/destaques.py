"""Destaques do painel: o que os números da escrituração dizem, em frases.

É a leitura que o contador faria ao abrir a ECD — o balanço fecha? houve
lucro, com que margem? o passivo pesa quanto? — feita a partir dos mesmos
totais que os cartões mostram.  Nada aqui é estimado ou previsto: cada frase
é uma conta sobre valores importados, e o texto diz qual.

A única leitura que vai além da conta direta é a do balanço que não fecha
pelo valor exato do resultado: é o sinal típico de saldos anteriores ao
encerramento do exercício, e a frase diz isso em vez de acusar erro.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.reports.base import fmt_moeda

OK = "ok"
ALERTA = "alerta"
INFO = "info"

_CENTAVO = 0.005


@dataclass(frozen=True)
class Destaque:
    tipo: str  # ok | alerta | info
    titulo: str
    texto: str


def _moeda(valor: float) -> str:
    return f"R$ {fmt_moeda(valor)}"


def _inteiro(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _pct(valor: float) -> str:
    return f"{valor:.1f}".replace(".", ",") + "%"


def _fechamento(ativo: float, passivo: float, pl: float, resultado: float) -> Destaque:
    diferenca = ativo - (passivo + pl)
    if abs(diferenca) < _CENTAVO:
        return Destaque(OK, "O balanço fecha", f"Ativo e passivo + PL somam {_moeda(ativo)}.")
    if abs(resultado) >= _CENTAVO and abs(diferenca - resultado) < _CENTAVO:
        return Destaque(
            INFO,
            "Resultado ainda fora do PL",
            f"A diferença de {_moeda(abs(diferenca))} entre ativo e passivo + PL é o "
            "resultado do exercício: os saldos parecem anteriores ao encerramento.",
        )
    return Destaque(
        ALERTA,
        "O balanço não fecha",
        f"Ativo e passivo + PL diferem em {_moeda(abs(diferenca))}. "
        "Confira as validações da escrituração.",
    )


def _resultado(resultado: float, receita: float) -> Destaque:
    margem = (
        f", margem de {_pct(resultado / receita * 100)} sobre a receita bruta" if receita else ""
    )
    if resultado >= _CENTAVO:
        return Destaque(
            OK, f"Lucro de {_moeda(resultado)}", f"Resultado positivo no período{margem}."
        )
    if resultado <= -_CENTAVO:
        return Destaque(
            ALERTA, f"Prejuízo de {_moeda(-resultado)}", f"Resultado negativo no período{margem}."
        )
    return Destaque(INFO, "Resultado zerado", "A DRE não apurou lucro nem prejuízo no período.")


def _estrutura(ativo: float, passivo: float, pl: float) -> Destaque | None:
    if pl <= -_CENTAVO:
        return Destaque(
            ALERTA,
            "Patrimônio líquido negativo",
            f"O passivo ({_moeda(passivo)}) supera o ativo ({_moeda(ativo)}).",
        )
    if ativo >= _CENTAVO:
        return Destaque(
            INFO,
            f"Endividamento de {_pct(passivo / ativo * 100)}",
            "É a parte do ativo financiada por terceiros (passivo ÷ ativo total).",
        )
    return None


def _variacao(ativo: float, anterior: float | None) -> Destaque | None:
    if anterior is None or abs(anterior) < _CENTAVO:
        return None
    variacao = (ativo - anterior) / abs(anterior) * 100
    if abs(variacao) < 0.05:
        return Destaque(
            INFO, "Ativo estável", "O ativo total ficou igual ao do exercício anterior."
        )
    verbo = "cresceu" if variacao > 0 else "recuou"
    return Destaque(
        INFO,
        f"Ativo {verbo} {_pct(abs(variacao))}",
        f"Em relação ao exercício anterior: de {_moeda(anterior)} para {_moeda(ativo)}.",
    )


def _movimento(lancamentos: int, contas: int) -> Destaque:
    if lancamentos == 0:
        return Destaque(
            ALERTA,
            "Nenhum lançamento",
            "A escrituração trouxe saldos, mas nenhum lançamento (I200) no período.",
        )
    return Destaque(
        INFO,
        f"{_inteiro(lancamentos)} lançamento{'s' if lancamentos != 1 else ''}",
        f"Distribuídos num plano de {_inteiro(contas)} conta{'s' if contas != 1 else ''}.",
    )


def gerar_destaques(data) -> list[Destaque]:
    """Os destaques de um `DashboardData`, na ordem em que o contador lê.

    Recebe o objeto já montado pelo painel para não refazer consulta
    nenhuma: os destaques são sempre coerentes com os cartões ao lado.
    """
    ativo = data.ativo_total
    passivo = data.passivo_total
    pl = data.pl_total
    resultado = data.resultado_liquido
    itens = [
        _fechamento(ativo, passivo, pl, resultado),
        # A receita chega com o sinal contábil (crédito negativo).
        _resultado(resultado, abs(data.receita_liquida)),
        _estrutura(ativo, passivo, pl),
        _variacao(ativo, getattr(data, "ativo_anterior", None)),
        _movimento(data.num_lancamentos, data.num_contas),
    ]
    return [item for item in itens if item is not None]
