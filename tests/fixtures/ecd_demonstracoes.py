"""ECD anual com os defeitos de uma escrituração real, em números pequenos.

Uma ECD de 2025, validada e transmitida pelo PVA, expôs o que as fixtures
não tinham (os dados reais não entram no repositório; esta é a reprodução
com números inventados):

* **superior trocado no I050**: CLIENTES A RECEBER e a PECLD penduradas em
  APLICAÇÕES FINANCEIRAS, e INVESTIMENTOS dentro dos EMPRÉSTIMOS MÚTUOS do
  realizável a longo prazo — o J100 da mesma ECD tem a estrutura certa;
* **PL com natureza 02**, dentro de "PATRIMÔNIO LÍQUIDO" sob o PASSIVO;
* **CMV e despesas financeiras** com o referencial genérico
  (3.01.01.09.01.99 para tudo que não é receita, dedução ou CMV) — o grupo
  e o nome da conta é que dizem o que são;
* "(-) DEVOLUÇÃO DE MERCADORIAS" **dentro do CMV** (devolução de compra, não
  dedução da receita);
* **transferência entre contas da própria empresa** banco a banco, por uma
  conta de passagem "TRANSFERÊNCIAS ENTRE CONTAS" no passivo, e para uma
  aplicação de liquidez imediata;
* **transação sem caixa**: veículo financiado, depreciação, devolução de
  compra abatida do fornecedor;
* **J150** com a coluna do exercício anterior e **J930** com o contador
  (CRC) e a própria empresa (e-CNPJ) como responsável legal.

Os I155, o I355 e o J100 são **derivados** dos saldos de abertura e dos
lançamentos, então a escrituração fecha por construção. Os números que os
testes esperam estão calculados à mão nos próprios testes.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

ANO = 2025
CNPJ = "11222333000181"
EMPRESA = "COMERCIAL EXEMPLO LTDA"
CONTADORA = "MARIA CONTADORA DE TESTE"
CPF_CONTADORA = "01234567890"  # começa com zero de propósito
CRC_CONTADORA = "012345/O-6"

# (código, superior no I050, natureza, S/A, nível, nome, referencial)
# Os superiores trocados estão marcados; o J100 usa `SUPERIOR_CERTO`.
PLANO = [
    ("1", "", "01", "S", 1, "ATIVO", ""),
    ("1.1", "1", "01", "S", 2, "ATIVO CIRCULANTE", ""),
    ("1.1.1", "1.1", "01", "S", 3, "DISPONÍVEL", ""),
    ("1.1.1.01", "1.1.1", "01", "A", 4, "CAIXA", "1.01.01.01.01"),
    ("1.1.1.02", "1.1.1", "01", "S", 4, "BANCOS CONTA MOVIMENTO", ""),
    ("1.1.1.02.01", "1.1.1.02", "01", "A", 5, "BANCO ALFA", "1.01.01.02.01"),
    ("1.1.1.02.02", "1.1.1.02", "01", "A", 5, "BANCO BETA", "1.01.01.02.01"),
    ("1.1.1.03", "1.1.1", "01", "S", 4, "APLICAÇÕES FINANCEIRAS LIQUIDEZ IMEDIATA", ""),
    ("1.1.1.03.01", "1.1.1.03", "01", "A", 5, "Aplicação renda fixa", "1.01.01.05.01"),
    ("1.1.2", "1.1", "01", "S", 3, "CLIENTES", ""),
    # superior trocado: APLICAÇÕES em vez de CLIENTES
    ("1.1.2.01", "1.1.1.03", "01", "A", 5, "CLIENTES A RECEBER", "1.01.02.02.01"),
    (
        "1.1.2.02",
        "1.1.1.03",
        "01",
        "A",
        5,
        "(-) PERDAS ESTIMADAS COM CREDITOS DE LIQUIDAÇÃO DUVIDOSA",
        "1.01.02.02.52",
    ),
    ("1.1.3", "1.1", "01", "S", 3, "ESTOQUE", ""),
    ("1.1.3.01", "1.1.3", "01", "A", 4, "MERCADORIAS PARA REVENDA", "1.01.03.01.01"),
    ("1.2", "1", "01", "S", 2, "ATIVO NÃO-CIRCULANTE", ""),
    ("1.2.1", "1.2", "01", "S", 3, "ATIVO REALIZÁVEL A LONGO PRAZO", ""),
    ("1.2.1.01", "1.2.1", "01", "S", 4, "EMPRÉSTIMOS MUTUOS", ""),
    ("1.2.1.01.01", "1.2.1.01", "01", "A", 5, "EMPRESTIMO MUTUO", "1.02.01.09.01"),
    ("1.2.2", "1.2", "01", "S", 3, "INVESTIMENTOS", ""),
    # superior trocado: EMPRÉSTIMOS MÚTUOS em vez de INVESTIMENTOS
    ("1.2.2.01", "1.2.1.01", "01", "A", 5, "INVESTIMENTOS", "1.02.02.10.10"),
    ("1.2.3", "1.2", "01", "S", 3, "IMOBILIZADO", ""),
    ("1.2.3.01", "1.2.3", "01", "A", 4, "VEÍCULOS", "1.02.03.01.08"),
    ("1.2.3.02", "1.2.3", "01", "A", 4, "(-) DEPRECIAÇÃO ACUMULADA", "1.02.03.01.30"),
    ("2", "", "02", "S", 1, "PASSIVO", ""),
    ("2.1", "2", "02", "S", 2, "PASSIVO CIRCULANTE", ""),
    ("2.1.1", "2.1", "02", "A", 3, "FORNECEDORES", "2.01.01.03.01"),
    ("2.1.2", "2.1", "02", "S", 3, "EMPRÉSTIMOS E FINANCIAMENTOS", ""),
    ("2.1.2.01", "2.1.2", "02", "A", 4, "EMPRÉSTIMO BANCÁRIO", "2.01.01.07.02"),
    ("2.1.2.02", "2.1.2", "02", "A", 4, "TRANSFERÊNCIAS ENTRE CONTAS", "2.01.01.07.02"),
    ("2.1.3", "2.1", "02", "S", 3, "OBRIGAÇÕES TRIBUTÁRIAS", ""),
    ("2.1.3.01", "2.1.3", "02", "A", 4, "ICMS A RECOLHER", "2.01.01.09.03"),
    ("2.2", "2", "02", "S", 2, "PASSIVO NÃO-CIRCULANTE", ""),
    ("2.2.1", "2.2", "02", "A", 3, "FINANCIAMENTO DE VEÍCULOS", "2.02.01.01.03"),
    ("2.3", "2", "02", "S", 2, "PATRIMÔNIO LÍQUIDO", ""),
    ("2.3.1", "2.3", "02", "A", 3, "CAPITAL SOCIAL", "2.03.01.01.01"),
    ("2.3.2", "2.3", "02", "A", 3, "LUCROS OU PREJUÍZOS ACUMULADOS", "2.03.04.01.11"),
    ("3", "", "04", "S", 1, "RECEITAS", ""),
    ("3.1", "3", "04", "S", 2, "RECEITA BRUTA DE VENDAS", ""),
    ("3.1.01", "3.1", "04", "A", 3, "RECEITA VENDA DE MERCADORIAS", "3.01.01.01.01.05"),
    ("3.2", "3", "04", "S", 2, "DEDUÇÃO DA RECEITA BRUTA", ""),
    ("3.2.01", "3.2", "04", "A", 3, "(-) ICMS", "3.01.01.01.02.03"),
    ("4", "", "04", "S", 1, "CUSTOS E DESPESAS", ""),
    ("4.1", "4", "04", "S", 2, "(-) CMV", ""),
    ("4.1.1", "4.1", "04", "S", 3, "CUSTO DA MERCADORIA VENDIDA", ""),
    ("4.1.1.01", "4.1.1", "04", "A", 4, "COMPRAS DE MERCADORIAS", "3.01.01.03.01.02"),
    ("4.1.1.02", "4.1.1", "04", "A", 4, "(-) DEVOLUÇÃO DE MERCADORIAS", "3.01.01.03.01.02"),
    ("4.2", "4", "04", "S", 2, "DESPESAS GERAIS", ""),
    ("4.2.01", "4.2", "04", "A", 3, "ENERGIA ELETRICA", "3.01.01.09.01.99"),
    ("4.2.02", "4.2", "04", "A", 3, "SALÁRIOS", "3.01.01.09.01.99"),
    ("4.2.03", "4.2", "04", "A", 3, "DESPESA DE DEPRECIAÇÃO", "3.01.01.09.01.99"),
    ("4.3", "4", "04", "S", 2, "DESPESAS FINANCEIRAS", ""),
    ("4.3.01", "4.3", "04", "A", 3, "IOF", "3.01.01.09.01.99"),
    ("4.3.02", "4.3", "04", "A", 3, "TARIFAS BANCÁRIAS", "3.01.01.09.01.99"),
    ("4.4", "4", "04", "S", 2, "RECEITAS FINANCEIRAS", ""),
    ("4.4.01", "4.4", "04", "A", 3, "RENDIMENTOS DE APLICAÇÕES", "3.01.01.09.01.99"),
    ("4.4.02", "4.4", "04", "A", 3, "OUTRAS RECEITAS NÃO OPERACIONAIS", "3.01.01.09.01.99"),
    ("5", "", "09", "S", 1, "CONTAS DE APURAÇÃO", ""),
    ("5.1", "5", "09", "A", 2, "RESULTADO DO EXERCÍCIO", ""),
]

SUPERIOR_CERTO = {"1.1.2.01": "1.1.2", "1.1.2.02": "1.1.2", "1.2.2.01": "1.2.2"}

ABERTURA = {
    "1.1.1.01": 500,
    "1.1.1.02.01": 5_000,
    "1.1.1.02.02": 1_000,
    "1.1.1.03.01": 2_000,
    "1.1.2.01": 3_000,
    "1.1.2.02": -100,
    "1.1.3.01": 4_000,
    "1.2.2.01": 1_500,
    "1.2.3.01": 10_000,
    "1.2.3.02": -2_000,
    "2.1.1": -3_000,
    "2.1.2.01": -4_000,
    "2.2.1": -5_000,
    "2.3.1": -10_000,
    "2.3.2": -2_900,
}

# (número, dia, mês, indicador, [(conta, valor, D/C, histórico)])
LANCAMENTOS = [
    ("1", 10, 1, "N", [("1.1.2.01", 10_000, "D", "VENDA A PRAZO"), ("3.1.01", 10_000, "C", "")]),
    ("2", 10, 1, "N", [("3.2.01", 1_200, "D", "ICMS S/ VENDA"), ("2.1.3.01", 1_200, "C", "")]),
    (
        "3",
        20,
        1,
        "N",
        [("1.1.1.02.01", 8_000, "D", "RECEBIMENTO DE CLIENTES"), ("1.1.2.01", 8_000, "C", "")],
    ),
    (
        "4",
        25,
        1,
        "N",
        [
            ("1.1.1.02.02", 3_000, "D", "TED MESMA TITULARIDADE"),
            ("1.1.1.02.01", 3_000, "C", ""),
        ],
    ),
    (
        "5",
        26,
        1,
        "N",
        [("2.1.2.02", 2_000, "D", "PIX EMITIDO MESMA TIT"), ("1.1.1.02.01", 2_000, "C", "")],
    ),
    (
        "6",
        26,
        1,
        "N",
        [("1.1.1.02.02", 2_000, "D", "PIX RECEBIDO MESMA TIT"), ("2.1.2.02", 2_000, "C", "")],
    ),
    (
        "7",
        27,
        1,
        "N",
        [("1.1.1.03.01", 1_000, "D", "APLICACAO AUTOMATICA"), ("1.1.1.02.02", 1_000, "C", "")],
    ),
    ("8", 5, 2, "N", [("4.1.1.01", 5_000, "D", "COMPRA A PRAZO"), ("2.1.1", 5_000, "C", "")]),
    (
        "9",
        15,
        2,
        "N",
        [("2.1.1", 4_000, "D", "PAGAMENTO A FORNECEDOR"), ("1.1.1.02.02", 4_000, "C", "")],
    ),
    (
        "10",
        16,
        2,
        "N",
        [("2.1.1", 500, "D", "DEVOLUCAO DE COMPRA"), ("4.1.1.02", 500, "C", "")],
    ),
    (
        "11",
        20,
        2,
        "N",
        [("2.1.3.01", 1_200, "D", "PAGAMENTO ICMS"), ("1.1.1.02.01", 1_200, "C", "")],
    ),
    ("12", 25, 2, "N", [("4.2.01", 300, "D", "ENERGIA"), ("1.1.1.02.01", 300, "C", "")]),
    ("13", 28, 2, "N", [("4.2.02", 1_000, "D", "FOLHA"), ("1.1.1.02.01", 1_000, "C", "")]),
    (
        "14",
        28,
        2,
        "N",
        [
            ("4.3.01", 20, "D", "IOF"),
            ("4.3.02", 30, "D", "TARIFAS"),
            ("1.1.1.02.01", 50, "C", ""),
        ],
    ),
    (
        "15",
        5,
        3,
        "N",
        [("1.1.1.02.01", 6_000, "D", "EMPRESTIMO BANCARIO"), ("2.1.2.01", 6_000, "C", "")],
    ),
    (
        "16",
        10,
        3,
        "N",
        [("2.1.2.01", 1_500, "D", "AMORTIZACAO"), ("1.1.1.02.01", 1_500, "C", "")],
    ),
    (
        "17",
        15,
        3,
        "N",
        [("1.2.3.01", 4_000, "D", "COMPRA DE VEICULO"), ("1.1.1.02.01", 4_000, "C", "")],
    ),
    (
        "18",
        16,
        3,
        "N",
        [("1.2.3.01", 2_000, "D", "VEICULO FINANCIADO"), ("2.2.1", 2_000, "C", "")],
    ),
    (
        "19",
        31,
        3,
        "N",
        [("4.2.03", 600, "D", "DEPRECIACAO"), ("1.2.3.02", 600, "C", "")],
    ),
    (
        "20",
        31,
        3,
        "N",
        [("1.2.1.01.01", 700, "D", "MUTUO CONCEDIDO"), ("1.1.1.02.01", 700, "C", "")],
    ),
    (
        "21",
        5,
        4,
        "N",
        [("1.1.1.02.01", 250, "D", "RECEITA EVENTUAL"), ("4.4.02", 250, "C", "")],
    ),
    (
        "22",
        30,
        4,
        "N",
        [("1.1.1.03.01", 40, "D", "RENDIMENTO"), ("4.4.01", 40, "C", "")],
    ),
    (
        "23",
        31,
        12,
        "E",
        [
            ("3.1.01", 10_000, "D", "ENCERRAMENTO"),
            ("4.1.1.02", 500, "D", ""),
            ("4.4.01", 40, "D", ""),
            ("4.4.02", 250, "D", ""),
            ("5.1", 10_790, "C", ""),
        ],
    ),
    (
        "24",
        31,
        12,
        "E",
        [
            ("5.1", 8_150, "D", "ENCERRAMENTO"),
            ("3.2.01", 1_200, "C", ""),
            ("4.1.1.01", 5_000, "C", ""),
            ("4.2.01", 300, "C", ""),
            ("4.2.02", 1_000, "C", ""),
            ("4.2.03", 600, "C", ""),
            ("4.3.01", 20, "C", ""),
            ("4.3.02", 30, "C", ""),
        ],
    ),
    (
        "25",
        31,
        12,
        "E",
        [("5.1", 2_640, "D", "LUCRO DO EXERCICIO"), ("2.3.2", 2_640, "C", "")],
    ),
]

# DRE do exercício anterior, como a ECD a publica no J150 (VL_CTA_INI).
DRE_ANTERIOR = {
    "3.1.01": -8_000,
    "3.2.01": 960,
    "4.1.1.01": 4_000,
    "4.2.01": 200,
}


def _valor(v: float) -> str:
    return f"{abs(v):.2f}".replace(".", ",")


def _dc(v: float) -> str:
    return "C" if v < 0 else "D"


def gerar_ecd_demonstracoes(destino: Path, *, com_j100: bool = True) -> Path:
    """Escreve a ECD. Sem `com_j100`, sai sem bloco J (nem balanço, nem DRE, nem J930)."""
    plano = {cod: (sup, nat, ind, niv, nome, ref) for cod, sup, nat, ind, niv, nome, ref in PLANO}

    movimento: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    antes_do_encerramento: dict[str, float] = defaultdict(float)
    for _num, _dia, _mes, indicador, partidas in LANCAMENTOS:
        for cod, valor, dc, _hist in partidas:
            movimento[cod][0 if dc == "D" else 1] += valor
            if indicador != "E" and plano[cod][1] == "04":
                antes_do_encerramento[cod] += valor if dc == "D" else -valor

    analiticas = [c for c, (_s, _n, ind, *_r) in plano.items() if ind == "A"]
    saldo_final = {c: ABERTURA.get(c, 0) + movimento[c][0] - movimento[c][1] for c in analiticas}

    linhas = [
        f"|0000|LECD|0101{ANO}|3112{ANO}|{EMPRESA}|{CNPJ}|SP||3550308|||0|1|0||1|0||N|N|0|0||",
        "|I001|0|",
        "|I010|G|009|",
    ]
    for cod, (sup, nat, ind, niv, nome, ref) in plano.items():
        linhas.append(f"|I050|0101{ANO}|{nat}|{ind}|{niv}|{cod}|{sup}|{nome}|")
        if ind == "A":
            if ref:
                linhas.append(f"|I051||{ref}|")
            linhas.append(f"|I052||{cod}|")

    linhas.append(f"|I150|0101{ANO}|3112{ANO}|")
    for cod in analiticas:
        si = ABERTURA.get(cod, 0)
        deb, cred = movimento[cod]
        sf = saldo_final[cod]
        if not (si or deb or cred):
            continue
        linhas.append(
            f"|I155|{cod}||{_valor(si)}|{_dc(si)}|{_valor(deb)}|{_valor(cred)}"
            f"|{_valor(sf)}|{_dc(sf)}|"
        )

    for num, dia, mes, indicador, partidas in LANCAMENTOS:
        total = sum(v for _c, v, dc, _h in partidas if dc == "D")
        data = f"{dia:02d}{mes:02d}{ANO}"
        linhas.append(f"|I200|{num}|{data}|{_valor(total)}|{indicador}||")
        for cod, valor, dc, hist in partidas:
            linhas.append(f"|I250|{cod}||{_valor(valor)}|{dc}|||{hist}||")

    linhas.append(f"|I350|3112{ANO}|")
    for cod, valor in antes_do_encerramento.items():
        linhas.append(f"|I355|{cod}||{_valor(valor)}|{_dc(valor)}|")
    linhas.append("|I990|0|")

    linhas.append("|J001|0|")
    if com_j100:
        linhas += _bloco_j(plano, analiticas, saldo_final, antes_do_encerramento)
    linhas.append("|J990|0|")
    linhas += ["|9001|0|", f"|9999|{len(linhas) + 1}|"]
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return destino


def _bloco_j(plano, analiticas, saldo_final, resultado) -> list[str]:
    """J005/J100/J150 com a estrutura certa, e o J930."""
    superior = {cod: SUPERIOR_CERTO.get(cod, dados[0]) for cod, dados in plano.items()}
    patrimoniais = [c for c in plano if plano[c][1] in ("01", "02")]

    def soma(cod: str, saldos: dict[str, float]) -> float:
        if plano[cod][2] == "A":
            return saldos.get(cod, 0)
        return sum(soma(f, saldos) for f in patrimoniais if superior[f] == cod)

    linhas = [f"|J005|0101{ANO}|3112{ANO}|1||"]
    for cod in patrimoniais:
        _sup, nat, ind, niv, nome, _ref = plano[cod]
        grupo = "A" if nat == "01" else "P"
        atual, anterior = soma(cod, saldo_final), soma(cod, ABERTURA)
        linhas.append(
            f"|J100|{cod}|{'T' if ind == 'S' else 'D'}|{niv}|{superior[cod] if niv > 1 else ''}"
            f"|{grupo}|{nome}|{_valor(anterior)}|{_dc(anterior)}|{_valor(atual)}|{_dc(atual)}||"
        )
    for ordem, cod in enumerate(c for c in analiticas if plano[c][1] == "04"):
        atual, anterior = resultado.get(cod, 0), DRE_ANTERIOR.get(cod, 0)
        linhas.append(
            f"|J150|{ordem + 1}|{cod}|D|5|DRE|{plano[cod][4]}|{_valor(anterior)}|{_dc(anterior)}"
            f"|{_valor(atual)}|{_dc(atual)}|{'R' if atual < 0 else 'D'}||"
        )
    linhas += [
        f"|J930|{CONTADORA}|{CPF_CONTADORA}|Contador|900|{CRC_CONTADORA}"
        f"|contadora@exemplo.test|11999999999|SP||01012020|N|",
        f"|J930|{EMPRESA}|{CNPJ}|Pessoa Jurídica (e-CNPJ ou e-PJ)|001|||11999999999||||S|",
    ]
    return linhas
