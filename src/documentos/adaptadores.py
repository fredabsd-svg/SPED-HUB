"""Adaptadores: cada origem vira o mesmo documento normalizado.

Um parser único para todos os documentos fiscais viraria uma cascata de
condicionais que ninguém altera sem quebrar outro município. A NF-e é nacional
e estável; a NFS-e varia por provedor e por prefeitura. Cada origem ganha um
adaptador, e o resto do sistema conhece só :class:`DocumentoNormalizado`.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

NS_NFE = "http://www.portalfiscal.inf.br/nfe"


class OrigemNaoReconhecida(ValueError):
    """Nenhum adaptador registrado sabe ler este conteúdo."""


class XMLPerigoso(ValueError):
    """O XML traz construção que não pertence a documento fiscal legítimo."""


@dataclass
class ItemNormalizado:
    """Um item, com os dois regimes tributários lado a lado.

    Os campos da reforma (IBS, CBS, IS) convivem com os de ICMS, IPI, PIS e
    Cofins porque os regimes coexistem de 2026 a 2032 — ver
    ``docs/reforma-tributaria.md``.
    """

    numero_item: int
    codigo: str | None = None
    descricao: str | None = None
    ncm: str | None = None
    cest: str | None = None
    codigo_servico: str | None = None
    unidade: str | None = None
    quantidade: float = 0.0
    valor_unitario: float = 0.0
    valor_total: float = 0.0
    valor_desconto: float = 0.0
    valor_frete: float = 0.0
    valor_seguro: float = 0.0
    valor_outras: float = 0.0

    cfop: str | None = None
    origem_mercadoria: str | None = None
    cst_icms: str | None = None
    csosn: str | None = None
    base_icms: float = 0.0
    aliquota_icms: float = 0.0
    valor_icms: float = 0.0
    base_icms_st: float = 0.0
    valor_icms_st: float = 0.0
    valor_fcp: float = 0.0
    cst_ipi: str | None = None
    valor_ipi: float = 0.0
    cst_pis: str | None = None
    base_pis: float = 0.0
    aliquota_pis: float = 0.0
    valor_pis: float = 0.0
    cst_cofins: str | None = None
    base_cofins: float = 0.0
    aliquota_cofins: float = 0.0
    valor_cofins: float = 0.0
    valor_iss: float = 0.0
    codigo_beneficio: str | None = None

    # Reforma Tributária
    cst_ibscbs: str | None = None
    class_trib_ibscbs: str | None = None
    base_ibscbs: float = 0.0
    aliquota_ibs_uf: float = 0.0
    valor_ibs_uf: float = 0.0
    aliquota_ibs_mun: float = 0.0
    valor_ibs_mun: float = 0.0
    aliquota_cbs: float = 0.0
    valor_cbs: float = 0.0
    # Uma redução, um diferimento e uma devolução por destinação — é assim que
    # a NT os organiza, e somá-los numa coluna só somaria tributos diferentes.
    percentual_reducao_ibs_uf: float = 0.0
    aliquota_efetiva_ibs_uf: float = 0.0
    valor_diferido_ibs_uf: float = 0.0
    valor_devolucao_ibs_uf: float = 0.0
    percentual_reducao_ibs_mun: float = 0.0
    aliquota_efetiva_ibs_mun: float = 0.0
    valor_diferido_ibs_mun: float = 0.0
    valor_devolucao_ibs_mun: float = 0.0
    percentual_reducao_cbs: float = 0.0
    aliquota_efetiva_cbs: float = 0.0
    valor_diferido_cbs: float = 0.0
    valor_devolucao_cbs: float = 0.0
    codigo_credito_presumido: str | None = None
    base_credito_presumido: float = 0.0
    percentual_credito_presumido_ibs: float = 0.0
    valor_credito_presumido_ibs: float = 0.0
    valor_credito_presumido_ibs_susp: float = 0.0
    percentual_credito_presumido_cbs: float = 0.0
    valor_credito_presumido_cbs: float = 0.0
    valor_credito_presumido_cbs_susp: float = 0.0
    quantidade_bc_mono: float = 0.0
    valor_bc_mono: float = 0.0
    valor_ibs_mono: float = 0.0
    valor_cbs_mono: float = 0.0
    valor_ibs_mono_reten: float = 0.0
    valor_cbs_mono_reten: float = 0.0
    valor_ibs_mono_retido: float = 0.0
    valor_cbs_mono_retido: float = 0.0
    quantidade_bio_diferenca: float = 0.0
    valor_ibs_bio_diferenca: float = 0.0
    valor_cbs_bio_diferenca: float = 0.0
    valor_transf_credito_ibs: float = 0.0
    valor_transf_credito_cbs: float = 0.0
    competencia_ajuste: str | None = None
    valor_ajuste_compet_ibs: float = 0.0
    valor_ajuste_compet_cbs: float = 0.0
    valor_estorno_credito_ibs: float = 0.0
    valor_estorno_credito_cbs: float = 0.0
    cst_is: str | None = None
    class_trib_is: str | None = None
    base_is: float = 0.0
    aliquota_is: float = 0.0
    aliquota_is_especifica: float = 0.0
    unidade_tributavel_is: str | None = None
    quantidade_tributavel_is: float = 0.0
    valor_is: float = 0.0


@dataclass
class DocumentoNormalizado:
    """O documento, independente da origem que o produziu.

    Não traz `sentido`: se a nota é entrada ou saída depende de **qual
    empresa** a está escriturando, e o adaptador não sabe disso. Quem resolve
    é o importador, comparando os CNPJ. `tipo_operacao_emitente` guarda o que
    o emitente declarou (`tpNF`), que é a visão dele, não a nossa.
    """

    chave: str
    modelo: str
    especie: str
    numero: str
    adaptador: str
    hash_original: str
    xml_original: str
    serie: str | None = None
    situacao: str = "autorizado"
    finalidade: str | None = None
    natureza_operacao: str | None = None
    tipo_operacao_emitente: str | None = None

    emitente_cnpj: str | None = None
    emitente_nome: str | None = None
    emitente_ie: str | None = None
    emitente_uf: str | None = None
    destinatario_cnpj: str | None = None
    destinatario_nome: str | None = None
    destinatario_ie: str | None = None
    destinatario_uf: str | None = None
    municipio_codigo: str | None = None
    municipio_fg_ibs: str | None = None

    data_emissao: datetime.date | None = None
    data_entrada_saida: datetime.date | None = None

    # `modFrete` do grupo `transp`.  Vale a pena guardar mesmo sem uso próprio:
    # é o `IND_FRT` do C100, com a mesma tabela de códigos, e sem ele o
    # registro sai com um campo a menos ou com um chute no lugar.
    modalidade_frete: str | None = None

    valor_total: float = 0.0
    valor_produtos: float = 0.0
    valor_desconto: float = 0.0
    valor_frete: float = 0.0
    valor_seguro: float = 0.0
    valor_outras: float = 0.0
    base_icms: float = 0.0
    valor_icms: float = 0.0
    valor_icms_st: float = 0.0
    valor_ipi: float = 0.0
    valor_pis: float = 0.0
    valor_cofins: float = 0.0
    valor_ibs: float = 0.0
    valor_cbs: float = 0.0
    valor_is: float = 0.0

    itens: list[ItemNormalizado] = field(default_factory=list)


@runtime_checkable
class Adaptador(Protocol):
    """O contrato que toda origem cumpre."""

    nome: str

    def reconhece(self, conteudo: bytes) -> bool:
        """Diz se sabe ler este conteúdo, sem levantar."""

    def normalizar(
        self, conteudo: bytes, *, nome_arquivo: str | None = None
    ) -> DocumentoNormalizado:
        """Converte para o modelo interno, ou levanta ``ValueError``."""


ADAPTADORES: list[Adaptador] = []


def registrar_adaptador(adaptador: Adaptador) -> Adaptador:
    """Põe o adaptador na fila de reconhecimento.

    A ordem importa: o primeiro que reconhecer o conteúdo é o escolhido, então
    adaptadores específicos devem ser registrados antes dos genéricos.
    """
    ADAPTADORES.append(adaptador)
    return adaptador


def adaptador_para(conteudo: bytes) -> Adaptador:
    for adaptador in ADAPTADORES:
        if adaptador.reconhece(conteudo):
            return adaptador
    raise OrigemNaoReconhecida(
        "nenhum adaptador reconheceu o conteúdo — se for NFS-e, o provedor "
        "ainda não tem adaptador"
    )


# ── XML: leitura defensiva ─────────────────────────────────────────────────

_DOCTYPE = re.compile(rb"<!DOCTYPE", re.I)


def carregar_xml(conteudo: bytes) -> ET.Element:
    """Lê o XML recusando o que não pertence a documento fiscal.

    `ElementTree` recusa entidade externa (não lê `/etc/passwd`), mas
    **expande** entidade interna — o "billion laughs": um arquivo de 1 KB com
    entidades aninhadas vira gigabytes na memória e derruba o processo.
    Medido: quatro níveis já produzem 3.000 caracteres, e cada nível
    multiplica por dez.

    Nenhum documento fiscal eletrônico legítimo declara `DOCTYPE` — o leiaute
    da NF-e é definido por XSD, não por DTD. Recusar a declaração inteira
    elimina a classe de ataque sem custo e sem dependência nova.
    """
    if _DOCTYPE.search(conteudo[:4096]):
        raise XMLPerigoso(
            "XML com DOCTYPE recusado: documento fiscal eletrônico não usa DTD, "
            "e entidades permitem esgotar a memória do servidor"
        )
    try:
        return ET.fromstring(conteudo)
    except ET.ParseError as erro:
        raise ValueError(f"XML inválido: {erro}") from erro


def _sem_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _achar(no: ET.Element | None, *caminho: str) -> ET.Element | None:
    """Desce pelo caminho ignorando namespace, tolerando ausência."""
    atual = no
    for nome in caminho:
        if atual is None:
            return None
        atual = next((f for f in atual if _sem_ns(f.tag) == nome), None)
    return atual


def _texto(no: ET.Element | None, *caminho: str) -> str | None:
    alvo = _achar(no, *caminho) if caminho else no
    if alvo is None or alvo.text is None:
        return None
    valor = alvo.text.strip()
    return valor or None


def _numero(no: ET.Element | None, *caminho: str) -> float:
    bruto = _texto(no, *caminho)
    if bruto is None:
        return 0.0
    try:
        return float(bruto)
    except ValueError:
        return 0.0


def _data(bruto: str | None) -> datetime.date | None:
    """Aceita `2026-07-30T10:00:00-03:00` e `2026-07-30`."""
    if not bruto:
        return None
    try:
        return datetime.date.fromisoformat(bruto[:10])
    except ValueError:
        return None


def _primeiro_filho(no: ET.Element | None) -> ET.Element | None:
    """O ICMS vem embrulhado em ICMS00, ICMS60, ICMSSN102… conforme o caso.

    Descer no primeiro filho evita listar as ~20 variantes, que mudam a cada
    nota técnica.
    """
    if no is None:
        return None
    return next(iter(no), None)


# ── NF-e / NFC-e ───────────────────────────────────────────────────────────


class AdaptadorNFe:
    """NF-e (modelo 55) e NFC-e (modelo 65), leiaute 4.00.

    Lê tanto o XML de distribuição (`nfeProc`, com o protocolo de autorização)
    quanto a nota sozinha (`NFe`).
    """

    nome = "nfe"

    def reconhece(self, conteudo: bytes) -> bool:
        amostra = conteudo[:2048]
        return b"portalfiscal.inf.br/nfe" in amostra and (
            b"<NFe" in amostra or b"<nfeProc" in amostra
        )

    def normalizar(
        self, conteudo: bytes, *, nome_arquivo: str | None = None
    ) -> DocumentoNormalizado:
        raiz = carregar_xml(conteudo)
        nfe = raiz if _sem_ns(raiz.tag) == "NFe" else _achar(raiz, "NFe")
        if nfe is None:
            raise ValueError("XML não contém elemento NFe")
        inf = _achar(nfe, "infNFe")
        if inf is None:
            raise ValueError("NFe sem infNFe")

        ide = _achar(inf, "ide")
        emit = _achar(inf, "emit")
        dest = _achar(inf, "dest")
        total = _achar(inf, "total")
        icms_tot = _achar(total, "ICMSTot")

        chave = (inf.get("Id") or "").removeprefix("NFe").strip()
        if not chave:
            raise ValueError("infNFe sem atributo Id — sem chave de acesso")

        modelo = _texto(ide, "mod") or "55"
        documento = DocumentoNormalizado(
            chave=chave,
            modelo=modelo,
            especie="nfce" if modelo == "65" else "nfe",
            numero=_texto(ide, "nNF") or "",
            serie=_texto(ide, "serie"),
            adaptador=self.nome,
            hash_original=hashlib.sha256(conteudo).hexdigest(),
            xml_original=conteudo.decode("utf-8", errors="replace"),
            finalidade=_texto(ide, "finNFe"),
            natureza_operacao=_texto(ide, "natOp"),
            tipo_operacao_emitente=_texto(ide, "tpNF"),
            municipio_codigo=_texto(ide, "cMunFG"),
            municipio_fg_ibs=_texto(ide, "cMunFGIBS"),
            data_emissao=_data(_texto(ide, "dhEmi") or _texto(ide, "dEmi")),
            data_entrada_saida=_data(_texto(ide, "dhSaiEnt") or _texto(ide, "dSaiEnt")),
            modalidade_frete=_texto(inf, "transp", "modFrete"),
            emitente_cnpj=_texto(emit, "CNPJ") or _texto(emit, "CPF"),
            emitente_nome=_texto(emit, "xNome"),
            emitente_ie=_texto(emit, "IE"),
            emitente_uf=_texto(emit, "enderEmit", "UF"),
            destinatario_cnpj=_texto(dest, "CNPJ") or _texto(dest, "CPF"),
            destinatario_nome=_texto(dest, "xNome"),
            destinatario_ie=_texto(dest, "IE"),
            destinatario_uf=_texto(dest, "enderDest", "UF"),
            valor_total=_numero(icms_tot, "vNF"),
            valor_produtos=_numero(icms_tot, "vProd"),
            valor_desconto=_numero(icms_tot, "vDesc"),
            valor_frete=_numero(icms_tot, "vFrete"),
            valor_seguro=_numero(icms_tot, "vSeg"),
            valor_outras=_numero(icms_tot, "vOutro"),
            base_icms=_numero(icms_tot, "vBC"),
            valor_icms=_numero(icms_tot, "vICMS"),
            valor_icms_st=_numero(icms_tot, "vST"),
            valor_ipi=_numero(icms_tot, "vIPI"),
            valor_pis=_numero(icms_tot, "vPIS"),
            valor_cofins=_numero(icms_tot, "vCOFINS"),
        )

        # Situação: o protocolo diz se foi autorizada, denegada ou cancelada.
        motivo = _achar(raiz, "protNFe", "infProt")
        c_stat = _texto(motivo, "cStat")
        if c_stat:
            documento.situacao = _SITUACAO_POR_CSTAT.get(c_stat, "autorizado")

        for det in (f for f in inf if _sem_ns(f.tag) == "det"):
            documento.itens.append(self._item(det))

        # Totais da reforma: o XML tem grupos próprios, mas nem toda NF-e do
        # período de transição os traz.  Somar os itens dá o mesmo número e
        # funciona nos dois casos.
        documento.valor_ibs = sum(i.valor_ibs_uf + i.valor_ibs_mun for i in documento.itens)
        documento.valor_cbs = sum(i.valor_cbs for i in documento.itens)
        documento.valor_is = sum(i.valor_is for i in documento.itens)
        return documento

    def _item(self, det: ET.Element) -> ItemNormalizado:
        prod = _achar(det, "prod")
        imposto = _achar(det, "imposto")

        item = ItemNormalizado(
            numero_item=int(det.get("nItem") or 0),
            codigo=_texto(prod, "cProd"),
            descricao=_texto(prod, "xProd"),
            ncm=_texto(prod, "NCM"),
            cest=_texto(prod, "CEST"),
            unidade=_texto(prod, "uCom"),
            quantidade=_numero(prod, "qCom"),
            valor_unitario=_numero(prod, "vUnCom"),
            valor_total=_numero(prod, "vProd"),
            valor_desconto=_numero(prod, "vDesc"),
            valor_frete=_numero(prod, "vFrete"),
            valor_seguro=_numero(prod, "vSeg"),
            valor_outras=_numero(prod, "vOutro"),
            cfop=_texto(prod, "CFOP"),
        )

        icms = _primeiro_filho(_achar(imposto, "ICMS"))
        if icms is not None:
            item.origem_mercadoria = _texto(icms, "orig")
            item.cst_icms = _texto(icms, "CST")
            item.csosn = _texto(icms, "CSOSN")
            item.base_icms = _numero(icms, "vBC")
            item.aliquota_icms = _numero(icms, "pICMS")
            item.valor_icms = _numero(icms, "vICMS")
            item.base_icms_st = _numero(icms, "vBCST")
            item.valor_icms_st = _numero(icms, "vICMSST")
            item.valor_fcp = _numero(icms, "vFCP")

        ipi = _primeiro_filho(_achar(imposto, "IPI", "IPITrib")) or _achar(
            imposto, "IPI", "IPITrib"
        )
        if ipi is None:
            ipi = _achar(imposto, "IPI")
        if ipi is not None:
            item.cst_ipi = _texto(ipi, "CST") or _texto(_achar(imposto, "IPI", "IPITrib"), "CST")
            item.valor_ipi = _numero(_achar(imposto, "IPI", "IPITrib"), "vIPI")

        pis = _primeiro_filho(_achar(imposto, "PIS"))
        if pis is not None:
            item.cst_pis = _texto(pis, "CST")
            item.base_pis = _numero(pis, "vBC")
            item.aliquota_pis = _numero(pis, "pPIS")
            item.valor_pis = _numero(pis, "vPIS")

        cofins = _primeiro_filho(_achar(imposto, "COFINS"))
        if cofins is not None:
            item.cst_cofins = _texto(cofins, "CST")
            item.base_cofins = _numero(cofins, "vBC")
            item.aliquota_cofins = _numero(cofins, "pCOFINS")
            item.valor_cofins = _numero(cofins, "vCOFINS")

        self._reforma(imposto, item)
        return item

    @staticmethod
    def _reforma(imposto: ET.Element | None, item: ItemNormalizado) -> None:
        """IBS, CBS e Imposto Seletivo (NT 2025.002 v1.50).

        Ausentes na NF-e emitida antes de 03/08/2026 — daí tudo ser opcional.
        Ver ``docs/reforma-tributaria.md``.

        **Cada valor é lido do grupo em que a NT o põe, e não do `gIBSCBS`.**
        Redução, diferimento e devolução existem uma vez por destinação, dentro
        de `gIBSUF`, `gIBSMun` e `gCBS`; o crédito presumido fica em
        `gCredPresOper`, irmão de `gIBSCBS`; o monofásico está a dois níveis de
        profundidade. Procurá-los como filhos diretos de `gIBSCBS` não levanta
        erro nenhum — devolve zero —, e foi assim que o leitor passou a existir
        sem ler nada disso.
        """
        ibscbs = _achar(imposto, "IBSCBS")
        if ibscbs is None:
            return
        item.cst_ibscbs = _texto(ibscbs, "CST")
        item.class_trib_ibscbs = _texto(ibscbs, "cClassTrib")

        grupo = _achar(ibscbs, "gIBSCBS")
        if grupo is not None:
            item.base_ibscbs = _numero(grupo, "vBC")
            # O IBS é UM tributo com DUAS destinações: a partilha entre estado
            # e município é o cerne do imposto, e some se somarmos.
            uf = _achar(grupo, "gIBSUF")
            item.aliquota_ibs_uf = _numero(uf, "pIBSUF")
            item.valor_ibs_uf = _numero(uf, "vIBSUF")
            AdaptadorNFe._beneficios(uf, item, "ibs_uf")

            mun = _achar(grupo, "gIBSMun")
            item.aliquota_ibs_mun = _numero(mun, "pIBSMun")
            item.valor_ibs_mun = _numero(mun, "vIBSMun")
            AdaptadorNFe._beneficios(mun, item, "ibs_mun")

            cbs = _achar(grupo, "gCBS")
            item.aliquota_cbs = _numero(cbs, "pCBS")
            item.valor_cbs = _numero(cbs, "vCBS")
            AdaptadorNFe._beneficios(cbs, item, "cbs")

        cred = _achar(ibscbs, "gCredPresOper")
        if cred is not None:
            item.codigo_credito_presumido = _texto(cred, "cCredPres")
            item.base_credito_presumido = _numero(cred, "vBCCredPres")
            for grupo_cred, sufixo in (("gIBSCredPres", "ibs"), ("gCBSCredPres", "cbs")):
                no = _achar(cred, grupo_cred)
                if no is None:
                    continue
                setattr(item, f"percentual_credito_presumido_{sufixo}", _numero(no, "pCredPres"))
                setattr(item, f"valor_credito_presumido_{sufixo}", _numero(no, "vCredPres"))
                setattr(
                    item,
                    f"valor_credito_presumido_{sufixo}_susp",
                    _numero(no, "vCredPresCondSus"),
                )

        AdaptadorNFe._monofasico(_achar(ibscbs, "gIBSCBSMono"), item)
        AdaptadorNFe._creditos_e_ajustes(ibscbs, item)

        seletivo = _achar(imposto, "IS")
        if seletivo is not None:
            item.cst_is = _texto(seletivo, "CSTIS")
            item.class_trib_is = _texto(seletivo, "cClassTribIS")
            item.base_is = _numero(seletivo, "vBCIS")
            item.aliquota_is = _numero(seletivo, "pIS")
            # O IS pode ser cobrado por unidade, não por percentual — bebidas
            # e cigarros usam essa forma, e aí a quantidade tributável é que
            # manda.
            item.aliquota_is_especifica = _numero(seletivo, "pISEspec")
            item.unidade_tributavel_is = _texto(seletivo, "uTrib")
            item.quantidade_tributavel_is = _numero(seletivo, "qTrib")
            item.valor_is = _numero(seletivo, "vIS")

    @staticmethod
    def _beneficios(destinacao: ET.Element | None, item: ItemNormalizado, sufixo: str) -> None:
        """Redução, diferimento e devolução de UMA destinação do tributo.

        A NT repete os três grupos dentro de `gIBSUF`, `gIBSMun` e `gCBS`, com
        os mesmos nomes de tag em cada um. São valores de tributos diferentes,
        e o que os distingue é só o grupo em que estão.
        """
        if destinacao is None:
            return
        red = _achar(destinacao, "gRed")
        setattr(item, f"percentual_reducao_{sufixo}", _numero(red, "pRedAliq"))
        setattr(item, f"aliquota_efetiva_{sufixo}", _numero(red, "pAliqEfet"))
        setattr(item, f"valor_diferido_{sufixo}", _numero(_achar(destinacao, "gDif"), "vDif"))
        setattr(
            item,
            f"valor_devolucao_{sufixo}",
            _numero(_achar(destinacao, "gDevTrib"), "vDevTrib"),
        )

    @staticmethod
    def _monofasico(mono: ET.Element | None, item: ItemNormalizado) -> None:
        """O monofásico de combustíveis, reformulado pela v1.50 da NT.

        O grupo tem quatro variantes — IBS e CBS, cada um ad rem ou ad valorem
        — e qual delas vem depende do ano e do `cClassTrib`. Ler as quatro para
        depois escolher seria refazer, com menos informação, a conta que a
        própria NT já fecha: `vTotIBSMonoItem` e `vTotCBSMonoItem` são filhos
        diretos do grupo e valem qualquer que tenha sido a variante.

        **A retenção já está dentro do total**, e a regra UB105a-10 da própria
        NT diz a conta: ``vTotIBSMonoItem = vIBSMono + vIBSMonoReten -
        vIBSMonoDif``. Guardá-la à parte é para saber *de que* o total é feito
        — nunca para somar ao total, que seria contar a retenção duas vezes.
        O que **não** está no total é `gMonoRet`, o cobrado anteriormente: a
        fórmula não o inclui, e por isso ele é grandeza separada de verdade.
        """
        if mono is None:
            return
        item.valor_ibs_mono = _numero(mono, "vTotIBSMonoItem")
        item.valor_cbs_mono = _numero(mono, "vTotCBSMonoItem")

        for variante in ("gIBSMonoAdRem", "gIBSMonoAdValorem"):
            AdaptadorNFe._variante_mono(_achar(mono, variante), item, "ibs")
        for variante in ("gCBSMonoAdRem", "gCBSMonoAdValorem"):
            AdaptadorNFe._variante_mono(_achar(mono, variante), item, "cbs")

    @staticmethod
    def _variante_mono(variante: ET.Element | None, item: ItemNormalizado, tributo: str) -> None:
        if variante is None:
            return
        padrao = _achar(variante, "gMonoPadrao")
        if padrao is not None:
            # A base é comum aos dois tributos — o ad rem tributa quantidade, o
            # ad valorem tributa valor, e o item tem uma só de cada.
            item.quantidade_bc_mono = _numero(padrao, "qBCMono") or item.quantidade_bc_mono
            item.valor_bc_mono = _numero(padrao, "vBCMono") or item.valor_bc_mono

        sigla = tributo.upper()
        reten = _achar(variante, "gMonoReten")
        setattr(item, f"valor_{tributo}_mono_reten", _numero(reten, f"v{sigla}MonoReten"))
        retido = _achar(variante, "gMonoRet")
        setattr(item, f"valor_{tributo}_mono_retido", _numero(retido, f"v{sigla}MonoRet"))

        # Mistura de etanol anidro em percentual diferente do obrigatório: o
        # mesmo campo é valor A RECOLHER com cClassTrib 620004 e A RESSARCIR
        # com 620005.  Guardamos o número; o sinal está no código, e o sistema
        # não interpreta a tabela cClassTrib.
        bio = _achar(variante, "gpBioDiferenca")
        if bio is not None:
            item.quantidade_bio_diferenca = _numero(bio, "qBCBioComb")
            setattr(item, f"valor_{tributo}_bio_diferenca", _numero(bio, f"v{sigla}Diferenca"))

    @staticmethod
    def _creditos_e_ajustes(ibscbs: ET.Element, item: ItemNormalizado) -> None:
        """Transferência de crédito, ajuste de competência e estorno.

        `gTransfCred` e `gAjusteCompet` são alternativas a `gIBSCBS` na mesma
        escolha do schema (UB14k): um item que transfere crédito **não traz**
        grupo de tributo nenhum. Por isso não valem como complemento do que já
        foi lido — são o conteúdo inteiro do item.
        """
        transf = _achar(ibscbs, "gTransfCred")
        if transf is not None:
            item.valor_transf_credito_ibs = _numero(transf, "vIBS")
            item.valor_transf_credito_cbs = _numero(transf, "vCBS")

        ajuste = _achar(ibscbs, "gAjusteCompet")
        if ajuste is not None:
            # `competApur` é AAAA-MM e pode ser retroativo: é o que diz a que
            # apuração o ajuste pertence, e sem ele o valor não tem destino.
            item.competencia_ajuste = _texto(ajuste, "competApur")
            item.valor_ajuste_compet_ibs = _numero(ajuste, "vIBS")
            item.valor_ajuste_compet_cbs = _numero(ajuste, "vCBS")

        estorno = _achar(ibscbs, "gEstornoCred")
        if estorno is not None:
            item.valor_estorno_credito_ibs = _numero(estorno, "vIBSEstCred")
            item.valor_estorno_credito_cbs = _numero(estorno, "vCBSEstCred")


# cStat do protocolo de autorização.  Só os desfechos que mudam a escrituração.
_SITUACAO_POR_CSTAT = {
    "100": "autorizado",
    "150": "autorizado",  # autorizada fora do prazo
    "101": "cancelado",
    "135": "cancelado",
    "151": "cancelado",
    "155": "cancelado",
    "110": "denegado",
    "301": "denegado",
    "302": "denegado",
    "303": "denegado",
}


registrar_adaptador(AdaptadorNFe())
