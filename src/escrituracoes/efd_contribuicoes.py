"""Gerador da EFD-Contribuições a partir dos documentos importados.

Mesma origem da EFD ICMS/IPI — os documentos da Central mais a camada efetiva
—, apuração diferente: PIS e Cofins, com a distinção que decide tudo neste
arquivo.

**No regime cumulativo não há crédito.**  A empresa que apura pelo lucro
presumido paga PIS e Cofins sobre a receita e não desconta nada das compras.
Um gerador que somasse os créditos das entradas em regime cumulativo produziria
um arquivo com contribuição a menor — o Fisco cobraria a diferença com multa, e
o erro passaria despercebido na conferência porque o arquivo é estruturalmente
válido.  Por isso o regime é cadastro obrigatório, não default.

O mesmo vale para o `IND_ATIV` do 0000 — e com uma armadilha a mais: o campo
tem o mesmo nome na EFD ICMS/IPI e **outra tabela**.  Lá a resposta é binária
(0=industrial, 1=outros); aqui são seis valores, e o "1" quer dizer prestador
de serviços.  Reaproveitar a resposta de lá declararia como prestador de
serviços toda empresa de comércio.  São dois campos de cadastro distintos.

**O que este gerador NÃO faz**, e é preciso saber antes de usar:

  * bloco A (serviços/NFS-e) — a Central ainda não importa NFS-e;
  * blocos D (transporte), F (demais operações) e I (instituições financeiras);
  * créditos extemporâneos, ajustes e o bloco 1 inteiro;
  * regimes especiais, monofásico, substituição e alíquota por unidade;
  * retenções na fonte.

A apuração dos blocos M é a soma direta dos documentos escriturados. Está dito
em `docs/modules/escrituracoes.md` e num aviso do resultado.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import AjusteFiscal, DocumentoFiscal, Empresa
from src.documentos.ajustes import valor_efetivo
from src.escrituracoes.base import (
    CampoObrigatorioAusente,
    GeradorBase,
    ResultadoGeracao,
    formatar_data,
    formatar_valor,
)
from src.escrituracoes.base import texto as _texto
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES

logger = logging.getLogger("sped-hub.escrituracoes")

COD_VER = "006"

# COD_INC_TRIB do registro 0110 — o campo que decide se há crédito.
REGIMES = {
    "1": "não cumulativo",
    "2": "cumulativo",
    "3": "ambos",
}

# Regimes em que a empresa desconta crédito das aquisições.
_COM_CREDITO = {"1", "3"}

# IND_ATIV do registro 0000.  Note que a tabela é OUTRA, não a da EFD ICMS/IPI:
# lá a resposta é binária, aqui são seis valores.  Daí o campo do cadastro ser
# separado — ver o comentário em `Empresa.ind_ativ_contribuicoes`.
ATIVIDADES = {
    "0": "industrial ou equiparado a industrial",
    "1": "prestador de serviços",
    "2": "atividade de comércio",
    "3": "pessoa jurídica dos §§ 6º, 8º e 9º do art. 3º da Lei 9.718/98",
    "4": "atividade imobiliária",
    "9": "outros",
}

# IND_NAT_PJ do 0000: 00 = sociedade empresária em geral, 01 = cooperativa,
# 02 = entidade que apura o PIS/Pasep sobre a folha de salários.  Fica fixo no
# caso geral, e o resultado avisa — cooperativa e entidade de folha são
# minoria identificável, e quem tem uma sabe que tem.
IND_NAT_PJ_GERAL = "00"


class GeradorEFDContribuicoes(GeradorBase):
    """Monta a EFD-Contribuições de um período."""

    LEIAUTE = EFD_CONTRIBUICOES

    def __init__(
        self,
        session: Session,
        *,
        empresa: Empresa,
        data_inicio: datetime.date,
        data_fim: datetime.date,
        tipo_escrituracao: str = "0",
    ):
        super().__init__()
        self.session = session
        self.empresa = empresa
        self.data_inicio = data_inicio
        self.data_fim = data_fim
        self.tipo_escrituracao = tipo_escrituracao

    # ── Entrada ────────────────────────────────────────────────────────────

    def gerar(self) -> ResultadoGeracao:
        self._conferir_cadastro()
        documentos = self._documentos()
        visoes = [self._visao(d) for d in documentos]

        self._reiniciar([d.id for d in documentos])
        self._bloco_0(visoes)
        self._bloco_c(visoes)
        self._bloco_m(visoes)
        self._bloco_9()

        if not documentos:
            self._resultado.avisos.append(
                "nenhum documento no período — o arquivo sai só com os blocos de abertura"
            )
        self._avisar_frete_sem_modalidade()
        return self._resultado

    def _conferir_cadastro(self) -> None:
        """Cadastro que o arquivo declara e o validador não tem como conferir."""
        if self.empresa.cod_inc_trib not in REGIMES:
            raise CampoObrigatorioAusente(
                f"a empresa {self.empresa.nome!r} não tem cod_inc_trib "
                f"(um de {sorted(REGIMES)}) — é o campo que decide se há crédito a "
                "descontar, e errar nele produz arquivo estruturalmente válido com "
                "contribuição errada"
            )
        if self.empresa.ind_ativ_contribuicoes not in ATIVIDADES:
            tabela = ", ".join(f"{c}={d}" for c, d in sorted(ATIVIDADES.items()))
            raise CampoObrigatorioAusente(
                f"a empresa {self.empresa.nome!r} não tem ind_ativ_contribuicoes "
                f"({tabela}) — é o enquadramento que o 0000 declara, e o validador "
                "aceita qualquer um porque não tem como saber qual é o certo. Note "
                "que a tabela NÃO é a da EFD ICMS/IPI: o `ind_ativ` de lá é binário"
            )

    @property
    def _tem_credito(self) -> bool:
        return self.empresa.cod_inc_trib in _COM_CREDITO

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

    def _visao(self, documento: DocumentoFiscal) -> dict:
        ajustes = (
            self.session.execute(
                select(AjusteFiscal).where(AjusteFiscal.documento_id == documento.id)
            )
            .scalars()
            .all()
        )
        do_cabecalho = [a for a in ajustes if a.item_id is None]
        cabecalho = {
            c.name: valor_efetivo(documento, c.name, do_cabecalho)
            for c in DocumentoFiscal.__table__.columns
        }
        itens = []
        for item in documento.itens:
            do_item = [a for a in ajustes if a.item_id == item.id]
            itens.append(
                {c.name: valor_efetivo(item, c.name, do_item) for c in item.__table__.columns}
            )
        return {"cabecalho": cabecalho, "itens": itens}

    # ── Bloco 0 ────────────────────────────────────────────────────────────

    def _bloco_0(self, visoes: Sequence[dict]) -> None:
        e = self.empresa
        self._add(
            "0000",
            COD_VER,
            self.tipo_escrituracao,
            "",  # IND_SIT_ESP
            "",  # NUM_REC_ANTERIOR
            formatar_data(self.data_inicio),
            formatar_data(self.data_fim),
            e.nome,
            e.cnpj,
            e.uf,
            e.cod_mun,
            "",  # SUFRAMA
            IND_NAT_PJ_GERAL,
            e.ind_ativ_contribuicoes,
        )
        self._resultado.avisos.append(
            f"o 0000 declara IND_NAT_PJ={IND_NAT_PJ_GERAL} (sociedade empresária em "
            "geral) — cooperativa (01) e entidade que apura sobre a folha de "
            "salários (02) precisam ser corrigidas à mão antes de transmitir"
        )
        self._add("0001", "0")
        # O registro que declara o regime — e portanto se há crédito.
        self._add("0110", e.cod_inc_trib, "1" if self._tem_credito else "", "", "")
        self._add("0140", e.cnpj, e.nome, e.cnpj, e.uf, _texto(e.ie), _texto(e.cod_mun), "", "")

        for campos in self._participantes(visoes):
            self._add("0150", *campos)
        for unidade in self._unidades(visoes):
            self._add("0190", unidade, unidade)
        for campos in self._itens(visoes):
            self._add("0200", *campos)

        self._encerrar_bloco("0", "0990")

    def _participantes(self, visoes: Sequence[dict]) -> list[list[str]]:
        vistos: dict[str, list[str]] = {}
        for visao in visoes:
            c = visao["cabecalho"]
            entrada = c["sentido"] == "entrada"
            cnpj = c["emitente_cnpj"] if entrada else c["destinatario_cnpj"]
            nome = c["emitente_nome"] if entrada else c["destinatario_nome"]
            if not cnpj or cnpj in vistos:
                continue
            vistos[cnpj] = [
                cnpj,
                _texto(nome),
                "",  # COD_PAIS
                cnpj if len(cnpj) == 14 else "",
                cnpj if len(cnpj) == 11 else "",
                "",  # IE
                _texto(c["municipio_codigo"]),
                "",  # SUFRAMA
                "",  # ENDERECO
                "",
                "",
                "",
            ]
        return list(vistos.values())

    def _unidades(self, visoes: Sequence[dict]) -> list[str]:
        return sorted({i["unidade"] for v in visoes for i in v["itens"] if i["unidade"]})

    def _itens(self, visoes: Sequence[dict]) -> list[list[str]]:
        vistos: dict[str, list[str]] = {}
        for visao in visoes:
            for item in visao["itens"]:
                codigo = item["codigo"]
                if not codigo or codigo in vistos:
                    continue
                vistos[codigo] = [
                    codigo,
                    _texto(item["descricao"]),
                    "",
                    "",
                    _texto(item["unidade"]),
                    "00",
                    _texto(item["ncm"]),
                    "",
                    "",
                    "",
                    "",
                    _texto(item["cest"]),
                ]
        return list(vistos.values())

    # ── Bloco C: documentos ────────────────────────────────────────────────

    def _bloco_c(self, visoes: Sequence[dict]) -> None:
        self._add("C001", "0" if visoes else "1")
        if visoes:
            e = self.empresa
            self._add("C010", e.cnpj, "0")  # IND_ESCRI: 0 = escrituração completa
            for visao in visoes:
                self._documento_c100(visao)
        self._encerrar_bloco("C", "C990")

    def _documento_c100(self, visao: dict) -> None:
        c = visao["cabecalho"]
        entrada = c["sentido"] == "entrada"
        participante = c["emitente_cnpj"] if entrada else c["destinatario_cnpj"]

        self._add(
            "C100",
            "0" if entrada else "1",
            "1" if entrada else "0",
            _texto(participante),
            _texto(c["modelo"]),
            "00" if c["situacao"] == "autorizado" else "02",
            _texto(c["serie"]),
            _texto(c["numero"]),
            _texto(c["chave"]),
            formatar_data(c["data_emissao"]),
            formatar_data(c["data_entrada_saida"] or c["data_emissao"]),
            formatar_valor(c["valor_total"]),
            "",  # IND_PGTO
            formatar_valor(c["valor_desconto"]),
            "",  # VL_ABAT_NT
            formatar_valor(c["valor_produtos"]),
            self._ind_frt(c),
            formatar_valor(c["valor_frete"]),
            formatar_valor(c["valor_seguro"]),
            formatar_valor(c["valor_outras"]),
            formatar_valor(c["base_icms"]),
            formatar_valor(c["valor_icms"]),
            "",
            formatar_valor(c["valor_icms_st"]),
            formatar_valor(c["valor_ipi"]),
            formatar_valor(c["valor_pis"]),
            formatar_valor(c["valor_cofins"]),
            "",
            "",
        )
        for item in visao["itens"]:
            self._item_c170(item)

    def _item_c170(self, item: dict) -> None:
        """O item, com o detalhamento de PIS e Cofins que interessa aqui."""
        self._add(
            "C170",
            _texto(item["numero_item"]),
            _texto(item["codigo"]),
            _texto(item["descricao"]),
            formatar_valor(item["quantidade"]),
            _texto(item["unidade"]),
            formatar_valor(item["valor_total"]),
            formatar_valor(item["valor_desconto"]),
            "0",  # IND_MOV
            f"{_texto(item['origem_mercadoria']) or '0'}{_texto(item['cst_icms'])}",
            _texto(item["cfop"]),
            "",  # COD_NAT
            formatar_valor(item["base_icms"]),
            formatar_valor(item["aliquota_icms"]),
            formatar_valor(item["valor_icms"]),
            "",
            "",
            "",
            "",
            _texto(item["cst_ipi"]),
            "",
            "",
            "",
            formatar_valor(item["valor_ipi"]),
            _texto(item["cst_pis"]),
            formatar_valor(item["base_pis"]),
            formatar_valor(item["aliquota_pis"]),
            "",
            "",
            formatar_valor(item["valor_pis"]),
            _texto(item["cst_cofins"]),
            formatar_valor(item["base_cofins"]),
            formatar_valor(item["aliquota_cofins"]),
            "",
            "",
            formatar_valor(item["valor_cofins"]),
            "",  # COD_CTA
            "",  # VL_ABAT_NT
        )

    # ── Bloco M: apuração de PIS e Cofins ──────────────────────────────────

    def _bloco_m(self, visoes: Sequence[dict]) -> None:
        self._add("M001", "0" if visoes else "1")
        if visoes:
            self._apuracao(visoes)
        self._encerrar_bloco("M", "M990")

    def _apuracao(self, visoes: Sequence[dict]) -> None:
        """Contribuição das saídas menos crédito das entradas — quando há crédito.

        A distinção do regime é o ponto: no cumulativo a empresa paga sobre a
        receita e não desconta nada das compras. Somar crédito ali produziria
        contribuição a menor num arquivo estruturalmente válido.
        """
        debito_pis = debito_cofins = 0.0
        credito_pis = credito_cofins = 0.0

        for visao in visoes:
            pis = sum(i["valor_pis"] or 0.0 for i in visao["itens"])
            cofins = sum(i["valor_cofins"] or 0.0 for i in visao["itens"])
            if visao["cabecalho"]["sentido"] == "saida":
                debito_pis += pis
                debito_cofins += cofins
            else:
                credito_pis += pis
                credito_cofins += cofins

        if not self._tem_credito:
            credito_pis = credito_cofins = 0.0
            self._resultado.avisos.append(
                "regime cumulativo: os créditos das entradas NÃO foram descontados, "
                "porque nesse regime não há crédito a descontar"
            )

        self._consolidacao("M200", debito_pis, credito_pis)
        self._consolidacao("M600", debito_cofins, credito_cofins)
        self._resultado.avisos.append(
            "apuração dos blocos M é a soma direta dos documentos: não inclui créditos "
            "extemporâneos, ajustes, retenções nem regimes especiais — confira antes "
            "de transmitir"
        )

    def _consolidacao(self, tipo: str, debito: float, credito: float) -> None:
        """M200 (PIS) e M600 (Cofins) têm o mesmo desenho de campos."""
        devido = max(debito - credito, 0.0)
        self._add(
            tipo,
            formatar_valor(debito if self._tem_credito else 0.0),  # NÃO cumulativa
            formatar_valor(credito),  # VL_TOT_CRED_DESC
            "",  # VL_TOT_CRED_DESC_ANT
            formatar_valor(devido if self._tem_credito else 0.0),
            "",  # VL_RET_NC
            "",  # VL_OUT_DED_NC
            formatar_valor(devido if self._tem_credito else 0.0),
            formatar_valor(debito if not self._tem_credito else 0.0),  # cumulativa
            "",  # VL_RET_CUM
            "",  # VL_OUT_DED_CUM
            formatar_valor(debito if not self._tem_credito else 0.0),
            formatar_valor(devido if self._tem_credito else debito),  # VL_TOT_CONT_REC
        )
