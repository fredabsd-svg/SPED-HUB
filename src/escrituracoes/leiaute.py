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
não global. Onde o leiaute é de fato o mesmo, a definição é compartilhada por
`_COMUNS`, para que não haja duas verdades.

**O critério para entrar em `_COMUNS` é ter sido conferido nos dois documentos
— nunca a semelhança dos nomes.** Ele existe porque foi violado: o `0200` foi
para lá por parecer igual, e não era. O Guia Prático da EFD-Contribuições
resolve isso registro a registro, e diz de duas maneiras:

  * quando ele **delega** — "registro com estrutura, campos e conteúdo
    definidos e constantes no Leiaute da EFD (ICMS e IPI), instituído pelo Ato
    COTEPE/ICMS nº 9" —, compartilhar é o que o próprio documento manda. É o
    caso do `C100` e do `C170`, que não têm tabela de campos no Guia;
  * quando ele **traz tabela própria**, o registro é daquela obrigação, ainda
    que o começo coincida. É o caso do `0200`, do `0150` e do `0140` — e o
    `0200` diverge no último campo.

Compartilhado sem conferir, o engano é invisível: `conferir` compara o gerador
com **estas** tabelas, nunca estas tabelas com o documento oficial.

Os nomes são os do Guia Prático. Campo que o gerador ainda não preenche
continua listado: ele ocupa posição no arquivo mesmo vazio, e é justamente a
posição que este módulo protege.

**Procedência (REGRA 8 §8.1).** Isto é tabela de terceiro embutida, e por isso
declara de onde veio e quando foi conferida — ver `VERIFICADO_CONTRA`. A
conferência é registro a registro, contra a Nota Técnica que institui o
leiaute, e o que ela compara é a **contagem e a ordem**, que é o que decide se
o arquivo é aceito.

A conferência é **por obrigação**: a EFD ICMS/IPI contra a Nota Técnica que
institui o leiaute, a EFD-Contribuições contra o Guia Prático dela. Foram duas
conferências, e a segunda achou o que a primeira não podia achar — um campo a
mais no `0200` desta, que estava em `_COMUNS` como se os dois leiautes fossem
o mesmo.

Duas armadilhas para quem reconferir:

  * **o PDF da NT perde linhas na extração de texto.** No `C100` ele salta de
    "15 VL_ABAT_NT" para "17 IND_FRT", e não há campo 16 no texto extraído. A
    posição existe: a prova é o próprio `IND_FRT` estar numerado 17. Concluir
    pela ausência apagaria um campo e deslocaria os doze seguintes.
  * **o documento repete nomes.** No `C170`, os campos 27 e 29 se chamam os
    dois `ALIQ_PIS` — um "em percentual", outro "em reais". Aqui eles são
    `ALIQ_PIS` e `ALIQ_PIS_QUANT`, como no Guia Prático; a diferença de nome é
    proposital e não é divergência.
"""

from __future__ import annotations

# A versão do leiaute contra a qual estes registros foram conferidos, campo a
# campo, e a data da conferência.
#
# Anda junto de `efd_icms.VERSOES_DO_LEIAUTE`: acrescentar uma versão nova lá
# sem reconferir os registros aqui derruba o CI
# (`test_leiaute_sped.py::TestProcedencia`).  As duas coisas precisam andar
# juntas — a versão diz o que o arquivo declara ser, e estas tabelas dizem o
# que ele é.
LEIAUTE_CONFERIDO = "020"
VERIFICADO_CONTRA = "NT 2025.001 v1.0 (leiaute versão 020), item 4.1 do Anexo Único"
VERIFICADO_EM = "2026-08-03"

# O leiaute diz quais campos existem e em que ordem; o Guia Prático diz o que
# entra em cada um.  São conferências separadas, e a segunda achou o que a
# primeira não podia achar: os ajustes do E111 iam para os campos 03 e 07 do
# E110, que são os do C197/D197.
GUIA_ICMS_VERIFICADO_CONTRA = "Guia Prático da EFD ICMS/IPI versão 3.2.2 (11/02/2026)"
GUIA_ICMS_VERIFICADO_EM = "2026-08-03"

# A EFD-Contribuições tem leiaute próprio, e por isso procedência própria.  A
# primeira conferência cobriu só a EFD ICMS/IPI, e foi na segunda — desta — que
# apareceu o `CEST` a mais no `0200`.
CONTRIBUICOES_VERIFICADO_CONTRA = "Guia Prático da EFD-Contribuições versão 1.35 (18/06/2021)"
CONTRIBUICOES_VERIFICADO_EM = "2026-08-03"

# Os registros que as duas obrigações compartilham, e por quê.  Estar aqui é
# afirmação, não conveniência: acrescentar um sem conferir nos dois documentos
# é como o `CEST` entrou no `0200` da EFD-Contribuições.
#
# `tests/test_leiaute_sped.py::TestOsDoisLeiautesDoMesmoRegistro` trava a
# lista: mexer em `_COMUNS` sem mexer aqui derruba o CI.
POR_QUE_E_COMUM = {
    "0001": "abertura de bloco: só o IND_MOV, idêntico em todo o SPED",
    "0150": "tabela própria nos dois documentos, conferida campo a campo",
    "0190": "tabela própria nos dois documentos, conferida campo a campo",
    "0990": "encerramento de bloco: só a contagem de linhas",
    "C001": "abertura de bloco",
    "C100": "o Guia da EFD-Contribuições DELEGA ao Ato COTEPE/ICMS 9",
    "C170": "o Guia da EFD-Contribuições DELEGA ao Ato COTEPE/ICMS 9",
    "C990": "encerramento de bloco",
    "9001": "abertura de bloco",
    "9900": "o mesmo par (registro, quantidade) nas duas",
    "9990": "encerramento de bloco",
    "9999": "encerramento do arquivo: só a contagem de linhas",
}

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
    # O `0200` **não** é o mesmo nas duas obrigações, e estava em `_COMUNS`.
    # Aqui ele termina em `CEST` (campo 13); na EFD-Contribuições ele acaba no
    # `ALIQ_ICMS`, e a palavra "CEST" não aparece uma única vez nas 433 páginas
    # do Guia Prático dela.  Compartilhado, o gerador de Contribuições escrevia
    # doze valores onde o validador espera onze — "quantidade de campos
    # inválida", registro recusado.
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
    # Filho do E110: um por ajuste do período.
    "E111": ("COD_AJ_APUR", "DESCR_COMPL_AJ", "VL_AJ_APUR"),
    "E990": ("QTD_LIN_E",),
}

# O 0000 daqui não se parece com o da EFD ICMS/IPI: nem os mesmos campos, nem
# a mesma ordem, nem a mesma quantidade.  Compartilhá-los seria o mesmo erro
# que o IND_ATIV — mesmo nome, tabela diferente.
EFD_CONTRIBUICOES: dict[str, tuple[str, ...]] = {
    **_COMUNS,
    # Sem `CEST`: o campo não existe nesta obrigação.  Ver a nota no `0200` da
    # EFD ICMS/IPI, acima.
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
    ),
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

# Por obrigação, com as mesmas chaves de `arquivadas.TIPOS` — há teste que
# cobra a igualdade dos dois conjuntos.  Sem ele, acrescentar uma obrigação
# nova daria um arquivo que se arquiva e não se sabe ler.
POR_OBRIGACAO: dict[str, dict[str, tuple[str, ...]]] = {
    "efd_icms": EFD_ICMS,
    "efd_contribuicoes": EFD_CONTRIBUICOES,
}


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
