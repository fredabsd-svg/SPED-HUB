"""O saldo credor anterior vem da escrituração transmitida do mês passado.

O leiaute é explícito: o `VL_SLD_CREDOR_ANT` de um período tem de ser igual ao
`VL_SLD_CREDOR_TRANSPORTAR` do período anterior. Sem isso, empresa que vinha de
saldo credor recolhia a mais — o E110 dizia "ICMS a recolher" um valor que
ignorava o crédito acumulado, e é esse número que vai para a guia.

O que estes testes protegem:

  * **só escrituração transmitida estabelece saldo.** Uma geração que ninguém
    entregou não vale nada perante o Fisco, e é justamente a que sobra em
    maior número, porque gerar para conferir é barato;
  * **só período contíguo.** Um mês sem entrega no meio torna o saldo daquele
    arquivo obsoleto; carregá-lo produziria imposto a MENOS com aparência de
    conta certa;
  * **o valor é lido do arquivo**, não recalculado — regerar o mês anterior
    hoje pode dar outro número, e o Fisco tem o primeiro;
  * **o silêncio avisa**, e avisa coisas diferentes conforme o motivo.


No Bloco E, campo numérico obrigatório sai com valor ou com "0" — nunca em
branco (Guia Prático da EFD ICMS/IPI 3.2.2, Capítulo III). Por isso as
ausências abaixo são conferidas como "0,00", e não como campo vazio.
"""

from __future__ import annotations

import datetime

import pytest

from src.db.models import Empresa, Escritorio, criar_engine, get_session, init_db
from src.documentos import ImportadorDeDocumentos
from src.escrituracoes import (
    GeradorEFDICMS,
    arquivar,
    campo_do_registro,
    marcar_transmitida,
)
from tests.fixtures_nfe import nfe_xml

JULHO = (datetime.date(2026, 7, 1), datetime.date(2026, 7, 31))
AGOSTO = (datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
SETEMBRO = (datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'saldo.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def empresa(sessao):
    escritorio = Escritorio(nome="Teste", slug="teste")
    sessao.add(escritorio)
    sessao.commit()
    e = Empresa(
        cnpj="98765432000198",
        nome="COMERCIO EXEMPLO LTDA",
        uf="TO",
        ie="293456789",
        cod_mun="1721000",
        ind_perfil="A",
        ind_ativ="1",
        escritorio_id=escritorio.id,
    )
    sessao.add(e)
    sessao.commit()
    return e


def importar_entrada(sessao, empresa, **kwargs):
    """Uma nota de ENTRADA: gera crédito de ICMS, que é o que se acumula."""
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(nfe_xml(**kwargs))
    sessao.commit()


def movimento_em(sessao, empresa, data: str, *, numero: str, chave: str):
    """Uma nota no período que se vai gerar.

    Sem movimento no período o E110 não sai — e sem E110 não há campo para
    conferir. Vários testes daqui precisam de uma nota só para ter onde olhar.
    """
    importar_entrada(sessao, empresa, numero=numero, chave=chave, data_emissao=data)


def gerar(sessao, empresa, periodo):
    inicio, fim = periodo
    return GeradorEFDICMS(sessao, empresa=empresa, data_inicio=inicio, data_fim=fim).gerar()


def arquivar_periodo(sessao, empresa, periodo, resultado=None):
    inicio, fim = periodo
    escrituracao = arquivar(
        sessao,
        resultado=resultado or gerar(sessao, empresa, periodo),
        empresa=empresa,
        tipo="efd_icms",
        data_inicio=inicio,
        data_fim=fim,
    )
    sessao.commit()
    return escrituracao


def e110(resultado, nome: str) -> str:
    from src.escrituracoes.leiaute import EFD_ICMS

    registro = next(r for r in resultado.registros if r.tipo == "E110")
    return registro.campos[EFD_ICMS["E110"].index(nome)]


def julho_transmitido_com_saldo(sessao, empresa):
    """Julho com uma entrada de 360,00 de crédito, entregue.

    O arquivo de julho fecha com `VL_SLD_CREDOR_TRANSPORTAR = 360,00`, que é o
    que agosto tem de carregar.
    """
    importar_entrada(sessao, empresa, itens=2)
    escrituracao = arquivar_periodo(sessao, empresa, JULHO)
    marcar_transmitida(sessao, escrituracao, recibo="R-JUL")
    sessao.commit()
    return escrituracao


# ── O saldo é carregado ────────────────────────────────────────────────────


def test_o_saldo_do_mes_anterior_transmitido_e_carregado(sessao, empresa):
    julho = julho_transmitido_com_saldo(sessao, empresa)
    assert campo_do_registro(julho, "E110", "VL_SLD_CREDOR_TRANSPORTAR") == "360,00"

    de_agosto = gerar(sessao, empresa, AGOSTO)

    assert e110(de_agosto, "VL_SLD_CREDOR_ANT") == "360,00"


def test_o_saldo_anterior_abate_o_imposto_a_recolher(sessao, empresa):
    """É o efeito que importa: sem isso, recolhe-se a mais.

    Agosto tem 180,00 de débito e 360,00 de crédito acumulado; nada a
    recolher, e sobram 180,00 para setembro.
    """
    julho_transmitido_com_saldo(sessao, empresa)
    # Uma saída EM AGOSTO: 180,00 de débito.
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
        nfe_xml(
            numero="20",
            chave="2" * 44,
            emitente_cnpj="98765432000198",
            destinatario_cnpj="12345678000195",
            data_emissao="2026-08-10",
        )
    )
    sessao.commit()

    de_agosto = gerar(sessao, empresa, AGOSTO)

    assert e110(de_agosto, "VL_ICMS_RECOLHER") == "0,00"
    assert e110(de_agosto, "VL_SLD_CREDOR_TRANSPORTAR") == "180,00"


def test_o_aviso_diz_de_onde_veio_o_numero(sessao, empresa):
    """Número que muda o imposto tem de dizer a origem."""
    julho = julho_transmitido_com_saldo(sessao, empresa)

    avisos = gerar(sessao, empresa, AGOSTO).avisos

    achado = next(a for a in avisos if "saldo credor anterior de" in a)
    assert f"#{julho.id}" in achado
    assert "não um recálculo" in achado


# ── Só transmitida estabelece saldo ────────────────────────────────────────


def test_geracao_nao_transmitida_nao_estabelece_saldo(sessao, empresa):
    """Gerar para conferir é barato; entregar não é a mesma coisa."""
    importar_entrada(sessao, empresa, itens=2)
    arquivar_periodo(sessao, empresa, JULHO)  # gerada, não transmitida
    movimento_em(sessao, empresa, "2026-08-10", numero="20", chave="2" * 44)

    de_agosto = gerar(sessao, empresa, AGOSTO)

    assert e110(de_agosto, "VL_SLD_CREDOR_ANT") == "0,00"


def test_o_aviso_distingue_nao_marcada_de_inexistente(sessao, empresa):
    """Dois silêncios, dois conselhos diferentes."""
    importar_entrada(sessao, empresa, itens=2)
    arquivar_periodo(sessao, empresa, JULHO)

    avisos = gerar(sessao, empresa, AGOSTO).avisos

    achado = next(a for a in avisos if "ZERADO" in a)
    assert "NENHUMA marcada como transmitida" in achado
    assert "sped-hub fiscal transmitida" in achado


def test_sem_escrituracao_anterior_nenhuma_o_aviso_e_outro(sessao, empresa):
    importar_entrada(sessao, empresa, itens=2)

    avisos = gerar(sessao, empresa, JULHO).avisos

    achado = next(a for a in avisos if "ZERADO" in a)
    assert "não há escrituração anterior transmitida" in achado
    assert "MAIOR do que o devido" in achado


# ── Só período contíguo ────────────────────────────────────────────────────


def test_mes_sem_entrega_no_meio_nao_carrega_o_saldo(sessao, empresa):
    """Julho entregue, agosto não, setembro não pode herdar julho.

    Carregar mesmo assim produziria imposto a MENOS com aparência de conta
    certa — o crédito de julho já foi consumido por agosto.
    """
    julho_transmitido_com_saldo(sessao, empresa)
    movimento_em(sessao, empresa, "2026-09-10", numero="30", chave="3" * 44)

    de_setembro = gerar(sessao, empresa, SETEMBRO)

    assert e110(de_setembro, "VL_SLD_CREDOR_ANT") == "0,00"


def test_o_intervalo_sem_entrega_e_avisado_com_as_datas(sessao, empresa):
    julho = julho_transmitido_com_saldo(sessao, empresa)

    avisos = gerar(sessao, empresa, SETEMBRO).avisos

    achado = next(a for a in avisos if "intervalo sem entrega" in a)
    assert f"#{julho.id}" in achado
    assert "2026-07-31" in achado and "2026-09-01" in achado


def test_a_vespera_e_o_criterio_nao_o_mes(sessao, empresa):
    """Contiguidade é `data_fim == data_inicio - 1 dia`, não "mês anterior".

    Período quinzenal, decendial ou de abertura não cai em mês cheio, e um
    critério de mês recusaria carregar saldo legítimo.
    """
    importar_entrada(sessao, empresa, itens=2, data_emissao="2026-07-10")
    quinzena = (datetime.date(2026, 7, 1), datetime.date(2026, 7, 15))
    primeira = arquivar_periodo(sessao, empresa, quinzena)
    movimento_em(sessao, empresa, "2026-07-20", numero="40", chave="4" * 44)
    marcar_transmitida(sessao, primeira)
    sessao.commit()

    segunda = gerar(
        sessao,
        empresa,
        (datetime.date(2026, 7, 16), datetime.date(2026, 7, 31)),
    )

    assert e110(segunda, "VL_SLD_CREDOR_ANT") == "360,00"


# ── O que não conta ────────────────────────────────────────────────────────


def test_escrituracao_de_outra_empresa_nao_conta(sessao, empresa):
    julho_transmitido_com_saldo(sessao, empresa)
    outra = Empresa(
        cnpj="11222333000181",
        nome="OUTRA LTDA",
        uf="TO",
        ie="111111111",
        cod_mun="1721000",
        ind_perfil="A",
        ind_ativ="1",
        escritorio_id=empresa.escritorio_id,
    )
    sessao.add(outra)
    sessao.commit()
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
        nfe_xml(
            numero="50",
            chave="5" * 44,
            destinatario_cnpj="11222333000181",
            data_emissao="2026-08-10",
        )
    )
    sessao.commit()

    de_agosto = gerar(sessao, outra, AGOSTO)

    assert e110(de_agosto, "VL_SLD_CREDOR_ANT") == "0,00"


def test_escrituracao_de_outra_obrigacao_nao_conta(sessao, empresa):
    """A EFD-Contribuições do mês passado não estabelece saldo de ICMS."""
    importar_entrada(sessao, empresa, itens=2)
    empresa.cod_inc_trib = "1"
    empresa.ind_ativ_contribuicoes = "2"
    sessao.commit()

    from src.escrituracoes import GeradorEFDContribuicoes

    contribuicoes = arquivar(
        sessao,
        resultado=GeradorEFDContribuicoes(
            sessao, empresa=empresa, data_inicio=JULHO[0], data_fim=JULHO[1]
        ).gerar(),
        empresa=empresa,
        tipo="efd_contribuicoes",
        data_inicio=JULHO[0],
        data_fim=JULHO[1],
    )
    marcar_transmitida(sessao, contribuicoes)
    sessao.commit()
    movimento_em(sessao, empresa, "2026-08-10", numero="60", chave="6" * 44)

    de_agosto = gerar(sessao, empresa, AGOSTO)

    assert e110(de_agosto, "VL_SLD_CREDOR_ANT") == "0,00"
    # O campo vazio sozinho não prova nada: a EFD-Contribuições não tem E110,
    # então lê-la por engano também daria vazio.  O aviso é o que distingue —
    # ele diz que NÃO HÁ escrituração de ICMS anterior, e só sai assim se a de
    # outra obrigação tiver sido descartada.
    assert [a for a in de_agosto.avisos if "não há escrituração anterior transmitida" in a]


def test_mes_anterior_sem_saldo_credor_nao_avisa_a_origem(sessao, empresa):
    """Aviso de origem só quando há número a explicar.

    Repeti-lo com 0,00 todo mês afogaria os avisos que importam.
    """
    julho = arquivar_periodo(sessao, empresa, JULHO)  # sem documento: saldo 0
    marcar_transmitida(sessao, julho)
    sessao.commit()

    avisos = gerar(sessao, empresa, AGOSTO).avisos

    assert not [a for a in avisos if "saldo credor anterior de" in a]
    assert not [a for a in avisos if "ZERADO" in a]


# ── Mês sem movimento ──────────────────────────────────────────────────────


def test_mes_sem_nota_mas_com_saldo_ainda_emite_o_e110(sessao, empresa):
    """Senão o crédito some da cadeia, e some de vez.

    O mês seguinte procura o `VL_SLD_CREDOR_TRANSPORTAR` do anterior; sem a
    linha, não acha, e o saldo acumulado evapora sem que ninguém veja.
    """
    julho_transmitido_com_saldo(sessao, empresa)

    de_agosto = gerar(sessao, empresa, AGOSTO)

    assert e110(de_agosto, "VL_SLD_CREDOR_ANT") == "360,00"
    assert e110(de_agosto, "VL_SLD_CREDOR_TRANSPORTAR") == "360,00"


def test_o_saldo_atravessa_dois_meses_sem_movimento(sessao, empresa):
    """A cadeia se sustenta: julho → agosto → setembro, sem nota nenhuma."""
    julho_transmitido_com_saldo(sessao, empresa)
    agosto = arquivar_periodo(sessao, empresa, AGOSTO)
    marcar_transmitida(sessao, agosto)
    sessao.commit()

    de_setembro = gerar(sessao, empresa, SETEMBRO)

    assert e110(de_setembro, "VL_SLD_CREDOR_ANT") == "360,00"


def test_mes_sem_nota_e_sem_saldo_nao_emite_apuracao(sessao, empresa):
    """Sem movimento e sem crédito, o bloco E sai vazio — como deve."""
    resultado = gerar(sessao, empresa, AGOSTO)

    assert not [r for r in resultado.registros if r.tipo == "E110"]
    e001 = next(r for r in resultado.registros if r.tipo == "E001")
    assert e001.campos == ["1"], "1 = bloco sem dados"
