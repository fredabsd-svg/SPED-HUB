"""Motor base de relatórios — dados → contexto → saída.

Fornece a infraestrutura comum para todos os relatórios:
- Convenção de sinais (IND_DC → valor sinalizado)
- Formatação pt-BR
- Estrutura de contexto para templates
"""

import datetime
from dataclasses import dataclass, field
from typing import Any


# ── Convenção de Sinais ────────────────────────────────────────────────────


def valor_sinalizado(vl: float, ind_dc: str) -> float:
    """Converte valor + IND_DC para valor sinalizado.

    Convenção interna: débito = positivo, crédito = negativo.
    """
    if ind_dc == "D":
        return abs(vl)
    elif ind_dc == "C":
        return -abs(vl)
    return vl


def saldo_por_natureza(vl_sinalizado: float, cod_nat: str) -> float:
    """Ajusta o sinal para exposição conforme a natureza da conta.

    Ativo (01) e Resultado/Despesa: débito = positivo
    Passivo (02), PL (03) e Resultado/Receita: crédito = positivo
    """
    if cod_nat in ("01",):  # Ativo — saldo devedor é positivo
        return vl_sinalizado
    elif cod_nat in ("02", "03"):  # Passivo, PL — saldo credor é positivo
        return -vl_sinalizado
    elif cod_nat == "04":  # Resultado — receitas (crédito) positivas, despesas (débito) negativas
        return -vl_sinalizado  # inverte: crédito vira positivo
    return vl_sinalizado


# ── Formatação ─────────────────────────────────────────────────────────────


def fmt_moeda(valor: float) -> str:
    """Formata valor como moeda pt-BR: 1.234.567,89."""
    if valor == 0:
        return "0,00"
    # Usa locale-independent formatting
    negativo = valor < 0
    v = abs(valor)
    inteiro = int(v)
    decimal = int(round((v - inteiro) * 100))
    # Formata parte inteira com separadores de milhar
    s_int = f"{inteiro:,}".replace(",", ".")
    s = f"{s_int},{decimal:02d}"
    if negativo:
        return f"({s})"  # negativos entre parênteses
    return s


def fmt_data(data: datetime.date) -> str:
    """Formata data em pt-BR: DD/MM/AAAA."""
    return data.strftime("%d/%m/%Y")


def fmt_data_hora(dt: datetime.datetime) -> str:
    """Formata data e hora: DD/MM/AAAA HH:MM."""
    return dt.strftime("%d/%m/%Y %H:%M")


# ── Contexto de Relatório ──────────────────────────────────────────────────


@dataclass
class ReportContext:
    """Contexto comum a todos os relatórios."""

    titulo: str
    empresa_nome: str = ""
    empresa_cnpj: str = ""
    periodo_ref: str = ""
    filtros_descricao: str = ""
    escritorio_nome: str = "SPED-HUB"
    data_emissao: str = field(default_factory=lambda: fmt_data_hora(datetime.datetime.now()))
    hash_ecd: str = ""
    total_paginas: int = 1
    pagina_atual: int = 1

    def to_dict(self) -> dict:
        return self.__dict__