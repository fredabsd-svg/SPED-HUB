"""Geração de escriturações a partir dos documentos importados.

Até aqui o SPED-HUB **lia** arquivos SPED prontos (`src/parsers/`). Este
pacote faz o caminho inverso: pega os documentos da Central, aplica a camada
efetiva — normalizado mais ajustes — e monta o arquivo.

Hoje existe o gerador da EFD ICMS/IPI. A EFD-Contribuições segue no
`docs/roadmap.md`.
"""

from src.escrituracoes.efd_icms import (
    BLOCOS,
    CampoObrigatorioAusente,
    GeradorEFDICMS,
    Registro,
    ResultadoGeracao,
    formatar_data,
    formatar_valor,
)

__all__ = [
    "BLOCOS",
    "CampoObrigatorioAusente",
    "GeradorEFDICMS",
    "Registro",
    "ResultadoGeracao",
    "formatar_data",
    "formatar_valor",
]
