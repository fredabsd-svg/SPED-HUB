"""Parser EFD-Contribuições — Escrituração Fiscal Digital das Contribuições.

Processa arquivos da EFD-Contribuições (PIS/COFINS) no leiaute 006.
Extrai registros de interesse: 0000, 0110, 0140, 0500, A010, C010, C100, C170, C500, F010, M100, M200, M400, etc.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# Registros de interesse da EFD-Contribuições
REGISTROS_INTERESSE = frozenset(
    {
        "0000",
        "0001",
        "0035",
        "0100",
        "0110",
        "0111",
        "0140",
        "0145",
        "0150",
        "0190",
        "0200",
        "0400",
        "0450",
        "0500",
        "0600",
        "A001",
        "A010",
        "A100",
        "A110",
        "A111",
        "A120",
        "A170",
        "C001",
        "C010",
        "C100",
        "C110",
        "C111",
        "C120",
        "C170",
        "C180",
        "C181",
        "C185",
        "C190",
        "C191",
        "C195",
        "C198",
        "C199",
        "C380",
        "C381",
        "C385",
        "C395",
        "C396",
        "C400",
        "C405",
        "C481",
        "C485",
        "C489",
        "C490",
        "C491",
        "C495",
        "C499",
        "C500",
        "C501",
        "C505",
        "C509",
        "C600",
        "C601",
        "C605",
        "C609",
        "C800",
        "C810",
        "C820",
        "C830",
        "C860",
        "C870",
        "C880",
        "C890",
        "D001",
        "D010",
        "D100",
        "D105",
        "D111",
        "D200",
        "D201",
        "D205",
        "D209",
        "D300",
        "D309",
        "D350",
        "D359",
        "D500",
        "D501",
        "D505",
        "D509",
        "D600",
        "D601",
        "D605",
        "D609",
        "F001",
        "F010",
        "F100",
        "F110",
        "F111",
        "F120",
        "F130",
        "F139",
        "F150",
        "F200",
        "F205",
        "F210",
        "F500",
        "F510",
        "F525",
        "F550",
        "F560",
        "F600",
        "F700",
        "F800",
        "I001",
        "I010",
        "I100",
        "I199",
        "I200",
        "I299",
        "I300",
        "I399",
        "M001",
        "M100",
        "M105",
        "M110",
        "M115",
        "M200",
        "M205",
        "M210",
        "M211",
        "M215",
        "M220",
        "M225",
        "M230",
        "M300",
        "M350",
        "M400",
        "M410",
        "M500",
        "M505",
        "M510",
        "M515",
        "M600",
        "M605",
        "M610",
        "M611",
        "M615",
        "M620",
        "M625",
        "M630",
        "M700",
        "M800",
        "P001",
        "P010",
        "P100",
        "P110",
        "P199",
        "P200",
        "P210",
        "1001",
        "1010",
        "1020",
        "1100",
        "1101",
        "1102",
        "1200",
        "1210",
        "1220",
        "1300",
        "1500",
        "1501",
        "1502",
        "1600",
        "1601",
        "1610",
        "1620",
        "1700",
        "1800",
        "1809",
        "1900",
        "9001",
        "9900",
        "9990",
        "9999",
    }
)

# Mapa de pais: registro filho → registro pai
PAI_DE = {
    "0111": "0110",
    "A110": "A100",
    "A111": "A110",
    "A120": "A100",
    "A170": "A100",
    "C110": "C100",
    "C111": "C110",
    "C120": "C100",
    "C170": "C100",
    "C181": "C180",
    "C185": "C180",
    "C191": "C190",
    "C195": "C190",
    "C198": "C190",
    "C199": "C190",
    "C381": "C380",
    "C385": "C380",
    "C396": "C395",
    "C405": "C400",
    "C481": "C400",
    "C485": "C400",
    "C489": "C400",
    "C491": "C490",
    "C495": "C490",
    "C499": "C490",
    "C501": "C500",
    "C505": "C500",
    "C509": "C500",
    "C601": "C600",
    "C605": "C600",
    "C609": "C600",
    "C810": "C800",
    "C820": "C800",
    "C830": "C800",
    "C860": "C800",
    "C870": "C800",
    "C880": "C800",
    "C890": "C800",
    "D105": "D100",
    "D111": "D100",
    "D201": "D200",
    "D205": "D200",
    "D209": "D200",
    "D309": "D300",
    "D359": "D350",
    "D501": "D500",
    "D505": "D500",
    "D509": "D500",
    "D601": "D600",
    "D605": "D600",
    "D609": "D600",
    "F111": "F110",
    "F120": "F100",
    "F130": "F100",
    "F139": "F130",
    "F150": "F100",
    "F205": "F200",
    "F210": "F200",
    "F510": "F500",
    "F525": "F500",
    "F550": "F500",
    "F560": "F500",
    "F600": "F500",
    "F700": "F500",
    "F800": "F500",
    "M105": "M100",
    "M110": "M100",
    "M115": "M100",
    "M205": "M200",
    "M210": "M200",
    "M211": "M200",
    "M215": "M200",
    "M220": "M200",
    "M225": "M200",
    "M230": "M200",
    "M350": "M300",
    "M410": "M400",
    "M505": "M500",
    "M510": "M500",
    "M515": "M500",
    "M605": "M600",
    "M610": "M600",
    "M611": "M600",
    "M615": "M600",
    "M620": "M600",
    "M625": "M600",
    "M630": "M600",
    "M700": "M600",
    "M800": "M600",
    "P110": "P100",
    "P199": "P100",
    "P210": "P200",
    "1101": "1100",
    "1102": "1100",
    "1501": "1500",
    "1502": "1500",
    "1601": "1600",
    "1610": "1600",
    "1620": "1600",
}


def detectar_encoding(caminho: Path, bytes_amostra: int = 4096) -> str:
    """Detecta se o arquivo é UTF-8 ou ISO-8859-1."""
    with open(caminho, "rb") as f:
        raw = f.read(bytes_amostra)
    try:
        raw.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError:
        return "ISO-8859-1"


def _parse_valor(valor_str: str, tipo: str = "C"):
    """Converte string do SPED para tipo Python."""
    if not valor_str:
        return None
    if tipo == "N":
        try:
            return float(valor_str.replace(",", "."))
        except ValueError:
            return None
    return valor_str.strip()


def _parse_linha(linha: str) -> dict | None:
    """Parseia uma linha SPED genérica (sem metadados YAML)."""
    conteudo = linha.strip().strip("|")
    if not conteudo:
        return None
    campos = conteudo.split("|")
    if not campos:
        return None
    reg_nome = campos[0].strip()
    return {"_reg": reg_nome, "_campos": campos[1:]}


class EFDParser:
    """Parser streaming da EFD-Contribuições."""

    def parse(self, caminho: Path) -> Iterator[dict]:
        """Faz o parse linha a linha."""
        encoding = detectar_encoding(caminho)
        logger.info("EFD %s — encoding detectado: %s", caminho.name, encoding)

        pais: dict[str, dict] = {}

        with open(caminho, encoding=encoding, errors="replace") as f:
            for num_linha, linha in enumerate(f, 1):
                linha = linha.strip()
                if not linha:
                    continue
                registro = _parse_linha(linha)
                if registro is None:
                    continue

                reg_nome = registro["_reg"]

                # Herda campos do pai
                if reg_nome in PAI_DE:
                    pai_nome = PAI_DE[reg_nome]
                    pai = pais.get(pai_nome)
                    if pai:
                        registro["_pai"] = pai_nome
                        registro["_pai_campos"] = pai["_campos"]
                else:
                    pais[reg_nome] = registro

                if reg_nome in REGISTROS_INTERESSE:
                    registro["_linha"] = num_linha
                    yield registro

    def parse_todos(self, caminho: Path) -> list[dict]:
        """Parse completo retornando lista."""
        return list(self.parse(caminho))

    def extrair_resumo(self, caminho: Path) -> dict:
        """Extrai resumo da EFD-Contribuições: PIS/COFINS apurados, créditos, etc."""
        resumo = {
            "empresa": {"cnpj": "", "nome": ""},
            "periodo": {"dt_ini": "", "dt_fin": ""},
            "pis": {"debito": 0.0, "credito": 0.0, "saldo": 0.0},
            "cofins": {"debito": 0.0, "credito": 0.0, "saldo": 0.0},
            "receita_bruta": 0.0,
            "total_registros": 0,
        }

        for r in self.parse(caminho):
            resumo["total_registros"] += 1
            reg = r["_reg"]
            campos = r["_campos"]

            if reg == "0000":
                if len(campos) >= 8:
                    resumo["empresa"]["cnpj"] = campos[5] if len(campos) > 5 else ""
                    resumo["empresa"]["nome"] = campos[6] if len(campos) > 6 else ""
                    resumo["periodo"]["dt_ini"] = campos[3] if len(campos) > 3 else ""
                    resumo["periodo"]["dt_fin"] = campos[4] if len(campos) > 4 else ""

            elif reg == "M100":
                # Crédito de PIS apurado
                if len(campos) >= 2:
                    resumo["pis"]["credito"] += _parse_valor(campos[1], "N") or 0.0

            elif reg == "M200":
                # Débito de PIS
                if len(campos) >= 2:
                    resumo["pis"]["debito"] += _parse_valor(campos[1], "N") or 0.0

            elif reg == "M500":
                # Crédito de COFINS apurado
                if len(campos) >= 2:
                    resumo["cofins"]["credito"] += _parse_valor(campos[1], "N") or 0.0

            elif reg == "M600":
                # Débito de COFINS
                if len(campos) >= 2:
                    resumo["cofins"]["debito"] += _parse_valor(campos[1], "N") or 0.0

            elif reg == "F100":
                # Receita bruta
                if len(campos) >= 4:
                    resumo["receita_bruta"] += _parse_valor(campos[3], "N") or 0.0

        resumo["pis"]["saldo"] = resumo["pis"]["credito"] - resumo["pis"]["debito"]
        resumo["cofins"]["saldo"] = resumo["cofins"]["credito"] - resumo["cofins"]["debito"]

        return resumo
