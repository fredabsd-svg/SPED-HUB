"""Motor de classificação: a regra sugere, ninguém aplica em silêncio.

O escritório sabe que "para este fornecedor e este NCM, o CFOP é aquele". Esse
conhecimento hoje mora na cabeça de alguém e é refeito todo mês. Aqui ele vira
:class:`RegraFiscal`, e o motor o aplica sobre os documentos importados.

**Sugerir e aplicar são passos separados**, de propósito. `avaliar` produz
:class:`Sugestao` — regra usada, valor anterior, valor sugerido, justificativa,
confiança e impacto — e não toca no banco. Só `aplicar` grava, e o que ela
grava são `AjusteFiscal` de origem ``regra``, que a camada efetiva já sabe
reverter. Uma classificação errada aplicada em silêncio sobre um mês inteiro é
o tipo de coisa que só se descobre na malha fina.
"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.db.models import DocumentoFiscal, ItemDocumentoFiscal, RegraFiscal
from src.documentos.ajustes import ORIGEM_REGRA, aplicar_ajuste, novo_lote, valor_efetivo
from src.documentos.tabelas_ibscbs import conferir_valor

logger = logging.getLogger("sped-hub.documentos")


class RegraInvalida(ValueError):
    """Condição ou ação que o motor não sabe executar.

    Falha ao cadastrar, não ao rodar: uma regra quebrada descoberta durante o
    fechamento é pior que uma regra recusada na hora de salvar.
    """


# ── Operadores ─────────────────────────────────────────────────────────────
#
# Deliberadamente poucos e sem escape para expressão avaliada.  Cada um é uma
# comparação entre o valor do campo e o valor da condição.


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


def _lista(valor: Any) -> list[str]:
    if isinstance(valor, str | bytes):
        return [_texto(valor)]
    if isinstance(valor, Iterable):
        return [_texto(v) for v in valor]
    return [_texto(valor)]


OPERADORES = {
    "igual": lambda campo, valor: _texto(campo) == _texto(valor),
    "diferente": lambda campo, valor: _texto(campo) != _texto(valor),
    "em": lambda campo, valor: _texto(campo) in _lista(valor),
    "nao_em": lambda campo, valor: _texto(campo) not in _lista(valor),
    "comeca_com": lambda campo, valor: _texto(campo).startswith(_texto(valor)),
    "contem": lambda campo, valor: _texto(valor) in _texto(campo),
    "vazio": lambda campo, valor: campo is None or _texto(campo) == "",
    "preenchido": lambda campo, valor: campo is not None and _texto(campo) != "",
    "maior_que": lambda campo, valor: _numero(campo) > _numero(valor),
    "menor_que": lambda campo, valor: _numero(campo) < _numero(valor),
}


def _numero(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return float("-inf")


@dataclass(frozen=True)
class Sugestao:
    """O que a regra propõe, com tudo que se precisa para decidir.

    Não é aplicação: é proposta. O §7 do pedido exige que a tela mostre a
    regra, o valor anterior, o sugerido, a justificativa, a confiança e o
    impacto — e é isso que esta estrutura carrega.
    """

    documento_id: int
    item_id: int | None
    numero_item: int | None
    campo: str
    valor_anterior: Any
    valor_sugerido: Any
    regra_id: int | None
    regra_nome: str
    justificativa: str
    confianca: float = 1.0

    @property
    def impacto(self) -> float | None:
        """Diferença, quando o campo é numérico. `None` quando não é.

        Trocar um CFOP não tem impacto em reais; trocar uma base de cálculo
        tem, e é o número que decide se a alteração passa ou não.
        """
        anterior, sugerido = self.valor_anterior, self.valor_sugerido
        if isinstance(anterior, bool) or isinstance(sugerido, bool):
            return None
        if not isinstance(anterior, int | float) or not isinstance(sugerido, int | float):
            return None
        return float(sugerido) - float(anterior)


@dataclass
class Conflito:
    """Duas regras de mesma prioridade disputando o mesmo campo.

    O motor não escolhe: escolher por sorteio faria a mesma importação
    produzir resultados diferentes, e ninguém desconfiaria. Denuncia e deixa
    o campo como está.
    """

    campo: str
    numero_item: int | None
    regras: list[str]
    prioridade: int

    def __str__(self) -> str:
        return (
            f"campo {self.campo!r}"
            + (f" do item {self.numero_item}" if self.numero_item else " do documento")
            + f": {', '.join(self.regras)} têm prioridade {self.prioridade}"
        )


@dataclass
class ResultadoClassificacao:
    sugestoes: list[Sugestao] = field(default_factory=list)
    conflitos: list[Conflito] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.sugestoes)


def _carregar(bruto: str, rotulo: str) -> list[dict]:
    try:
        dados = json.loads(bruto or "[]")
    except json.JSONDecodeError as erro:
        raise RegraInvalida(f"{rotulo} não é JSON válido: {erro}") from erro
    if not isinstance(dados, list):
        raise RegraInvalida(f"{rotulo} deve ser uma lista, veio {type(dados).__name__}")
    return dados


def validar_regra(regra: RegraFiscal) -> None:
    """Recusa a regra quebrada na hora de salvar, não durante o fechamento."""
    condicoes = _carregar(regra.condicoes, "condicoes")
    acoes = _carregar(regra.acoes, "acoes")

    for condicao in condicoes:
        if not isinstance(condicao, dict) or "campo" not in condicao:
            raise RegraInvalida(f"condição sem campo: {condicao!r}")
        operador = condicao.get("operador", "igual")
        if operador not in OPERADORES:
            raise RegraInvalida(
                f"operador {operador!r} não existe — use um de {sorted(OPERADORES)}"
            )
    if not acoes:
        raise RegraInvalida("regra sem ação não muda nada")
    for acao in acoes:
        if not isinstance(acao, dict) or "campo" not in acao:
            raise RegraInvalida(f"ação sem campo: {acao!r}")
        # Uma regra escreve o mesmo tipo de valor que `fiscal alterar`, mas em
        # todo documento que casar com ela — inclusive nos que ainda nem foram
        # importados.  Aceitar um código inventado aqui é aceitá-lo mil vezes,
        # com origem `regra`, sem que ninguém tenha digitado nenhuma delas.
        for problema in conferir_valor(acao["campo"], acao.get("valor")):
            raise RegraInvalida(f"ação inválida: {problema}")


def _valor_do_campo(
    campo: str,
    documento: DocumentoFiscal,
    item: ItemDocumentoFiscal | None,
    ajustes: Sequence,
) -> Any:
    """Lê do item quando o campo é dele, senão do documento.

    Lê o **efetivo**, não o normalizado: uma regra que roda depois de outra
    precisa enxergar o que a primeira decidiu, senão a segunda classificaria
    em cima de um valor que já não vale.
    """
    if item is not None and campo in ItemDocumentoFiscal.__table__.columns:
        return valor_efetivo(item, campo, [a for a in ajustes if a.item_id == item.id])
    if campo in DocumentoFiscal.__table__.columns:
        return valor_efetivo(documento, campo, [a for a in ajustes if a.item_id is None])
    return None


def _casa(
    regra: RegraFiscal,
    documento: DocumentoFiscal,
    item: ItemDocumentoFiscal | None,
    ajustes: Sequence,
) -> bool:
    for condicao in _carregar(regra.condicoes, "condicoes"):
        campo = condicao["campo"]
        operador = OPERADORES[condicao.get("operador", "igual")]
        if not operador(_valor_do_campo(campo, documento, item, ajustes), condicao.get("valor")):
            return False
    return True


def _vigente(regra: RegraFiscal, data: datetime.date | None) -> bool:
    """Regra fiscal nasce e morre com a legislação.

    Sem data no documento, vale a regra — recusar seria pior: o documento
    ficaria sem classificação nenhuma por falta de um campo opcional.
    """
    if data is None:
        return True
    if regra.vigencia_inicio and data < regra.vigencia_inicio:
        return False
    return not (regra.vigencia_fim and data > regra.vigencia_fim)


def _dele_ou_de_todos(coluna, valor):
    """`coluna == valor` OU `coluna IS NULL`, que quer dizer "vale para todos".

    Não dá para escrever `coluna.in_([valor, None])`: em SQL o `NULL` não
    participa de `IN` — `empresa_id IN (1, NULL)` nunca casa com uma linha de
    `empresa_id IS NULL`.  As regras de escopo global, que são a maioria,
    ficariam invisíveis, e a classificação simplesmente não aconteceria.
    """
    if valor is None:
        return coluna.is_(None)
    return or_(coluna == valor, coluna.is_(None))


def regras_aplicaveis(
    session: Session,
    documento: DocumentoFiscal,
    *,
    obrigacao: str | None = None,
) -> list[RegraFiscal]:
    """As regras que valem para este documento, da maior prioridade à menor."""
    consulta = select(RegraFiscal).where(RegraFiscal.ativa.is_(True))
    consulta = consulta.where(_dele_ou_de_todos(RegraFiscal.escritorio_id, documento.escritorio_id))
    consulta = consulta.where(_dele_ou_de_todos(RegraFiscal.empresa_id, documento.empresa_id))
    if obrigacao is not None:
        consulta = consulta.where(_dele_ou_de_todos(RegraFiscal.obrigacao, obrigacao))

    regras = session.execute(consulta).scalars().all()
    vigentes = [r for r in regras if _vigente(r, documento.data_emissao)]
    return sorted(vigentes, key=lambda r: (-r.prioridade, r.id or 0))


class MotorDeClassificacao:
    """Aplica as regras e devolve propostas — nunca grava sozinho."""

    def __init__(self, session: Session, *, obrigacao: str | None = None):
        self.session = session
        self.obrigacao = obrigacao

    def avaliar(self, documento: DocumentoFiscal) -> ResultadoClassificacao:
        from src.db.models import AjusteFiscal

        regras = regras_aplicaveis(self.session, documento, obrigacao=self.obrigacao)
        ajustes = (
            self.session.execute(
                select(AjusteFiscal).where(AjusteFiscal.documento_id == documento.id)
            )
            .scalars()
            .all()
        )
        resultado = ResultadoClassificacao()
        alvos: list[ItemDocumentoFiscal | None] = [None, *documento.itens]
        for item in alvos:
            self._avaliar_alvo(documento, item, regras, ajustes, resultado)
        return resultado

    def _avaliar_alvo(
        self,
        documento: DocumentoFiscal,
        item: ItemDocumentoFiscal | None,
        regras: Sequence[RegraFiscal],
        ajustes: Sequence,
        resultado: ResultadoClassificacao,
    ) -> None:
        # campo -> (prioridade, [regras que o disputam], sugestão vencedora)
        vencedoras: dict[str, tuple[int, list[str], Sugestao]] = {}

        for regra in regras:
            if not _casa(regra, documento, item, ajustes):
                continue
            for acao in _carregar(regra.acoes, "acoes"):
                campo = acao["campo"]
                alvo = item if item is not None else documento
                colunas = type(alvo).__table__.columns
                if campo not in colunas:
                    # Ação de item sobre o cabeçalho (ou vice-versa) não é
                    # erro: a mesma regra vale para os dois, e só um dos dois
                    # tem o campo.
                    continue
                anterior = _valor_do_campo(campo, documento, item, ajustes)
                sugerido = acao.get("valor")
                if _texto(anterior) == _texto(sugerido):
                    continue

                if campo in vencedoras:
                    prioridade, nomes, _ = vencedoras[campo]
                    if regra.prioridade == prioridade:
                        nomes.append(regra.nome)
                    continue

                vencedoras[campo] = (
                    regra.prioridade,
                    [regra.nome],
                    Sugestao(
                        documento_id=documento.id,
                        item_id=item.id if item is not None else None,
                        numero_item=item.numero_item if item is not None else None,
                        campo=campo,
                        valor_anterior=anterior,
                        valor_sugerido=sugerido,
                        regra_id=regra.id,
                        regra_nome=regra.nome,
                        justificativa=regra.descricao or f"regra {regra.nome}",
                        confianca=regra.confianca,
                    ),
                )

        for campo, (prioridade, nomes, sugestao) in vencedoras.items():
            if len(nomes) > 1:
                resultado.conflitos.append(
                    Conflito(
                        campo=campo,
                        numero_item=item.numero_item if item is not None else None,
                        regras=sorted(nomes),
                        prioridade=prioridade,
                    )
                )
                continue
            resultado.sugestoes.append(sugestao)


def aplicar(
    session: Session,
    documento: DocumentoFiscal,
    sugestoes: Iterable[Sugestao],
    *,
    lote: str | None = None,
    usuario_id: int | None = None,
) -> str:
    """Transforma sugestões aceitas em ajustes, e devolve o lote.

    O lote é o que permite desfazer a classificação inteira depois — se as
    regras estavam erradas, `desfazer_lote` devolve o mês ao que era.
    """
    lote = lote or novo_lote()
    por_id = {item.id: item for item in documento.itens}
    for sugestao in sugestoes:
        aplicar_ajuste(
            session,
            documento=documento,
            item=por_id.get(sugestao.item_id) if sugestao.item_id else None,
            campo=sugestao.campo,
            valor_novo=sugestao.valor_sugerido,
            origem=ORIGEM_REGRA,
            regra=sugestao.regra_nome,
            motivo=sugestao.justificativa,
            lote=lote,
            usuario_id=usuario_id,
        )
    return lote


def criar_regra(
    session: Session,
    *,
    nome: str,
    condicoes: list[dict],
    acoes: list[dict],
    escritorio_id: int | None = None,
    empresa_id: int | None = None,
    descricao: str | None = None,
    prioridade: int = 0,
    obrigacao: str | None = None,
    vigencia_inicio: datetime.date | None = None,
    vigencia_fim: datetime.date | None = None,
    confianca: float = 1.0,
    usuario_id: int | None = None,
) -> RegraFiscal:
    """Cadastra a regra já validada."""
    regra = RegraFiscal(
        nome=nome,
        descricao=descricao,
        escritorio_id=escritorio_id,
        empresa_id=empresa_id,
        prioridade=prioridade,
        condicoes=json.dumps(condicoes, ensure_ascii=False),
        acoes=json.dumps(acoes, ensure_ascii=False),
        obrigacao=obrigacao,
        vigencia_inicio=vigencia_inicio,
        vigencia_fim=vigencia_fim,
        confianca=confianca,
        usuario_id=usuario_id,
    )
    validar_regra(regra)
    session.add(regra)
    session.flush()
    return regra
