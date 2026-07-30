"""A camada efetiva: o normalizado mais os ajustes, calculado na hora.

O que vai para o SPED não é coluna nenhuma. É o que este módulo calcula:
partindo do valor normalizado — que veio do XML e nunca muda —, aplica os
:class:`AjusteFiscal` do campo, na ordem em que foram feitos, e devolve o
último.

Calcular em vez de gravar é o que torna a reversão trivial (desfazer um lote é
apagar seus ajustes) e a auditoria exata (o histórico de um campo é a lista
dos ajustes que o alcançam). Uma coluna com o valor final divergiria das
outras duas camadas no primeiro ``UPDATE`` escrito fora do fluxo.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Date, DateTime, Float, Integer, delete, select
from sqlalchemy.orm import Session

from src.db.models import AjusteFiscal, DocumentoFiscal, ItemDocumentoFiscal

logger = logging.getLogger("sped-hub.documentos")

ORIGEM_REGRA = "regra"
ORIGEM_USUARIO = "usuario"
ORIGENS = (ORIGEM_REGRA, ORIGEM_USUARIO)


class CampoInexistente(ValueError):
    """Ajuste que aponta para campo que o modelo não tem.

    Vale a pena falhar cedo: um ajuste com nome de campo errado ficaria
    gravado, invisível, e o valor nunca chegaria à escrituração.
    """


class OrigemInvalida(ValueError):
    """Só `regra` e `usuario` — é o que separa sugestão de decisão."""


def _coluna(alvo: DocumentoFiscal | ItemDocumentoFiscal, campo: str):
    colunas = type(alvo).__table__.columns
    if campo not in colunas:
        raise CampoInexistente(
            f"{type(alvo).__name__} não tem o campo {campo!r} — "
            "ajuste gravado com nome errado nunca chegaria ao SPED"
        )
    return colunas[campo]


def serializar(valor: Any) -> str | None:
    """Valor → texto, para caber na coluna do ajuste.

    `None` vira `None` de propósito: a linha do ajuste existir já significa
    "alguém mexeu", então nulo pode significar "limpou o campo" sem se
    confundir com "não ajustou".
    """
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime | datetime.date):
        return valor.isoformat()
    return str(valor)


def desserializar(texto: str | None, coluna) -> Any:
    """Texto → o tipo que a coluna espera.

    Sem isto, um CFOP ajustado voltaria como `str` e um valor como `str`
    também — e a soma na apuração concatenaria em vez de somar.
    """
    if texto is None:
        return None
    tipo = coluna.type
    try:
        if isinstance(tipo, Float):
            return float(texto)
        if isinstance(tipo, Integer):
            return int(texto)
        if isinstance(tipo, DateTime):
            return datetime.datetime.fromisoformat(texto)
        if isinstance(tipo, Date):
            return datetime.date.fromisoformat(texto[:10])
    except (TypeError, ValueError):
        # Ajuste com valor que não converte é dado corrompido, não motivo
        # para derrubar a geração inteira: registra e mantém o texto.
        logger.warning(
            "Ajuste com valor %r incompatível com a coluna %s (%s); mantido como texto",
            texto,
            coluna.name,
            tipo,
        )
    return texto


def _ordenados(ajustes: Iterable[AjusteFiscal]) -> list[AjusteFiscal]:
    """Do mais antigo ao mais recente.

    Desempata por `id` porque dois ajustes do mesmo lote nascem no mesmo
    instante, e aí só a ordem de inserção distingue.
    """
    return sorted(
        ajustes,
        key=lambda a: (a.criado_em or datetime.datetime.min, a.id or 0),
    )


def valor_efetivo(
    alvo: DocumentoFiscal | ItemDocumentoFiscal,
    campo: str,
    ajustes: Iterable[AjusteFiscal] = (),
) -> Any:
    """O valor que vai para o SPED: o normalizado, ou o último ajuste dele.

    Recebe os ajustes já carregados de propósito. Buscá-los aqui dentro faria
    uma consulta por campo — com 68 campos por item e milhares de itens, a
    geração de um mês viraria centenas de milhares de consultas.
    """
    coluna = _coluna(alvo, campo)
    do_campo = [a for a in _ordenados(ajustes) if a.campo == campo]
    if not do_campo:
        return getattr(alvo, campo)
    return desserializar(do_campo[-1].valor_novo, coluna)


@dataclass
class VisaoEfetiva:
    """O documento como ele sairá na escrituração.

    Carrega os ajustes uma vez e resolve tudo em memória.
    """

    documento: DocumentoFiscal
    valores: dict[str, Any] = field(default_factory=dict)
    itens: list[dict[str, Any]] = field(default_factory=list)
    campos_alterados: set[str] = field(default_factory=set)
    itens_alterados: dict[int, set[str]] = field(default_factory=dict)

    @property
    def alterado(self) -> bool:
        return bool(self.campos_alterados or self.itens_alterados)

    def item(self, numero_item: int) -> dict[str, Any]:
        for valores in self.itens:
            if valores.get("numero_item") == numero_item:
                return valores
        raise KeyError(f"documento não tem item {numero_item}")


def _visao_de(alvo, ajustes: Sequence[AjusteFiscal]) -> tuple[dict[str, Any], set[str]]:
    valores = {c.name: getattr(alvo, c.name) for c in type(alvo).__table__.columns}
    alterados: set[str] = set()
    for ajuste in _ordenados(ajustes):
        if ajuste.campo not in valores:
            logger.warning(
                "Ajuste %s aponta para campo inexistente %r em %s; ignorado",
                ajuste.id,
                ajuste.campo,
                type(alvo).__name__,
            )
            continue
        valores[ajuste.campo] = desserializar(
            ajuste.valor_novo, type(alvo).__table__.columns[ajuste.campo]
        )
        alterados.add(ajuste.campo)
    return valores, alterados


def efetivo(session: Session, documento: DocumentoFiscal) -> VisaoEfetiva:
    """Monta a visão efetiva do documento inteiro, numa consulta só."""
    ajustes = (
        session.execute(select(AjusteFiscal).where(AjusteFiscal.documento_id == documento.id))
        .scalars()
        .all()
    )
    do_cabecalho = [a for a in ajustes if a.item_id is None]
    por_item: dict[int, list[AjusteFiscal]] = {}
    for ajuste in ajustes:
        if ajuste.item_id is not None:
            por_item.setdefault(ajuste.item_id, []).append(ajuste)

    valores, alterados = _visao_de(documento, do_cabecalho)
    visao = VisaoEfetiva(documento=documento, valores=valores, campos_alterados=alterados)

    for item in documento.itens:
        do_item, alterados_item = _visao_de(item, por_item.get(item.id, []))
        visao.itens.append(do_item)
        if alterados_item:
            visao.itens_alterados[item.numero_item] = alterados_item
    return visao


def novo_lote() -> str:
    """Identificador de um conjunto de ajustes feitos juntos.

    É o que permite desfazer uma alteração em massa inteira, e não ajuste a
    ajuste.
    """
    return uuid.uuid4().hex[:16]


def aplicar_ajuste(
    session: Session,
    *,
    documento: DocumentoFiscal,
    campo: str,
    valor_novo: Any,
    origem: str,
    item: ItemDocumentoFiscal | None = None,
    regra: str | None = None,
    motivo: str | None = None,
    lote: str | None = None,
    usuario_id: int | None = None,
    ajustes_atuais: Iterable[AjusteFiscal] | None = None,
) -> AjusteFiscal | None:
    """Registra uma alteração — sem tocar no normalizado.

    Devolve `None` quando o valor pedido já é o efetivo: gravar um ajuste que
    não muda nada poluiria o histórico e faria uma alteração em massa relatar
    impacto que não existe.

    `origem` separa o que a regra sugeriu do que a pessoa decidiu, que é a
    distinção que o §6 do pedido exige manter.
    """
    if origem not in ORIGENS:
        raise OrigemInvalida(f"origem deve ser uma de {ORIGENS}, veio {origem!r}")

    alvo = item if item is not None else documento
    coluna = _coluna(alvo, campo)

    if ajustes_atuais is None:
        consulta = select(AjusteFiscal).where(
            AjusteFiscal.documento_id == documento.id,
            AjusteFiscal.campo == campo,
        )
        consulta = consulta.where(
            AjusteFiscal.item_id == item.id if item is not None else AjusteFiscal.item_id.is_(None)
        )
        ajustes_atuais = session.execute(consulta).scalars().all()

    anterior = valor_efetivo(alvo, campo, ajustes_atuais)
    novo = desserializar(serializar(valor_novo), coluna)
    if anterior == novo:
        return None

    ajuste = AjusteFiscal(
        documento_id=documento.id,
        item_id=item.id if item is not None else None,
        campo=campo,
        valor_anterior=serializar(anterior),
        valor_novo=serializar(valor_novo),
        origem=origem,
        regra=regra,
        motivo=motivo,
        lote=lote,
        usuario_id=usuario_id,
    )
    session.add(ajuste)
    session.flush()
    return ajuste


def desfazer_lote(session: Session, lote: str) -> int:
    """Apaga os ajustes de um lote e devolve quantos saíram.

    É toda a reversão: o normalizado nunca foi tocado, então remover os
    ajustes basta para o documento voltar ao que era. Nenhum valor precisa ser
    restaurado, e por isso não há como restaurar errado.
    """
    if not lote:
        raise ValueError("lote vazio apagaria os ajustes avulsos")
    resultado = session.execute(delete(AjusteFiscal).where(AjusteFiscal.lote == lote))
    session.flush()
    return resultado.rowcount or 0


def historico(
    session: Session,
    documento: DocumentoFiscal,
    campo: str | None = None,
    *,
    item: ItemDocumentoFiscal | None = None,
) -> list[AjusteFiscal]:
    """Por que este registro saiu assim — na ordem em que aconteceu."""
    consulta = select(AjusteFiscal).where(AjusteFiscal.documento_id == documento.id)
    if campo is not None:
        consulta = consulta.where(AjusteFiscal.campo == campo)
    if item is not None:
        consulta = consulta.where(AjusteFiscal.item_id == item.id)
    return _ordenados(session.execute(consulta).scalars().all())
