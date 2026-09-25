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
        """Extrai resumo da EFD-Contribuições: PIS/COFINS apurados, créditos, etc.

        As posições seguem o Guia Prático da EFD-Contribuições (tabela de
        campos de cada registro). `_campos` não traz o REG, então o campo nº N
        do guia está em ``campos[N - 2]``:

        * 0000 — 06 DT_INI, 07 DT_FIN, 08 NOME, 09 CNPJ;
        * M100/M500 (crédito do período) — 08 VL_CRED, "valor total do
          crédito apurado no período";
        * M200/M600 (consolidação da contribuição) — 02 VL_TOT_CONT_NC_PER
          (não cumulativa) + 09 VL_TOT_CONT_CUM_PER (cumulativa) é a
          contribuição do período; 13 VL_TOT_CONT_REC é o total a recolher;
        * 0111 — 06 REC_BRU_TOTAL, a receita bruta do mês.

        A leitura anterior pegava o CNPJ no DT_FIN, as datas no
        NUM_REC_ANTERIOR e no DT_INI, o débito de PIS no VL_TOT_CRED_DESC
        (crédito descontado), o crédito no IND_CRED_ORI (um indicador 0/1) e
        somava como "receita bruta" o DT_OPER do F100 — uma data.

        Receita bruta só existe inteira no 0111, obrigatório apenas quando o
        crédito comum é rateado pela receita (0110, IND_APRO_CRED = 2). Sem
        0111, `receita_bruta` fica `None`: somar M210 daria só a receita
        tributada, e o F100 só as "demais operações".
        """
        resumo: dict = {
            "empresa": {"cnpj": "", "nome": ""},
            "periodo": {"dt_ini": "", "dt_fin": ""},
            "pis": {"debito": 0.0, "credito": 0.0, "saldo": 0.0, "a_recolher": 0.0},
            "cofins": {"debito": 0.0, "credito": 0.0, "saldo": 0.0, "a_recolher": 0.0},
            "receita_bruta": None,
            "total_registros": 0,
        }

        def campo(campos: list[str], numero: int) -> str:
            """O campo nº `numero` do guia (o 01 é o REG, fora de `_campos`)."""
            indice = numero - 2
            return campos[indice].strip() if 0 <= indice < len(campos) else ""

        def valor(campos: list[str], numero: int) -> float:
            return _parse_valor(campo(campos, numero), "N") or 0.0

        tributo_do_registro = {"M100": "pis", "M200": "pis", "M500": "cofins", "M600": "cofins"}

        for r in self.parse(caminho):
            resumo["total_registros"] += 1
            reg = r["_reg"]
            campos = r["_campos"]

            if reg == "0000":
                resumo["empresa"]["cnpj"] = campo(campos, 9)
                resumo["empresa"]["nome"] = campo(campos, 8)
                resumo["periodo"]["dt_ini"] = campo(campos, 6)
                resumo["periodo"]["dt_fin"] = campo(campos, 7)

            elif reg in ("M100", "M500"):
                # Crédito apurado no período (08 VL_CRED)
                resumo[tributo_do_registro[reg]]["credito"] += valor(campos, 8)

            elif reg in ("M200", "M600"):
                # Contribuição do período: não cumulativa (02) + cumulativa (09)
                tributo = resumo[tributo_do_registro[reg]]
                tributo["debito"] += valor(campos, 2) + valor(campos, 9)
                tributo["a_recolher"] += valor(campos, 13)

            elif reg == "0111":
                # Receita bruta total do mês (06 REC_BRU_TOTAL)
                resumo["receita_bruta"] = (resumo["receita_bruta"] or 0.0) + valor(campos, 6)

        for tributo in ("pis", "cofins"):
            resumo[tributo]["saldo"] = resumo[tributo]["credito"] - resumo[tributo]["debito"]

        return resumo
