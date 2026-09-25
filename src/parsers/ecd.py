"""Parser ECD em streaming — dirigido por metadados YAML.

Suporta Leiaute 9 (a partir do ano-calendário 2020).
Detecta automaticamente encoding (UTF-8 ou ISO-8859-1).
Processa linha a linha para suportar arquivos de até 500 MB.
Rastreia hierarquia pai-filho (I050→I051/I052, I200→I250, I150→I155, I350→I355).
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Blocos de registros que nos interessam para a Fase 1
REGISTROS_INTERESSE = frozenset(
    {
        "0000",
        "I001",
        "I010",
        "I015",
        "I030",
        "I050",
        "I051",
        "I052",
        "I075",
        "I100",
        "I150",
        "I155",
        "I200",
        "I250",
        "I350",
        "I355",
        "I990",
        "J001",
        "J005",
        "J100",
        "J150",
        "J210",
        "J990",
        "9001",
        "9900",
        "9999",
    }
)

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
    # O J215 detalha o fato contábil de uma linha do J210, não do J005.
    "J215": "J210",
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
    # As linhas do bloco J herdam o período e a identificação do J005 — é o
    # que permite ligar cada uma à demonstração a que pertence.
    "J100": ["DT_INI", "DT_FIN", "ID_DEM"],
    "J150": ["DT_INI", "DT_FIN", "ID_DEM"],
    "J210": ["DT_INI", "DT_FIN", "ID_DEM"],
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


# Número com, no máximo, um separador decimal — vírgula (o formato do SPED)
# ou ponto (o das fixtures e de alguns geradores). Separador de milhar não.
_NUMERO = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)$")


class CampoInvalidoError(ValueError):
    """Campo monetário (VL_*) que não é um número.

    "1.234,56" e "1 234,56" não viram número; lidos como antes, eles viravam
    `None` e o importador gravava 0,00 — a escrituração entrava com um valor
    a menos e nada indicava isso (§6.1).
    """

    def __init__(self, registro: str, campo: str, valor: str, linha: int | None = None):
        self.registro = registro
        self.campo = campo
        self.valor = valor
        self.linha = linha
        super().__init__(registro, campo, valor, linha)

    def __str__(self) -> str:
        onde = f"linha {self.linha}, " if self.linha is not None else ""
        return (
            f"Valor monetário inválido ({onde}{self.registro}, campo {self.campo}): "
            f"{self.valor!r}. O leiaute aceita só dígitos com vírgula decimal, sem "
            "separador de milhar"
        )


def _parse_valor(valor_str: str, tipo: str, campo: str = "", registro: str = ""):
    """Converte string do SPED para tipo Python.

    Campo `N` ilegível vira `None` — exceto os monetários (`VL_*`), que
    levantam `CampoInvalidoError`: um valor que some vira zero no banco.
    """
    if not valor_str or not valor_str.strip():
        return None
    if tipo == "N":
        texto = valor_str.strip()
        if campo.startswith("VL_"):
            if not _NUMERO.match(texto):
                raise CampoInvalidoError(registro, campo, valor_str)
            return float(texto.replace(",", "."))
        try:
            return float(texto.replace(",", "."))
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
        resultado[campo_meta["nome"]] = _parse_valor(
            valor_str, campo_meta["tipo"], campo_meta["nome"], reg_nome
        )

    return resultado


class ECDParser:
    """Parser streaming da ECD com rastreamento de hierarquia."""

    def __init__(self, layout_path: Path | None = None):
        if layout_path is None:
            layout_path = Path(__file__).parent.parent / "layouts" / "ecd_v9.yml"
        with open(layout_path, encoding="utf-8") as f:
            self.metadados = yaml.safe_load(f)

    @property
    def versao(self) -> str:
        """A versão do leiaute que este parser carregou."""
        return str(self.metadados["versao"])

    def parse(self, caminho: Path) -> Iterator[dict]:
        """Faz o parse linha a linha, yieldando registros de interesse."""
        encoding = detectar_encoding(caminho)
        logger.info("ECD %s — encoding detectado: %s", caminho.name, encoding)

        # Pilha de pais ativos
        pais: dict[str, dict] = {}

        offset_bytes = 0
        with open(caminho, "rb") as stream:
            for num_linha, raw_line in enumerate(stream, 1):
                offset_bytes += len(raw_line)
                linha = raw_line.decode(encoding, errors="replace").strip()
                if not linha:
                    continue
                try:
                    registro = _parse_linha(linha, self.metadados)
                except CampoInvalidoError as exc:
                    exc.linha = num_linha
                    raise
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
                    registro["_offset_bytes"] = offset_bytes
                    yield registro

    def parse_todos(self, caminho: Path) -> list[dict]:
        """Parse completo retornando lista (para testes e arquivos pequenos)."""
        return list(self.parse(caminho))

    def parse_em_lotes(self, caminho: Path, tamanho_lote: int = 1000):
        """Parse streaming em lotes para arquivos grandes (>100MB).

        Yields batches de registros para processamento incremental,
        evitando estourar memória com arquivos muito grandes.

        Args:
            caminho: Caminho do arquivo ECD
            tamanho_lote: Quantos registros por lote

        Yields:
            list[dict]: Lote de registros parseados
        """
        lote = []
        for registro in self.parse(caminho):
            lote.append(registro)
            if len(lote) >= tamanho_lote:
                yield lote
                lote = []
        if lote:
            yield lote

    def contar_registros(self, caminho: Path) -> dict[str, int]:
        """Conta registros por tipo sem armazenar todos em memória.

        Útil para estimar tamanho antes de processar arquivos grandes.

        Returns:
            dict com contagem por tipo de registro
        """
        contagem: dict[str, int] = {}
        for registro in self.parse(caminho):
            reg = registro["_reg"]
            contagem[reg] = contagem.get(reg, 0) + 1
        return contagem
