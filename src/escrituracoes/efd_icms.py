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
  * ajustes de apuração por código da tabela 5.1.1 (E111 e vizinhos);
  * substituição tributária apurada (E200 e seguintes).

A apuração do bloco E é a soma direta dos débitos e créditos dos documentos
escriturados. Empresa com ajuste, benefício ou saldo credor anterior precisa
conferir e complementar — está registrado em `docs/escrituracoes.md`.
"""

from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import AjusteFiscal, DocumentoFiscal, Empresa
from src.documentos.ajustes import valor_efetivo

logger = logging.getLogger("sped-hub.escrituracoes")

# Versão do leiaute declarada no 0000.  Fixa e explícita: o leiaute muda por
# ato normativo, e um gerador que "descobre" a versão sozinho erra calado.
COD_VER = "018"

BLOCOS = ("0", "C", "E", "9")

_PERFIS = {"A", "B", "C"}
_ATIVIDADES = {"0", "1"}


class CampoObrigatorioAusente(ValueError):
    """Falta cadastro sem o qual o arquivo sairia errado — e aceito.

    O validador do Fisco não recusa um perfil de enquadramento errado: ele não
    tem como saber qual é o certo.  O erro só aparece meses depois, em
    intimação.  Por isso o gerador para aqui em vez de assumir um padrão.
    """


# ── Formatação do leiaute ──────────────────────────────────────────────────


def formatar_valor(valor: float | Decimal | None) -> str:
    """Duas casas, vírgula decimal, sem separador de milhar.

    Zero vira campo vazio: o leiaute trata valor ausente e valor zero como a
    mesma coisa na maioria dos campos, e escrever `0,00` onde o validador
    espera vazio gera advertência.
    """
    if valor is None:
        return ""
    numero = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if numero == 0:
        return ""
    return f"{numero:.2f}".replace(".", ",")


def formatar_data(data: datetime.date | None) -> str:
    """ddmmaaaa — o formato do leiaute, sem separador."""
    return data.strftime("%d%m%Y") if data else ""


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


@dataclass
class Registro:
    """Uma linha do arquivo.

    Guardar os campos em lista, e só juntá-los na hora de escrever, é o que
    permite contar e conferir antes de gerar o texto final.
    """

    tipo: str
    campos: list[str] = field(default_factory=list)

    def linha(self) -> str:
        return "|" + "|".join([self.tipo, *self.campos]) + "|"


@dataclass
class ResultadoGeracao:
    """O arquivo e o que se precisa saber sobre ele."""

    registros: list[Registro] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def total_linhas(self) -> int:
        return len(self.registros)

    def contagem_por_tipo(self) -> dict[str, int]:
        contagem: dict[str, int] = defaultdict(int)
        for registro in self.registros:
            contagem[registro.tipo] += 1
        return dict(contagem)

    def texto(self) -> str:
        """O arquivo, com quebra de linha CRLF.

        O leiaute do SPED pede CRLF; gerar com LF faz alguns validadores
        recusarem o arquivo inteiro sem dizer por quê.
        """
        return "\r\n".join(r.linha() for r in self.registros) + "\r\n"


class GeradorEFDICMS:
    """Monta a EFD ICMS/IPI de um período."""

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
        self._resultado = ResultadoGeracao()

    # ── Entrada ────────────────────────────────────────────────────────────

    def gerar(self) -> ResultadoGeracao:
        self._conferir_cadastro()
        documentos = self._documentos()
        visoes = [self._visao(d) for d in documentos]

        self._resultado = ResultadoGeracao()
        self._bloco_0(visoes)
        self._bloco_c(visoes)
        self._bloco_e(visoes)
        self._bloco_9()

        if not documentos:
            self._resultado.avisos.append(
                "nenhum documento no período — o arquivo sai só com os blocos de abertura"
            )
        return self._resultado

    def _conferir_cadastro(self) -> None:
        faltando = []
        if self.empresa.ind_perfil not in _PERFIS:
            faltando.append("ind_perfil (A, B ou C)")
        if self.empresa.ind_ativ not in _ATIVIDADES:
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

    def _add(self, tipo: str, *campos: Any) -> None:
        self._resultado.registros.append(Registro(tipo, [_texto(c) for c in campos]))

    # ── Bloco 0: identificação e cadastros ─────────────────────────────────

    def _bloco_0(self, visoes: Sequence[dict]) -> None:
        e = self.empresa
        self._add(
            "0000",
            COD_VER,
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
        vistas = {i["unidade"] for v in visoes for i in v["itens"] if i["unidade"]}
        return sorted(vistas)

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
                    aliquota,
                    formatar_valor(soma["valor_operacao"]),
                    formatar_valor(soma["base_icms"]),
                    formatar_valor(soma["valor_icms"]),
                    formatar_valor(soma["base_icms_st"]),
                    formatar_valor(soma["valor_icms_st"]),
                    "",  # VL_RED_BC
                    formatar_valor(soma["valor_ipi"]),
                    "",  # COD_OBS
                ]
            )
        return linhas

    # ── Bloco E: apuração do ICMS ──────────────────────────────────────────

    def _bloco_e(self, visoes: Sequence[dict]) -> None:
        self._add("E001", "0" if visoes else "1")
        if visoes:
            self._add("E100", formatar_data(self.data_inicio), formatar_data(self.data_fim))
            self._apuracao_e110(visoes)
        self._encerrar_bloco("E", "E990")

    def _apuracao_e110(self, visoes: Sequence[dict]) -> None:
        """Débito das saídas menos crédito das entradas.

        Soma direta, sem ajuste da tabela 5.1.1 e sem saldo credor anterior —
        que este gerador não conhece. Empresa que tenha qualquer um dos dois
        precisa complementar; está dito em `docs/escrituracoes.md` e num aviso
        do resultado.
        """
        debitos = credito = 0.0
        for visao in visoes:
            valor = sum(i["valor_icms"] or 0.0 for i in visao["itens"])
            if visao["cabecalho"]["sentido"] == "saida":
                debitos += valor
            else:
                credito += valor

        saldo = debitos - credito
        self._add(
            "E110",
            formatar_valor(debitos),
            "",  # VL_AJ_DEBITOS
            "",  # VL_TOT_AJ_DEBITOS
            "",  # VL_ESTORNOS_CRED
            formatar_valor(credito),
            "",  # VL_AJ_CREDITOS
            "",  # VL_TOT_AJ_CREDITOS
            "",  # VL_ESTORNOS_DEB
            "",  # VL_SLD_CREDOR_ANT
            formatar_valor(saldo) if saldo > 0 else "",
            "",  # VL_TOT_DED
            formatar_valor(saldo) if saldo > 0 else "",
            formatar_valor(-saldo) if saldo < 0 else "",
        )
        self._resultado.avisos.append(
            "apuração do E110 é a soma direta dos documentos: não inclui ajustes da "
            "tabela 5.1.1, saldo credor anterior nem deduções — confira antes de transmitir"
        )

    # ── Bloco 9: controle ──────────────────────────────────────────────────

    def _encerrar_bloco(self, bloco: str, tipo_encerramento: str) -> None:
        """`|X990|n|`, onde n conta o próprio encerramento.

        Contar antes de acrescentar a linha deixaria o total um a menos, e o
        validador recusa o arquivo inteiro por causa disso.
        """
        do_bloco = sum(1 for r in self._resultado.registros if r.tipo.startswith(bloco))
        self._add(tipo_encerramento, do_bloco + 1)

    def _bloco_9(self) -> None:
        """O bloco que conta os outros — e a si mesmo.

        É onde gerador próprio erra: o 9900 tem de contar também os registros
        do bloco 9, inclusive os 9900 que ainda vão ser escritos, o 9990 e o
        9999. A ordem aqui existe para fechar essa conta sem chute.
        """
        self._add("9001", "0")

        contagem = self._resultado.contagem_por_tipo()
        # Um 9900 para cada tipo já presente, mais os que este bloco produz.
        tipos = sorted(contagem)
        # +1 pelo 9900 do próprio "9900", +1 pelo 9990, +1 pelo 9999.
        contagem["9900"] = len(tipos) + 3
        contagem["9990"] = 1
        contagem["9999"] = 1

        for tipo in sorted(contagem):
            self._add("9900", tipo, contagem[tipo])

        do_bloco_9 = sum(1 for r in self._resultado.registros if r.tipo.startswith("9"))
        self._add("9990", do_bloco_9 + 2)  # +9990 +9999
        self._add("9999", len(self._resultado.registros) + 1)
