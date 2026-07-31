"""O espelho: ler o arquivo antes de transmitir, em vez de depois.

Um arquivo SPED é ilegível de propósito — `|C100|0|1|12345678000195|55|00|...`.
Quem fecha o mês precisa responder três perguntas antes de entregar: quais
documentos entraram, quanto deu a apuração, e se o arquivo é coerente consigo
mesmo. Hoje as três só se respondem depois, comparando o que foi entregue com
o que sairia agora (`arquivadas.comparar`). Isso é tarde: o erro já saiu.

**O espelho é lido dos registros, não do banco.** É a decisão que dá sentido ao
módulo. Um espelho montado a partir dos documentos responderia "o que eu
acredito que vai sair" — e concordaria com o banco mesmo quando o gerador
discorda dele, escondendo exatamente o erro que se quer ver. Lendo os
registros, o espelho responde "o que vai sair", que é a pergunta.

Pelo mesmo motivo as conferências recalculam a partir do arquivo. Perguntar ao
gerador se ele somou certo é aceitar a resposta dele; somar de novo, a partir
das linhas que ele escreveu, é conferir.

O que se confere aqui é o que o validador do Fisco confere e que produz recusa
do arquivo inteiro ou imposto errado:

  * a soma dos itens contra o total do documento (`C170` × `C100`);
  * o consolidado contra os itens (`C190` × `C170`);
  * a apuração contra os documentos (`E110`, `M200`, `M600`);
  * as contagens do bloco 9, refeitas linha a linha.

O espelho **não** transmite nem grava escrituração: é prosa, não arquivo SPED.
Por isso ele pode ser produzido sem arquivar, ao contrário de `fiscal gerar` —
não há como confundir um espelho com uma entrega.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from src.escrituracoes.arquivadas import TIPOS
from src.escrituracoes.base import Registro, ResultadoGeracao
from src.escrituracoes.leiaute import POR_OBRIGACAO

# O mesmo formato dos relatórios e da CLI — 1.234.567,89.  Escrever outra
# formatação aqui faria o mesmo número aparecer de dois jeitos no sistema.
from src.reports.base import fmt_moeda as _moeda

# Cada valor do arquivo já vem arredondado ao centavo.  A soma de N itens
# arredondados pode afastar-se do total arredondado em até meio centavo por
# item — daí a tolerância crescer com a quantidade em vez de ser fixa.  Uma
# tolerância fixa acusaria documento correto de 40 itens; uma frouxa demais
# engoliria erro de verdade num documento de dois.
MEIO_CENTAVO = 0.005

# Regimes do 0110 em que a empresa desconta crédito.  Repetido de propósito
# em `efd_contribuicoes`?  Não: aqui a origem é o **arquivo**, não o cadastro.
# O espelho lê o regime que o arquivo declara, que é o que o Fisco vai ler.
_COM_CREDITO = {"1", "3"}


class TipoSemLeiaute(ValueError):
    """Espelho pedido para uma obrigação cujos campos não estão descritos."""


def _valor(bruto: str) -> float:
    """`"1000,00"` → `1000.0`; campo vazio → `0.0`.

    Vazio é zero porque o leiaute trata ausente e zero como a mesma coisa —
    é o inverso de `formatar_valor`.
    """
    if not bruto:
        return 0.0
    try:
        return float(bruto.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _data(bruto: str) -> str:
    """`ddmmaaaa` → `dd/mm/aaaa`, para quem lê."""
    if len(bruto) != 8 or not bruto.isdigit():
        return bruto
    return f"{bruto[:2]}/{bruto[2:4]}/{bruto[4:]}"


@dataclass(frozen=True)
class Conferencia:
    """Uma pergunta que o validador faria, respondida antes dele."""

    nome: str
    ok: bool
    detalhe: str = ""


@dataclass(frozen=True)
class LinhaDocumento:
    """Um documento do arquivo, como alguém o reconheceria."""

    sentido: str
    modelo: str
    serie: str
    numero: str
    data: str
    participante: str
    valor_documento: float
    valor_mercadorias: float
    itens: int
    soma_dos_itens: float


@dataclass
class Espelho:
    """O arquivo que vai sair, em forma de leitura."""

    tipo: str
    identificacao: dict[str, str] = field(default_factory=dict)
    documentos: list[LinhaDocumento] = field(default_factory=list)
    apuracao: list[tuple[str, float]] = field(default_factory=list)
    conferencias: list[Conferencia] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def divergencias(self) -> list[Conferencia]:
        return [c for c in self.conferencias if not c.ok]

    @property
    def total_entradas(self) -> float:
        return sum(d.valor_documento for d in self.documentos if d.sentido == "entrada")

    @property
    def total_saidas(self) -> float:
        return sum(d.valor_documento for d in self.documentos if d.sentido == "saída")

    def texto(self) -> str:
        """O espelho como se lê na tela ou em papel."""
        linhas = [f"ESPELHO — {self.identificacao.get('obrigacao', self.tipo)}"]
        for rotulo in ("empresa", "cnpj", "período", "leiaute"):
            if valor := self.identificacao.get(rotulo):
                linhas.append(f"  {rotulo:10} {valor}")

        linhas.append("")
        linhas.append(f"DOCUMENTOS ({len(self.documentos)})")
        for doc in self.documentos:
            linhas.append(
                f"  {doc.sentido:7} mod {doc.modelo} sér {doc.serie or '-'} "
                f"nº {doc.numero:<10} {doc.data}  {doc.participante:<18} "
                f"{_moeda(doc.valor_documento):>14}  {doc.itens} item(ns)"
            )
        if self.documentos:
            linhas.append("")
            linhas.append(f"  {'total das entradas':<34} {_moeda(self.total_entradas):>14}")
            linhas.append(f"  {'total das saídas':<34} {_moeda(self.total_saidas):>14}")

        if self.apuracao:
            linhas.append("")
            linhas.append("APURAÇÃO")
            for rotulo, valor in self.apuracao:
                linhas.append(f"  {rotulo:<34} {_moeda(valor):>14}")

        linhas.append("")
        linhas.append("CONFERÊNCIAS")
        for c in self.conferencias:
            marca = "ok  " if c.ok else "NÃO "
            linhas.append(f"  {marca} {c.nome}")
            if c.detalhe:
                linhas.append(f"         {c.detalhe}")

        if self.avisos:
            linhas.append("")
            linhas.append("LEIA ANTES DE TRANSMITIR")
            for aviso in self.avisos:
                linhas.append(f"  · {aviso}")

        return "\n".join(linhas) + "\n"


def espelho(resultado: ResultadoGeracao, *, tipo: str) -> Espelho:
    """Monta o espelho a partir dos registros que serão escritos."""
    leiaute = POR_OBRIGACAO.get(tipo)
    if leiaute is None:
        raise TipoSemLeiaute(
            f"não há leiaute descrito para {tipo!r} — o espelho não tem como "
            f"nomear os campos (conhecidos: {sorted(POR_OBRIGACAO)})"
        )

    ler = _Leitor(resultado.registros, leiaute)
    return Espelho(
        tipo=tipo,
        identificacao=ler.identificacao(tipo),
        documentos=ler.documentos(),
        apuracao=ler.apuracao(tipo),
        conferencias=ler.conferencias(tipo),
        avisos=list(resultado.avisos),
    )


class _Leitor:
    """Lê um arquivo SPED já montado, resolvendo campo por nome."""

    def __init__(self, registros: list[Registro], leiaute: dict[str, tuple[str, ...]]):
        self.registros = registros
        self.leiaute = leiaute

    # ── Acesso aos campos ──────────────────────────────────────────────────

    def campo(self, registro: Registro, nome: str) -> str:
        """O campo pelo nome. Registro fora do leiaute devolve vazio.

        Devolver vazio em vez de levantar é deliberado: o espelho é a última
        parada antes de transmitir, e não pode ser ele a impedir que se veja o
        arquivo. Registro desconhecido já é impossível na geração — `_add`
        recusa — e aqui só apareceria lendo arquivo de outra origem.
        """
        campos = self.leiaute.get(registro.tipo)
        if not campos or nome not in campos:
            return ""
        indice = campos.index(nome)
        return registro.campos[indice] if indice < len(registro.campos) else ""

    def primeiro(self, tipo: str) -> Registro | None:
        return next((r for r in self.registros if r.tipo == tipo), None)

    def todos(self, tipo: str) -> list[Registro]:
        return [r for r in self.registros if r.tipo == tipo]

    # ── Identificação ──────────────────────────────────────────────────────

    def identificacao(self, tipo: str) -> dict[str, str]:
        abertura = self.primeiro("0000")
        if abertura is None:
            return {"obrigacao": TIPOS.get(tipo, tipo)}
        inicio = _data(self.campo(abertura, "DT_INI"))
        fim = _data(self.campo(abertura, "DT_FIN"))
        return {
            "obrigacao": TIPOS.get(tipo, tipo),
            "empresa": self.campo(abertura, "NOME"),
            "cnpj": self.campo(abertura, "CNPJ"),
            "período": f"{inicio} a {fim}",
            "leiaute": self.campo(abertura, "COD_VER"),
        }

    # ── Documentos ─────────────────────────────────────────────────────────

    def _por_documento(self) -> list[tuple[Registro, list[Registro], list[Registro]]]:
        """Agrupa cada C100 com os C170 e C190 que vêm depois dele.

        A hierarquia do SPED é posicional: o item pertence ao último documento
        aberto. Não há chave ligando um ao outro, e é assim que o Fisco lê.
        """
        grupos: list[tuple[Registro, list[Registro], list[Registro]]] = []
        for registro in self.registros:
            if registro.tipo == "C100":
                grupos.append((registro, [], []))
            elif grupos and registro.tipo == "C170":
                grupos[-1][1].append(registro)
            elif grupos and registro.tipo == "C190":
                grupos[-1][2].append(registro)
        return grupos

    def documentos(self) -> list[LinhaDocumento]:
        linhas = []
        for c100, itens, _ in self._por_documento():
            linhas.append(
                LinhaDocumento(
                    # IND_OPER: 0 = entrada, 1 = saída.  Do ponto de vista de
                    # quem escritura, não de quem emitiu.
                    sentido="entrada" if self.campo(c100, "IND_OPER") == "0" else "saída",
                    modelo=self.campo(c100, "COD_MOD"),
                    serie=self.campo(c100, "SER"),
                    numero=self.campo(c100, "NUM_DOC"),
                    data=_data(self.campo(c100, "DT_DOC")),
                    participante=self.campo(c100, "COD_PART"),
                    valor_documento=_valor(self.campo(c100, "VL_DOC")),
                    valor_mercadorias=_valor(self.campo(c100, "VL_MERC")),
                    itens=len(itens),
                    soma_dos_itens=sum(_valor(self.campo(i, "VL_ITEM")) for i in itens),
                )
            )
        return linhas

    # ── Apuração ───────────────────────────────────────────────────────────

    def apuracao(self, tipo: str) -> list[tuple[str, float]]:
        if tipo == "efd_icms":
            return self._apuracao_icms()
        return self._apuracao_contribuicoes()

    def _apuracao_icms(self) -> list[tuple[str, float]]:
        e110 = self.primeiro("E110")
        if e110 is None:
            return []
        linhas = [
            ("débitos das saídas", _valor(self.campo(e110, "VL_TOT_DEBITOS"))),
            ("créditos das entradas", _valor(self.campo(e110, "VL_TOT_CREDITOS"))),
        ]
        # Só aparece quando existe: uma linha de 0,00 todo mês faria a que tem
        # valor passar despercebida, e é ela que explica por que o imposto a
        # recolher é menor que débito menos crédito.
        if anterior := _valor(self.campo(e110, "VL_SLD_CREDOR_ANT")):
            linhas.append(("saldo credor do período anterior", anterior))
        linhas.append(("ICMS a recolher", _valor(self.campo(e110, "VL_ICMS_RECOLHER"))))
        linhas.append(
            ("saldo credor a transportar", _valor(self.campo(e110, "VL_SLD_CREDOR_TRANSPORTAR")))
        )
        return linhas

    def _cumulativo(self) -> bool:
        """O regime que o ARQUIVO declara, não o que o cadastro diz.

        São a mesma coisa quando tudo está certo — e quando não estão, o que
        vale para o Fisco é o que está no arquivo.
        """
        r0110 = self.primeiro("0110")
        if r0110 is None:
            return False
        return self.campo(r0110, "COD_INC_TRIB") not in _COM_CREDITO

    def _apuracao_contribuicoes(self) -> list[tuple[str, float]]:
        cumulativo = self._cumulativo()
        campo_devido = "VL_CONT_CUM_REC" if cumulativo else "VL_CONT_NC_REC"
        linhas = []
        for registro, nome in (("M200", "PIS"), ("M600", "Cofins")):
            m = self.primeiro(registro)
            if m is None:
                continue
            linhas.append((f"{nome} devido", _valor(self.campo(m, campo_devido))))
            if not cumulativo:
                linhas.append(
                    (f"{nome} — créditos descontados", _valor(self.campo(m, "VL_TOT_CRED_DESC")))
                )
        return linhas

    # ── Conferências ───────────────────────────────────────────────────────

    def conferencias(self, tipo: str) -> list[Conferencia]:
        feitas = [self._itens_contra_o_documento(), self._bloco_9()]
        if tipo == "efd_icms":
            feitas.append(self._consolidado_contra_os_itens())
            feitas.append(self._apuracao_contra_os_documentos_icms())
        else:
            feitas.extend(self._apuracao_contra_os_documentos_contribuicoes())
        return feitas

    def _itens_contra_o_documento(self) -> Conferencia:
        """`VL_MERC` do C100 × soma dos `VL_ITEM` dos C170.

        É o que o validador confere documento a documento, e o que uma
        alteração em massa nos itens desfazia até a correção do §12.5.
        """
        divergentes = []
        for c100, itens, _ in self._por_documento():
            if not itens:
                continue
            declarado = _valor(self.campo(c100, "VL_MERC"))
            somado = sum(_valor(self.campo(i, "VL_ITEM")) for i in itens)
            if abs(declarado - somado) > MEIO_CENTAVO * len(itens):
                numero = self.campo(c100, "NUM_DOC") or "sem número"
                divergentes.append(f"nº {numero}: {_moeda(declarado)} × {_moeda(somado)}")

        return Conferencia(
            nome="a soma dos itens bate com o total de cada documento",
            ok=not divergentes,
            detalhe="; ".join(divergentes),
        )

    def _consolidado_contra_os_itens(self) -> Conferencia:
        """`C190` × `C170`, por documento — o segundo erro mais comum."""
        divergentes = []
        for c100, itens, analiticos in self._por_documento():
            if not itens:
                continue
            dos_itens = sum(_valor(self.campo(i, "VL_ICMS")) for i in itens)
            do_consolidado = sum(_valor(self.campo(a, "VL_ICMS")) for a in analiticos)
            if abs(dos_itens - do_consolidado) > MEIO_CENTAVO * len(itens):
                numero = self.campo(c100, "NUM_DOC") or "sem número"
                divergentes.append(
                    f"nº {numero}: C190 {_moeda(do_consolidado)} × C170 {_moeda(dos_itens)}"
                )

        return Conferencia(
            nome="o consolidado C190 bate com a soma dos itens",
            ok=not divergentes,
            detalhe="; ".join(divergentes),
        )

    def _somar_por_sentido(self, campo_do_item: str) -> tuple[float, float]:
        """Soma um campo dos itens separando saídas de entradas."""
        debito = credito = 0.0
        for c100, itens, _ in self._por_documento():
            valor = sum(_valor(self.campo(i, campo_do_item)) for i in itens)
            if self.campo(c100, "IND_OPER") == "1":
                debito += valor
            else:
                credito += valor
        return debito, credito

    def _apuracao_contra_os_documentos_icms(self) -> Conferencia:
        e110 = self.primeiro("E110")
        if e110 is None:
            return Conferencia(nome="a apuração bate com os documentos", ok=True)

        debito, credito = self._somar_por_sentido("VL_ICMS")
        problemas = []
        declarado_debito = _valor(self.campo(e110, "VL_TOT_DEBITOS"))
        declarado_credito = _valor(self.campo(e110, "VL_TOT_CREDITOS"))
        if abs(declarado_debito - debito) > MEIO_CENTAVO:
            problemas.append(f"débitos: {_moeda(declarado_debito)} × {_moeda(debito)}")
        if abs(declarado_credito - credito) > MEIO_CENTAVO:
            problemas.append(f"créditos: {_moeda(declarado_credito)} × {_moeda(credito)}")

        return Conferencia(
            nome="o E110 bate com o ICMS dos documentos",
            ok=not problemas,
            detalhe="; ".join(problemas),
        )

    def _apuracao_contra_os_documentos_contribuicoes(self) -> list[Conferencia]:
        """M200 e M600 × os itens — respeitando o regime declarado no 0110.

        No cumulativo o débito sai num campo diferente e os créditos **não**
        entram. Conferir sempre pelo mesmo campo acusaria de errada toda
        empresa do lucro presumido.
        """
        cumulativo = self._cumulativo()
        campo_debito = "VL_TOT_CONT_CUM_PER" if cumulativo else "VL_TOT_CONT_NC_PER"

        feitas = []
        for registro, nome, campo_do_item in (
            ("M200", "PIS", "VL_PIS"),
            ("M600", "Cofins", "VL_COFINS"),
        ):
            m = self.primeiro(registro)
            if m is None:
                continue
            debito, credito = self._somar_por_sentido(campo_do_item)
            problemas = []
            declarado = _valor(self.campo(m, campo_debito))
            if abs(declarado - debito) > MEIO_CENTAVO:
                problemas.append(f"contribuição: {_moeda(declarado)} × {_moeda(debito)}")

            declarado_credito = _valor(self.campo(m, "VL_TOT_CRED_DESC"))
            esperado_credito = 0.0 if cumulativo else credito
            if abs(declarado_credito - esperado_credito) > MEIO_CENTAVO:
                problemas.append(
                    f"créditos: {_moeda(declarado_credito)} × {_moeda(esperado_credito)}"
                )

            feitas.append(
                Conferencia(
                    nome=f"o {registro} bate com o {nome} dos documentos",
                    ok=not problemas,
                    detalhe="; ".join(problemas),
                )
            )
        return feitas

    def _bloco_9(self) -> Conferencia:
        """As contagens do bloco 9, refeitas linha a linha.

        Errar aqui faz o validador recusar o arquivo inteiro sem apontar a
        linha. A contagem é refeita a partir dos registros, não lida do
        gerador: perguntar ao gerador se ele contou certo é aceitar a resposta
        dele.
        """
        real: Counter = Counter(r.tipo for r in self.registros)
        declarado: dict[str, int] = defaultdict(int)
        for r9900 in self.todos("9900"):
            tipo = self.campo(r9900, "REG_BLC")
            try:
                declarado[tipo] += int(self.campo(r9900, "QTD_REG_BLC") or 0)
            except ValueError:
                declarado[tipo] += 0

        problemas = [
            f"{tipo}: 9900 diz {declarado.get(tipo, 0)}, o arquivo tem {real[tipo]}"
            for tipo in sorted(set(real) | set(declarado))
            if declarado.get(tipo, 0) != real.get(tipo, 0)
        ]

        r9999 = self.primeiro("9999")
        if r9999 is not None:
            total = _inteiro(self.campo(r9999, "QTD_LIN"))
            if total != len(self.registros):
                problemas.append(f"9999 diz {total} linhas, o arquivo tem {len(self.registros)}")

        return Conferencia(
            nome="as contagens do bloco 9 batem com o arquivo",
            ok=not problemas,
            detalhe="; ".join(problemas),
        )


def _inteiro(bruto: str) -> int:
    try:
        return int(bruto)
    except ValueError:
        return -1
