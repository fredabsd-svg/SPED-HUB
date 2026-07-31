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
    municipio_fg_ibs: str | None = None
    aliquota_cbs: float = 0.0
    valor_cbs: float = 0.0
    percentual_reducao_aliquota: float = 0.0
    aliquota_efetiva: float = 0.0
    valor_diferido: float = 0.0
    valor_devolucao_tributo: float = 0.0
    codigo_credito_presumido: str | None = None
    valor_credito_presumido: float = 0.0
    valor_credito_presumido_susp: float = 0.0
    quantidade_bc_mono: float = 0.0
    valor_ibs_mono: float = 0.0
    valor_cbs_mono: float = 0.0
    valor_ibs_mono_retido: float = 0.0
    valor_cbs_mono_retido: float = 0.0
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
        """IBS, CBS e Imposto Seletivo (NT 2025.002).

        Ausentes na NF-e emitida antes de 03/08/2026 — daí tudo ser opcional.
        Ver ``docs/reforma-tributaria.md``.
        """
        ibscbs = _achar(imposto, "IBSCBS")
        if ibscbs is not None:
            item.cst_ibscbs = _texto(ibscbs, "CST")
            item.class_trib_ibscbs = _texto(ibscbs, "cClassTrib")
            grupo = _achar(ibscbs, "gIBSCBS")
            if grupo is not None:
                item.base_ibscbs = _numero(grupo, "vBC")
                item.municipio_fg_ibs = _texto(grupo, "cMunFGIBS")
                # O IBS é UM tributo com DUAS destinações: a partilha entre
                # estado e município é o cerne do imposto, e some se somarmos.
                uf = _achar(grupo, "gIBSUF")
                item.aliquota_ibs_uf = _numero(uf, "pIBSUF")
                item.valor_ibs_uf = _numero(uf, "vIBSUF")
                mun = _achar(grupo, "gIBSMun")
                item.aliquota_ibs_mun = _numero(mun, "pIBSMun")
                item.valor_ibs_mun = _numero(mun, "vIBSMun")
                cbs = _achar(grupo, "gCBS")
                item.aliquota_cbs = _numero(cbs, "pCBS")
                item.valor_cbs = _numero(cbs, "vCBS")

                red = _achar(grupo, "gRed")
                item.percentual_reducao_aliquota = _numero(red, "pRedAliq")
                item.aliquota_efetiva = _numero(red, "pAliqEfet")
                item.valor_diferido = _numero(_achar(grupo, "gDif"), "vDif")
                item.valor_devolucao_tributo = _numero(_achar(grupo, "gDevTrib"), "vDevTrib")
                cred = _achar(grupo, "gCredPres")
                item.codigo_credito_presumido = _texto(cred, "cCredPres")
                item.valor_credito_presumido = _numero(cred, "vCredPres")
                item.valor_credito_presumido_susp = _numero(cred, "vCredPresCondSus")

            mono = _achar(ibscbs, "gIBSCBSMono")
            if mono is not None:
                item.quantidade_bc_mono = _numero(mono, "qBCMono")
                item.valor_ibs_mono = _numero(mono, "vIBSMono")
                item.valor_cbs_mono = _numero(mono, "vCBSMono")
                item.valor_ibs_mono_retido = _numero(mono, "vIBSMonoReten")
                item.valor_cbs_mono_retido = _numero(mono, "vCBSMonoReten")

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
