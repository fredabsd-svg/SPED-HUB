"""Gerador de ECDs sintéticas para exercitar arquivos grandes.

A fixture `ecd_sample.txt` tem 138 registros — suficiente para testar
correção contábil, inútil para testar o que acontece com um arquivo de
centenas de megabytes.  Este gerador produz escriturações válidas de
qualquer tamanho sem versionar arquivos enormes no repositório.
"""

from __future__ import annotations

from pathlib import Path


def gerar_ecd(
    destino: Path,
    *,
    lancamentos: int,
    contas: int = 200,
    cnpj: str = "00123456000199",
    empresa: str = "EMPRESA SINTETICA LTDA",
) -> Path:
    """Escreve uma ECD válida com ``lancamentos`` I200 (2 partidas cada).

    A escrituração fecha: cada lançamento tem um débito e um crédito de mesmo
    valor, então o balancete confere.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8") as arquivo:
        escrever = arquivo.write
        escrever(f"|0000|LECD|01012024|31122024|{empresa}|{cnpj}|SP||1234567||0|0|1|0|0|E||1|0||\n")
        escrever("|I001|0|\n")
        escrever("|I010|G|009|\n")
        escrever(
            "|I030|TERMO DE ABERTURA|1|Diario|500|EMPRESA TESTE|31123456789|11111111000191|01012015||BELO HORIZONTE|31122023|\n"
        )

        for i in range(contas):
            # Conta 1 é topo; as demais apontam para ela. O sup=1 na
            # própria conta 1 era auto-ciclo, hoje recusado na importação.
            sup = "" if i == 0 else "1"
            escrever(f"|I050|01012024|01|A|3|{i + 1}|{sup}|CONTA SINTETICA {i + 1}|\n")

        escrever("|I150|01012024|31122024|\n")
        for i in range(contas):
            escrever(f"|I155|{i + 1}||1000.00|D|5000.00|4000.00|2000.00|D|\n")

        for i in range(lancamentos):
            debito = (i % contas) + 1
            credito = ((i + 7) % contas) + 1
            escrever(f"|I200|{i + 1}|15012024|1000.00|N||\n")
            escrever(f"|I250|{debito}||500.00|D|||HISTORICO DO LANCAMENTO {i + 1}|001|\n")
            escrever(f"|I250|{credito}||500.00|C||||\n")

        escrever("|I350|31122024|\n")
        escrever("|I990|99|\n")
        escrever("|9001|0|\n")
        escrever("|9999|0|\n")
    return destino


def registros_esperados(lancamentos: int, contas: int = 200) -> dict[str, int]:
    """Contagem por tipo, para os testes conferirem sem recontar o arquivo."""
    return {
        "I050": contas,
        "I155": contas,
        "I200": lancamentos,
        "I250": lancamentos * 2,
    }
