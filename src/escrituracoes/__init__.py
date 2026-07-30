"""Geração de escriturações a partir dos documentos importados.

Até aqui o SPED-HUB **lia** arquivos SPED prontos (`src/parsers/`). Este
pacote faz o caminho inverso: pega os documentos da Central, aplica a camada
efetiva — normalizado mais ajustes — e monta o arquivo.

O que os geradores têm em comum vive em :mod:`src.escrituracoes.base`:
formatação do leiaute, estrutura de registro e as contagens do bloco 9. Essa
última é a razão principal de a base existir — é onde gerador próprio erra, e
acertá-la numa escrituração e errá-la na seguinte seria o resultado natural de
duplicar o código.
"""

from src.escrituracoes.base import (
    CampoObrigatorioAusente,
    GeradorBase,
    Registro,
    ResultadoGeracao,
    formatar_data,
    formatar_valor,
)
from src.escrituracoes.efd_contribuicoes import (
    ATIVIDADES,
    REGIMES,
    GeradorEFDContribuicoes,
)
from src.escrituracoes.efd_icms import BLOCOS, COD_VER, GeradorEFDICMS

__all__ = [
    "ATIVIDADES",
    "BLOCOS",
    "COD_VER",
    "REGIMES",
    "CampoObrigatorioAusente",
    "GeradorBase",
    "GeradorEFDContribuicoes",
    "GeradorEFDICMS",
    "Registro",
    "ResultadoGeracao",
    "formatar_data",
    "formatar_valor",
]
