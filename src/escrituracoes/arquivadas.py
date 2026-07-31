"""A terceira camada: guardar o arquivo que efetivamente saiu.

O pedido separa três camadas — documento original, tratamento fiscal do
sistema e **registro efetivamente enviado ao SPED**. As duas primeiras já
existiam: o XML byte a byte em `DocumentoFiscal.xml_original`, e o tratamento
em `AjusteFiscal`, do qual sai a camada efetiva. A terceira faltava.

**Guardar não é o mesmo que poder regerar.** Um sistema que reconstrói o
arquivo sob demanda responde "o que eu enviaria hoje". A pergunta da
intimação é outra: "o que você enviou". Basta um ajuste depois da entrega
para as duas respostas divergirem — e é exatamente aí que a diferença
importa. Por isso o conteúdo é gravado, com o hash do texto como saiu.

O que este módulo oferece:

  * `arquivar` — grava o resultado de uma geração, com os documentos que
    entraram nele;
  * `marcar_transmitida` — diz qual das gerações foi de fato entregue;
  * `comparar` — confronta o arquivado com uma geração nova e diz o que mudou;
  * `escrituracoes_do_documento` — em que arquivos uma nota entrou.

**Guardar todas as gerações não responde qual foi entregue.** Um mês costuma
ter várias: a primeira, a de depois da correção, a que se gerou só para
conferir. O sistema não transmite — quem transmite é o programa validador da
Receita — então a informação vem de fora e precisa ser dita. Enquanto ninguém
disser, nenhuma é marcada: deduzir pela mais recente responderia que foi
entregue justamente a que se acabou de gerar para olhar.
"""

from __future__ import annotations

import datetime
import difflib
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escrituracao,
    EscrituracaoDocumento,
)
from src.escrituracoes.base import ResultadoGeracao
from src.escrituracoes.leiaute import POR_OBRIGACAO

# As obrigações que este pacote gera.  Arquivar sob um tipo desconhecido
# tornaria a escrituração inencontrável na hora em que ela é procurada.
TIPOS = {
    "efd_icms": "EFD ICMS/IPI",
    "efd_contribuicoes": "EFD-Contribuições",
}


class TipoDesconhecido(ValueError):
    """Tipo de escrituração fora de `TIPOS`."""


def hash_do_conteudo(texto: str) -> str:
    """SHA-256 do arquivo exatamente como sai, com CRLF.

    É o que permite conferir contra o arquivo que o contribuinte tem em mãos.
    Normalizar a quebra de linha antes de somar daria o mesmo hash para dois
    arquivos que o validador do Fisco trata de forma diferente.
    """
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def arquivar(
    session: Session,
    *,
    resultado: ResultadoGeracao,
    empresa: Empresa,
    tipo: str,
    data_inicio: datetime.date,
    data_fim: datetime.date,
    usuario_id: int | None = None,
) -> Escrituracao:
    """Grava o arquivo gerado, com os documentos que entraram nele.

    Não substitui uma escrituração anterior do mesmo período: cria outra. Duas
    gerações do mesmo mês são dois fatos distintos, e qual delas foi
    transmitida é informação que o sistema ainda não tem — sobrescrever seria
    inventá-la, e apagaria a única cópia do que saiu antes.
    """
    if tipo not in TIPOS:
        raise TipoDesconhecido(
            f"tipo {tipo!r} não é uma escrituração conhecida (um de {sorted(TIPOS)})"
        )

    texto = resultado.texto()
    escrituracao = Escrituracao(
        escritorio_id=empresa.escritorio_id,
        empresa_id=empresa.id,
        tipo=tipo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        conteudo=texto,
        hash_conteudo=hash_do_conteudo(texto),
        total_linhas=resultado.total_linhas,
        avisos=json.dumps(resultado.avisos, ensure_ascii=False),
        usuario_id=usuario_id,
    )
    session.add(escrituracao)
    session.flush()

    # `dict.fromkeys` em vez de `set`: preserva a ordem de escrituração, e a
    # ordem é o que torna dois arquivamentos do mesmo período comparáveis.
    for documento_id in dict.fromkeys(resultado.documentos_ids):
        session.add(
            EscrituracaoDocumento(escrituracao_id=escrituracao.id, documento_id=documento_id)
        )
    session.flush()
    return escrituracao


class TransmissaoInvalida(ValueError):
    """Marcar como transmitida algo que não pode ser marcado assim."""


def campo_do_registro(escrituracao: Escrituracao, tipo: str, nome: str) -> str:
    """Um campo, pelo nome, do arquivo que foi guardado.

    Ler o arquivo — e não recalcular a partir dos documentos — é o ponto: o
    que vale é o que foi entregue. Um período reaberto e ajustado produziria
    outro número, e o Fisco continua com o primeiro.

    Devolve vazio quando o registro não existe no arquivo ou o campo não está
    no leiaute daquela obrigação.
    """
    campos_do_tipo = POR_OBRIGACAO.get(escrituracao.tipo, {}).get(tipo)
    if not campos_do_tipo or nome not in campos_do_tipo:
        return ""

    posicao = campos_do_tipo.index(nome) + 2  # +1 pela barra inicial, +1 pelo tipo
    for linha in escrituracao.conteudo.replace("\r\n", "\n").split("\n"):
        partes = linha.split("|")
        if len(partes) > 1 and partes[1] == tipo:
            return partes[posicao] if posicao < len(partes) else ""
    return ""


def _finalidade(escrituracao: Escrituracao) -> str:
    """`0` = original, `1` = retificadora — lido do arquivo que saiu.

    O campo tem nome diferente nas duas escriturações — `COD_FIN` na EFD
    ICMS/IPI, `TIPO_ESCRIT` na EFD-Contribuições — e a mesma posição e os
    mesmos valores. Lido do conteúdo, e não do parâmetro de geração, porque o
    que o Fisco recebeu foi o arquivo.
    """
    return campo_do_registro(escrituracao, "0000", "COD_FIN") or campo_do_registro(
        escrituracao, "0000", "TIPO_ESCRIT"
    )


def ultima_transmitida_antes(
    session: Session,
    *,
    empresa_id: int,
    tipo: str,
    data: datetime.date,
) -> Escrituracao | None:
    """A última escrituração **transmitida** que termina antes de `data`.

    Só as transmitidas contam. Uma geração que ninguém entregou não estabelece
    nada perante o Fisco — e é justamente a que sobra em maior número, porque
    gerar para conferir é barato.
    """
    consulta = (
        select(Escrituracao)
        .where(
            Escrituracao.empresa_id == empresa_id,
            Escrituracao.tipo == tipo,
            Escrituracao.data_fim < data,
            Escrituracao.transmitida_em.is_not(None),
        )
        .order_by(Escrituracao.data_fim.desc(), Escrituracao.transmitida_em.desc())
    )
    return session.execute(consulta).scalars().first()


def existe_geracao_antes(
    session: Session,
    *,
    empresa_id: int,
    tipo: str,
    data: datetime.date,
) -> bool:
    """Se há qualquer geração anterior, transmitida ou não.

    Serve para distinguir dois silêncios que pedem avisos diferentes: "esta é
    a primeira escrituração desta empresa" e "existe a do mês passado, mas
    ninguém disse que foi entregue".
    """
    consulta = select(Escrituracao.id).where(
        Escrituracao.empresa_id == empresa_id,
        Escrituracao.tipo == tipo,
        Escrituracao.data_fim < data,
    )
    return session.execute(consulta).first() is not None


def transmitidas_do_periodo(session: Session, escrituracao: Escrituracao) -> list[Escrituracao]:
    """As já transmitidas do mesmo período, empresa e obrigação.

    Da mais antiga para a mais nova, sem contar a própria — é o histórico de
    entregas daquele mês, que é o que decide se uma nova entrega é retificação
    ou engano.
    """
    consulta = (
        select(Escrituracao)
        .where(
            Escrituracao.empresa_id == escrituracao.empresa_id,
            Escrituracao.tipo == escrituracao.tipo,
            Escrituracao.data_inicio == escrituracao.data_inicio,
            Escrituracao.data_fim == escrituracao.data_fim,
            Escrituracao.transmitida_em.is_not(None),
            Escrituracao.id != escrituracao.id,
        )
        .order_by(Escrituracao.transmitida_em, Escrituracao.id)
    )
    return list(session.execute(consulta).scalars().all())


def marcar_transmitida(
    session: Session,
    escrituracao: Escrituracao,
    *,
    recibo: str | None = None,
    quando: datetime.datetime | None = None,
    usuario_id: int | None = None,
    forcar: bool = False,
) -> Escrituracao:
    """Registra que **esta** geração foi a entregue.

    Três regras, e cada uma existe por um motivo:

    **Marcar não se desfaz.** Transmitir é fato do mundo, não estado do
    sistema; apagar a marca apagaria o registro de que aconteceu. Errou o
    arquivo? Transmite-se uma retificadora — que é outra escrituração, com o
    `0000` declarando finalidade `1` — e ela é marcada por sua vez. As duas
    ficam, na ordem em que saíram.

    **Uma segunda entrega original no mesmo período é recusada.** Se o período
    já tem transmissão e o arquivo novo se declara original, ou o arquivo devia
    ter sido gerado como retificadora, ou a marca anterior está errada. Nos
    dois casos alguém precisa olhar. `forcar=True` passa por cima — existe
    porque o caso legítimo existe: transmissão rejeitada pelo Fisco e
    reenviada como original.

    **O conteúdo não é tocado.** Marcar não altera texto nem hash: a linha
    continua valendo como prova.
    """
    if escrituracao.transmitida:
        raise TransmissaoInvalida(
            f"a escrituração #{escrituracao.id} já está marcada como transmitida em "
            f"{escrituracao.transmitida_em:%d/%m/%Y %H:%M} — transmitir é fato do "
            "mundo, não estado do sistema. Se o arquivo entregue estava errado, gere "
            "uma retificadora e marque essa"
        )

    anteriores = transmitidas_do_periodo(session, escrituracao)
    if anteriores and _finalidade(escrituracao) == "0" and not forcar:
        ids = ", ".join(f"#{e.id}" for e in anteriores)
        raise TransmissaoInvalida(
            f"o período {escrituracao.data_inicio} a {escrituracao.data_fim} já tem "
            f"escrituração transmitida ({ids}), e a #{escrituracao.id} se declara "
            "ORIGINAL no 0000 — uma segunda entrega do mesmo período é retificadora "
            "(finalidade 1). Gere de novo com a finalidade certa, ou use forcar=True "
            "se a entrega anterior foi rejeitada pelo Fisco e esta a substitui"
        )

    escrituracao.transmitida_em = quando or datetime.datetime.now(datetime.UTC)
    escrituracao.recibo = recibo
    escrituracao.transmitida_por_id = usuario_id
    session.flush()
    return escrituracao


def avisos_de(escrituracao: Escrituracao) -> list[str]:
    """Os avisos como estavam na hora de gerar."""
    try:
        return json.loads(escrituracao.avisos or "[]")
    except json.JSONDecodeError:
        return []


@dataclass
class Comparacao:
    """O que mudou entre o arquivo que saiu e o que sairia agora."""

    iguais: bool
    resumo: list[str] = field(default_factory=list)
    diff: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Verdadeiro quando há divergência — `if comparar(...)` lê direito."""
        return not self.iguais


def comparar(escrituracao: Escrituracao, resultado: ResultadoGeracao) -> Comparacao:
    """Confronta o arquivo arquivado com uma geração nova.

    O `resumo` é por tipo de registro, que é como se lê um arquivo SPED; o
    `diff` traz as linhas, para quando o resumo não basta. Um ajuste feito
    depois da entrega aparece nos dois.
    """
    texto_novo = resultado.texto()
    if hash_do_conteudo(texto_novo) == escrituracao.hash_conteudo:
        return Comparacao(iguais=True)

    antigas = escrituracao.conteudo.replace("\r\n", "\n").rstrip("\n").split("\n")
    novas = texto_novo.replace("\r\n", "\n").rstrip("\n").split("\n")

    return Comparacao(
        iguais=False,
        resumo=_resumo_por_tipo(antigas, novas),
        diff=list(
            difflib.unified_diff(antigas, novas, fromfile="arquivado", tofile="atual", lineterm="")
        ),
    )


def _tipo_do_registro(linha: str) -> str:
    partes = linha.split("|")
    return partes[1] if len(partes) > 1 else ""


def _por_tipo(linhas: list[str]) -> dict[str, Counter]:
    agrupadas: dict[str, Counter] = {}
    for linha in linhas:
        agrupadas.setdefault(_tipo_do_registro(linha), Counter())[linha] += 1
    return agrupadas


def _resumo_por_tipo(antigas: list[str], novas: list[str]) -> list[str]:
    """Quantos registros de cada tipo entraram, saíram ou mudaram.

    Contar por tipo separa o que interessa do ruído: acrescentar um documento
    desloca as contagens do bloco 9, e um diff cru faria parecer que meia dúzia
    de coisas mudou quando mudou uma.

    **A contagem é de multiconjunto, não de conjunto.**  Linha repetida é o
    normal num arquivo SPED — o mesmo produto, com os mesmos valores, em dois
    documentos gera dois C170 idênticos.  Perguntar "esta linha continua no
    arquivo?" responderia que sim quando uma das duas mudou, e o resumo diria
    que o C170 está intacto justamente quando não está.
    """
    antes, depois = _por_tipo(antigas), _por_tipo(novas)
    vazio: Counter = Counter()

    resumo = []
    for tipo in sorted(set(antes) | set(depois)):
        do_tipo_antes = antes.get(tipo, vazio)
        do_tipo_depois = depois.get(tipo, vazio)
        de, para = sum(do_tipo_antes.values()), sum(do_tipo_depois.values())
        if de != para:
            resumo.append(f"{tipo}: {de} → {para} registros")
            continue
        # Mesma quantidade não quer dizer mesmo conteúdo.
        alteradas = sum((do_tipo_antes - do_tipo_depois).values())
        if alteradas:
            resumo.append(f"{tipo}: {alteradas} de {de} registros com conteúdo diferente")

    # O bloco 9 se mexe sempre que a contagem de qualquer outro registro muda;
    # dizer isso evita que alguém procure causa própria para ele.
    sumiram = Counter(antigas) - Counter(novas)
    surgiram = Counter(novas) - Counter(antigas)
    if any(linha.startswith("|9") for linha in (sumiram | surgiram)):
        resumo.append(
            "as contagens do bloco 9 acompanham as demais mudanças — não são causa própria"
        )
    return resumo


def escrituracoes_do_documento(session: Session, documento: DocumentoFiscal) -> list[Escrituracao]:
    """Em que arquivos esta nota entrou, da mais antiga para a mais nova."""
    consulta = (
        select(Escrituracao)
        .join(EscrituracaoDocumento)
        .where(EscrituracaoDocumento.documento_id == documento.id)
        .order_by(Escrituracao.gerada_em, Escrituracao.id)
    )
    return list(session.execute(consulta).scalars().all())
