"""Alterações em massa: selecionar, simular, comparar, confirmar.

O fluxo que o §13 do pedido exige, e a razão de cada passo:

    Selecionar → Configurar → Simular → Comparar → Confirmar → Validar

**Nada é aplicado no momento da seleção.**  `simular` devolve o que mudaria —
quantos documentos, quantos itens, valor por valor, impacto em reais e os
avisos — e não toca no banco.  Só `confirmar` grava, num lote que
`desfazer_lote` reverte inteiro.

**O filtro trabalha sobre o efetivo, não sobre o normalizado.**  Um item já
classificado para CFOP 5405 tem de aparecer quando alguém filtra por 5405,
ainda que o XML diga 6102 — senão a segunda passada de saneamento não
enxergaria o que a primeira fez.  Como o efetivo não existe em SQL (é
normalizado + ajustes), o recorte é em duas etapas: o banco reduz pelo escopo
(escritório, empresa, período, sentido, situação), que quase nunca é ajustado,
e o conteúdo é conferido em memória, exato.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import AjusteFiscal, DocumentoFiscal, ItemDocumentoFiscal
from src.documentos.ajustes import (
    ORIGEM_USUARIO,
    aplicar_ajuste,
    desserializar,
    novo_lote,
    valor_efetivo,
)
from src.documentos.classificacao import OPERADORES

logger = logging.getLogger("sped-hub.documentos")


class SelecaoVazia(ValueError):
    """Alteração em massa sem nenhum filtro alcançaria a base inteira."""


class AlteracaoInvalida(ValueError):
    """Campo que não existe, ou operação que o leiaute não admite."""


@dataclass(frozen=True)
class Filtro:
    """Uma condição do recorte. Todas precisam casar."""

    campo: str
    operador: str = "igual"
    valor: Any = None

    def __post_init__(self):
        if self.operador not in OPERADORES:
            raise AlteracaoInvalida(
                f"operador {self.operador!r} não existe — use um de {sorted(OPERADORES)}"
            )


@dataclass(frozen=True)
class Alteracao:
    """O que fazer com o que foi selecionado.

    `apenas_vazios` atende o §12.3: preencher o que falta sem tocar no que já
    está preenchido é a operação mais comum do saneamento, e a mais perigosa
    de fazer errado.
    """

    campo: str
    valor: Any
    apenas_vazios: bool = False


@dataclass
class Mudanca:
    """Uma alteração concreta, no nível em que ela acontece."""

    documento_id: int
    chave: str
    item_id: int | None
    numero_item: int | None
    campo: str
    valor_anterior: Any
    valor_novo: Any
    # Total do cabeçalho recomposto a partir das parcelas (§12.5), e não uma
    # alteração que alguém pediu.  A distinção existe por causa do impacto: o
    # recálculo não é dinheiro novo, é o mesmo dinheiro refletido para cima.
    recalculada: bool = False

    @property
    def impacto(self) -> float:
        """Diferença em reais; zero quando o campo não é numérico.

        Zero também quando a mudança é recálculo: somar o total do cabeçalho
        junto com as parcelas que o compõem contaria a mesma quantia duas
        vezes, e o impacto sairia dobrado.
        """
        if self.recalculada:
            return 0.0
        a, n = self.valor_anterior, self.valor_novo
        if isinstance(a, bool) or isinstance(n, bool):
            return 0.0
        if isinstance(a, int | float) and isinstance(n, int | float):
            return float(n) - float(a)
        return 0.0


@dataclass
class Aviso:
    """Problema detectado na simulação, com o que fazer a respeito.

    `impeditivo` separa o que o sistema recusa do que ele só sinaliza: recusar
    tudo que parece estranho travaria correções legítimas, e não recusar nada
    deixaria passar erro que o validador do Fisco pegaria depois.
    """

    documento_id: int
    numero_item: int | None
    campo: str
    problema: str
    impeditivo: bool = False

    def __str__(self) -> str:
        marca = "IMPEDITIVO" if self.impeditivo else "aviso"
        onde = f"item {self.numero_item}" if self.numero_item else "documento"
        return f"[{marca}] {onde}, {self.campo}: {self.problema}"


@dataclass
class Simulacao:
    """O que aconteceria — para ser lido antes de confirmar."""

    mudancas: list[Mudanca] = field(default_factory=list)
    avisos: list[Aviso] = field(default_factory=list)

    @property
    def documentos_afetados(self) -> int:
        return len({m.documento_id for m in self.mudancas})

    @property
    def itens_afetados(self) -> int:
        return len({m.item_id for m in self.mudancas if m.item_id is not None})

    @property
    def total_mudancas(self) -> int:
        return len(self.mudancas)

    @property
    def impacto_total(self) -> float:
        return sum(m.impacto for m in self.mudancas)

    @property
    def impedida(self) -> bool:
        return any(a.impeditivo for a in self.avisos)

    def por_campo(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for mudanca in self.mudancas:
            contagem[mudanca.campo] = contagem.get(mudanca.campo, 0) + 1
        return contagem

    def to_dict(self) -> dict:
        return {
            "documentos_afetados": self.documentos_afetados,
            "itens_afetados": self.itens_afetados,
            "total_mudancas": self.total_mudancas,
            "impacto_total": round(self.impacto_total, 2),
            "impedida": self.impedida,
            "por_campo": self.por_campo(),
            "avisos": [str(a) for a in self.avisos],
        }


# ── Escopo: o que o banco consegue recortar ────────────────────────────────
#
# Campos que praticamente nunca são ajustados, e por isso podem ser filtrados
# em SQL sem risco de perder o que a classificação já mudou.

_ESCOPO_DOCUMENTO = {
    "especie",
    "modelo",
    "sentido",
    "situacao",
    "emitente_cnpj",
    "destinatario_cnpj",
    "emitente_uf",
    "destinatario_uf",
    "serie",
    "numero",
    "chave",
}


@dataclass
class Selecao:
    """O recorte: escopo obrigatório mais filtros de conteúdo."""

    escritorio_id: int | None = None
    empresa_id: int | None = None
    data_inicio: datetime.date | None = None
    data_fim: datetime.date | None = None
    filtros: Sequence[Filtro] = ()

    def _tem_recorte(self) -> bool:
        return bool(
            self.empresa_id is not None or self.data_inicio or self.data_fim or self.filtros
        )

    def documentos(self, session: Session) -> list[DocumentoFiscal]:
        """Os documentos que o recorte alcança.

        Exige pelo menos um critério além do escritório: uma alteração em
        massa sem filtro pegaria a base inteira, e o `desfazer_lote` seria a
        única saída depois do estrago.
        """
        if not self._tem_recorte():
            raise SelecaoVazia(
                "seleção sem filtro alcançaria todos os documentos do escritório — "
                "informe ao menos período, empresa ou um filtro"
            )

        consulta = select(DocumentoFiscal).options(selectinload(DocumentoFiscal.itens))
        consulta = consulta.where(DocumentoFiscal.escritorio_id == self.escritorio_id)
        if self.empresa_id is not None:
            consulta = consulta.where(DocumentoFiscal.empresa_id == self.empresa_id)
        if self.data_inicio:
            consulta = consulta.where(DocumentoFiscal.data_emissao >= self.data_inicio)
        if self.data_fim:
            consulta = consulta.where(DocumentoFiscal.data_emissao <= self.data_fim)

        # Só os filtros de escopo entram no SQL; os de conteúdo são conferidos
        # sobre o efetivo, adiante.
        for filtro in self.filtros:
            if filtro.campo in _ESCOPO_DOCUMENTO and filtro.operador == "igual":
                consulta = consulta.where(getattr(DocumentoFiscal, filtro.campo) == filtro.valor)
        return list(session.execute(consulta).scalars().unique().all())


def _ajustes_de(session: Session, documentos: Sequence[DocumentoFiscal]) -> dict[int, list]:
    """Todos os ajustes dos documentos, numa consulta só."""
    if not documentos:
        return {}
    ids = [d.id for d in documentos]
    ajustes = (
        session.execute(select(AjusteFiscal).where(AjusteFiscal.documento_id.in_(ids)))
        .scalars()
        .all()
    )
    por_documento: dict[int, list] = {}
    for ajuste in ajustes:
        por_documento.setdefault(ajuste.documento_id, []).append(ajuste)
    return por_documento


def _efetivo_de(
    alvo: DocumentoFiscal | ItemDocumentoFiscal,
    campo: str,
    ajustes: Sequence,
) -> Any:
    if campo not in type(alvo).__table__.columns:
        return None
    return valor_efetivo(alvo, campo, ajustes)


def _casa(
    documento: DocumentoFiscal,
    item: ItemDocumentoFiscal | None,
    filtros: Iterable[Filtro],
    ajustes: Sequence,
) -> bool:
    """Confere os filtros sobre o efetivo — inclusive os já filtrados em SQL.

    Reconferir o que o banco já recortou custa pouco e fecha a diferença entre
    os dois caminhos: se um campo de escopo tiver sido ajustado, é o ajuste que
    vale, não a coluna.

    **Filtro de item alcança o cabeçalho quando ALGUM item casa.**  Sem isso,
    "documentos que tenham item com NCM 2203, mudar a natureza de operação"
    seria impossível: o filtro é de item, o campo alterado é de cabeçalho, e o
    cabeçalho nunca casaria com um filtro sobre coluna que ele não tem.
    """
    do_cabecalho = [a for a in ajustes if a.item_id is None]
    do_item = [a for a in ajustes if item is not None and a.item_id == item.id]

    for filtro in filtros:
        de_item = filtro.campo in ItemDocumentoFiscal.__table__.columns
        if item is not None and de_item:
            valor = _efetivo_de(item, filtro.campo, do_item)
        elif item is None and de_item and filtro.campo not in DocumentoFiscal.__table__.columns:
            if not _algum_item_casa(documento, filtro, ajustes):
                return False
            continue
        else:
            valor = _efetivo_de(documento, filtro.campo, do_cabecalho)
        if not OPERADORES[filtro.operador](valor, filtro.valor):
            return False
    return True


def _algum_item_casa(documento: DocumentoFiscal, filtro: Filtro, ajustes: Sequence) -> bool:
    for item in documento.itens:
        do_item = [a for a in ajustes if a.item_id == item.id]
        if OPERADORES[filtro.operador](_efetivo_de(item, filtro.campo, do_item), filtro.valor):
            return True
    return False


# ── Proteções (§16) ────────────────────────────────────────────────────────

_CFOP_ENTRADA = ("1", "2", "3")
_CFOP_SAIDA = ("5", "6", "7")


def _verificar(
    campo: str,
    valor: Any,
    documento: DocumentoFiscal,
    item: ItemDocumentoFiscal | None,
) -> list[str]:
    """As incompatibilidades que dá para checar sem cadastro de regime.

    Deliberadamente curta.  Cada verificação aqui é uma que não depende de
    informação que o sistema ainda não tem — CSOSN em empresa não optante, por
    exemplo, exigiria o regime tributário cadastrado, que não existe.  Fingir
    que verifica seria pior que não verificar.
    """
    problemas: list[str] = []
    texto = "" if valor is None else str(valor)

    if campo == "cfop" and texto:
        if not re.fullmatch(r"\d{4}", texto):
            problemas.append(f"CFOP {texto!r} não tem quatro dígitos")
        else:
            sentido = documento.sentido
            if sentido == "entrada" and texto[0] in _CFOP_SAIDA:
                problemas.append(f"CFOP {texto} é de saída, e o documento é de entrada")
            elif sentido == "saida" and texto[0] in _CFOP_ENTRADA:
                problemas.append(f"CFOP {texto} é de entrada, e o documento é de saída")

    if campo == "ncm" and texto and not re.fullmatch(r"\d{8}", texto):
        problemas.append(f"NCM {texto!r} não tem oito dígitos")

    if campo == "cest" and texto and not re.fullmatch(r"\d{7}", texto):
        problemas.append(f"CEST {texto!r} não tem sete dígitos")

    if campo in {"cst_icms", "cst_pis", "cst_cofins", "cst_ipi"} and texto:
        if not texto.isdigit():
            problemas.append(f"{campo.upper()} {texto!r} não é numérico")

    if campo == "cst_ibscbs" and texto and not re.fullmatch(r"\d{3}", texto):
        problemas.append(f"CST do IBS/CBS {texto!r} não tem três dígitos")

    return problemas


def valor_tipado(campo: str, bruto: str):
    """Texto vindo de fora, no tipo que a coluna espera.

    Terminal e formulário HTML entregam `str` sempre. Sem converter, alterar
    `base_icms` para `1000` mostraria **impacto R$ 0,00** na simulação —
    porque a diferença entre `0.0` e `"1000"` não é numérica —, e a simulação
    existe exatamente para mostrar o impacto financeiro antes de confirmar.

    A conversão é a mesma que a camada efetiva usa para ler ajustes
    (`desserializar`): duas conversões diferentes para o mesmo campo acabariam
    divergindo, e a que divergisse seria a menos usada.
    """
    for modelo in (ItemDocumentoFiscal, DocumentoFiscal):
        colunas = modelo.__table__.columns
        if campo in colunas:
            return desserializar(bruto, colunas[campo])
    raise ValueError(
        f"campo {campo!r} não existe em documento nem em item — "
        "alteração em massa com nome errado não alcançaria nada"
    )


def simular(
    session: Session,
    selecao: Selecao,
    alteracoes: Sequence[Alteracao],
    *,
    recompor_totais: bool = True,
) -> Simulacao:
    """O que mudaria, sem mudar nada.

    Documento cancelado é recusado com aviso impeditivo: alterar escrituração
    de nota que não existe mais produz arquivo que o validador rejeita.

    `recompor_totais` traz para a simulação os totais do cabeçalho que a
    alteração dos itens torna obsoletos (§12.5). Aparecem aqui, e não dentro do
    `confirmar`, de propósito: a simulação é o que a pessoa lê antes de decidir,
    e um recálculo que só acontecesse na gravação mudaria mais coisas do que o
    que foi mostrado.
    """
    if not alteracoes:
        raise AlteracaoInvalida("nenhuma alteração configurada")

    documentos = selecao.documentos(session)
    ajustes = _ajustes_de(session, documentos)
    simulacao = Simulacao()

    for documento in documentos:
        dos_ajustes = ajustes.get(documento.id, [])
        alvos: list[ItemDocumentoFiscal | None] = [None, *documento.itens]
        for item in alvos:
            if not _casa(documento, item, selecao.filtros, dos_ajustes):
                continue
            _simular_alvo(documento, item, alteracoes, dos_ajustes, simulacao)

    if recompor_totais:
        tocados = {m.documento_id for m in simulacao.mudancas if m.item_id is not None}
        if tocados:
            recalculadas, avisos = recalcular(
                session,
                [d for d in documentos if d.id in tocados],
                mudancas=simulacao.mudancas,
            )
            simulacao.mudancas.extend(recalculadas)
            simulacao.avisos.extend(avisos)

    return simulacao


def _simular_alvo(
    documento: DocumentoFiscal,
    item: ItemDocumentoFiscal | None,
    alteracoes: Sequence[Alteracao],
    ajustes: Sequence,
    simulacao: Simulacao,
) -> None:
    alvo = item if item is not None else documento
    relevantes = [
        a for a in ajustes if (a.item_id == item.id if item is not None else a.item_id is None)
    ]

    for alteracao in alteracoes:
        if alteracao.campo not in type(alvo).__table__.columns:
            continue
        # Campo que existe nos DOIS níveis é sempre um valor: no item é a
        # parcela, no documento é o total.  Sobrescrever o total com o valor de
        # uma parcela produziria um documento cujo cabeçalho não bate com a
        # soma dos itens — e ninguém pediu isso ao mandar "alterar a base de
        # cálculo".  Ajustar o total é recálculo (§12.5), não substituição.
        if (
            item is None
            and alteracao.campo in ItemDocumentoFiscal.__table__.columns
            and alteracao.campo in DocumentoFiscal.__table__.columns
        ):
            continue
        anterior = valor_efetivo(alvo, alteracao.campo, relevantes)

        if alteracao.apenas_vazios and anterior not in (None, ""):
            continue
        if str(anterior or "") == str(alteracao.valor or ""):
            continue

        for problema in _verificar(alteracao.campo, alteracao.valor, documento, item):
            simulacao.avisos.append(
                Aviso(
                    documento_id=documento.id,
                    numero_item=item.numero_item if item is not None else None,
                    campo=alteracao.campo,
                    problema=problema,
                    impeditivo=True,
                )
            )

        if documento.situacao == "cancelado":
            simulacao.avisos.append(
                Aviso(
                    documento_id=documento.id,
                    numero_item=item.numero_item if item is not None else None,
                    campo=alteracao.campo,
                    problema="documento cancelado — alterá-lo gera arquivo que o Fisco rejeita",
                    impeditivo=True,
                )
            )

        simulacao.mudancas.append(
            Mudanca(
                documento_id=documento.id,
                chave=documento.chave,
                item_id=item.id if item is not None else None,
                numero_item=item.numero_item if item is not None else None,
                campo=alteracao.campo,
                valor_anterior=anterior,
                valor_novo=alteracao.valor,
            )
        )


# Total do cabeçalho → parcela do item que o compõe.  Só somas diretas entram
# aqui: cada uma destas é, por definição do leiaute, a soma da parcela
# correspondente dos itens.
#
# `valor_total` do documento fica **de fora de propósito**.  Ele não é soma de
# nada: é o vNF, que sai de produtos menos desconto mais frete, seguro,
# despesas, ST e IPI — e ainda de componentes que este modelo não carrega
# (ICMS desonerado, imposto de importação, valor de serviços).  Calculá-lo com
# a metade dos termos produziria um total errado apresentado como certo, que é
# pior que um total desatualizado: o desatualizado ao menos é o número que o
# emitente declarou.  O recálculo avisa quando o deixa para trás.
SOMAS = {
    "valor_produtos": "valor_total",
    "valor_desconto": "valor_desconto",
    "valor_frete": "valor_frete",
    "valor_seguro": "valor_seguro",
    "valor_outras": "valor_outras",
    "base_icms": "base_icms",
    "valor_icms": "valor_icms",
    "valor_icms_st": "valor_icms_st",
    "valor_ipi": "valor_ipi",
    "valor_pis": "valor_pis",
    "valor_cofins": "valor_cofins",
}

# Diferença abaixo da qual dois totais são o mesmo número.  Somar onze parcelas
# em ponto flutuante não devolve exatamente o mesmo valor que o emitente
# escreveu, e propor uma mudança de um centésimo de centavo encheria a
# simulação de ruído que ninguém vai conferir.
TOLERANCIA = 0.005


def _numero(valor: Any) -> float:
    try:
        return float(valor or 0.0)
    except (TypeError, ValueError):
        return 0.0


def recalcular(
    session: Session,
    documentos: Sequence[DocumentoFiscal],
    *,
    mudancas: Sequence[Mudanca] = (),
) -> tuple[list[Mudanca], list[Aviso]]:
    """Os totais do cabeçalho recompostos a partir das parcelas dos itens.

    §12.5. Alterar a parcela de um item deixa o cabeçalho para trás, e o
    arquivo sai com o C100 dizendo um valor e a soma dos C170 dizendo outro —
    que é exatamente o que o validador do Fisco confere.

    Recebe as `mudancas` que a simulação ainda **não** aplicou: o recálculo tem
    de partir de como os itens vão ficar, não de como estão. Sem isso, o total
    proposto seria o de antes da alteração.
    """
    por_item: dict[tuple[int, str], Any] = {
        (m.item_id, m.campo): m.valor_novo for m in mudancas if m.item_id is not None
    }
    ajustes = _ajustes_de(session, documentos)

    recalculadas: list[Mudanca] = []
    avisos: list[Aviso] = []

    for documento in documentos:
        dos_ajustes = ajustes.get(documento.id, [])
        do_cabecalho = [a for a in dos_ajustes if a.item_id is None]

        for total, parcela in SOMAS.items():
            soma = 0.0
            for item in documento.itens:
                if (item.id, parcela) in por_item:
                    soma += _numero(por_item[(item.id, parcela)])
                else:
                    do_item = [a for a in dos_ajustes if a.item_id == item.id]
                    soma += _numero(valor_efetivo(item, parcela, do_item))

            atual = _numero(valor_efetivo(documento, total, do_cabecalho))
            if abs(soma - atual) < TOLERANCIA:
                continue

            recalculadas.append(
                Mudanca(
                    documento_id=documento.id,
                    chave=documento.chave,
                    item_id=None,
                    numero_item=None,
                    campo=total,
                    valor_anterior=atual,
                    valor_novo=round(soma, 2),
                    recalculada=True,
                )
            )

    # Um aviso só, e não um por documento: a mensagem é a mesma, e repeti-la
    # quinhentas vezes num fechamento faria ninguém ler nenhuma.
    if recalculadas:
        avisos.append(
            Aviso(
                documento_id=recalculadas[0].documento_id,
                numero_item=None,
                campo="valor_total",
                problema=(
                    "o total do documento (vNF) NÃO foi recalculado: ele não é soma de "
                    "parcela, e este modelo não carrega todos os termos da fórmula "
                    "(ICMS desonerado, imposto de importação, serviços). Confira antes "
                    "de gerar"
                ),
            )
        )

    return recalculadas, avisos


def confirmar(
    session: Session,
    simulacao: Simulacao,
    *,
    motivo: str | None = None,
    usuario_id: int | None = None,
    forcar: bool = False,
) -> str:
    """Grava o que a simulação previu, num lote reversível.

    Recusa quando há aviso impeditivo, a menos que `forcar` diga o contrário —
    a decisão de passar por cima existe, mas tem de ser tomada de propósito e
    fica registrada no motivo de cada ajuste.
    """
    if simulacao.impedida and not forcar:
        impeditivos = [str(a) for a in simulacao.avisos if a.impeditivo]
        raise AlteracaoInvalida(
            f"{len(impeditivos)} problema(s) impeditivo(s); "
            f"corrija ou use forcar=True: {impeditivos[0]}"
        )

    lote = novo_lote()
    por_documento: dict[int, DocumentoFiscal] = {}
    por_item: dict[int, ItemDocumentoFiscal] = {}

    for mudanca in simulacao.mudancas:
        documento = por_documento.get(mudanca.documento_id)
        if documento is None:
            documento = session.get(DocumentoFiscal, mudanca.documento_id)
            por_documento[mudanca.documento_id] = documento
            for item in documento.itens:
                por_item[item.id] = item

        aplicar_ajuste(
            session,
            documento=documento,
            item=por_item.get(mudanca.item_id) if mudanca.item_id else None,
            campo=mudanca.campo,
            valor_novo=mudanca.valor_novo,
            origem=ORIGEM_USUARIO,
            motivo=motivo or "alteração em massa",
            lote=lote,
            usuario_id=usuario_id,
        )
    logger.info(
        "Alteração em massa %s: %d mudanças em %d documentos, impacto R$ %.2f",
        lote,
        simulacao.total_mudancas,
        simulacao.documentos_afetados,
        simulacao.impacto_total,
    )
    return lote
