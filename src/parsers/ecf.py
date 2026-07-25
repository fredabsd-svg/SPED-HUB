"""Parser ECF — Escrituração Contábil Fiscal.

Processa arquivos da ECF (IRPJ/CSLL) no leiaute 7.
Extrai registros de interesse: 0000, 0010, 0020, J050, J051, J053, K030, K155, K156, K355, K356, L030, L100, L200, L300, M010, M030, M300, M350, M410, N030, N500, N600, N610, N620, N630, N650, N660, N670, P030, P100, P130, P150, P200, P300, P400, P500, Q100, R100, S100, T030, T120, T150, T170, T181, U100, U150, U180, X280, X291, X300, X310, X320, X330, X340, X350, X351, X352, X353, X354, X355, X356, X357, X358, X359, X360, X361, X362, X363, X364, X365, X366, X367, X368, X369, X370, X371, X372, X373, X374, X375, X376, X377, X378, X379, X380, X381, X382, X383, X384, X385, X386, X387, X388, X389, X390, X391, X392, X393, X394, X395, X396, X397, X398, X399, X400, X410, X420, X430, X450, X460, X470, X480, X490, X500, X510, Y540, Y550, Y560, Y570, Y580, Y590, Y600, Y612, Y620, Y630, Y640, Y650, Y660, Y671, Y672, Y680, Y690, Y720, Y800, Y810, Y820, Y830, Y840, Y850, Y860, Y870, Y880, Y890, Y900, Y910, Z010, Z020, Z030, Z040, Z050, Z060, Z070, Z080, Z090, Z100, Z110, Z120, Z130, Z140, Z150, Z160, Z170, Z180, Z190, Z200, Z210, Z220, Z230, Z240, Z250, Z260, Z270, Z280, Z290, Z300, Z310, Z320, Z330, Z340, Z350, Z360, Z370, Z380, Z390, Z400, Z410, Z420, Z430, Z440, Z450, Z460, Z470, Z480, Z490, Z500, Z510, Z520, Z530, Z540, Z550, Z560, Z570, Z580, Z590, Z600, Z610, Z620, Z630, Z640, Z650, Z660, Z670, Z680, Z690, Z700, Z710, Z720, Z730, Z740, Z750, Z760, Z770, Z780, Z790, Z800, Z810, Z820, Z830, Z840, Z850, Z860, Z870, Z880, Z890, Z900, Z910, Z920, Z930, Z940, Z950, Z960, Z970, Z980, Z990, 9001, 9100, 9900, 9999,
"""

import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# Registros de interesse da ECF
REGISTROS_INTERESSE = frozenset({
    "0000", "0010", "0020", "0030", "0035",
    "J050", "J051", "J053",
    "K030", "K155", "K156", "K355", "K356",
    "L030", "L100", "L200", "L210", "L300",
    "M010", "M030", "M300", "M305", "M310", "M315", "M350", "M355", "M360",
    "M410", "M415", "M420", "M500",
    "N030", "N500", "N600", "N610", "N615", "N620", "N630", "N650", "N660", "N670",
    "P030", "P100", "P130", "P150", "P200", "P300", "P400", "P500",
    "Q100",
    "R100", "R150", "R200", "R300", "R500",
    "S100",
    "T030", "T120", "T150", "T170", "T181",
    "U100", "U150", "U180", "U182",
    "X280", "X291", "X292", "X300", "X305", "X310", "X320", "X330",
    "X340", "X350", "X351", "X352", "X353", "X354", "X355", "X356",
    "X357", "X358", "X359", "X360", "X361", "X362", "X363", "X364",
    "X365", "X366", "X367", "X368", "X369", "X370", "X371", "X372",
    "X373", "X374", "X375", "X376", "X377", "X378", "X379", "X380",
    "X381", "X382", "X383", "X384", "X385", "X386", "X387", "X388",
    "X389", "X390", "X391", "X392", "X393", "X394", "X395", "X396",
    "X397", "X398", "X399", "X400", "X410", "X420", "X430", "X450",
    "X460", "X470", "X480", "X490", "X500", "X510",
    "Y540", "Y550", "Y560", "Y570", "Y580", "Y590", "Y600", "Y612",
    "Y620", "Y630", "Y640", "Y650", "Y660", "Y671", "Y672", "Y680",
    "Y690", "Y720", "Y800", "Y810", "Y820", "Y830", "Y840", "Y850",
    "Y860", "Y870", "Y880", "Y890", "Y900", "Y910",
    "9001", "9100", "9900", "9999",
})


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
        """Extrai resumo da ECF: IRPJ/CSLL apurados, lucro tributável, etc."""
        registros = self.parse_todos(caminho)

        resumo = {
            "empresa": {"cnpj": "", "nome": ""},
            "periodo": {"dt_ini": "", "dt_fin": ""},
            "lucro_contabil": 0.0,
            "lucro_tributavel": 0.0,
            "irpj": 0.0,
            "csll": 0.0,
            "total_registros": len(registros),
        }

        for r in registros:
            reg = r["_reg"]
            campos = r["_campos"]

            if reg == "0000":
                if len(campos) >= 8:
                    resumo["empresa"]["cnpj"] = campos[5] if len(campos) > 5 else ""
                    resumo["empresa"]["nome"] = campos[6] if len(campos) > 6 else ""
                    resumo["periodo"]["dt_ini"] = campos[3] if len(campos) > 3 else ""
                    resumo["periodo"]["dt_fin"] = campos[4] if len(campos) > 4 else ""

            elif reg == "N630":
                # Lucro contábil
                if len(campos) >= 3:
                    resumo["lucro_contabil"] += _parse_valor(campos[2], "N") or 0.0

            elif reg == "N650":
                # Lucro tributável
                if len(campos) >= 3:
                    resumo["lucro_tributavel"] += _parse_valor(campos[2], "N") or 0.0

            elif reg == "N670":
                # IRPJ devido
                if len(campos) >= 3:
                    resumo["irpj"] += _parse_valor(campos[2], "N") or 0.0

            elif reg == "N660":
                # CSLL devido
                if len(campos) >= 3:
                    resumo["csll"] += _parse_valor(campos[2], "N") or 0.0

        return resumo