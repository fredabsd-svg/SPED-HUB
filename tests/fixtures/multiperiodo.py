"""ECD trimestral com três I150 mensais, sintéticas e centro de custo.

A `ecd_sample.txt` tem um único I150 e é o formato que o código assumia.  Uma
ECD real traz um I150/I155 por mês, e o I155 — como o I250 — só existe para
conta **analítica** (o manual do leiaute diz "código da conta analítica").
Esta escrituração exercita exatamente o que a amostra não exercita:

* três períodos (janeiro, fevereiro e março), com o saldo final de um mês
  igual ao inicial do seguinte;
* sintéticas em três níveis, **sem** I155 próprio;
* uma despesa rateada em dois centros de custo (CC1 e CC2), em I155, I250
  e I355;
* uma conta que só aparece no último mês (despesa de depreciação);
* dois lançamentos na mesma data com números 9 e 10 (a ordem textual põe o
  "10" antes do "9");
* um lançamento com duas partidas na mesma conta (despesas administrativas,
  CC1 e CC2);
* encerramento do exercício (lançamento "E") em 31/03.

Os I155 e I355 são **derivados** dos lançamentos e dos saldos de abertura,
então a escrituração fecha por construção.  Os números esperados pelos
testes são calculados à mão nos próprios testes — não reaproveitam as contas
deste gerador.

Saldos em 31/03 (fator 1): ativo 221.000, passivo 64.000, PL 157.000;
resultado do trimestre 21.000; caixa e equivalentes de 60.000 para 66.000.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from pathlib import Path

# (código, superior, natureza, S/A, nível, nome)
PLANO = [
    ("1", "", "01", "S", 1, "ATIVO"),
    ("1.1", "1", "01", "S", 2, "ATIVO CIRCULANTE"),
    ("1.1.01", "1.1", "01", "A", 3, "CAIXA"),
    ("1.1.02", "1.1", "01", "A", 3, "BANCOS CONTA MOVIMENTO"),
    ("1.1.03", "1.1", "01", "A", 3, "CLIENTES"),
    ("1.1.04", "1.1", "01", "A", 3, "ESTOQUES DE MERCADORIAS"),
    ("1.2", "1", "01", "S", 2, "ATIVO NAO CIRCULANTE"),
    ("1.2.01", "1.2", "01", "A", 3, "IMOBILIZADO"),
    ("1.2.02", "1.2", "01", "A", 3, "(-) DEPRECIACAO ACUMULADA"),
    ("2", "", "02", "S", 1, "PASSIVO"),
    ("2.1", "2", "02", "S", 2, "PASSIVO CIRCULANTE"),
    ("2.1.01", "2.1", "02", "A", 3, "FORNECEDORES"),
    ("2.1.02", "2.1", "02", "A", 3, "IMPOSTOS A RECOLHER"),
    ("2.2", "2", "02", "S", 2, "PASSIVO NAO CIRCULANTE"),
    ("2.2.01", "2.2", "02", "A", 3, "EMPRESTIMOS BANCARIOS"),
    ("3", "", "03", "S", 1, "PATRIMONIO LIQUIDO"),
    ("3.1", "3", "03", "A", 2, "CAPITAL SOCIAL"),
    ("3.2", "3", "03", "A", 2, "LUCROS ACUMULADOS"),
    ("4", "", "04", "S", 1, "RESULTADO"),
    ("4.1", "4", "04", "S", 2, "RECEITAS"),
    ("4.1.01", "4.1", "04", "A", 3, "RECEITA DE VENDAS"),
    ("4.2", "4", "04", "S", 2, "DESPESAS"),
    ("4.2.01", "4.2", "04", "A", 3, "CMV"),
    ("4.2.02", "4.2", "04", "A", 3, "DESPESAS ADMINISTRATIVAS"),
    ("4.2.03", "4.2", "04", "A", 3, "DESPESA DE DEPRECIACAO"),
]

# Saldos de abertura em 01/01, sinalizados (devedor +, credor −).
ABERTURA = {
    "1.1.01": 10_000,
    "1.1.02": 50_000,
    "1.1.03": 30_000,
    "1.1.04": 20_000,
    "1.2.01": 100_000,
    "1.2.02": -10_000,
    "2.1.01": -25_000,
    "2.1.02": -5_000,
    "2.2.01": -40_000,
    "3.1": -100_000,
    "3.2": -30_000,
}

# (número, dia, mês, indicador, [(conta, centro de custo, valor, D/C, histórico)])
LANCAMENTOS = [
    (
        "1",
        10,
        1,
        "N",
        [("1.1.03", "", 40_000, "D", "VENDA A PRAZO"), ("4.1.01", "", 40_000, "C", "")],
    ),
    (
        "2",
        10,
        1,
        "N",
        [("4.2.01", "", 15_000, "D", "BAIXA DE ESTOQUE"), ("1.1.04", "", 15_000, "C", "")],
    ),
    (
        "3",
        20,
        1,
        "N",
        [("1.1.02", "", 35_000, "D", "RECEBIMENTO DE CLIENTES"), ("1.1.03", "", 35_000, "C", "")],
    ),
    (
        "4",
        25,
        1,
        "N",
        [
            ("4.2.02", "CC1", 4_000, "D", "DESPESAS DO ADMINISTRATIVO"),
            ("4.2.02", "CC2", 2_000, "D", "DESPESAS DO COMERCIAL"),
            ("1.1.02", "", 6_000, "C", "PAGAMENTO DE DESPESAS"),
        ],
    ),
    (
        "5",
        5,
        2,
        "N",
        [("1.1.04", "", 18_000, "D", "COMPRA A PRAZO"), ("2.1.01", "", 18_000, "C", "")],
    ),
    (
        "6",
        15,
        2,
        "N",
        [("2.1.01", "", 20_000, "D", "PAGAMENTO A FORNECEDOR"), ("1.1.02", "", 20_000, "C", "")],
    ),
    (
        "7",
        20,
        2,
        "N",
        [("1.2.01", "", 12_000, "D", "COMPRA DE EQUIPAMENTO"), ("1.1.02", "", 12_000, "C", "")],
    ),
    (
        "8",
        28,
        2,
        "N",
        [("1.1.02", "", 10_000, "D", "AUMENTO DE CAPITAL"), ("3.1", "", 10_000, "C", "")],
    ),
    ("9", 5, 3, "N", [("1.1.01", "", 8_000, "D", "VENDA A VISTA"), ("4.1.01", "", 8_000, "C", "")]),
    (
        "10",
        5,
        3,
        "N",
        [("4.2.01", "", 3_000, "D", "BAIXA DE ESTOQUE"), ("1.1.04", "", 3_000, "C", "")],
    ),
    (
        "11",
        10,
        3,
        "N",
        [("2.2.01", "", 5_000, "D", "AMORTIZACAO DE EMPRESTIMO"), ("1.1.02", "", 5_000, "C", "")],
    ),
    ("12", 15, 3, "N", [("4.2.02", "CC1", 1_000, "D", "TAXAS"), ("2.1.02", "", 1_000, "C", "")]),
    (
        "13",
        31,
        3,
        "N",
        [("4.2.03", "", 2_000, "D", "DEPRECIACAO DO TRIMESTRE"), ("1.2.02", "", 2_000, "C", "")],
    ),
    (
        "14",
        31,
        3,
        "N",
        [("3.2", "", 4_000, "D", "LUCROS DISTRIBUIDOS"), ("1.1.02", "", 4_000, "C", "")],
    ),
    (
        "15",
        31,
        3,
        "E",
        [
            ("4.1.01", "", 48_000, "D", "ENCERRAMENTO DO EXERCICIO"),
            ("4.2.01", "", 18_000, "C", ""),
            ("4.2.02", "CC1", 5_000, "C", ""),
            ("4.2.02", "CC2", 2_000, "C", ""),
            ("4.2.03", "", 2_000, "C", ""),
            ("3.2", "", 21_000, "C", ""),
        ],
    ),
]


def _valor(v: float) -> str:
    return f"{abs(v):.2f}".replace(".", ",")


def _dc(v: float) -> str:
    return "C" if v < 0 else "D"


def gerar_ecd_multiperiodo(
    destino: Path,
    *,
    ano: int = 2024,
    fator: int = 1,
    cnpj: str = "11222333000181",
    empresa: str = "EMPRESA TRIMESTRAL LTDA",
) -> Path:
    """Escreve a ECD trimestral. ``fator`` multiplica todos os valores.

    Com ``fator=2`` e ``ano=2023`` sai uma escrituração do ano anterior com
    o dobro de cada número — é o que os testes de comparativo usam para ver
    que a coluna "período anterior" não repete a atual.
    """
    ultimo_mes = 3
    fim = calendar.monthrange(ano, ultimo_mes)[1]
    linhas = [
        f"|0000|LECD|0101{ano}|{fim:02d}{ultimo_mes:02d}{ano}|{empresa}|{cnpj}|SP||3550308|||0|1|0||1|0||N|N|0|0||",
        "|I001|0|",
        "|I010|G|009|",
    ]
    for cod, sup, nat, ind, nivel, nome in PLANO:
        linhas.append(f"|I050|0101{ano}|{nat}|{ind}|{nivel}|{cod}|{sup}|{nome}|")
    linhas.append(f"|I100|0101{ano}|CC1|ADMINISTRATIVO|")
    linhas.append(f"|I100|0101{ano}|CC2|COMERCIAL|")

    # Saldo corrente por (conta, centro de custo) e movimento por mês.
    saldo: dict[tuple[str, str], float] = defaultdict(float)
    for cod, valor in ABERTURA.items():
        saldo[(cod, "")] = valor * fator
    movimento: dict[int, dict[tuple[str, str], list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0])
    )
    resultado_antes: dict[tuple[str, str], float] = defaultdict(float)
    natureza = {cod: nat for cod, _sup, nat, _ind, _niv, _nome in PLANO}
    for _num, _dia, mes, ind_lcto, partidas in LANCAMENTOS:
        for cod, ccus, valor, dc, _hist in partidas:
            valor *= fator
            movimento[mes][(cod, ccus)][0 if dc == "D" else 1] += valor
            if ind_lcto != "E" and natureza[cod] == "04":
                resultado_antes[(cod, ccus)] += valor if dc == "D" else -valor

    for mes in range(1, ultimo_mes + 1):
        ultimo_dia = calendar.monthrange(ano, mes)[1]
        linhas.append(f"|I150|01{mes:02d}{ano}|{ultimo_dia:02d}{mes:02d}{ano}|")
        chaves = sorted(set(saldo) | set(movimento[mes]))
        for chave in chaves:
            debito, credito = movimento[mes].get(chave, [0.0, 0.0])
            inicial = saldo[chave]
            final = inicial + debito - credito
            if not (inicial or debito or credito or final):
                continue
            cod, ccus = chave
            linhas.append(
                f"|I155|{cod}|{ccus}|{_valor(inicial)}|{_dc(inicial)}|{_valor(debito)}|"
                f"{_valor(credito)}|{_valor(final)}|{_dc(final)}|"
            )
            saldo[chave] = final

    for num, dia, mes, ind_lcto, partidas in LANCAMENTOS:
        total = sum(v for _c, _cc, v, dc, _h in partidas if dc == "D") * fator
        linhas.append(f"|I200|{num}|{dia:02d}{mes:02d}{ano}|{_valor(total)}|{ind_lcto}||")
        for cod, ccus, valor, dc, hist in partidas:
            linhas.append(f"|I250|{cod}|{ccus}|{_valor(valor * fator)}|{dc}|||{hist}||")

    linhas.append(f"|I350|{fim:02d}{ultimo_mes:02d}{ano}|")
    for (cod, ccus), valor in sorted(resultado_antes.items()):
        linhas.append(f"|I355|{cod}|{ccus}|{_valor(valor)}|{_dc(valor)}|")
    linhas.append("|I990|0|")
    linhas.append("|9001|1|")
    linhas.append("|9999|0|")

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return destino
