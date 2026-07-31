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

# IND_ATIV do registro 0000.  O nome traz a obrigação de propósito: existe um
# `ATIVIDADES_ICMS` com o MESMO nome de campo e outra tabela, e chamar um dos
# dois só de `ATIVIDADES` é o convite exato para o erro que já custou caro —
# lá a resposta é binária, aqui são seis valores e o "1" quer dizer prestador
# de serviços.  Ver o comentário em `Empresa.ind_ativ_contribuicoes`.
ATIVIDADES_CONTRIBUICOES = {
    "0": "industrial ou equiparado a industrial",
    "1": "prestador de serviços",
    "2": "atividade de comércio",
    "3": "pessoa jurídica dos §§ 6º, 8º e 9º do art. 3º da Lei 9.718/98",
    "4": "atividade imobiliária",
    "9": "outros",
}

# IND_NAT_PJ do 0000 — a natureza da pessoa jurídica.
NATUREZAS_PJ = {
    "00": "sociedade empresária em geral",
    "01": "sociedade cooperativa",
    "02": "entidade que apura o PIS/Pasep sobre a folha de salários",
    "03": "pessoa jurídica em geral, sócia ostensiva de SCP",
    "04": "sociedade cooperativa sócia ostensiva de SCP",
    "05": "sociedade em conta de participação (SCP)",
}

# O valor usado quando a empresa não declarou natureza.  É o caso da imensa
# maioria, e por isso é default e não recusa — mas sai com aviso, porque
# cooperativa e entidade de folha apuram por outra regra e o validador aceita
# o enquadramento errado sem reclamar.
IND_NAT_PJ_GERAL = "00"

# Naturezas que exigem o registro 0035 (identificação da SCP), que este
# gerador não escreve.
_COM_SCP = {"03", "04", "05"}

# CST de PIS/Cofins — tabelas 4.3.3 e 4.3.4 do Guia Prático.  **O CST decide se
# o valor destacado entra na apuração**, e ignorá-lo é a diferença entre
# recolher o certo e recolher a menos com multa.
#
# Saídas que geram contribuição.  Ficam de fora, de propósito: 04 (monofásica,
# revenda a alíquota zero — a contribuição já foi paga no início da cadeia),
# 06 (alíquota zero), 07 (isenta), 08 (sem incidência) e 09 (suspensão).
CST_SAIDA_TRIBUTADA = {"01", "02", "03", "05"}
CST_SAIDA_SEM_DEBITO = {"04", "06", "07", "08", "09"}

# Entradas que dão direito a crédito: 50 a 56 (crédito) e 60 a 67 (presumido).
CST_ENTRADA_COM_CREDITO = {
    "50",
    "51",
    "52",
    "53",
    "54",
    "55",
    "56",
    "60",
    "61",
    "62",
    "63",
    "64",
    "65",
    "66",
    "67",
}
# Entradas que NÃO dão: aquisição sem direito, isenta, suspensa, alíquota zero,
# sem incidência, por substituição.  Somar crédito aqui é contribuição a menor.
CST_ENTRADA_SEM_CREDITO = {"70", "71", "72", "73", "74", "75"}

# "Outras operações", nos dois sentidos: o código não diz o tratamento, e o
# sistema não tem como decidir.  Entram na soma — é o comportamento de sempre —
# mas o resultado avisa, porque quem sabe é quem escriturou.
CST_INDEFINIDO = {"49", "98", "99"}


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

    def _natureza_pj(self) -> str:
        """O IND_NAT_PJ do cadastro, ou o geral com aviso.

        Não recusa quando falta, ao contrário do regime e da atividade: aqui há
        um default que vale para a imensa maioria, e exigir a resposta de todo
        mundo por causa da minoria travaria quem não tem o que declarar. Mas o
        silêncio é dito em voz alta — cooperativa e entidade de folha apuram
        por outra regra, e o validador aceita o enquadramento errado.
        """
        natureza = self.empresa.ind_nat_pj
        if natureza not in NATUREZAS_PJ:
            self._resultado.avisos.append(
                f"a empresa não declarou a natureza jurídica: o 0000 saiu com "
                f"IND_NAT_PJ={IND_NAT_PJ_GERAL} ({NATUREZAS_PJ[IND_NAT_PJ_GERAL]}). "
                "Cooperativa e entidade que apura sobre a folha apuram por outra "
                "regra — informe com `sped-hub fiscal cadastro --ind-nat-pj`"
            )
            return IND_NAT_PJ_GERAL

        if natureza in _COM_SCP:
            self._resultado.avisos.append(
                f"IND_NAT_PJ={natureza} ({NATUREZAS_PJ[natureza]}) exige o registro "
                "0035, que identifica a SCP e que este gerador NÃO escreve — "
                "complemente à mão antes de transmitir"
            )
        return natureza

    def _conferir_cadastro(self) -> None:
        """Cadastro que o arquivo declara e o validador não tem como conferir."""
        if self.empresa.cod_inc_trib not in REGIMES:
            raise CampoObrigatorioAusente(
                f"a empresa {self.empresa.nome!r} não tem cod_inc_trib "
                f"(um de {sorted(REGIMES)}) — é o campo que decide se há crédito a "
                "descontar, e errar nele produz arquivo estruturalmente válido com "
                "contribuição errada"
            )
        if self.empresa.ind_ativ_contribuicoes not in ATIVIDADES_CONTRIBUICOES:
            tabela = ", ".join(f"{c}={d}" for c, d in sorted(ATIVIDADES_CONTRIBUICOES.items()))
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
            self._natureza_pj(),
            e.ind_ativ_contribuicoes,
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

    def _entra_na_apuracao(self, cst: str, saida: bool) -> bool:
        """Se o valor destacado neste item conta — decidido pelo CST.

        O CST não é decoração: ele diz o tratamento tributário da operação, e
        o valor destacado sozinho não. Uma revenda monofásica traz zero, mas
        uma aquisição sem direito a crédito pode vir com PIS destacado pelo
        fornecedor — somá-lo produz contribuição a MENOR, que volta como
        cobrança com multa, num arquivo estruturalmente válido.

        **Numa entrada, o CST que veio no XML é o do fornecedor**, porque o
        documento é dele. Quem escritura tem de classificar a aquisição com o
        CST do adquirente — 50 a 56 dão crédito, 70 a 75 não —, e é para isso
        que existe o motor de classificação. Item de entrada ainda com CST de
        saída não foi classificado, e o gerador não decide por ele: soma, como
        sempre fez, e diz em voz alta o que aconteceu.
        """
        codigo = (cst or "").strip()
        do_sentido, do_outro = (
            (
                CST_SAIDA_TRIBUTADA | CST_SAIDA_SEM_DEBITO,
                CST_ENTRADA_COM_CREDITO | CST_ENTRADA_SEM_CREDITO,
            )
            if saida
            else (
                CST_ENTRADA_COM_CREDITO | CST_ENTRADA_SEM_CREDITO,
                CST_SAIDA_TRIBUTADA | CST_SAIDA_SEM_DEBITO,
            )
        )

        if codigo in do_sentido:
            return codigo not in (CST_SAIDA_SEM_DEBITO | CST_ENTRADA_SEM_CREDITO)
        if codigo in do_outro:
            self._cst_do_outro_sentido.add(codigo)
            return True

        self._cst_indefinido.add(codigo or "(vazio)")
        return True

    def _somar_item(self, item: dict, campo: str, cst: str, saida: bool) -> float:
        """O valor do item que entra na apuração, ou zero.

        Descartar valor destacado é decisão forte, e por isso o valor
        descartado é registrado: documento que traz contribuição num item cujo
        CST diz que não há é documento inconsistente, e quem fecha o mês
        precisa saber disso antes de transmitir.
        """
        valor = item[campo] or 0.0
        if self._entra_na_apuracao(cst, saida):
            return valor
        if valor:
            self._descartados.append((cst, valor))
        return 0.0

    def _apuracao(self, visoes: Sequence[dict]) -> None:
        """Contribuição das saídas menos crédito das entradas — quando há crédito.

        Duas distinções decidem o número, e as duas produzem arquivo
        estruturalmente válido quando erradas:

        **O regime.** No cumulativo a empresa paga sobre a receita e não
        desconta nada das compras; somar crédito ali produz contribuição a
        menor.

        **O CST de cada item.** É ele que diz se a operação gera débito ou
        crédito — ver `_entra_na_apuracao`.
        """
        debito_pis = debito_cofins = 0.0
        credito_pis = credito_cofins = 0.0
        self._cst_indefinido: set[str] = set()
        self._cst_do_outro_sentido: set[str] = set()
        self._descartados: list[tuple[str, float]] = []

        for visao in visoes:
            saida = visao["cabecalho"]["sentido"] == "saida"
            pis = sum(self._somar_item(i, "valor_pis", i["cst_pis"], saida) for i in visao["itens"])
            cofins = sum(
                self._somar_item(i, "valor_cofins", i["cst_cofins"], saida) for i in visao["itens"]
            )
            if saida:
                debito_pis += pis
                debito_cofins += cofins
            else:
                credito_pis += pis
                credito_cofins += cofins

        self._avisar_sobre_os_cst()

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

    def _avisar_sobre_os_cst(self) -> None:
        """Um aviso por motivo, não um por item.

        Num fechamento com centenas de notas, um aviso por item afogaria todos
        os outros — e é justamente aí que os outros importam.
        """
        if self._cst_do_outro_sentido:
            codigos = ", ".join(sorted(self._cst_do_outro_sentido))
            self._resultado.avisos.append(
                f"há itens cujo CST de PIS/Cofins ({codigos}) é do OUTRO sentido da "
                "operação — numa entrada, o CST que vem no XML é o do fornecedor, e "
                "quem escritura precisa classificar a aquisição com o CST do "
                "adquirente (50 a 56 dão crédito, 70 a 75 não). Os valores ENTRARAM "
                "na apuração; use `sped-hub fiscal classificar` antes de transmitir"
            )
        if self._cst_indefinido:
            codigos = ", ".join(sorted(self._cst_indefinido))
            self._resultado.avisos.append(
                f"há itens com CST de PIS/Cofins que não define o tratamento ({codigos}): "
                "os valores destacados ENTRARAM na apuração, porque é o que se fazia "
                "antes de haver a conferência. Confira se é isso mesmo"
            )
        if self._descartados:
            total = sum(valor for _, valor in self._descartados)
            codigos = ", ".join(sorted({cst for cst, _ in self._descartados}))
            self._resultado.avisos.append(
                f"{formatar_valor(total)} de PIS/Cofins destacado foi DESCARTADO da "
                f"apuração: são itens com CST {codigos}, que não gera débito nem "
                "crédito. Documento com valor destacado nesses CST está inconsistente "
                "— confira a origem antes de transmitir"
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
