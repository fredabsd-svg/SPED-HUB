"""Ajustes da apuração do ICMS — o registro E111 e o que ele compõe.

A apuração do bloco E era a soma dos documentos e mais nada. Empresa com
benefício fiscal, crédito outorgado, estorno ou dedução tem valores que **não
estão em nota nenhuma**, e sem eles o imposto sai errado — a menos quando
falta um crédito outorgado, a mais quando falta um estorno.

O que estes testes protegem:

  * **o sistema conhece a estrutura do código, não a tabela.** A 5.1.1 é de
    cada Secretaria da Fazenda, muda por ato normativo e tem centenas de
    entradas; embuti-la aqui seria embutir uma tabela errada para 26 dos 27
    estados. A estrutura é nacional, e é ela que decide o destino do valor;
  * **a 4ª posição do código decide o campo do E110.** Quem informa o código
    informa junto o tratamento;
  * **o sinal está no código, não no número.** Valor negativo é recusado;
  * **a fórmula do E110 inteira**, incluindo a dedução, que entra depois do
    saldo apurado e não dentro dele.
"""

from __future__ import annotations

import datetime

import pytest

from src.db.models import AjusteApuracao, Empresa, Escritorio, criar_engine, get_session, init_db
from src.documentos import ImportadorDeDocumentos
from src.escrituracoes import (
    AjusteInvalido,
    GeradorEFDICMS,
    ajustes_do_periodo,
    criar_ajuste,
    totais_por_campo,
    utilizacao,
    validar_codigo,
)
from src.escrituracoes.leiaute import EFD_ICMS
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'ajustes.db'}")
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


@pytest.fixture
def com_saida(sessao, empresa):
    """Uma saída de 180,00 de ICMS: dá débito para os ajustes mexerem."""
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
        nfe_xml(emitente_cnpj="98765432000198", destinatario_cnpj="12345678000195")
    )
    sessao.commit()
    return empresa


def ajustar(sessao, empresa, codigo, valor, **kwargs):
    ajuste = criar_ajuste(
        sessao,
        empresa=empresa,
        data_inicio=INICIO,
        data_fim=FIM,
        cod_aj=codigo,
        valor=valor,
        **kwargs,
    )
    sessao.commit()
    return ajuste


def gerar(sessao, empresa):
    return GeradorEFDICMS(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM).gerar()


def e110(resultado, nome: str) -> str:
    registro = next(r for r in resultado.registros if r.tipo == "E110")
    return registro.campos[EFD_ICMS["E110"].index(nome)]


def e111(resultado) -> list[list[str]]:
    return [r.campos for r in resultado.registros if r.tipo == "E111"]


# ── A estrutura do código ──────────────────────────────────────────────────


def test_o_codigo_e_normalizado(sessao, empresa):
    assert validar_codigo(" to020007 ", uf="TO") == "TO020007"


@pytest.mark.parametrize("codigo", ["TO2007", "TO0200077", ""])
def test_codigo_sem_oito_caracteres_e_recusado(codigo):
    with pytest.raises(AjusteInvalido, match="8 caracteres"):
        validar_codigo(codigo)


def test_codigo_de_outro_estado_e_recusado(empresa):
    """Erro fácil de cometer copiando o código de um cliente para outro."""
    with pytest.raises(AjusteInvalido, match="é do estado SP"):
        validar_codigo("SP020007", uf="TO")


def test_apuracao_desconhecida_e_recusada():
    with pytest.raises(AjusteInvalido, match="3ª posição"):
        validar_codigo("TO720007")


def test_utilizacao_desconhecida_e_recusada():
    with pytest.raises(AjusteInvalido, match="4ª posição"):
        validar_codigo("TO080007")


def test_sequencial_nao_numerico_e_recusado():
    with pytest.raises(AjusteInvalido, match="sequencial"):
        validar_codigo("TO02ABCD")


def test_o_sequencial_nao_e_conferido_contra_tabela_nenhuma():
    """A 5.1.1 é de cada estado e muda por ato normativo.

    Um sequencial inventado passa de propósito: conferi-lo exigiria embutir 27
    tabelas, e a embutida estaria errada para 26 estados.
    """
    assert validar_codigo("TO029999") == "TO029999"


# ── A 4ª posição decide o destino ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("codigo", "rotulo", "campo"),
    [
        ("TO000001", "outros débitos", "VL_AJ_DEBITOS"),
        ("TO010001", "estorno de créditos", "VL_ESTORNOS_CRED"),
        ("TO020001", "outros créditos", "VL_AJ_CREDITOS"),
        ("TO030001", "estorno de débitos", "VL_ESTORNOS_DEB"),
        ("TO040001", "deduções", "VL_TOT_DED"),
        ("TO050001", "débito especial", "DEB_ESP"),
    ],
)
def test_cada_utilizacao_tem_o_seu_campo(codigo, rotulo, campo):
    assert utilizacao(codigo) == (rotulo, campo)


def test_controle_extra_apuracao_nao_tem_campo(sessao):
    """O 9 existe justamente para NÃO entrar na apuração do período."""
    assert utilizacao("TO090001") == ("controle extra-apuração", None)


def test_totais_ignoram_o_controle_extra_apuracao(sessao, empresa):
    ajustar(sessao, empresa, "TO020001", 100.0)
    ajustar(sessao, empresa, "TO090001", 999.0)

    totais = totais_por_campo(
        ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM)
    )

    assert totais == {"VL_AJ_CREDITOS": 100.0}


def test_totais_ignoram_ajuste_de_outra_apuracao(sessao, empresa):
    """ST, DIFAL e FCP têm registro próprio, que este gerador não escreve."""
    ajustar(sessao, empresa, "TO020001", 100.0)
    ajustar(sessao, empresa, "TO120001", 500.0)  # 3ª posição 1 = ICMS-ST

    totais = totais_por_campo(
        ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM)
    )

    assert totais == {"VL_AJ_CREDITOS": 100.0}


def test_ajustes_da_mesma_utilizacao_somam(sessao, empresa):
    ajustar(sessao, empresa, "TO020001", 100.0)
    ajustar(sessao, empresa, "TO020009", 50.0)

    totais = totais_por_campo(
        ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM)
    )

    assert totais == {"VL_AJ_CREDITOS": 150.0}


# ── O sinal está no código ─────────────────────────────────────────────────


def test_valor_negativo_e_recusado(sessao, empresa):
    """Um "outros créditos" negativo seria um débito que o validador não lê."""
    with pytest.raises(AjusteInvalido, match="sinal está no código"):
        ajustar(sessao, empresa, "TO020001", -100.0)


def test_a_recusa_diz_o_que_o_codigo_significa(sessao, empresa):
    with pytest.raises(AjusteInvalido, match="outros créditos"):
        ajustar(sessao, empresa, "TO020001", -1.0)


def test_valor_zero_e_aceito(sessao, empresa):
    """Ajuste de zero é raro, mas não é erro — e recusá-lo surpreenderia."""
    assert ajustar(sessao, empresa, "TO020001", 0.0).valor == 0.0


# ── O período é exato ──────────────────────────────────────────────────────


def test_ajuste_de_outro_periodo_nao_entra(sessao, empresa):
    """Aproximar aqui faria o mesmo valor entrar em dois meses."""
    criar_ajuste(
        sessao,
        empresa=empresa,
        data_inicio=datetime.date(2026, 8, 1),
        data_fim=datetime.date(2026, 8, 31),
        cod_aj="TO020001",
        valor=100.0,
    )
    sessao.commit()

    assert ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM) == []


def test_periodo_que_comeca_junto_e_termina_antes_nao_entra(sessao, empresa):
    """As duas datas contam, não só a de início.

    Um mês fechado (01–31) e uma quinzena (01–15) começam no mesmo dia. Se só
    o início fosse conferido, o ajuste da quinzena entraria na apuração do mês
    inteiro — e no da quinzena seguinte também.
    """
    criar_ajuste(
        sessao,
        empresa=empresa,
        data_inicio=INICIO,
        data_fim=datetime.date(2026, 7, 15),
        cod_aj="TO020001",
        valor=100.0,
    )
    sessao.commit()

    assert ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM) == []


def test_periodo_que_termina_junto_e_comeca_depois_nao_entra(sessao, empresa):
    """A simétrica: a segunda quinzena termina no mesmo dia que o mês."""
    criar_ajuste(
        sessao,
        empresa=empresa,
        data_inicio=datetime.date(2026, 7, 16),
        data_fim=FIM,
        cod_aj="TO020001",
        valor=100.0,
    )
    sessao.commit()

    assert ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM) == []


def test_ajuste_de_outra_empresa_nao_entra(sessao, empresa):
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
    ajustar(sessao, outra, "TO020001", 100.0)

    assert ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=INICIO, data_fim=FIM) == []


# ── O E111 no arquivo ──────────────────────────────────────────────────────


def test_cada_ajuste_vira_um_e111(sessao, com_saida):
    ajustar(sessao, com_saida, "TO020001", 100.0, descricao="crédito outorgado")
    ajustar(sessao, com_saida, "TO000001", 30.0)

    linhas = e111(gerar(sessao, com_saida))

    assert len(linhas) == 2
    assert linhas[0] == ["TO020001", "crédito outorgado", "100,00"]
    assert linhas[1] == ["TO000001", "", "30,00"]


def test_sem_ajuste_nao_ha_e111(sessao, com_saida):
    assert e111(gerar(sessao, com_saida)) == []


def test_periodo_so_com_ajuste_ainda_gera_a_apuracao(sessao, empresa):
    """Mês sem nota mas com estorno precisa do E110 para o E111 pendurar."""
    ajustar(sessao, empresa, "TO010001", 40.0)

    resultado = gerar(sessao, empresa)

    assert e110(resultado, "VL_ESTORNOS_CRED") == "40,00"
    assert len(e111(resultado)) == 1


# ── A fórmula do E110 ──────────────────────────────────────────────────────


def test_outros_debitos_aumentam_o_imposto(sessao, com_saida):
    ajustar(sessao, com_saida, "TO000001", 20.0)

    resultado = gerar(sessao, com_saida)

    assert e110(resultado, "VL_AJ_DEBITOS") == "20,00"
    assert e110(resultado, "VL_ICMS_RECOLHER") == "200,00", "180 + 20"


def test_outros_creditos_diminuem_o_imposto(sessao, com_saida):
    ajustar(sessao, com_saida, "TO020001", 30.0)

    resultado = gerar(sessao, com_saida)

    assert e110(resultado, "VL_AJ_CREDITOS") == "30,00"
    assert e110(resultado, "VL_ICMS_RECOLHER") == "150,00", "180 − 30"


def test_estorno_de_credito_aumenta_o_imposto(sessao, com_saida):
    """Estornar crédito é devolver o que se tomou: aumenta o devido."""
    ajustar(sessao, com_saida, "TO010001", 25.0)

    assert e110(gerar(sessao, com_saida), "VL_ICMS_RECOLHER") == "205,00"


def test_estorno_de_debito_diminui_o_imposto(sessao, com_saida):
    ajustar(sessao, com_saida, "TO030001", 25.0)

    assert e110(gerar(sessao, com_saida), "VL_ICMS_RECOLHER") == "155,00"


def test_a_deducao_entra_depois_do_saldo_apurado(sessao, com_saida):
    """É a diferença entre o que se apurou e o que se recolhe.

    O `VL_SLD_APURADO` continua 180,00; o `VL_ICMS_RECOLHER` é que cai. Somar
    a dedução dentro do saldo apurado daria o mesmo total a recolher e um
    saldo apurado errado — que é o número que o Fisco confere contra o E111.
    """
    ajustar(sessao, com_saida, "TO040001", 50.0)

    resultado = gerar(sessao, com_saida)

    assert e110(resultado, "VL_SLD_APURADO") == "180,00"
    assert e110(resultado, "VL_TOT_DED") == "50,00"
    assert e110(resultado, "VL_ICMS_RECOLHER") == "130,00"


def test_debito_especial_sai_no_campo_proprio(sessao, com_saida):
    """Não entra no saldo: é recolhido em guia separada."""
    ajustar(sessao, com_saida, "TO050001", 70.0)

    resultado = gerar(sessao, com_saida)

    assert e110(resultado, "DEB_ESP") == "70,00"
    assert e110(resultado, "VL_ICMS_RECOLHER") == "180,00"


def test_credito_maior_que_debito_vira_saldo_credor(sessao, com_saida):
    ajustar(sessao, com_saida, "TO020001", 500.0)

    resultado = gerar(sessao, com_saida)

    assert e110(resultado, "VL_ICMS_RECOLHER") == ""
    assert e110(resultado, "VL_SLD_CREDOR_TRANSPORTAR") == "320,00"


def test_os_ajustes_de_documento_seguem_vazios(sessao, com_saida):
    """`VL_TOT_AJ_*` são do C197/D197, que nascem de uma nota — outro assunto."""
    ajustar(sessao, com_saida, "TO000001", 20.0)

    resultado = gerar(sessao, com_saida)

    assert e110(resultado, "VL_TOT_AJ_DEBITOS") == ""
    assert e110(resultado, "VL_TOT_AJ_CREDITOS") == ""


# ── Os avisos ──────────────────────────────────────────────────────────────


def test_sem_ajuste_o_aviso_aponta_o_comando(sessao, com_saida):
    avisos = gerar(sessao, com_saida).avisos

    achado = next(a for a in avisos if "não há ajustes de apuração" in a)
    assert "fiscal ajuste" in achado


def test_com_ajuste_o_aviso_de_soma_direta_some(sessao, com_saida):
    """Dizer que é soma direta com ajuste cadastrado seria mentira."""
    ajustar(sessao, com_saida, "TO020001", 10.0)

    avisos = gerar(sessao, com_saida).avisos

    assert not [a for a in avisos if "não há ajustes de apuração" in a]


def test_ajuste_que_nao_entra_na_apuracao_e_avisado(sessao, com_saida):
    ajustar(sessao, com_saida, "TO090001", 300.0)
    ajustar(sessao, com_saida, "TO120001", 200.0)

    avisos = gerar(sessao, com_saida).avisos

    achado = next(a for a in avisos if "NÃO entraram na apuração" in a)
    assert "TO090001" in achado and "TO120001" in achado
    assert "500,00" in achado


def test_ajuste_que_entra_nao_vira_aviso(sessao, com_saida):
    ajustar(sessao, com_saida, "TO020001", 10.0)

    assert not [a for a in gerar(sessao, com_saida).avisos if "NÃO entraram" in a]


def test_o_ajuste_fora_da_apuracao_ainda_sai_no_e111(sessao, com_saida):
    """Ele é declarado; o que não acontece é entrar na conta do E110."""
    ajustar(sessao, com_saida, "TO090001", 300.0)

    resultado = gerar(sessao, com_saida)

    assert len(e111(resultado)) == 1
    assert e110(resultado, "VL_ICMS_RECOLHER") == "180,00"


def test_o_ajuste_e_persistido_com_a_empresa_e_o_escritorio(sessao, empresa):
    ajuste = ajustar(sessao, empresa, "TO020001", 10.0)

    guardado = sessao.get(AjusteApuracao, ajuste.id)

    assert guardado.empresa_id == empresa.id
    assert guardado.escritorio_id == empresa.escritorio_id
    assert guardado.tipo == "efd_icms"
