"""Ajustes da apuração do ICMS — o registro E111 e o que ele compõe.

A apuração do bloco E era a soma dos documentos e mais nada. Empresa com
benefício fiscal, crédito outorgado, estorno ou dedução tem valores que **não
estão em nota nenhuma**, e sem eles o imposto sai errado — a menos quando
falta um crédito outorgado, a mais quando falta um estorno.

**O que este módulo sabe é a estrutura do código, não a tabela.** A tabela
5.1.1 é de cada Secretaria da Fazenda: os quatro últimos dígitos e o que cada
um significa mudam por estado, e há centenas deles. Tentar embutir isso aqui
seria embutir uma tabela desatualizada — e errada para 26 dos 27 estados.

A estrutura, essa é nacional (Ato COTEPE/ICMS 09/2008) — `PRBCDDDD`, onde a
**quarta posição decide em que campo do E110 o valor entra**. Quem informa o
código informa junto o tratamento, e o resto se deriva sem palpite.

O que fica de fora, e está no roadmap: ajustes de documento (`C197`/`D197`),
que compõem os campos `VL_TOT_AJ_*` do E110 e são outra coisa — nascem de uma
nota específica, não do período.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import AjusteApuracao, Empresa

# A quarta posição do código: para onde o valor vai no E110, e como se lê.
#
# Conferido (§8.1) contra o Guia Prático da EFD ICMS/IPI versão 3.2.2
# (11/02/2026), validações dos campos 04, 05, 08, 09, 12 e 15 do registro E110,
# em 2026-08-03.  O próprio Guia resume no cabeçalho do E111: ele discrimina os
# ajustes lançados em VL_TOT_AJ_DEBITOS, VL_ESTORNOS_CRED, VL_TOT_AJ_CREDITOS,
# VL_ESTORNOS_DEB, VL_TOT_DED e DEB_ESP.  Os campos VL_AJ_DEBITOS (03) e
# VL_AJ_CREDITOS (07) **não** estão nessa lista: são os ajustes que nascem de
# um documento (C197/D197), que este gerador não escreve.
#
# `campo` é `None` no controle extra-apuração (9): ele existe justamente para
# registrar valor que **não** entra na apuração do período — somá-lo seria o
# oposto do que o código pede.
UTILIZACOES: dict[str, tuple[str, str | None]] = {
    "0": ("outros débitos", "VL_TOT_AJ_DEBITOS"),
    "1": ("estorno de créditos", "VL_ESTORNOS_CRED"),
    "2": ("outros créditos", "VL_TOT_AJ_CREDITOS"),
    "3": ("estorno de débitos", "VL_ESTORNOS_DEB"),
    "4": ("deduções", "VL_TOT_DED"),
    "5": ("débito especial", "DEB_ESP"),
    "9": ("controle extra-apuração", None),
}

# A terceira posição: qual apuração o ajuste alcança.  O E111 é filho do E110,
# que é a apuração do ICMS próprio; ajuste de ST, DIFAL ou FCP pertence a
# outro registro, que este gerador não escreve.
APURACOES = {
    "0": "ICMS",
    "1": "ICMS-ST",
    "2": "ICMS DIFAL",
    "3": "FCP",
}
APURACAO_ICMS = "0"


class AjusteInvalido(ValueError):
    """Código de ajuste que não pode entrar na apuração como está."""


def validar_codigo(cod_aj: str, *, uf: str | None = None) -> str:
    """Confere a estrutura do código e devolve-o normalizado.

    Só a estrutura: o sequencial é da tabela do estado, e conferi-lo aqui
    exigiria embutir 27 tabelas que mudam por ato normativo.

    A UF é conferida quando informada, porque um código de outro estado num
    E111 é recusado pelo validador — e é um erro fácil de cometer copiando o
    código de um cliente para outro.
    """
    codigo = (cod_aj or "").strip().upper()
    if len(codigo) != 8:
        raise AjusteInvalido(
            f"{cod_aj!r} não tem os 8 caracteres do código da tabela 5.1.1 "
            "(UF + apuração + utilização + sequencial, como em 'SP020007')"
        )

    if uf and codigo[:2] != uf.upper():
        raise AjusteInvalido(
            f"o código {codigo} é do estado {codigo[:2]}, e a empresa é de {uf.upper()} — "
            "a tabela 5.1.1 é de cada Secretaria da Fazenda, e o validador recusa "
            "código de outra UF"
        )

    if codigo[2] not in APURACOES:
        opcoes = ", ".join(f"{c}={d}" for c, d in sorted(APURACOES.items()))
        raise AjusteInvalido(
            f"a 3ª posição de {codigo} ({codigo[2]!r}) não é uma apuração conhecida: {opcoes}"
        )

    if codigo[3] not in UTILIZACOES:
        opcoes = ", ".join(f"{c}={d}" for c, (d, _) in sorted(UTILIZACOES.items()))
        raise AjusteInvalido(
            f"a 4ª posição de {codigo} ({codigo[3]!r}) não é uma utilização conhecida: {opcoes}"
        )

    if not codigo[4:].isdigit():
        raise AjusteInvalido(
            f"o sequencial de {codigo} ({codigo[4:]!r}) não é numérico — os quatro "
            "últimos dígitos vêm da tabela do seu estado"
        )
    return codigo


def utilizacao(cod_aj: str) -> tuple[str, str | None]:
    """Como se lê o ajuste e em que campo do E110 ele entra."""
    return UTILIZACOES.get(cod_aj[3:4], ("utilização desconhecida", None))


def apuracao(cod_aj: str) -> str:
    return APURACOES.get(cod_aj[2:3], "apuração desconhecida")


def criar_ajuste(
    session: Session,
    *,
    empresa: Empresa,
    data_inicio: datetime.date,
    data_fim: datetime.date,
    cod_aj: str,
    valor: float,
    descricao: str | None = None,
    usuario_id: int | None = None,
    tipo: str = "efd_icms",
) -> AjusteApuracao:
    """Registra um ajuste do período, já validado.

    Valor negativo é recusado: o sinal do ajuste está no **código**, não no
    número. Um "outros créditos" de valor negativo seria um débito escrito de
    um jeito que o validador não entende, e a apuração sairia com o sinal
    trocado sem que ninguém visse.
    """
    codigo = validar_codigo(cod_aj, uf=empresa.uf)
    if valor < 0:
        rotulo, _ = utilizacao(codigo)
        raise AjusteInvalido(
            f"valor negativo ({valor}) não é ajuste: o sinal está no código, não no "
            f"número. {codigo} é '{rotulo}'; para o efeito contrário, use o código da "
            "utilização correspondente na tabela do seu estado"
        )

    ajuste = AjusteApuracao(
        escritorio_id=empresa.escritorio_id,
        empresa_id=empresa.id,
        tipo=tipo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cod_aj=codigo,
        descricao=descricao,
        valor=valor,
        usuario_id=usuario_id,
    )
    session.add(ajuste)
    session.flush()
    return ajuste


def ajustes_do_periodo(
    session: Session,
    *,
    empresa_id: int,
    data_inicio: datetime.date,
    data_fim: datetime.date,
    tipo: str = "efd_icms",
) -> list[AjusteApuracao]:
    """Os ajustes daquele período exato, na ordem em que foram cadastrados.

    O casamento é por período **exato**, não por sobreposição: um ajuste de
    julho não pertence "um pouco" a uma apuração de agosto, e aproximar aqui
    faria o mesmo valor entrar em dois meses.
    """
    consulta = (
        select(AjusteApuracao)
        .where(
            AjusteApuracao.empresa_id == empresa_id,
            AjusteApuracao.tipo == tipo,
            AjusteApuracao.data_inicio == data_inicio,
            AjusteApuracao.data_fim == data_fim,
        )
        .order_by(AjusteApuracao.criado_em, AjusteApuracao.id)
    )
    return list(session.execute(consulta).scalars().all())


def totais_por_campo(ajustes: list[AjusteApuracao]) -> dict[str, float]:
    """Quanto cada campo do E110 recebe, somado por utilização.

    Só o que alcança a apuração do ICMS próprio: ajuste de ST, DIFAL ou FCP
    pertence a outro registro, e o controle extra-apuração (9) existe
    justamente para **não** entrar.
    """
    totais: dict[str, float] = {}
    for ajuste in ajustes:
        if ajuste.cod_aj[2:3] != APURACAO_ICMS:
            continue
        _, campo = utilizacao(ajuste.cod_aj)
        if campo is None:
            continue
        totais[campo] = totais.get(campo, 0.0) + ajuste.valor
    return totais
