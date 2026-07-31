"""Apuração dos tributos da Reforma do Consumo: CBS, IBS e Imposto Seletivo.

Os campos já eram lidos do XML desde a Central de Documentos; nada os
consumia. Os grupos passam a ser exigidos na NF-e em **03/08/2026**
(NT 2025.002), e a partir daí todo documento importado carrega valores que
ninguém somava.

Três distinções decidem o resultado, e errar qualquer uma produz número
plausível e errado:

**O Imposto Seletivo não gera crédito.** É extrafiscal e monofásico — incide
uma vez, sobre bens prejudiciais à saúde e ao meio ambiente, e quem revende
não credita o que veio na entrada. Tratá-lo como os outros dois reduziria o
imposto devido pelo valor do IS das compras, num número que parece uma
apuração normal.

**O IBS tem duas parcelas e elas não se somam.** A estadual e a municipal vão
para entes diferentes, e o município do fato gerador pode nem ser o do
destinatário. Apurar "IBS" como um número só destruiria a informação de que a
partilha depende — que é o cerne do imposto.

**2026 é ano de teste.** CBS a 0,9% e IBS a 0,1% são destacados no documento,
com mecanismo de compensação e dispensa para quem cumpre as obrigações
acessórias. Este módulo **soma o que está destacado**; apresentar esse total
como "a recolher" seria enganoso, e o resultado avisa.

**O que esta apuração NÃO cobre**, e sai como aviso em todo resultado:

  * monofásico (`valor_ibs_mono`, `valor_cbs_mono`) e o que foi retido;
  * diferimento, crédito presumido e devolução de tributo;
  * split payment;
  * regimes específicos e diferenciados;
  * o mecanismo de compensação e dispensa de 2026.

Ver `docs/reforma-tributaria.md` para o cronograma e a procedência de cada
informação — o portal oficial respondeu HTTP 503 nas consultas, e os valores
de alíquota e códigos de classificação são dado de entrada, lidos do XML.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import AjusteFiscal, DocumentoFiscal, Empresa
from src.documentos.ajustes import valor_efetivo

# O ano em que CBS e IBS são destacados em alíquota de teste, com compensação
# e dispensa que este módulo não modela.
ANO_DE_TESTE = 2026

# Os campos que o documento traz e que esta apuração **não** consome.  Estão
# aqui para serem MEDIDOS, não para serem somados: o aviso genérico "não cobre
# monofásico, diferimento…" é idêntico para quem tem cinquenta mil reais de
# diferimento e para quem tem zero, e um aviso que aparece sempre treina a
# pessoa a ignorar todos os outros.
#
# Medir é livre de palpite: são valores destacados no próprio documento, não
# códigos cuja semântica seria preciso interpretar.
NAO_CONSUMIDOS = {
    "valor_diferido": "diferimento",
    "valor_credito_presumido": "crédito presumido",
    "valor_credito_presumido_susp": "crédito presumido suspenso",
    "valor_devolucao_tributo": "devolução de tributo",
    "valor_ibs_mono": "IBS monofásico",
    "valor_cbs_mono": "CBS monofásica",
    "valor_ibs_mono_retido": "IBS monofásico retido",
    "valor_cbs_mono_retido": "CBS monofásica retida",
}

# O CST que dispensa tratamento específico.  É o único código da tabela do
# IBS/CBS cuja leitura é consensual entre as fontes consultadas; os demais são
# LISTADOS, nunca interpretados — a IT 002/2025 ainda está em revisão e as
# fontes secundárias divergem entre si.  Ver `docs/reforma-tributaria.md`.
CST_TRIBUTACAO_INTEGRAL = "000"


@dataclass
class Tributo:
    """Débito das saídas contra crédito das entradas."""

    nome: str
    debito: float = 0.0
    credito: float = 0.0

    @property
    def devido(self) -> float:
        """Nunca negativo: o excedente é saldo credor, não imposto a devolver."""
        return max(self.debito - self.credito, 0.0)

    @property
    def saldo_credor(self) -> float:
        """O que sobra quando o crédito supera o débito."""
        return max(self.credito - self.debito, 0.0)


@dataclass
class ResultadoApuracao:
    """O que a apuração encontrou — para ser lido antes de qualquer decisão."""

    data_inicio: datetime.date
    data_fim: datetime.date
    documentos: int = 0
    cbs: Tributo = field(default_factory=lambda: Tributo("CBS"))
    ibs_uf: Tributo = field(default_factory=lambda: Tributo("IBS estadual"))
    ibs_municipal: Tributo = field(default_factory=lambda: Tributo("IBS municipal"))
    # O IS é só débito.  Não é `Tributo` de propósito: dar-lhe um campo
    # `credito` seria convidar alguém a preenchê-lo.
    seletivo: float = 0.0
    # Rótulo → (valor somado, quantidade de itens).  O que o documento trouxe e
    # esta apuração não consumiu.
    nao_cobertos: dict[str, tuple[float, int]] = field(default_factory=dict)
    # CST de IBS/CBS diferentes de `000`, com quantos itens em cada.  São
    # listados, não interpretados.
    cst_encontrados: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    @property
    def ibs_total_devido(self) -> float:
        """As duas parcelas somadas — só para exibição.

        A apuração de cada uma continua separada: elas vão para entes
        diferentes, e é assim que são recolhidas.
        """
        return self.ibs_uf.devido + self.ibs_municipal.devido

    @property
    def total_devido(self) -> float:
        return self.cbs.devido + self.ibs_total_devido + self.seletivo

    def to_dict(self) -> dict:
        return {
            "periodo": [self.data_inicio.isoformat(), self.data_fim.isoformat()],
            "documentos": self.documentos,
            "cbs": {"debito": self.cbs.debito, "credito": self.cbs.credito},
            "ibs_uf": {"debito": self.ibs_uf.debito, "credito": self.ibs_uf.credito},
            "ibs_municipal": {
                "debito": self.ibs_municipal.debito,
                "credito": self.ibs_municipal.credito,
            },
            "seletivo": self.seletivo,
            "nao_cobertos": {
                rotulo: {"valor": valor, "itens": itens}
                for rotulo, (valor, itens) in sorted(self.nao_cobertos.items())
            },
            "cst_encontrados": dict(sorted(self.cst_encontrados.items())),
            "total_devido": round(self.total_devido, 2),
            "avisos": list(self.avisos),
        }


class ApuracaoIBSCBS:
    """Soma CBS, IBS e IS de um período, a partir da camada efetiva."""

    def __init__(
        self,
        session: Session,
        *,
        empresa: Empresa,
        data_inicio: datetime.date,
        data_fim: datetime.date,
    ):
        self.session = session
        self.empresa = empresa
        self.data_inicio = data_inicio
        self.data_fim = data_fim

    def apurar(self) -> ResultadoApuracao:
        documentos = self._documentos()
        resultado = ResultadoApuracao(
            data_inicio=self.data_inicio,
            data_fim=self.data_fim,
            documentos=len(documentos),
        )

        for documento in documentos:
            self._somar(documento, resultado)

        self._avisar(resultado)
        return resultado

    def _documentos(self) -> list[DocumentoFiscal]:
        consulta = (
            select(DocumentoFiscal)
            .options(selectinload(DocumentoFiscal.itens))
            .where(
                DocumentoFiscal.empresa_id == self.empresa.id,
                DocumentoFiscal.data_emissao >= self.data_inicio,
                DocumentoFiscal.data_emissao <= self.data_fim,
            )
            .order_by(DocumentoFiscal.data_emissao, DocumentoFiscal.id)
        )
        return list(self.session.execute(consulta).scalars().unique().all())

    def _somar(self, documento: DocumentoFiscal, resultado: ResultadoApuracao) -> None:
        ajustes = (
            self.session.execute(
                select(AjusteFiscal).where(AjusteFiscal.documento_id == documento.id)
            )
            .scalars()
            .all()
        )
        do_cabecalho = [a for a in ajustes if a.item_id is None]
        saida = valor_efetivo(documento, "sentido", do_cabecalho) == "saida"

        for item in documento.itens:
            do_item = [a for a in ajustes if a.item_id == item.id]

            def efetivo(campo: str, _item=item, _ajustes=do_item) -> float:
                return valor_efetivo(_item, campo, _ajustes) or 0.0

            for tributo, campo in (
                (resultado.cbs, "valor_cbs"),
                (resultado.ibs_uf, "valor_ibs_uf"),
                (resultado.ibs_municipal, "valor_ibs_mun"),
            ):
                if saida:
                    tributo.debito += efetivo(campo)
                else:
                    tributo.credito += efetivo(campo)

            # O Imposto Seletivo só entra pelas saídas.  Ele incide uma vez na
            # cadeia; o IS que veio numa compra é custo, não crédito, e somá-lo
            # como tal reduziria o imposto devido num número que parece certo.
            if saida:
                resultado.seletivo += efetivo("valor_is")

            self._medir_o_que_nao_cobre(item, do_item, resultado)

    def _medir_o_que_nao_cobre(self, item, ajustes: list, resultado: ResultadoApuracao) -> None:
        """Registra o que o documento trouxe e a apuração não consumiu.

        Medir em vez de avisar sempre: o aviso genérico é idêntico para quem
        tem diferimento e para quem não tem, e aviso que aparece sempre treina
        a pessoa a ignorar todos os outros. Com valor e contagem, quem lê sabe
        se aquilo é o mês dele.
        """
        for campo, rotulo in NAO_CONSUMIDOS.items():
            valor = valor_efetivo(item, campo, ajustes) or 0.0
            if not valor:
                continue
            somado, itens = resultado.nao_cobertos.get(rotulo, (0.0, 0))
            resultado.nao_cobertos[rotulo] = (somado + valor, itens + 1)

        cst = (valor_efetivo(item, "cst_ibscbs", ajustes) or "").strip()
        if cst and cst != CST_TRIBUTACAO_INTEGRAL:
            resultado.cst_encontrados[cst] = resultado.cst_encontrados.get(cst, 0) + 1

    def _avisar(self, resultado: ResultadoApuracao) -> None:
        """O que o número não diz — e sem o que ele engana."""
        if not resultado.documentos:
            resultado.avisos.append(
                "nenhum documento no período — a apuração sai zerada por falta de dado, "
                "não por não haver tributo"
            )

        if self.data_inicio.year <= ANO_DE_TESTE <= self.data_fim.year:
            resultado.avisos.append(
                f"{ANO_DE_TESTE} é ano de teste: CBS a 0,9% e IBS a 0,1% são destacados no "
                "documento, com mecanismo de compensação e dispensa para quem cumpre as "
                "obrigações acessórias. O total apurado NÃO é o valor a recolher — "
                "ver docs/reforma-tributaria.md"
            )

        resultado.avisos.append(
            "o Imposto Seletivo não gera crédito: só os débitos das saídas entram. "
            "O IS destacado nas entradas é custo"
        )
        resultado.avisos.append(
            "as parcelas estadual e municipal do IBS são apuradas em separado porque "
            "vão para entes diferentes; o município do fato gerador pode não ser o do "
            "destinatário"
        )
        # O aviso genérico continua, porque split payment e regimes
        # específicos não têm campo próprio no documento para serem medidos.
        resultado.avisos.append(
            "esta apuração é a soma direta do que está destacado nos documentos: não "
            "cobre split payment nem regimes específicos e diferenciados"
        )

        if resultado.nao_cobertos:
            detalhe = "; ".join(
                f"{rotulo}: {valor:.2f} em {itens} item(ns)"
                for rotulo, (valor, itens) in sorted(resultado.nao_cobertos.items())
            )
            resultado.avisos.append(
                f"HÁ valores no período que esta apuração NÃO consumiu — {detalhe}. "
                "Não estão no total; precisam de tratamento próprio antes de recolher"
            )

        if resultado.cst_encontrados:
            detalhe = ", ".join(
                f"{cst} ({itens} item(ns))"
                for cst, itens in sorted(resultado.cst_encontrados.items())
            )
            resultado.avisos.append(
                f"há itens com CST de IBS/CBS diferente de {CST_TRIBUTACAO_INTEGRAL} "
                f"(tributação integral): {detalhe}. O valor destacado foi somado sem "
                "tratamento específico — confira o enquadramento de cada um"
            )
