"""Gerador da EFD ICMS/IPI a partir dos documentos importados.

Monta o arquivo a partir do que a Central tem: os documentos normalizados mais
os ajustes — a camada efetiva. O que o operador corrigiu na tela é o que sai
no arquivo, e o XML original continua intocado para conferência.

**Os cadastros do bloco 0 são derivados dos documentos**, não digitados de
novo. Participantes (0150), unidades (0190) e itens (0200) já estão dentro das
notas; pedir que alguém os recadastre seria pedir para errar. O que não dá para
derivar — o perfil de enquadramento e o indicador de atividade — é cadastro da
empresa, e o gerador recusa gerar sem ele em vez de inventar um padrão.

**O que este gerador NÃO faz**, e é preciso saber antes de usar:

  * inventário (bloco H), ativo imobilizado (bloco G) e o bloco 1 inteiro;
  * documentos de serviço, energia, comunicação e transporte (C500, D100…);
  * ajustes que nascem de um documento (`C197`/`D197`), que compõem os campos
    `VL_TOT_AJ_*` do E110 — os do período, esses o E111 cobre;
  * substituição tributária apurada (E200 e seguintes).

A apuração do bloco E soma os documentos, carrega o saldo credor da
escrituração transmitida do período anterior e aplica os ajustes cadastrados
para o período (E111). O que segue fora está listado acima e em
`docs/modules/escrituracoes.md`.
"""

from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import AjusteFiscal, DocumentoFiscal, Empresa
from src.documentos.ajustes import valor_efetivo
from src.escrituracoes.ajustes_apuracao import (
    ajustes_do_periodo,
    totais_por_campo,
    utilizacao,
)
from src.escrituracoes.arquivadas import (
    campo_do_registro,
    existe_geracao_antes,
    ultima_transmitida_antes,
)
from src.escrituracoes.base import (
    CampoObrigatorioAusente,
    GeradorBase,
    ResultadoGeracao,
    formatar_data,
    formatar_valor,
    formatar_valor_obrigatorio,
)
from src.escrituracoes.base import texto as _texto
from src.escrituracoes.leiaute import EFD_ICMS

logger = logging.getLogger("sped-hub.escrituracoes")


def _numero(bruto: str) -> float:
    """O inverso de `formatar_valor`: `"1000,00"` → `1000.0`, vazio → `0.0`."""
    if not bruto:
        return 0.0
    try:
        return float(bruto.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


# O `COD_VER` do 0000 **depende do período**, e era fixo em "018" aqui.
#
# O campo é "Código da versão do leiaute conforme a tabela indicada no Ato
# Cotepe", e o validador o confere contra a data do `DT_FIN`: versão que não
# vale para o período faz o arquivo inteiro ser recusado, com a mensagem "A
# versão do leiaute não é válida para o período informado".  Fixo em 018, todo
# arquivo de 2025 em diante saía recusado.
#
# Cada faixa vem da Nota Técnica que institui o leiaute, baixada do portal do
# SPED.  A data está na capa de cada uma, em "Institui o leiaute válido a
# partir de", e o número da versão no cabeçalho de todas as páginas:
#
#   018  a partir de 01/01/2024 — NT 2023.001 v1.2
#   019  a partir de 01/01/2025 — NT 2024.001 v1.0
#   020  a partir de 01/01/2026 — NT 2025.001 v1.0 (Ato COTEPE/ICMS 79/2025)
#
# Continua explícito, e não "descoberto": o que muda é depender do período, que
# é o que o leiaute manda.  Versão nova entra aqui com a NT que a instituiu.
VERSOES_DO_LEIAUTE = (
    (datetime.date(2026, 1, 1), "020"),
    (datetime.date(2025, 1, 1), "019"),
    (datetime.date(2024, 1, 1), "018"),
)


class PeriodoSemLeiaute(ValueError):
    """Período anterior ao leiaute mais antigo que este sistema conhece."""


def cod_ver(data_fim: datetime.date) -> str:
    """A versão do leiaute válida para o período que termina em `data_fim`.

    É o `DT_FIN` que decide, não o `DT_INI`: é contra ele que o validador
    confere.  Um período que atravessa a virada do ano usa a versão do fim.

    Período anterior a 2024 **levanta**, em vez de cair na versão mais antiga
    conhecida.  Devolver `018` para um arquivo de 2020 seria repetir em menor
    escala o defeito que esta função existe para corrigir: um código que o
    validador recusa, escrito com a confiança de quem sabe.
    """
    for inicio, versao in VERSOES_DO_LEIAUTE:
        if data_fim >= inicio:
            return versao
    mais_antiga = VERSOES_DO_LEIAUTE[-1]
    raise PeriodoSemLeiaute(
        f"período terminando em {data_fim:%d/%m/%Y}: o leiaute mais antigo que este "
        f"sistema conhece é o {mais_antiga[1]}, válido a partir de "
        f"{mais_antiga[0]:%d/%m/%Y}. Escriturar período anterior exige acrescentar a "
        "versão em VERSOES_DO_LEIAUTE, com a Nota Técnica que a instituiu"
    )


BLOCOS = ("0", "C", "E", "9")

# IND_PERFIL do 0000 — o perfil de enquadramento, que decide o nível de
# detalhe exigido no arquivo.
PERFIS = {
    "A": "apresentação de todos os documentos, registro a registro",
    "B": "apresentação por totais no período",
    "C": "apresentação por totais mensais (perfil reduzido)",
}

# IND_ATIV do 0000 desta escrituração: binário.  O nome traz a obrigação de
# propósito — existe `ATIVIDADES_CONTRIBUICOES` com o MESMO nome de campo e
# outra tabela, onde "1" quer dizer prestador de serviços.  São dois campos de
# cadastro separados, e chamar qualquer um dos dois só de `ATIVIDADES` é o
# convite exato para o erro.
ATIVIDADES_ICMS = {
    "0": "industrial ou equiparado a industrial",
    "1": "outros",
}


# Modelo da NFC-e.  O Guia a trata à parte em três lugares, e sempre para
# tirá-la de onde a NF-e entra.
NFCE = "65"


def leva_itens_no_arquivo(cabecalho: dict) -> bool:
    """O documento leva registros `C170`?

    Guia Prático da EFD ICMS/IPI 3.2.2, Exceção 2 do `C100`: "Notas Fiscais
    Eletrônicas — NF-e de emissão própria: regra geral, devem ser apresentados
    somente os registros C100 e C190 [...] somente será admitida a informação
    do registro C170 quando também houver sido informado o registro C176,
    C180, C181 ou o Registro C177" — e este gerador não escreve nenhum dos
    quatro.

    O título do próprio `C170` confirma pelo outro lado: "ITENS DO DOCUMENTO
    (CÓDIGO 01, 1B, 04 e 55)", sem o modelo 65, e o texto diz "inclusive em
    operações de entrada de mercadorias acompanhadas de NF-e de emissão de
    terceiros".  É a entrada que pede o item, não a saída.

    Emissão própria aqui é a saída, como no `IND_EMIT` do `C100`.
    """
    if cabecalho["modelo"] == NFCE:
        return False
    if cabecalho["modelo"] == "55":
        return cabecalho["sentido"] == "entrada"
    return True


class GeradorEFDICMS(GeradorBase):
    """Monta a EFD ICMS/IPI de um período."""

    LEIAUTE = EFD_ICMS

    def __init__(
        self,
        session: Session,
        *,
        empresa: Empresa,
        data_inicio: datetime.date,
        data_fim: datetime.date,
        cod_fin: str = "0",
    ):
        self.session = session
        self.empresa = empresa
        self.data_inicio = data_inicio
        self.data_fim = data_fim
        self.cod_fin = cod_fin
        super().__init__()

    # ── Entrada ────────────────────────────────────────────────────────────

    def gerar(self) -> ResultadoGeracao:
        self._conferir_cadastro()
        documentos = self._documentos()
        visoes = [self._visao(d) for d in documentos]

        self._reiniciar([d.id for d in documentos])
        self._bloco_0(visoes)
        self._bloco_c(visoes)
        self._bloco_e(visoes)
        self._bloco_9()

        if not documentos:
            self._resultado.avisos.append(
                "nenhum documento no período — o arquivo sai só com os blocos de abertura"
            )
        self._avisar_frete_sem_modalidade()
        self._avisar_reforma_fora_do_arquivo(visoes)
        return self._resultado

    def _conferir_cadastro(self) -> None:
        faltando = []
        if self.empresa.ind_perfil not in PERFIS:
            faltando.append("ind_perfil (A, B ou C)")
        if self.empresa.ind_ativ not in ATIVIDADES_ICMS:
            faltando.append("ind_ativ (0=industrial, 1=outros)")
        if not self.empresa.ie:
            faltando.append("ie (inscrição estadual)")
        if faltando:
            raise CampoObrigatorioAusente(
                f"a empresa {self.empresa.nome!r} não tem {', '.join(faltando)} — "
                "sem isso o arquivo sai com enquadramento errado, e o validador "
                "aceita porque não tem como saber"
            )

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
        """Documento e itens já com os ajustes aplicados.

        É a razão de o gerador existir em cima da camada efetiva: o que o
        operador corrigiu é o que sai no arquivo.
        """
        ajustes = (
            self.session.execute(
                select(AjusteFiscal).where(AjusteFiscal.documento_id == documento.id)
            )
            .scalars()
            .all()
        )
        do_cabecalho = [a for a in ajustes if a.item_id is None]
        colunas_doc = DocumentoFiscal.__table__.columns
        cabecalho = {c.name: valor_efetivo(documento, c.name, do_cabecalho) for c in colunas_doc}

        itens = []
        for item in documento.itens:
            do_item = [a for a in ajustes if a.item_id == item.id]
            itens.append(
                {c.name: valor_efetivo(item, c.name, do_item) for c in item.__table__.columns}
            )
        return {"documento": documento, "cabecalho": cabecalho, "itens": itens}

    # ── Bloco 0: identificação e cadastros ─────────────────────────────────

    def _bloco_0(self, visoes: Sequence[dict]) -> None:
        e = self.empresa
        self._add(
            "0000",
            cod_ver(self.data_fim),
            self.cod_fin,
            formatar_data(self.data_inicio),
            formatar_data(self.data_fim),
            e.nome,
            e.cnpj,
            "",  # CPF: vazio quando há CNPJ
            e.uf,
            e.ie,
            e.cod_mun,
            e.im or "",
            "",  # SUFRAMA
            e.ind_perfil,
            e.ind_ativ,
        )
        self._add("0001", "0")  # 0 = bloco com dados

        for campos in self._participantes(visoes):
            self._add("0150", *campos)
        for unidade in self._unidades(visoes):
            self._add("0190", unidade, unidade)
        for campos in self._itens(visoes):
            self._add("0200", *campos)

        self._encerrar_bloco("0", "0990")

    def _participantes(self, visoes: Sequence[dict]) -> list[list[str]]:
        """Derivados dos documentos — quem aparece nas notas do período.

        O participante é a contraparte: numa entrada é o emitente, numa saída
        é o destinatário. Cadastrar à mão o que já está na nota seria pedir
        para divergir.
        """
        vistos: dict[str, list[str]] = {}
        for visao in visoes:
            c = visao["cabecalho"]
            # "Não devem ser informados como participantes os CNPJ e CPF
            # apenas citados [...] no C100, quando se tratar de NFC-e" — e o
            # `COD_PART` da NFC-e sai vazio, então não há o que referenciar.
            if c["modelo"] == NFCE:
                continue
            if c["sentido"] == "entrada":
                cnpj, nome, uf, ie = (
                    c["emitente_cnpj"],
                    c["emitente_nome"],
                    c["emitente_uf"],
                    c["emitente_ie"],
                )
            else:
                cnpj, nome, uf, ie = (
                    c["destinatario_cnpj"],
                    c["destinatario_nome"],
                    c["destinatario_uf"],
                    c["destinatario_ie"],
                )
            if not cnpj or cnpj in vistos:
                continue
            vistos[cnpj] = [
                cnpj,  # COD_PART: o próprio CNPJ, estável entre períodos
                _texto(nome),
                "",  # COD_PAIS
                cnpj if len(cnpj) == 14 else "",
                cnpj if len(cnpj) == 11 else "",
                _texto(ie),
                _texto(c["municipio_codigo"]),
                "",  # SUFRAMA
                "",  # ENDERECO
                "",  # NUM
                "",  # COMPL
                "",  # BAIRRO
            ]
            _ = uf
        return list(vistos.values())

    def _unidades(self, visoes: Sequence[dict]) -> list[str]:
        """ "Somente devem constar as unidades de medidas informadas em
        qualquer outro registro" — e quem as informa é o `C170`."""
        vistas = {
            i["unidade"]
            for v in visoes
            if leva_itens_no_arquivo(v["cabecalho"])
            for i in v["itens"]
            if i["unidade"]
        }
        return sorted(vistas)

    def _itens(self, visoes: Sequence[dict]) -> list[list[str]]:
        """ "Somente devem ser apresentados itens referenciados nos demais
        blocos" — a validação do próprio `0200`.

        Quem referencia item é o `COD_ITEM` do `C170`; o `C190` totaliza por
        CST, CFOP e alíquota e não cita item nenhum.  Num período só de NF-e
        de emissão própria este bloco sai vazio, e é o que o Guia manda.
        """
        vistos: dict[str, list[str]] = {}
        for visao in visoes:
            if not leva_itens_no_arquivo(visao["cabecalho"]):
                continue
            for item in visao["itens"]:
                codigo = item["codigo"]
                if not codigo or codigo in vistos:
                    continue
                vistos[codigo] = [
                    codigo,
                    _texto(item["descricao"]),
                    "",  # COD_BARRA
                    "",  # COD_ANT_ITEM
                    _texto(item["unidade"]),
                    "00",  # TIPO_ITEM: 00 = mercadoria para revenda
                    _texto(item["ncm"]),
                    "",  # EX_IPI
                    "",  # COD_GEN
                    "",  # COD_LST
                    formatar_valor(item["aliquota_icms"]),
                    _texto(item["cest"]),
                ]
        return list(vistos.values())

    # ── Bloco C: documentos de mercadoria ──────────────────────────────────

    def _bloco_c(self, visoes: Sequence[dict]) -> None:
        self._add("C001", "0" if visoes else "1")
        for visao in visoes:
            self._documento_c100(visao)
        self._encerrar_bloco("C", "C990")

    def _documento_c100(self, visao: dict) -> None:
        c = visao["cabecalho"]
        entrada = c["sentido"] == "entrada"
        participante = c["emitente_cnpj"] if entrada else c["destinatario_cnpj"]
        # "Quando se tratar de NFC-e (modelo 65), o campo não deve ser
        # preenchido" — validação do campo 04 do C100.
        if c["modelo"] == NFCE:
            participante = ""

        self._add(
            "C100",
            "0" if entrada else "1",  # IND_OPER
            "1" if entrada else "0",  # IND_EMIT: 0=própria, 1=terceiros
            _texto(participante),
            _texto(c["modelo"]),
            "00" if c["situacao"] == "autorizado" else "02",  # COD_SIT
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
            "",  # VL_BC_ICMS_ST
            formatar_valor(c["valor_icms_st"]),
            formatar_valor(c["valor_ipi"]),
            formatar_valor(c["valor_pis"]),
            formatar_valor(c["valor_cofins"]),
            "",  # VL_PIS_ST
            "",  # VL_COFINS_ST
        )

        if leva_itens_no_arquivo(c):
            for item in visao["itens"]:
                self._item_c170(item)
        for campos in self._analitico_c190(visao):
            self._add("C190", *campos)

    def _item_c170(self, item: dict) -> None:
        self._add(
            "C170",
            _texto(item["numero_item"]),
            _texto(item["codigo"]),
            _texto(item["descricao"]),
            formatar_valor(item["quantidade"]),
            _texto(item["unidade"]),
            formatar_valor(item["valor_total"]),
            formatar_valor(item["valor_desconto"]),
            "0",  # IND_MOV: 0 = movimentação física sim
            f"{_texto(item['origem_mercadoria']) or '0'}{_texto(item['cst_icms'])}",
            _texto(item["cfop"]),
            "",  # COD_NAT
            formatar_valor(item["base_icms"]),
            formatar_valor(item["aliquota_icms"]),
            formatar_valor(item["valor_icms"]),
            formatar_valor(item["base_icms_st"]),
            "",  # ALIQ_ST
            formatar_valor(item["valor_icms_st"]),
            "",  # IND_APUR
            _texto(item["cst_ipi"]),
            "",  # COD_ENQ
            "",  # VL_BC_IPI
            "",  # ALIQ_IPI
            formatar_valor(item["valor_ipi"]),
            _texto(item["cst_pis"]),
            formatar_valor(item["base_pis"]),
            formatar_valor(item["aliquota_pis"]),
            "",  # QUANT_BC_PIS
            "",  # ALIQ_PIS_QUANT
            formatar_valor(item["valor_pis"]),
            _texto(item["cst_cofins"]),
            formatar_valor(item["base_cofins"]),
            formatar_valor(item["aliquota_cofins"]),
            "",  # QUANT_BC_COFINS
            "",  # ALIQ_COFINS_QUANT
            formatar_valor(item["valor_cofins"]),
            "",  # COD_CTA
            "",  # VL_ABAT_NT
        )

    def _analitico_c190(self, visao: dict) -> list[list[str]]:
        """O consolidado por CST, CFOP e alíquota.

        O validador confere o C190 contra a soma dos C170 do documento. Somar
        errado aqui invalida a nota inteira, e é o erro mais comum de gerador
        próprio — por isso a soma sai dos mesmos valores efetivos que
        alimentaram os C170, e não de uma segunda leitura.
        """
        grupos: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for item in visao["itens"]:
            chave = (
                f"{_texto(item['origem_mercadoria']) or '0'}{_texto(item['cst_icms'])}",
                _texto(item["cfop"]),
                formatar_valor(item["aliquota_icms"]),
            )
            grupo = grupos[chave]
            grupo["valor_operacao"] += item["valor_total"] or 0.0
            grupo["base_icms"] += item["base_icms"] or 0.0
            grupo["valor_icms"] += item["valor_icms"] or 0.0
            grupo["base_icms_st"] += item["base_icms_st"] or 0.0
            grupo["valor_icms_st"] += item["valor_icms_st"] or 0.0
            grupo["valor_ipi"] += item["valor_ipi"] or 0.0

        linhas = []
        for (cst, cfop, aliquota), soma in grupos.items():
            linhas.append(
                [
                    cst,
                    cfop,
                    # ALIQ_ICMS é "OC": só sai quando há alíquota.  Os sete
                    # campos de valor abaixo são "O", e num C190 de operação
                    # isenta todos valem zero — que se escreve, não se omite.
                    aliquota,
                    formatar_valor_obrigatorio(soma["valor_operacao"]),
                    formatar_valor_obrigatorio(soma["base_icms"]),
                    formatar_valor_obrigatorio(soma["valor_icms"]),
                    formatar_valor_obrigatorio(soma["base_icms_st"]),
                    formatar_valor_obrigatorio(soma["valor_icms_st"]),
                    formatar_valor_obrigatorio(0.0),  # VL_RED_BC
                    formatar_valor_obrigatorio(soma["valor_ipi"]),
                    "",  # COD_OBS: "OC"
                ]
            )
        return linhas

    # ── Bloco E: apuração do ICMS ──────────────────────────────────────────

    def _bloco_e(self, visoes: Sequence[dict]) -> None:
        """A apuração sai quando há movimento **ou** saldo credor a carregar.

        Mês sem nota mas com crédito acumulado precisa do E110 assim mesmo: é
        ele que transporta o saldo para o mês seguinte. Sem essa linha o
        crédito desaparece da cadeia — e some de vez, porque o mês seguinte
        procura o `VL_SLD_CREDOR_TRANSPORTAR` do anterior e não acha.
        """
        credor_anterior = self._saldo_credor_anterior()
        ajustes = ajustes_do_periodo(
            self.session,
            empresa_id=self.empresa.id,
            data_inicio=self.data_inicio,
            data_fim=self.data_fim,
        )
        tem_apuracao = bool(visoes) or credor_anterior > 0 or bool(ajustes)

        self._add("E001", "0" if tem_apuracao else "1")
        if tem_apuracao:
            self._add("E100", formatar_data(self.data_inicio), formatar_data(self.data_fim))
            self._apuracao_e110(visoes, credor_anterior, ajustes)
            for ajuste in ajustes:
                self._add(
                    "E111",
                    ajuste.cod_aj,
                    _texto(ajuste.descricao),
                    formatar_valor(ajuste.valor),
                )
        self._encerrar_bloco("E", "E990")

    def _saldo_credor_anterior(self) -> float:
        """O `VL_SLD_CREDOR_TRANSPORTAR` do período anterior, se houver.

        O leiaute é explícito: o `VL_SLD_CREDOR_ANT` de um período tem de ser
        igual ao `VL_SLD_CREDOR_TRANSPORTAR` do período anterior. Três decisões
        cercam isso:

        **Só de escrituração transmitida.** Uma geração que ninguém entregou
        não estabelece saldo nenhum perante o Fisco — e é justamente a que
        sobra em maior número, porque gerar para conferir é barato.

        **Só se o período for contíguo.** Se o anterior transmitido termina
        antes da véspera, há um mês sem entrega no meio, e o saldo daquele
        arquivo já não é o de agora. Carregá-lo mesmo assim produziria imposto
        a menos com aparência de conta certa.

        **Lido do arquivo, não recalculado.** O que vale é o número que foi
        declarado; regerar o mês anterior hoje pode dar outro.
        """
        anterior = ultima_transmitida_antes(
            self.session,
            empresa_id=self.empresa.id,
            tipo="efd_icms",
            data=self.data_inicio,
        )

        if anterior is None:
            houve = existe_geracao_antes(
                self.session,
                empresa_id=self.empresa.id,
                tipo="efd_icms",
                data=self.data_inicio,
            )
            self._resultado.avisos.append(
                "há escrituração anterior gerada, mas NENHUMA marcada como transmitida: "
                "o saldo credor anterior saiu ZERADO. Se o mês passado foi entregue, "
                "marque com `sped-hub fiscal transmitida` e gere de novo"
                if houve
                else "não há escrituração anterior transmitida no sistema: o saldo credor "
                "anterior saiu ZERADO. Se a empresa vinha de saldo credor, o imposto a "
                "recolher está MAIOR do que o devido — informe o saldo à mão"
            )
            return 0.0

        vespera = self.data_inicio - datetime.timedelta(days=1)
        if anterior.data_fim != vespera:
            self._resultado.avisos.append(
                f"a última escrituração transmitida (#{anterior.id}) termina em "
                f"{anterior.data_fim} e este período começa em {self.data_inicio}: há "
                "intervalo sem entrega no meio, e o saldo credor daquele arquivo não "
                "vale para este. Saiu ZERADO"
            )
            return 0.0

        saldo = _numero(campo_do_registro(anterior, "E110", "VL_SLD_CREDOR_TRANSPORTAR"))
        if saldo:
            self._resultado.avisos.append(
                f"saldo credor anterior de {saldo:.2f} veio da escrituração #{anterior.id}, "
                f"transmitida em {anterior.transmitida_em:%d/%m/%Y} — é o "
                "VL_SLD_CREDOR_TRANSPORTAR daquele arquivo, não um recálculo"
            )
        return saldo

    def _apuracao_e110(self, visoes: Sequence[dict], credor_anterior: float, ajustes: list) -> None:
        """A fórmula do E110, inteira.

        `VL_SLD_APURADO` = débitos + ajustes a débito + estornos de crédito
        − créditos − ajustes a crédito − estornos de débito − saldo credor
        anterior; e `VL_ICMS_RECOLHER` = saldo apurado − deduções.

        Os ajustes do período vão para `VL_TOT_AJ_DEBITOS` (campo 04) e
        `VL_TOT_AJ_CREDITOS` (campo 08). Quem fica zerado é o par de campos
        03 e 07 — `VL_AJ_DEBITOS` e `VL_AJ_CREDITOS` —, que o Guia descreve
        como "ajustes decorrentes do documento fiscal": são os `C197`/`D197`,
        que este gerador não escreve. Ver o roadmap.

        Zerado, e não vazio: no Bloco E todo campo numérico é obrigatório e
        sai com valor ou com zero — ver `formatar_valor_obrigatorio`.
        """
        debitos = credito = 0.0
        for visao in visoes:
            valor = sum(i["valor_icms"] or 0.0 for i in visao["itens"])
            if visao["cabecalho"]["sentido"] == "saida":
                debitos += valor
            else:
                credito += valor

        do_ajuste = totais_por_campo(ajustes)

        def ajustado(campo: str) -> float:
            return do_ajuste.get(campo, 0.0)

        saldo = (
            debitos
            + ajustado("VL_TOT_AJ_DEBITOS")
            + ajustado("VL_ESTORNOS_CRED")
            - credito
            - ajustado("VL_TOT_AJ_CREDITOS")
            - ajustado("VL_ESTORNOS_DEB")
            - credor_anterior
        )
        # As deduções entram DEPOIS do saldo apurado, não dentro dele: é a
        # diferença entre o que se apurou e o que se recolhe.
        a_recolher = saldo - ajustado("VL_TOT_DED")

        self._add(
            "E110",
            formatar_valor_obrigatorio(debitos),
            # Campos 03 e 07: ajustes decorrentes do documento fiscal
            # (C197/D197), que este gerador não escreve.  Os do período são os
            # campos 04 e 08 — o Guia diz isso no cabeçalho do próprio E111.
            formatar_valor_obrigatorio(0.0),
            formatar_valor_obrigatorio(ajustado("VL_TOT_AJ_DEBITOS")),
            formatar_valor_obrigatorio(ajustado("VL_ESTORNOS_CRED")),
            formatar_valor_obrigatorio(credito),
            formatar_valor_obrigatorio(0.0),
            formatar_valor_obrigatorio(ajustado("VL_TOT_AJ_CREDITOS")),
            formatar_valor_obrigatorio(ajustado("VL_ESTORNOS_DEB")),
            formatar_valor_obrigatorio(credor_anterior),
            formatar_valor_obrigatorio(saldo if saldo > 0 else 0.0),
            formatar_valor_obrigatorio(ajustado("VL_TOT_DED")),
            formatar_valor_obrigatorio(a_recolher if a_recolher > 0 else 0.0),
            formatar_valor_obrigatorio(-saldo if saldo < 0 else 0.0),
            formatar_valor_obrigatorio(ajustado("DEB_ESP")),
        )
        self._avisar_sobre_os_ajustes(ajustes)

    def _avisar_sobre_os_ajustes(self, ajustes: list) -> None:
        """O que entrou e o que não entrou, com o número.

        Sem ajuste nenhum, o aviso diz que a apuração é a soma direta — que é
        a informação certa para quem tem benefício fiscal e ainda não o
        cadastrou. Com ajustes, dizer isso seria mentira.
        """
        if not ajustes:
            self._resultado.avisos.append(
                "não há ajustes de apuração cadastrados no período: o E110 é a soma "
                "direta dos documentos. Empresa com benefício, crédito outorgado, "
                "estorno ou dedução precisa cadastrá-los com `sped-hub fiscal ajuste`"
            )
            return

        fora = [a for a in ajustes if utilizacao(a.cod_aj)[1] is None or a.cod_aj[2:3] != "0"]
        if fora:
            codigos = ", ".join(sorted({a.cod_aj for a in fora}))
            total = sum(a.valor for a in fora)
            self._resultado.avisos.append(
                f"{len(fora)} ajuste(s) saíram no E111 mas NÃO entraram na apuração do "
                f"E110 ({codigos}, somando {formatar_valor(total)}): são de controle "
                "extra-apuração ou de outra apuração (ST, DIFAL, FCP), que tem registro "
                "próprio e este gerador não escreve"
            )
