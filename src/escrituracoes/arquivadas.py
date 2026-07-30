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
  * `comparar` — confronta o arquivado com uma geração nova e diz o que mudou;
  * `escrituracoes_do_documento` — em que arquivos uma nota entrou.
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
