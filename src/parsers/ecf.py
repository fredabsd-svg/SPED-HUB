"""Parser ECF — Escrituração Contábil Fiscal.

Processa arquivos da ECF (IRPJ/CSLL) no leiaute 7.
Extrai registros de interesse: 0000, 0010, 0020, J050, J051, J053, K030, K155, K156, K355, K356, L030, L100, L200, L300, M010, M030, M300, M350, M410, N030, N500, N600, N610, N620, N630, N650, N660, N670, P030, P100, P130, P150, P200, P300, P400, P500, Q100, R100, S100, T030, T120, T150, T170, T181, U100, U150, U180, X280, X291, X300, X310, X320, X330, X340, X350, X351, X352, X353, X354, X355, X356, X357, X358, X359, X360, X361, X362, X363, X364, X365, X366, X367, X368, X369, X370, X371, X372, X373, X374, X375, X376, X377, X378, X379, X380, X381, X382, X383, X384, X385, X386, X387, X388, X389, X390, X391, X392, X393, X394, X395, X396, X397, X398, X399, X400, X410, X420, X430, X450, X460, X470, X480, X490, X500, X510, Y540, Y550, Y560, Y570, Y580, Y590, Y600, Y612, Y620, Y630, Y640, Y650, Y660, Y671, Y672, Y680, Y690, Y720, Y800, Y810, Y820, Y830, Y840, Y850, Y860, Y870, Y880, Y890, Y900, Y910, Z010, Z020, Z030, Z040, Z050, Z060, Z070, Z080, Z090, Z100, Z110, Z120, Z130, Z140, Z150, Z160, Z170, Z180, Z190, Z200, Z210, Z220, Z230, Z240, Z250, Z260, Z270, Z280, Z290, Z300, Z310, Z320, Z330, Z340, Z350, Z360, Z370, Z380, Z390, Z400, Z410, Z420, Z430, Z440, Z450, Z460, Z470, Z480, Z490, Z500, Z510, Z520, Z530, Z540, Z550, Z560, Z570, Z580, Z590, Z600, Z610, Z620, Z630, Z640, Z650, Z660, Z670, Z680, Z690, Z700, Z710, Z720, Z730, Z740, Z750, Z760, Z770, Z780, Z790, Z800, Z810, Z820, Z830, Z840, Z850, Z860, Z870, Z880, Z890, Z900, Z910, Z920, Z930, Z940, Z950, Z960, Z970, Z980, Z990, 9001, 9100, 9900, 9999,
"""

import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# Registros de interesse da ECF
REGISTROS_INTERESSE = frozenset(
    {
        "0000",
        "0010",
        "0020",
        "0030",
        "0035",
        "J050",
        "J051",
        "J053",
        "K030",
        "K155",
        "K156",
        "K355",
        "K356",
        "L030",
        "L100",
        "L200",
        "L210",
        "L300",
        "M010",
        "M030",
        "M300",
        "M305",
        "M310",
        "M315",
        "M350",
        "M355",
        "M360",
        "M410",
        "M415",
        "M420",
        "M500",
        "N030",
        "N500",
        "N600",
        "N610",
        "N615",
        "N620",
        "N630",
        "N650",
        "N660",
        "N670",
        "P030",
        "P100",
        "P130",
        "P150",
        "P200",
        "P300",
        "P400",
        "P500",
        "Q100",
        "R100",
        "R150",
        "R200",
        "R300",
        "R500",
        "S100",
        "T030",
        "T120",
        "T150",
        "T170",
        "T181",
        "U100",
        "U150",
        "U180",
        "U182",
        "X280",
        "X291",
        "X292",
        "X300",
        "X305",
        "X310",
        "X320",
        "X330",
        "X340",
        "X350",
        "X351",
        "X352",
        "X353",
        "X354",
        "X355",
        "X356",
        "X357",
        "X358",
        "X359",
        "X360",
        "X361",
        "X362",
        "X363",
        "X364",
        "X365",
        "X366",
        "X367",
        "X368",
        "X369",
        "X370",
        "X371",
        "X372",
        "X373",
        "X374",
        "X375",
        "X376",
        "X377",
        "X378",
        "X379",
        "X380",
        "X381",
        "X382",
        "X383",
        "X384",
        "X385",
        "X386",
        "X387",
        "X388",
        "X389",
        "X390",
        "X391",
        "X392",
        "X393",
        "X394",
        "X395",
        "X396",
        "X397",
        "X398",
        "X399",
        "X400",
        "X410",
        "X420",
        "X430",
        "X450",
        "X460",
        "X470",
        "X480",
        "X490",
        "X500",
        "X510",
        "Y540",
        "Y550",
        "Y560",
        "Y570",
        "Y580",
        "Y590",
        "Y600",
        "Y612",
        "Y620",
        "Y630",
        "Y640",
        "Y650",
        "Y660",
        "Y671",
        "Y672",
        "Y680",
        "Y690",
        "Y720",
        "Y800",
        "Y810",
        "Y820",
        "Y830",
        "Y840",
        "Y850",
        "Y860",
        "Y870",
        "Y880",
        "Y890",
        "Y900",
        "Y910",
        "9001",
        "9100",
        "9900",
        "9999",
    }
)


def detectar_encoding(caminho: Path, bytes_amostra: int = 4096) -> str:
    with open(caminho, "rb") as f:
        raw = f.read(bytes_amostra)
    try:
        raw.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError:
        return "ISO-8859-1"


def _parse_valor(valor_str: str, tipo: str = "C"):
    if not valor_str:
        return None
    if tipo == "N":
        try:
            return float(valor_str.replace(",", "."))
        except ValueError:
            return None
    return valor_str.strip()


def _parse_linha(linha: str) -> dict | None:
    conteudo = linha.strip().strip("|")
    if not conteudo:
        return None
    campos = conteudo.split("|")
    if not campos:
        return None
    reg_nome = campos[0].strip()
    return {"_reg": reg_nome, "_campos": campos[1:]}


class ECFParser:
    """Parser streaming da ECF."""

    def parse(self, caminho: Path) -> Iterator[dict]:
        encoding = detectar_encoding(caminho)
        logger.info("ECF %s — encoding detectado: %s", caminho.name, encoding)

        with open(caminho, encoding=encoding, errors="replace") as f:
            for num_linha, linha in enumerate(f, 1):
                linha = linha.strip()
                if not linha:
                    continue
                registro = _parse_linha(linha)
                if registro is None:
                    continue

                reg_nome = registro["_reg"]
                if reg_nome in REGISTROS_INTERESSE:
                    registro["_linha"] = num_linha
                    yield registro

    def parse_todos(self, caminho: Path) -> list[dict]:
        return list(self.parse(caminho))

    def extrair_resumo(self, caminho: Path) -> dict:
        """Extrai o resumo da ECF: identificação e as linhas da apuração declarada.

        Posições do Manual de Orientação do Leiaute da ECF. `_campos` não traz
        o REG, então o campo nº N está em ``campos[N - 2]``:

        * 0000 — 01 REG, 02 NOME_ESC ("LECF"), 03 COD_VER, 04 CNPJ, 05 NOME,
          06 IND_SIT_INI_PER, 07 SIT_ESPECIAL, 08 PAT_REMAN_CIS,
          09 DT_SIT_ESP, 10 DT_INI, 11 DT_FIN, …;
        * N630 (cálculo do IRPJ com base no lucro real) e N670 (cálculo da
          CSLL com base no lucro real) — 02 CODIGO, 03 DESCRICAO, 04 VALOR,
          uma linha da tabela dinâmica da RFB por registro.

        A leitura anterior pegava o CNPJ no PAT_REMAN_CIS, o nome no
        DT_SIT_ESP e as datas no SIT_ESPECIAL e no NOME; somava **todas** as
        linhas do N670 — que é CSLL, não IRPJ — como "irpj", e todas as do
        N630 (IRPJ) como "lucro contábil". Base, alíquota, adicional,
        deduções e imposto a pagar viravam um número só, sem significado.

        Qual linha é "o imposto devido" depende do código da tabela dinâmica,
        que a RFB publica por leiaute e ano-calendário; o leiaute do registro
        não diz. Sem a tabela embutida, `irpj`, `csll`, `lucro_contabil` e
        `lucro_tributavel` ficam `None` e as linhas declaradas vêm em
        `apuracao_irpj` e `apuracao_csll`, como estão no arquivo.
        """
        resumo: dict = {
            "empresa": {"cnpj": "", "nome": ""},
            "periodo": {"dt_ini": "", "dt_fin": ""},
            "lucro_contabil": None,
            "lucro_tributavel": None,
            "irpj": None,
            "csll": None,
            "apuracao_irpj": [],
            "apuracao_csll": [],
            "observacao": (
                "IRPJ, CSLL e lucros não são totalizados: a linha que representa cada "
                "valor depende do código da tabela dinâmica da RFB, não do leiaute. "
                "As linhas do N630 (IRPJ) e do N670 (CSLL) seguem como declaradas."
            ),
            "total_registros": 0,
        }

        def campo(campos: list[str], numero: int) -> str:
            """O campo nº `numero` do manual (o 01 é o REG, fora de `_campos`)."""
            indice = numero - 2
            return campos[indice].strip() if 0 <= indice < len(campos) else ""

        destino = {"N630": "apuracao_irpj", "N670": "apuracao_csll"}

        for r in self.parse(caminho):
            resumo["total_registros"] += 1
            reg = r["_reg"]
            campos = r["_campos"]

            if reg == "0000":
                resumo["empresa"]["cnpj"] = campo(campos, 4)
                resumo["empresa"]["nome"] = campo(campos, 5)
                resumo["periodo"]["dt_ini"] = campo(campos, 10)
                resumo["periodo"]["dt_fin"] = campo(campos, 11)

            elif reg in destino:
                resumo[destino[reg]].append(
                    {
                        "linha": r["_linha"],
                        "codigo": campo(campos, 2),
                        "descricao": campo(campos, 3),
                        "valor": _parse_valor(campo(campos, 4), "N"),
                    }
                )

        return resumo
