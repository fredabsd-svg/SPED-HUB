"""Os campos de cada registro, na ordem em que o leiaute os define.

Este módulo existe porque um gerador de SPED erra de um jeito silencioso e
específico: **esquecer um campo no meio do registro**. Nada estoura. O arquivo
sai bem-formado, com barras nos lugares certos, e todos os valores depois do
campo esquecido passam a ocupar a posição do vizinho — o valor do frete vira o
indicador do frete, a base do ICMS vira "outras despesas", e assim por diante
até o fim da linha. Quem lê o arquivo não vê nada errado; quem valida recebe
"quantidade de campos inválida" sem saber qual campo faltou.

Foi exatamente o que aconteceu aqui: o `C100` saía sem o `IND_FRT`, e os doze
valores seguintes estavam todos deslocados uma casa.

A resposta não é conferir com mais atenção. É nomear os campos num lugar só e
fazer o gerador conferir contra essa lista a cada registro que escreve — ver
`GeradorBase._add`. Acrescentar um campo ao gerador sem acrescentá-lo aqui, ou
o contrário, para na hora, com o nome do registro e a contagem dos dois lados.

**As duas escriturações têm registros de mesmo nome e leiaute diferente.** O
`0000` é o caso óbvio, mas não o único, e por isso a tabela é por obrigação,
não global. Onde o leiaute é de fato o mesmo — `C100`, `C170`, `0150` — a
definição é compartilhada por `_COMUNS`, para que não haja duas verdades.

Os nomes são os do Guia Prático. Campo que o gerador ainda não preenche
continua listado: ele ocupa posição no arquivo mesmo vazio, e é justamente a
posição que este módulo protege.
"""

from __future__ import annotations

# ── Registros de estrutura, iguais em toda escrituração ────────────────────
_ABERTURA_DE_BLOCO = ("IND_MOV",)

_COMUNS: dict[str, tuple[str, ...]] = {
    "0001": _ABERTURA_DE_BLOCO,
    "0150": (
        "COD_PART",
        "NOME",
        "COD_PAIS",
        "CNPJ",
        "CPF",
        "IE",
        "COD_MUN",
        "SUFRAMA",
        "END",
        "NUM",
        "COMPL",
        "BAIRRO",
    ),
    "0190": ("UNID", "DESCR"),
    "0200": (
        "COD_ITEM",
        "DESCR_ITEM",
        "COD_BARRA",
        "COD_ANT_ITEM",
        "UNID_INV",
        "TIPO_ITEM",
        "COD_NCM",
        "EX_IPI",
        "COD_GEN",
        "COD_LST",
        "ALIQ_ICMS",
        "CEST",
    ),
    "0990": ("QTD_LIN_0",),
    "C001": _ABERTURA_DE_BLOCO,
    # O IND_FRT é o campo 17, logo depois do VL_MERC.  Foi o que faltava.
    "C100": (
        "IND_OPER",
        "IND_EMIT",
        "COD_PART",
        "COD_MOD",
        "COD_SIT",
        "SER",
        "NUM_DOC",
        "CHV_NFE",
        "DT_DOC",
        "DT_E_S",
        "VL_DOC",
        "IND_PGTO",
        "VL_DESC",
        "VL_ABAT_NT",
        "VL_MERC",
        "IND_FRT",
        "VL_FRT",
        "VL_SEG",
        "VL_OUT_DA",
        "VL_BC_ICMS",
        "VL_ICMS",
        "VL_BC_ICMS_ST",
        "VL_ICMS_ST",
        "VL_IPI",
        "VL_PIS",
        "VL_COFINS",
        "VL_PIS_ST",
        "VL_COFINS_ST",
    ),
    "C170": (
        "NUM_ITEM",
        "COD_ITEM",
        "DESCR_COMPL",
        "QTD",
        "UNID",
        "VL_ITEM",
        "VL_DESC",
        "IND_MOV",
        "CST_ICMS",
        "CFOP",
        "COD_NAT",
        "VL_BC_ICMS",
        "ALIQ_ICMS",
        "VL_ICMS",
        "VL_BC_ICMS_ST",
        "ALIQ_ST",
        "VL_ICMS_ST",
        "IND_APUR",
        "CST_IPI",
        "COD_ENQ",
        "VL_BC_IPI",
        "ALIQ_IPI",
        "VL_IPI",
        "CST_PIS",
        "VL_BC_PIS",
        "ALIQ_PIS",
        "QUANT_BC_PIS",
        "ALIQ_PIS_QUANT",
        "VL_PIS",
        "CST_COFINS",
        "VL_BC_COFINS",
        "ALIQ_COFINS",
        "QUANT_BC_COFINS",
        "ALIQ_COFINS_QUANT",
        "VL_COFINS",
        "COD_CTA",
        "VL_ABAT_NT",
    ),
    "C990": ("QTD_LIN_C",),
    "9001": _ABERTURA_DE_BLOCO,
    "9900": ("REG_BLC", "QTD_REG_BLC"),
    "9990": ("QTD_LIN_9",),
    "9999": ("QTD_LIN",),
}

EFD_ICMS: dict[str, tuple[str, ...]] = {
    **_COMUNS,
    "0000": (
        "COD_VER",
        "COD_FIN",
        "DT_INI",
        "DT_FIN",
        "NOME",
        "CNPJ",
        "CPF",
        "UF",
        "IE",
        "COD_MUN",
        "IM",
        "SUFRAMA",
        "IND_PERFIL",
        "IND_ATIV",
    ),
    "C190": (
        "CST_ICMS",
        "CFOP",
        "ALIQ_ICMS",
        "VL_OPR",
        "VL_BC_ICMS",
        "VL_ICMS",
        "VL_BC_ICMS_ST",
        "VL_ICMS_ST",
        "VL_RED_BC",
        "VL_IPI",
        "COD_OBS",
    ),
    "E001": _ABERTURA_DE_BLOCO,
    "E100": ("DT_INI", "DT_FIN"),
    "E110": (
        "VL_TOT_DEBITOS",
        "VL_AJ_DEBITOS",
        "VL_TOT_AJ_DEBITOS",
        "VL_ESTORNOS_CRED",
        "VL_TOT_CREDITOS",
        "VL_AJ_CREDITOS",
        "VL_TOT_AJ_CREDITOS",
        "VL_ESTORNOS_DEB",
        "VL_SLD_CREDOR_ANT",
        "VL_SLD_APURADO",
        "VL_TOT_DED",
        "VL_ICMS_RECOLHER",
        "VL_SLD_CREDOR_TRANSPORTAR",
        "DEB_ESP",
    ),
    "E990": ("QTD_LIN_E",),
}

# O 0000 daqui não se parece com o da EFD ICMS/IPI: nem os mesmos campos, nem
# a mesma ordem, nem a mesma quantidade.  Compartilhá-los seria o mesmo erro
# que o IND_ATIV — mesmo nome, tabela diferente.
EFD_CONTRIBUICOES: dict[str, tuple[str, ...]] = {
    **_COMUNS,
    "0000": (
        "COD_VER",
        "TIPO_ESCRIT",
        "IND_SIT_ESP",
        "NUM_REC_ANTERIOR",
        "DT_INI",
        "DT_FIN",
        "NOME",
        "CNPJ",
        "UF",
        "COD_MUN",
        "SUFRAMA",
        "IND_NAT_PJ",
        "IND_ATIV",
    ),
    "0110": ("COD_INC_TRIB", "IND_APRO_CRED", "COD_TIPO_CONT", "IND_REG_CUM"),
    "0140": ("COD_EST", "NOME", "CNPJ", "UF", "IE", "COD_MUN", "IM", "SUFRAMA"),
    "C010": ("CNPJ", "IND_ESCRI"),
    "M001": _ABERTURA_DE_BLOCO,
    # M200 (PIS) e M600 (Cofins) têm o mesmo desenho; os nomes oficiais falam
    # em "CONT" nos dois, sem distinguir a contribuição.
    "M200": (
        "VL_TOT_CONT_NC_PER",
        "VL_TOT_CRED_DESC",
        "VL_TOT_CRED_DESC_ANT",
        "VL_TOT_CONT_NC_DEV",
        "VL_RET_NC",
        "VL_OUT_DED_NC",
        "VL_CONT_NC_REC",
        "VL_TOT_CONT_CUM_PER",
        "VL_RET_CUM",
        "VL_OUT_DED_CUM",
        "VL_CONT_CUM_REC",
        "VL_TOT_CONT_REC",
    ),
    "M990": ("QTD_LIN_M",),
}
EFD_CONTRIBUICOES["M600"] = EFD_CONTRIBUICOES["M200"]


class RegistroForaDoLeiaute(ValueError):
    """O gerador escreveu um registro que a tabela de campos não conhece.

    Ou o registro é novo e ninguém o descreveu, ou o nome está errado. Nos dois
    casos o arquivo sairia com um registro que este módulo não sabe conferir —
    e conferir é a única razão de ele existir.
    """


class CamposEmDesacordo(ValueError):
    """A quantidade de campos escritos não bate com a do leiaute.

    A mensagem diz qual campo ficaria em qual posição, porque a pergunta
    seguinte é sempre essa.
    """


def conferir(leiaute: dict[str, tuple[str, ...]], tipo: str, campos: list[str]) -> None:
    """Levanta se o registro não existe no leiaute ou tem campos a mais/menos."""
    if tipo not in leiaute:
        raise RegistroForaDoLeiaute(
            f"o registro {tipo!r} não está descrito em src/escrituracoes/leiaute.py — "
            "acrescente os campos dele lá antes de gerá-lo"
        )

    esperados = leiaute[tipo]
    if len(campos) == len(esperados):
        return

    if len(campos) < len(esperados):
        faltando = esperados[len(campos) :]
        detalhe = f"faltam {', '.join(faltando)}"
    else:
        detalhe = f"sobram {len(campos) - len(esperados)}"

    raise CamposEmDesacordo(
        f"o registro {tipo} saiu com {len(campos)} campos e o leiaute pede "
        f"{len(esperados)} — {detalhe}. Campo faltando no meio do registro "
        "desloca todos os seguintes: confira a ordem, não só a contagem"
    )
