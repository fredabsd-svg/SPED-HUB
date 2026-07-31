"""O CST decide se o valor destacado entra na apuração do PIS/Cofins.

Até aqui a apuração somava o valor destacado em cada item, qualquer que fosse
o CST. Isso produz contribuição errada em duas direções, e nas duas o arquivo
sai estruturalmente válido — o validador não recusa, e a diferença volta como
cobrança com multa:

  * **entrada com CST 70 a 75** não dá direito a crédito. Se o fornecedor
    destacou PIS na nota e o valor for somado como crédito, a contribuição
    devida sai a MENOR;
  * **saída com CST 04, 06, 07, 08 ou 09** não gera débito — monofásica já
    paga no início da cadeia, alíquota zero, isenta, sem incidência,
    suspensão.

O que estes testes protegem, além disso:

  * **numa entrada, o CST do XML é o do fornecedor.** O documento é dele. Quem
    escritura tem de classificar a aquisição com o CST do adquirente, e o
    gerador não decide por ele: soma e avisa, apontando o comando que resolve;
  * **valor descartado é dito em voz alta.** Documento com contribuição
    destacada num item cujo CST diz que não há é documento inconsistente.
"""

from __future__ import annotations

import datetime

import pytest

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ORIGEM_USUARIO, ImportadorDeDocumentos, aplicar_ajuste
from src.escrituracoes import GeradorEFDContribuicoes
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)
CNPJ_EMPRESA = "98765432000198"
CNPJ_TERCEIRO = "12345678000195"


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'cst.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def empresa(sessao):
    escritorio = Escritorio(nome="Teste", slug="teste")
    sessao.add(escritorio)
    sessao.commit()
    e = Empresa(
        cnpj=CNPJ_EMPRESA,
        nome="COMERCIO EXEMPLO LTDA",
        uf="TO",
        ie="293456789",
        cod_mun="1721000",
        ind_perfil="A",
        ind_ativ="1",
        ind_ativ_contribuicoes="2",
        cod_inc_trib="1",  # não cumulativo: há crédito a descontar
        escritorio_id=escritorio.id,
    )
    sessao.add(e)
    sessao.commit()
    return e


def importar(sessao, empresa, *, saida: bool, cst: str, **kwargs) -> DocumentoFiscal:
    """Uma nota com o CST desejado nos itens, no sentido desejado.

    O CST é posto por ajuste e não pelo XML: é assim que ele chega ao arquivo
    na vida real, porque numa entrada o CST do XML é o do fornecedor e quem
    escritura reclassifica.
    """
    partes = (
        {"emitente_cnpj": CNPJ_EMPRESA, "destinatario_cnpj": CNPJ_TERCEIRO}
        if saida
        else {"emitente_cnpj": CNPJ_TERCEIRO, "destinatario_cnpj": CNPJ_EMPRESA}
    )
    ocorrencia = ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
        nfe_xml(**partes, **kwargs)
    )
    sessao.commit()

    documento = sessao.get(DocumentoFiscal, ocorrencia.documento_id)
    for item in documento.itens:
        for campo in ("cst_pis", "cst_cofins"):
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo=campo,
                valor_novo=cst,
                origem=ORIGEM_USUARIO,
            )
    sessao.commit()
    return documento


def gerar(sessao, empresa):
    return GeradorEFDContribuicoes(
        sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM
    ).gerar()


def m200(resultado, nome: str) -> str:
    registro = next(r for r in resultado.registros if r.tipo == "M200")
    return registro.campos[EFD_CONTRIBUICOES["M200"].index(nome)]


def avisos_sobre(resultado, trecho: str) -> list[str]:
    return [a for a in resultado.avisos if trecho in a]


# ── Entradas: o CST decide se há crédito ───────────────────────────────────


@pytest.mark.parametrize("cst", ["50", "53", "56", "60", "67"])
def test_entrada_com_cst_de_credito_gera_credito(sessao, empresa, cst):
    """50 a 56 dão crédito; 60 a 67 dão crédito presumido.

    A ausência de aviso faz parte da asserção: CST que falte na tabela cai no
    caminho de "não define o tratamento", que também soma — o número sozinho
    não distingue tabela completa de tabela furada.
    """
    importar(sessao, empresa, saida=False, cst=cst)
    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CRED_DESC") == "16,50"
    assert not avisos_sobre(resultado, "não define o tratamento")


@pytest.mark.parametrize("cst", ["70", "71", "72", "73", "74", "75"])
def test_entrada_sem_direito_a_credito_nao_gera_credito(sessao, empresa, cst):
    """Somar aqui produz contribuição a MENOR, e o validador aceita."""
    importar(sessao, empresa, saida=False, cst=cst)

    assert m200(gerar(sessao, empresa), "VL_TOT_CRED_DESC") == ""


def test_o_valor_descartado_e_dito_em_voz_alta(sessao, empresa):
    """Nota com PIS destacado num CST que não dá crédito está inconsistente."""
    importar(sessao, empresa, saida=False, cst="70")

    avisos = avisos_sobre(gerar(sessao, empresa), "DESCARTADO")

    assert len(avisos) == 1
    assert "70" in avisos[0]
    assert "92,50" in avisos[0], "16,50 de PIS + 76,00 de Cofins"


def test_descarte_sem_valor_destacado_nao_vira_aviso(sessao, empresa):
    """Revenda monofásica traz zero destacado: não há o que dizer.

    O aviso existe para a inconsistência — valor destacado num CST que diz
    que não há contribuição. Sem valor, não há inconsistência, e repetir o
    aviso em todo item monofásico afogaria os que importam.
    """
    documento = importar(sessao, empresa, saida=True, cst="04")
    for item in documento.itens:
        for campo in ("valor_pis", "valor_cofins"):
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo=campo,
                valor_novo=0.0,
                origem=ORIGEM_USUARIO,
            )
    sessao.commit()

    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CONT_NC_PER") == ""
    assert not avisos_sobre(resultado, "DESCARTADO")


# ── Saídas: o CST decide se há débito ──────────────────────────────────────


@pytest.mark.parametrize("cst", ["01", "02", "03", "05"])
def test_saida_tributada_gera_debito(sessao, empresa, cst):
    importar(sessao, empresa, saida=True, cst=cst)
    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CONT_NC_PER") == "16,50"
    assert not avisos_sobre(resultado, "não define o tratamento")


@pytest.mark.parametrize("cst", ["04", "06", "07", "08", "09"])
def test_saida_sem_incidencia_nao_gera_debito(sessao, empresa, cst):
    """Monofásica já foi paga no início da cadeia; as outras não incidem."""
    importar(sessao, empresa, saida=True, cst=cst)

    assert m200(gerar(sessao, empresa), "VL_TOT_CONT_NC_PER") == ""


# ── O CST do outro sentido ─────────────────────────────────────────────────


def test_entrada_com_cst_de_saida_soma_e_aponta_a_classificacao(sessao, empresa):
    """É o estado de toda nota recém-importada: o CST é o do fornecedor."""
    importar(sessao, empresa, saida=False, cst="01")

    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CRED_DESC") == "16,50", "soma, como sempre fez"
    aviso = avisos_sobre(resultado, "OUTRO sentido")[0]
    assert "fiscal classificar" in aviso
    assert "50 a 56" in aviso


def test_saida_com_cst_de_entrada_tambem_e_apontada(sessao, empresa):
    """A troca vale nos dois sentidos."""
    importar(sessao, empresa, saida=True, cst="50")

    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CONT_NC_PER") == "16,50"
    assert avisos_sobre(resultado, "OUTRO sentido")


def test_o_aviso_do_outro_sentido_nao_se_confunde_com_o_de_indefinido(sessao, empresa):
    """São dois problemas, com dois conselhos diferentes."""
    importar(sessao, empresa, saida=False, cst="01")

    resultado = gerar(sessao, empresa)

    assert avisos_sobre(resultado, "OUTRO sentido")
    assert not avisos_sobre(resultado, "não define o tratamento")


# ── CST que não decide nada ────────────────────────────────────────────────


@pytest.mark.parametrize("cst", ["49", "98", "99"])
def test_outras_operacoes_somam_e_avisam(sessao, empresa, cst):
    """ "Outras operações" não diz o tratamento; quem sabe é quem escriturou."""
    importar(sessao, empresa, saida=True, cst=cst)

    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CONT_NC_PER") == "16,50"
    assert avisos_sobre(resultado, "não define o tratamento")


def test_cst_vazio_soma_e_avisa(sessao, empresa):
    """Documento sem CST não pode travar o mês, mas não pode passar calado."""
    importar(sessao, empresa, saida=True, cst="")

    resultado = gerar(sessao, empresa)

    assert m200(resultado, "VL_TOT_CONT_NC_PER") == "16,50"
    assert avisos_sobre(resultado, "(vazio)")


def test_cst_fora_de_qualquer_tabela_soma_e_avisa(sessao, empresa):
    importar(sessao, empresa, saida=True, cst="88")

    assert avisos_sobre(gerar(sessao, empresa), "88")


# ── O aviso é por motivo, não por item ─────────────────────────────────────


def test_um_aviso_por_motivo_e_nao_um_por_item(sessao, empresa):
    """Num fechamento com centenas de notas, um por item afoga os outros."""
    importar(sessao, empresa, saida=False, cst="70", numero="1", chave="1" * 44)
    importar(sessao, empresa, saida=False, cst="70", numero="2", chave="2" * 44, itens=3)

    resultado = gerar(sessao, empresa)

    assert len(avisos_sobre(resultado, "DESCARTADO")) == 1


def test_gerar_duas_vezes_nao_soma_os_descartes_da_primeira(sessao, empresa):
    """O segundo arquivo não pode acusar valor que já foi acusado."""
    importar(sessao, empresa, saida=False, cst="70")
    gerador = GeradorEFDContribuicoes(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM)

    gerador.gerar()
    segunda = gerador.gerar()

    assert "92,50" in avisos_sobre(segunda, "DESCARTADO")[0], "não 185,00"


def test_cada_contribuicao_le_o_seu_proprio_cst(sessao, empresa):
    """`cst_pis` e `cst_cofins` são campos separados no leiaute.

    Ler os dois do mesmo campo passaria despercebido em todo documento onde
    eles coincidem — que é o caso comum, e por isso o teste faz questão de
    divergi-los.
    """
    documento = importar(sessao, empresa, saida=False, cst="50")
    for item in documento.itens:
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cst_cofins",
            valor_novo="70",
            origem=ORIGEM_USUARIO,
        )
    sessao.commit()

    resultado = gerar(sessao, empresa)
    m600 = next(r for r in resultado.registros if r.tipo == "M600")

    assert m200(resultado, "VL_TOT_CRED_DESC") == "16,50", "PIS com CST 50: crédito"
    assert (
        m600.campos[EFD_CONTRIBUICOES["M200"].index("VL_TOT_CRED_DESC")] == ""
    ), "Cofins com CST 70: sem crédito"


# ── O regime continua mandando ─────────────────────────────────────────────


def test_no_cumulativo_o_credito_some_mesmo_com_cst_de_credito(sessao):
    """A conferência de CST não afrouxa a regra do regime."""
    escritorio = Escritorio(nome="T2", slug="t2")
    sessao.add(escritorio)
    sessao.commit()
    empresa = Empresa(
        cnpj=CNPJ_EMPRESA,
        nome="PRESUMIDO LTDA",
        uf="TO",
        ie="293456789",
        cod_mun="1721000",
        ind_perfil="A",
        ind_ativ="1",
        ind_ativ_contribuicoes="2",
        cod_inc_trib="2",  # cumulativo
        escritorio_id=escritorio.id,
    )
    sessao.add(empresa)
    sessao.commit()
    importar(sessao, empresa, saida=False, cst="50")

    assert m200(gerar(sessao, empresa), "VL_TOT_CRED_DESC") == ""
