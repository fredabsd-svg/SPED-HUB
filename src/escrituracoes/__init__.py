"""Geração de escriturações a partir dos documentos importados.

Até aqui o SPED-HUB **lia** arquivos SPED prontos (`src/parsers/`). Este
pacote faz o caminho inverso: pega os documentos da Central, aplica a camada
efetiva — normalizado mais ajustes — e monta o arquivo.

O que os geradores têm em comum vive em :mod:`src.escrituracoes.base`:
formatação do leiaute, estrutura de registro e as contagens do bloco 9. Essa
última é a razão principal de a base existir — é onde gerador próprio erra, e
acertá-la numa escrituração e errá-la na seguinte seria o resultado natural de
duplicar o código.

Gerado o arquivo, :mod:`src.escrituracoes.arquivadas` guarda o que saiu — a
terceira camada, ao lado do documento original e do tratamento fiscal. O
conteúdo é gravado, não reconstruído: regerar responde "o que eu enviaria
hoje", e a pergunta que a intimação faz é "o que você enviou".

:mod:`src.escrituracoes.leiaute` guarda os campos de cada registro na ordem
oficial, e ``GeradorBase._add`` confere cada linha contra ela — campo esquecido
no meio de um registro desloca todos os seguintes e produz arquivo que parece
certo.
"""

from src.escrituracoes.arquivadas import (
    TIPOS,
    Comparacao,
    TipoDesconhecido,
    arquivar,
    avisos_de,
    comparar,
    escrituracoes_do_documento,
    hash_do_conteudo,
)
from src.escrituracoes.base import (
    MODALIDADES_DE_FRETE,
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
from src.escrituracoes.espelhos import (
    Conferencia,
    Espelho,
    LinhaDocumento,
    TipoSemLeiaute,
    espelho,
)
from src.escrituracoes.leiaute import (
    EFD_CONTRIBUICOES,
    EFD_ICMS,
    CamposEmDesacordo,
    RegistroForaDoLeiaute,
    conferir,
)
from src.escrituracoes.reforma import (
    ANO_DE_TESTE,
    ApuracaoIBSCBS,
    ResultadoApuracao,
    Tributo,
)

__all__ = [
    "ANO_DE_TESTE",
    "ATIVIDADES",
    "BLOCOS",
    "COD_VER",
    "EFD_CONTRIBUICOES",
    "EFD_ICMS",
    "MODALIDADES_DE_FRETE",
    "REGIMES",
    "TIPOS",
    "ApuracaoIBSCBS",
    "CampoObrigatorioAusente",
    "CamposEmDesacordo",
    "Comparacao",
    "Conferencia",
    "GeradorBase",
    "GeradorEFDContribuicoes",
    "Espelho",
    "GeradorEFDICMS",
    "LinhaDocumento",
    "Registro",
    "RegistroForaDoLeiaute",
    "ResultadoApuracao",
    "ResultadoGeracao",
    "TipoDesconhecido",
    "TipoSemLeiaute",
    "Tributo",
    "arquivar",
    "avisos_de",
    "comparar",
    "conferir",
    "escrituracoes_do_documento",
    "espelho",
    "formatar_data",
    "formatar_valor",
    "hash_do_conteudo",
]
