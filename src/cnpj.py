"""O CNPJ como texto — inclusive quando ele tem letras.

A Receita implantou o primeiro CNPJ alfanumérico em **31 de julho de 2026**
(IN RFB 2.229/2024).  O formato continua com 14 posições, mas as doze
primeiras passam a aceitar letras maiúsculas de A a Z além dos algarismos:

    AA.AAA.AAA/AAAA-DV

Os dois dígitos verificadores seguem numéricos.  CNPJ já existente não muda,
e continua válido — o que obriga o algoritmo novo a produzir exatamente o
mesmo resultado para entrada só de algarismos.  `tests/test_cnpj.py` cobra
essa propriedade: é ela que impede uma reescrita de quebrar a base inteira.

Procedência (§8.1): algoritmo e exemplo conferidos contra "CNPJ Alfanumérico —
Perguntas e Respostas", da Receita Federal, pergunta 14, em 2026-08-20.

O perigo aqui não é o cálculo, é a **normalização**.  Tirar "tudo que não é
dígito" de um CNPJ alfanumérico não devolve um CNPJ errado: devolve um CNPJ
de outra empresa, com aparência perfeita.  Por isso `normalizar` remove só a
pontuação que o formato prevê, e nada mais.
"""

from __future__ import annotations

import re

COMPRIMENTO = 14
POSICOES_DA_BASE = 12

# Só a pontuação do formato — ponto, barra, hífen e espaço em branco.  Não é
# `\D`: `\D` come as letras, que é justamente o defeito que este módulo fecha.
_PONTUACAO = re.compile(r"[.\-/\s]")
_VALIDO = re.compile(rf"^[0-9A-Z]{{{POSICOES_DA_BASE}}}[0-9]{{2}}$")

# Módulo 11, como no CNPJ de sempre.
_PESOS_PRIMEIRO = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESOS_SEGUNDO = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

# "Todos os caracteres [...] serão transformados pelo código ASCII": '0' vale
# 0 e 'A' vale 17, porque 48 é subtraído de cada um.  Para entrada numérica a
# conta se reduz ao valor do próprio algarismo, que é o que preserva os DV
# dos CNPJ que já existem.
_DESLOCAMENTO_ASCII = 48


def normalizar(bruto: str | None) -> str:
    """Sem pontuação e em maiúsculas, preservando letras e zeros à esquerda.

    Devolve `""` para entrada vazia.  Não valida: normalizar e conferir são
    coisas separadas, e quem importa arquivo de terceiro precisa guardar o
    que veio mesmo quando o dígito não fecha.
    """
    if not bruto:
        return ""
    return _PONTUACAO.sub("", str(bruto)).upper()


def _digito(corpo: str, pesos: tuple[int, ...]) -> str:
    soma = sum((ord(c) - _DESLOCAMENTO_ASCII) * peso for c, peso in zip(corpo, pesos, strict=True))
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def digitos_verificadores(base: str) -> str:
    """Os dois DV das doze primeiras posições, pelo módulo 11.

    O `resto < 2 → 0` não está escrito na cartilha da Receita, mas é forçado:
    sem ele o resultado seria 10 ou 11 em duas casas, e todo CNPJ existente
    terminado em 0 deixaria de validar — e a Receita garante que os números
    atuais continuam valendo.
    """
    corpo = normalizar(base)
    if len(corpo) != POSICOES_DA_BASE or not corpo.isalnum():
        raise ValueError(f"base de CNPJ deve ter {POSICOES_DA_BASE} posições: {base!r}")
    primeiro = _digito(corpo, _PESOS_PRIMEIRO)
    return primeiro + _digito(corpo + primeiro, _PESOS_SEGUNDO)


def bem_formado(cnpj: str | None) -> bool:
    """Catorze posições, as doze primeiras alfanuméricas e o DV numérico."""
    return bool(_VALIDO.match(normalizar(cnpj)))


def valido(cnpj: str | None) -> bool:
    """Bem formado **e** com o dígito verificador fechando."""
    normalizado = normalizar(cnpj)
    if not bem_formado(normalizado):
        return False
    return digitos_verificadores(normalizado[:POSICOES_DA_BASE]) == normalizado[POSICOES_DA_BASE:]


def alfanumerico(cnpj: str | None) -> bool:
    """Tem letra na base — ou seja, é do formato novo."""
    return any(c.isalpha() for c in normalizar(cnpj)[:POSICOES_DA_BASE])


def formatar(cnpj: str | None) -> str:
    """`12ABC34501DE35` → `12.ABC.345/01DE-35`; o que não couber sai como veio."""
    n = normalizar(cnpj)
    if len(n) != COMPRIMENTO:
        return n
    return f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
