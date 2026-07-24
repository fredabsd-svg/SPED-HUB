"""Parser ECD em streaming — dirigido por metadados YAML.

Suporta Leiaute 9 (a partir do ano-calendário 2020).
Detecta automaticamente encoding (UTF-8 ou ISO-8859-1).
Processa linha a linha para suportar arquivos de até 500 MB.
Rastreia hierarquia pai-filho (I050→I051/I052, I200→I250, I150→I155, I350→I355).
"""

import logging
from pathlib import Path
from typing import Iterator

import yaml

logger = logging.getLogger(__name__)

# Blocos de registros que nos interessam para a Fase 1
REGISTROS_INTERESSE = frozenset({
    "0000", "I001", "I010", "I015", "I030",
    "I050", "I051", "I052",
    "I075", "I100",
    "I150", "I155",
    "I200", "I250",
    "I350", "I355",
    "I990", "J001", "J005", "J100", "J150", "J210", "J990",
    "9001", "9900", "9999",
})

# Mapa de pais: registro filho → registro pai
PAI_DE = {
    "I051": "I050",
    "I052": "I050",
    "I053": "I050",
    "I155": "I150",
    "I157": "I150",
    "I250": "I200",
    "I310": "I300",
    "I355": "I350",
    "I510": "I500",
    "I550": "I500",
    "I555": "I500",
    "J100": "J005",
    "J150": "J005",
    "J210": "J005",
    "J215": "J005",
}

# Campos do pai que o filho herda
HERDA_DE = {
    "I051": ["COD_CTA"],
    "I052": ["COD_CTA"],
    "I053": ["COD_CTA"],
    "I155": ["DT_INI", "DT_FIN"],
    "I157": ["DT_INI", "DT_FIN"],
    "I250": ["NUM_LCTO", "DT_LCTO"],
    "I355": ["DT_RES"],
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


def _parse_valor(valor_str: str, tipo: str):
    """Converte string do SPED para tipo Python."""
    if not valor_str:
        return None
    if tipo == "N":
        try:
            return float(valor_str.replace(",", "."))
        except ValueError:
            return None
    return valor_str.strip()


def _parse_linha(linha: str, metadados: dict) -> dict | None:
    """Parseia uma linha conforme os metadados do registro.

    Formato SPED: |REG|CAMPO1|CAMPO2|...|
    """
    conteudo = linha.strip().strip("|")
    if not conteudo:
        return None

    campos = conteudo.split("|")
    if not campos:
        return None

    reg_nome = campos[0].strip()
    if reg_nome not in metadados["registros"]:
        return None

    reg_meta = metadados["registros"][reg_nome]
    resultado = {"_reg": reg_nome}

    for campo_meta in reg_meta["campos"]:
        pos = campo_meta["posicao"] - 1
        valor_str = campos[pos] if pos < len(campos) else ""
        resultado[campo_meta["nome"]] = _parse_valor(valor_str, campo_meta["tipo"])

    return resultado


class ECDParser:
    """Parser streaming da ECD com rastreamento de hierarquia."""

    def __init__(self, layout_path: Path | None = None):
        if layout_path is None:
            layout_path = Path(__file__).parent.parent / "layouts" / "ecd_v9.yml"
        with open(layout_path, encoding="utf-8") as f:
            self.metadados = yaml.safe_load(f)

    def parse(self, caminho: Path) -> Iterator[dict]:
        """Faz o parse linha a linha, yieldando registros de interesse."""
        encoding = detectar_encoding(caminho)
        logger.info("ECD %s — encoding detectado: %s", caminho.name, encoding)

        # Pilha de pais ativos
        pais: dict[str, dict] = {}

        with open(caminho, encoding=encoding, errors="replace") as f:
            for num_linha, linha in enumerate(f, 1):
                linha = linha.strip()
                if not linha:
                    continue
                registro = _parse_linha(linha, self.metadados)
                if registro is None:
                    continue

                reg_nome = registro["_reg"]

                # Atualiza pilha de pais
                if reg_nome in PAI_DE:
                    pai_nome = PAI_DE[reg_nome]
                    pai = pais.get(pai_nome)
                    if pai:
                        # Herda campos do pai
                        for campo in HERDA_DE.get(reg_nome, []):
                            if campo in pai:
                                registro[campo] = pai[campo]
                else:
                    # Este registro pode ser pai de outros — registra
                    pais[reg_nome] = registro

                if reg_nome in REGISTROS_INTERESSE:
                    registro["_linha"] = num_linha
                    yield registro

    def parse_todos(self, caminho: Path) -> list[dict]:
        """Parse completo retornando lista (para testes e arquivos pequenos)."""
        return list(self.parse(caminho))