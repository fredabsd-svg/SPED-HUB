"""O espelho da escrituração — ler o arquivo antes de transmitir.

O que estes testes protegem:

  * **o espelho é lido dos registros, não do banco.** É a decisão que dá
    sentido ao módulo, e a única forma de testá-la é adulterar o registro sem
    tocar no banco e exigir que o espelho mostre o registro adulterado. Um
    espelho montado a partir dos documentos concordaria com o banco mesmo
    quando o gerador discorda dele — escondendo o erro que ele existe para
    mostrar;
  * **as conferências recalculam a partir do arquivo**, e por isso acusam
    divergência que o gerador não acusaria;
  * **o regime muda o que se confere** na EFD-Contribuições: no cumulativo não
    há crédito, e conferir pelo mesmo campo acusaria toda empresa do lucro
    presumido de estar errada.
"""

from __future__ import annotations

import datetime

import pytest

from src.db.models import Empresa, Escritorio, criar_engine, get_session, init_db
from src.documentos import ImportadorDeDocumentos
from src.escrituracoes import (
    Espelho,
    GeradorEFDContribuicoes,
    GeradorEFDICMS,
    TipoSemLeiaute,
    espelho,
)
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES, EFD_ICMS
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'espelho.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


def _empresa(sessao, **kwargs) -> Empresa:
    escritorio = Escritorio(nome="Teste", slug="teste")
    sessao.add(escritorio)
    sessao.commit()
    campos = {
        "cnpj": "98765432000198",
        "nome": "COMERCIO EXEMPLO LTDA",
        "uf": "TO",
        "ie": "293456789",
        "cod_mun": "1721000",
        "ind_perfil": "A",
        "ind_ativ": "1",
        "ind_ativ_contribuicoes": "2",
        "cod_inc_trib": "1",
        "escritorio_id": escritorio.id,
    }
    campos.update(kwargs)
    e = Empresa(**campos)
    sessao.add(e)
    sessao.commit()
    return e


@pytest.fixture
def empresa(sessao):
    return _empresa(sessao)


def importar(sessao, empresa, **kwargs) -> None:
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(nfe_xml(**kwargs))
    sessao.commit()


def uma_entrada_e_uma_saida(sessao, empresa) -> None:
    """Uma nota de cada sentido — a apuração só é interessante com as duas."""
    importar(sessao, empresa, numero="10", chave="1" * 44, itens=2)
    importar(
        sessao,
        empresa,
        numero="11",
        chave="2" * 44,
        emitente_cnpj="98765432000198",
        destinatario_cnpj="12345678000195",
    )


def gerar_icms(sessao, empresa):
    return GeradorEFDICMS(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM).gerar()


def gerar_contribuicoes(sessao, empresa):
    return GeradorEFDContribuicoes(
        sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM
    ).gerar()


def adulterar(resultado, tipo: str, nome: str, valor: str, leiaute=EFD_ICMS, ocorrencia: int = 0):
    """Troca um campo de um registro já montado, pelo nome.

    É como se planta a divergência: o banco continua íntegro, e só o arquivo
    que vai sair está errado. Um espelho que lesse o banco não veria nada.
    """
    registros = [r for r in resultado.registros if r.tipo == tipo]
    registros[ocorrencia].campos[leiaute[tipo].index(nome)] = valor


def divergencia(visao: Espelho, trecho: str):
    """A conferência divergente cujo nome contém o trecho."""
    achadas = [c for c in visao.divergencias() if trecho in c.nome]
    assert achadas, f"nenhuma divergência sobre {trecho!r}; houve {visao.divergencias()}"
    return achadas[0]


# ── A decisão central: o espelho lê o arquivo ──────────────────────────────


def test_o_espelho_mostra_o_registro_e_nao_o_documento_do_banco(sessao, empresa):
    """Adulterado o registro, o espelho acompanha — o banco segue intacto."""
    importar(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "C100", "VL_DOC", "7777,77")

    visao = espelho(resultado, tipo="efd_icms")

    assert visao.documentos[0].valor_documento == 7777.77


def test_a_identificacao_vem_do_0000_e_nao_do_cadastro(sessao, empresa):
    """Se o 0000 sair com outro nome, é esse nome que o Fisco vai ler."""
    importar(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "0000", "NOME", "OUTRO NOME LTDA")

    visao = espelho(resultado, tipo="efd_icms")

    assert visao.identificacao["empresa"] == "OUTRO NOME LTDA"
    assert visao.identificacao["período"] == "01/07/2026 a 31/07/2026"


# ── O espelho de uma geração limpa ─────────────────────────────────────────


def test_geracao_limpa_nao_tem_divergencia_nenhuma(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    visao = espelho(gerar_icms(sessao, empresa), tipo="efd_icms")

    assert visao.divergencias() == []
    assert len(visao.conferencias) == 4


def test_os_documentos_saem_com_sentido_numero_e_valor(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    visao = espelho(gerar_icms(sessao, empresa), tipo="efd_icms")

    entrada, saida = visao.documentos
    assert (entrada.sentido, entrada.numero, entrada.itens) == ("entrada", "10", 2)
    assert (saida.sentido, saida.numero, saida.itens) == ("saída", "11", 1)
    assert visao.total_entradas == 2000.0
    assert visao.total_saidas == 1000.0


def test_a_apuracao_do_icms_traz_debito_credito_e_saldo(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    visao = espelho(gerar_icms(sessao, empresa), tipo="efd_icms")
    lido = dict(visao.apuracao)

    assert lido["débitos das saídas"] == 180.0
    assert lido["créditos das entradas"] == 360.0
    assert lido["saldo credor a transportar"] == 180.0


def test_os_avisos_da_geracao_chegam_ao_espelho(sessao, empresa):
    """O espelho é a última leitura antes de transmitir: os avisos vão junto."""
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    visao = espelho(resultado, tipo="efd_icms")

    assert visao.avisos == resultado.avisos
    assert visao.avisos


# ── As conferências ────────────────────────────────────────────────────────


def test_total_do_documento_que_nao_bate_com_os_itens_e_acusado(sessao, empresa):
    """O caso que o §12.5 corrigiu na origem — aqui é a rede de segurança."""
    importar(sessao, empresa, numero="10", itens=2)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "C100", "VL_MERC", "500,00")

    achada = divergencia(espelho(resultado, tipo="efd_icms"), "soma dos itens")

    assert "nº 10" in achada.detalhe
    assert "500,00" in achada.detalhe and "2.000,00" in achada.detalhe


def test_diferenca_de_arredondamento_nao_vira_divergencia(sessao, empresa):
    """Um centavo em documento de dois itens cabe na tolerância.

    Cada valor do arquivo já vem arredondado; exigir igualdade exata acusaria
    documento correto e ensinaria a ignorar a conferência.
    """
    importar(sessao, empresa, itens=2)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "C100", "VL_MERC", "2000,01")

    assert espelho(resultado, tipo="efd_icms").divergencias() == []


def test_a_tolerancia_nao_engole_diferenca_de_verdade(sessao, empresa):
    """Dez centavos em dois itens já é erro, não arredondamento."""
    importar(sessao, empresa, itens=2)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "C100", "VL_MERC", "2000,10")

    assert espelho(resultado, tipo="efd_icms").divergencias()


def test_consolidado_c190_que_nao_bate_com_os_itens_e_acusado(sessao, empresa):
    importar(sessao, empresa, numero="10", itens=2)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "C190", "VL_ICMS", "1,00")

    achada = divergencia(espelho(resultado, tipo="efd_icms"), "consolidado C190")

    assert "nº 10" in achada.detalhe


def test_e110_que_nao_bate_com_o_icms_dos_documentos_e_acusado(sessao, empresa):
    """A apuração é recalculada do arquivo, não perguntada ao gerador."""
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "E110", "VL_TOT_DEBITOS", "9999,00")

    achada = divergencia(espelho(resultado, tipo="efd_icms"), "E110")

    assert "débitos" in achada.detalhe
    assert "9.999,00" in achada.detalhe and "180,00" in achada.detalhe


def test_credito_do_e110_tambem_e_conferido(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "E110", "VL_TOT_CREDITOS", "1,00")

    assert "créditos" in divergencia(espelho(resultado, tipo="efd_icms"), "E110").detalhe


def test_contagem_do_bloco_9_adulterada_e_acusada(sessao, empresa):
    """Errar aqui faz o validador recusar o arquivo inteiro."""
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "9900", "QTD_REG_BLC", "99")

    achada = divergencia(espelho(resultado, tipo="efd_icms"), "bloco 9")

    assert "9900 diz 99" in achada.detalhe


def test_total_de_linhas_do_9999_adulterado_e_acusado(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "9999", "QTD_LIN", "3")

    assert (
        "9999 diz 3 linhas" in divergencia(espelho(resultado, tipo="efd_icms"), "bloco 9").detalhe
    )


# ── EFD-Contribuições: o regime decide o que se confere ────────────────────


def test_no_nao_cumulativo_o_credito_das_entradas_e_esperado(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    visao = espelho(gerar_contribuicoes(sessao, empresa), tipo="efd_contribuicoes")
    lido = dict(visao.apuracao)

    assert visao.divergencias() == []
    # A entrada tem dois itens, 16,50 de PIS cada.
    assert lido["PIS — créditos descontados"] == 33.0


def test_no_cumulativo_o_credito_nao_e_esperado_nem_exibido(sessao):
    """Conferir pelo campo do não cumulativo acusaria todo lucro presumido."""
    empresa = _empresa(sessao, cod_inc_trib="2")
    uma_entrada_e_uma_saida(sessao, empresa)
    visao = espelho(gerar_contribuicoes(sessao, empresa), tipo="efd_contribuicoes")
    lido = dict(visao.apuracao)

    assert visao.divergencias() == []
    assert lido["PIS devido"] == 16.50
    assert "PIS — créditos descontados" not in lido


def test_o_regime_lido_e_o_do_arquivo_nao_o_do_cadastro(sessao, empresa):
    """Quando os dois discordam, quem vale para o Fisco é o arquivo.

    O cadastro diz não cumulativo; o 0110 sai adulterado como cumulativo. O
    espelho tem de conferir pelo regime do arquivo — e acusar, porque o M200
    foi montado para o outro regime.
    """
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_contribuicoes(sessao, empresa)
    adulterar(resultado, "0110", "COD_INC_TRIB", "2", leiaute=EFD_CONTRIBUICOES)

    assert espelho(resultado, tipo="efd_contribuicoes").divergencias()


def test_m600_adulterado_e_acusado(sessao, empresa):
    uma_entrada_e_uma_saida(sessao, empresa)
    resultado = gerar_contribuicoes(sessao, empresa)
    adulterar(resultado, "M600", "VL_TOT_CONT_NC_PER", "5,00", leiaute=EFD_CONTRIBUICOES)

    assert divergencia(espelho(resultado, tipo="efd_contribuicoes"), "M600").detalhe


# ── Bordas ─────────────────────────────────────────────────────────────────


def test_periodo_sem_documento_produz_espelho_sem_acusacao(sessao, empresa):
    """Mês sem movimento é normal, e o espelho não pode inventar problema."""
    visao = espelho(gerar_icms(sessao, empresa), tipo="efd_icms")

    assert visao.documentos == []
    assert visao.divergencias() == []
    assert visao.avisos


def test_tipo_sem_leiaute_descrito_e_recusado(sessao, empresa):
    resultado = gerar_icms(sessao, empresa)
    with pytest.raises(TipoSemLeiaute, match="efd_reinf"):
        espelho(resultado, tipo="efd_reinf")


def test_o_texto_traz_as_secoes_e_marca_o_que_nao_bate(sessao, empresa):
    importar(sessao, empresa, numero="10", itens=2)
    resultado = gerar_icms(sessao, empresa)
    adulterar(resultado, "C100", "VL_MERC", "1,00")

    texto = espelho(resultado, tipo="efd_icms").texto()

    assert "ESPELHO — EFD ICMS/IPI" in texto
    assert "DOCUMENTOS (1)" in texto
    assert "CONFERÊNCIAS" in texto
    assert "NÃO  a soma dos itens" in texto
    assert "LEIA ANTES DE TRANSMITIR" in texto
