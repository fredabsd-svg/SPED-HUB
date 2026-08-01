"""Apuração de CBS, IBS e Imposto Seletivo.

O que estes testes protegem:

  * **o Imposto Seletivo não gera crédito** — ele incide uma vez na cadeia, e
    creditá-lo reduziria o imposto devido num número que parece uma apuração
    normal;
  * **as duas parcelas do IBS não se somam** — vão para entes diferentes, e
    apurar "IBS" como um número só destruiria a informação da partilha;
  * **2026 é ano de teste** — apresentar o total como "a recolher" seria
    enganoso, e o resultado tem de dizer isso.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ORIGEM_USUARIO, ImportadorDeDocumentos, aplicar_ajuste
from src.escrituracoes import ANO_DE_TESTE, ApuracaoIBSCBS, ResultadoApuracao, Tributo
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)

# O que o fixture destaca por item.
CBS_POR_ITEM = 9.00
IBS_UF_POR_ITEM = 0.70
IBS_MUN_POR_ITEM = 0.30
IS_POR_ITEM = 10.00


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'reforma.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def escritorio(sessao):
    e = Escritorio(nome="Teste", slug="teste")
    sessao.add(e)
    sessao.commit()
    return e


def _empresa(sessao, escritorio, cnpj="98765432000198"):
    e = Empresa(
        cnpj=cnpj,
        nome="COMERCIO EXEMPLO LTDA",
        uf="TO",
        ie="293456789",
        cod_mun="1721000",
        escritorio_id=escritorio.id,
    )
    sessao.add(e)
    sessao.commit()
    return e


def _importar(sessao, escritorio, **kwargs):
    ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml(**kwargs))
    sessao.commit()


def _apurar(sessao, empresa, inicio=INICIO, fim=FIM):
    return ApuracaoIBSCBS(sessao, empresa=empresa, data_inicio=inicio, data_fim=fim).apurar()


class TestOSeletivoNaoGeraCredito:
    """A distinção que mais barato erra e mais caro custa."""

    def test_entrada_com_is_nao_vira_credito(self, sessao, escritorio):
        """O IS que veio na compra é custo, não crédito.

        Creditá-lo reduziria o imposto devido pelo valor do IS das entradas,
        num resultado com a mesma cara de uma apuração correta.
        """
        empresa = _empresa(sessao, escritorio)  # destinatário: é entrada
        _importar(sessao, escritorio, itens=2)

        resultado = _apurar(sessao, empresa)

        assert resultado.seletivo == 0.0, "o IS da entrada entrou na apuração"

    def test_saida_com_is_vira_debito(self, sessao, escritorio):
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=2)

        resultado = _apurar(sessao, emitente)

        assert resultado.seletivo == pytest.approx(2 * IS_POR_ITEM)

    def test_o_is_nao_tem_onde_guardar_credito(self):
        """`seletivo` é um número, não um `Tributo`.

        Dar-lhe um campo `credito` seria convidar alguém a preenchê-lo.
        """
        resultado = ResultadoApuracao(data_inicio=INICIO, data_fim=FIM)

        assert isinstance(resultado.seletivo, float)
        assert not hasattr(resultado.seletivo, "credito")

    def test_o_aviso_diz_que_o_is_e_custo(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)

        avisos = _apurar(sessao, empresa).avisos

        assert any("não gera crédito" in a and "custo" in a for a in avisos)

    def test_o_is_entra_no_total_devido(self, sessao, escritorio):
        """Não creditar não quer dizer não dever.

        A soma é conferida por igualdade, não por `>=`: com um item, CBS mais
        IBS dão exatamente 10,00, e o IS também — um `>=` passaria mesmo com o
        IS fora da conta.
        """
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=2)

        resultado = _apurar(sessao, emitente)

        assert resultado.seletivo == pytest.approx(2 * IS_POR_ITEM)
        assert resultado.total_devido == pytest.approx(
            resultado.cbs.devido + resultado.ibs_total_devido + resultado.seletivo
        )
        assert resultado.total_devido > resultado.cbs.devido + resultado.ibs_total_devido


class TestAsDuasParcelasDoIBS:
    """Somá-las numa coluna só destruiria o cerne do imposto."""

    def test_ficam_separadas(self, sessao, escritorio):
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=3)

        resultado = _apurar(sessao, emitente)

        assert resultado.ibs_uf.debito == pytest.approx(3 * IBS_UF_POR_ITEM)
        assert resultado.ibs_municipal.debito == pytest.approx(3 * IBS_MUN_POR_ITEM)
        assert resultado.ibs_uf.debito != resultado.ibs_municipal.debito

    def test_o_total_e_derivado_e_nao_a_fonte(self, sessao, escritorio):
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=2)

        resultado = _apurar(sessao, emitente)

        assert resultado.ibs_total_devido == pytest.approx(
            resultado.ibs_uf.devido + resultado.ibs_municipal.devido
        )

    def test_uma_parcela_pode_ter_saldo_credor_e_a_outra_nao(self):
        resultado_uf = Tributo("IBS estadual", debito=10.0, credito=2.0)
        resultado_mun = Tributo("IBS municipal", debito=1.0, credito=5.0)

        assert resultado_uf.devido == 8.0
        assert resultado_mun.devido == 0.0
        assert resultado_mun.saldo_credor == 4.0

    def test_o_saldo_credor_de_uma_nao_abate_o_devido_da_outra(self):
        """Compensar uma parcela com a outra é pagar o estado com dinheiro do
        município.

        A conta certa é `max(débito−crédito, 0)` **em cada parcela**, e só
        depois somar. Somar tudo antes de aplicar o piso daria 4,00 aqui — o
        saldo credor municipal abatendo o imposto estadual, que pertence a
        outro ente. As duas formas só divergem neste cenário, e é exatamente
        ele que a separação existe para tratar.
        """
        resultado = ResultadoApuracao(
            data_inicio=INICIO,
            data_fim=FIM,
            ibs_uf=Tributo("IBS estadual", debito=10.0, credito=2.0),
            ibs_municipal=Tributo("IBS municipal", debito=1.0, credito=5.0),
        )

        assert resultado.ibs_total_devido == 8.0

        somando_antes = max((10.0 + 1.0) - (2.0 + 5.0), 0.0)  # o que a conta errada daria
        assert somando_antes == 4.0
        assert resultado.ibs_total_devido != somando_antes

    def test_o_aviso_explica_a_separacao(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)

        avisos = _apurar(sessao, empresa).avisos

        assert any("entes diferentes" in a for a in avisos)
        assert any("fato gerador" in a for a in avisos)


class TestDebitoECredito:
    def test_saida_gera_debito(self, sessao, escritorio):
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=2)

        resultado = _apurar(sessao, emitente)

        assert resultado.cbs.debito == pytest.approx(2 * CBS_POR_ITEM)
        assert resultado.cbs.credito == 0.0

    def test_entrada_gera_credito(self, sessao, escritorio):
        """CBS e IBS são não cumulativos com crédito amplo — ao contrário do IS."""
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio, itens=2)

        resultado = _apurar(sessao, empresa)

        assert resultado.cbs.credito == pytest.approx(2 * CBS_POR_ITEM)
        assert resultado.cbs.debito == 0.0

    def test_devido_e_debito_menos_credito(self):
        assert Tributo("CBS", debito=100.0, credito=30.0).devido == 70.0

    def test_devido_nao_fica_negativo(self):
        """O excedente é saldo credor, não imposto a devolver."""
        tributo = Tributo("CBS", debito=10.0, credito=40.0)

        assert tributo.devido == 0.0
        assert tributo.saldo_credor == 30.0

    def test_saldo_credor_e_zero_quando_ha_imposto_a_pagar(self):
        assert Tributo("CBS", debito=40.0, credito=10.0).saldo_credor == 0.0


class TestSaiDoEfetivo:
    def test_ajuste_muda_a_apuracao(self, sessao, escritorio):
        """A apuração lê a camada efetiva, como os geradores."""
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=2)
        antes = _apurar(sessao, emitente).cbs.debito

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="valor_cbs",
            valor_novo="100.00",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        depois = _apurar(sessao, emitente).cbs.debito

        assert antes == pytest.approx(2 * CBS_POR_ITEM)
        assert depois == pytest.approx(100.00 + CBS_POR_ITEM)

    def test_ajuste_no_sentido_troca_debito_por_credito(self, sessao, escritorio):
        """O sentido é do cabeçalho, e também vem do efetivo."""
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio)
        assert _apurar(sessao, empresa).cbs.credito > 0

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            campo="sentido",
            valor_novo="saida",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        resultado = _apurar(sessao, empresa)

        assert resultado.cbs.credito == 0.0
        assert resultado.cbs.debito == pytest.approx(CBS_POR_ITEM)


class TestEscopo:
    def test_documento_de_outra_empresa_fica_de_fora(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        outra = _empresa(sessao, escritorio, cnpj="11111111000111")
        _importar(sessao, escritorio)

        resultado = _apurar(sessao, outra)

        assert resultado.documentos == 0
        assert empresa is not outra

    def test_documento_fora_do_periodo_fica_de_fora(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio)

        resultado = _apurar(sessao, empresa, datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))

        assert resultado.documentos == 0
        assert resultado.cbs.credito == 0.0

    @pytest.mark.parametrize("borda", ["inicio", "fim"])
    def test_documento_na_borda_entra(self, sessao, escritorio, borda):
        """A nota é de 30/07/2026; o período que começa ou termina nesse dia inclui."""
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio)

        emissao = datetime.date(2026, 7, 30)
        inicio, fim = (emissao, FIM) if borda == "inicio" else (INICIO, emissao)

        assert _apurar(sessao, empresa, inicio, fim).documentos == 1


class TestAvisos:
    def test_o_ano_de_teste_e_avisado(self, sessao, escritorio):
        """Apresentar o total de 2026 como "a recolher" seria enganoso."""
        empresa = _empresa(sessao, escritorio)

        avisos = _apurar(sessao, empresa).avisos

        assert any(str(ANO_DE_TESTE) in a and "NÃO é o valor a recolher" in a for a in avisos)

    def test_fora_do_ano_de_teste_o_aviso_some(self, sessao, escritorio):
        """Um aviso que sai sempre não informa nada."""
        empresa = _empresa(sessao, escritorio)

        avisos = _apurar(
            sessao, empresa, datetime.date(2030, 1, 1), datetime.date(2030, 1, 31)
        ).avisos

        assert not any("ano de teste" in a for a in avisos)

    def test_periodo_vazio_distingue_zero_de_ausencia(self, sessao, escritorio):
        """Zero por falta de documento não é zero por não haver tributo."""
        empresa = _empresa(sessao, escritorio)

        resultado = _apurar(sessao, empresa)

        assert resultado.documentos == 0
        assert any("por falta de dado" in a for a in resultado.avisos)

    def test_periodo_com_documento_nao_traz_esse_aviso(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio)

        avisos = _apurar(sessao, empresa).avisos

        assert not any("por falta de dado" in a for a in avisos)

    def test_o_que_nao_tem_campo_proprio_e_dito_sempre(self, sessao, escritorio):
        """Split payment e regimes específicos não têm valor no documento.

        Não dá para medi-los, então o aviso genérico continua — só que
        reduzido ao que de fato não se pode medir. Monofásico, diferimento e
        crédito presumido saíram daqui: têm campo próprio e passaram a ser
        medidos, e avisar sobre eles quando valem zero treinaria a ignorar.
        """
        empresa = _empresa(sessao, escritorio)

        texto = " ".join(_apurar(sessao, empresa).avisos)

        assert "split payment" in texto
        assert "regimes específicos" in texto
        assert "diferimento" not in texto, "não há diferimento neste período"


class TestSerializacao:
    def test_to_dict_traz_as_parcelas_separadas(self, sessao, escritorio):
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, itens=2)

        dados = _apurar(sessao, emitente).to_dict()

        assert dados["ibs_uf"]["debito"] == pytest.approx(2 * IBS_UF_POR_ITEM)
        assert dados["ibs_municipal"]["debito"] == pytest.approx(2 * IBS_MUN_POR_ITEM)
        assert dados["ibs_uf"] != dados["ibs_municipal"]

    def test_to_dict_traz_o_periodo_e_os_avisos(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)

        dados = _apurar(sessao, empresa).to_dict()

        assert dados["periodo"] == ["2026-07-01", "2026-07-31"]
        assert dados["avisos"], "sem os avisos o número engana"

    def test_to_dict_nao_compartilha_a_lista_de_avisos(self, sessao, escritorio):
        """Quem consumir o dicionário não pode alterar o resultado."""
        empresa = _empresa(sessao, escritorio)
        resultado = _apurar(sessao, empresa)

        dados = resultado.to_dict()
        dados["avisos"].clear()

        assert resultado.avisos, "o to_dict devolveu a própria lista"


class TestNotaSemOsGruposNovos:
    def test_documento_anterior_a_reforma_nao_quebra(self, sessao, escritorio):
        """Histórico importado não tem IBS/CBS/IS, e apurar tem de seguir.

        A transição dura sete anos: os dois regimes convivem, e o mesmo
        período mistura notas com e sem os grupos novos.
        """
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, com_reforma=False)

        resultado = _apurar(sessao, emitente)

        assert resultado.documentos == 1
        assert resultado.cbs.debito == 0.0
        assert resultado.seletivo == 0.0

    def test_periodo_misto_soma_so_o_que_tem(self, sessao, escritorio):
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        _importar(sessao, escritorio, com_reforma=False)
        _importar(
            sessao,
            escritorio,
            chave="35260712345678000195550010000000021000000017",
            numero="2",
        )

        resultado = _apurar(sessao, emitente)

        assert resultado.documentos == 2
        assert resultado.cbs.debito == pytest.approx(CBS_POR_ITEM)


class TestOQueNaoEConsumidoEMedido:
    """Aviso genérico é o mesmo para quem tem e para quem não tem.

    E aviso que aparece sempre treina a pessoa a ignorar todos os outros. Os
    campos que a apuração não consome têm valor no próprio documento, então
    dá para medi-los — sem depender de interpretar código nenhum.
    """

    def _com_valor(self, sessao, escritorio, campo, valor, **kwargs):
        _importar(sessao, escritorio, **kwargs)
        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        for item in documento.itens:
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo=campo,
                valor_novo=valor,
                origem=ORIGEM_USUARIO,
            )
        sessao.commit()
        return documento

    def test_sem_esses_valores_nao_ha_aviso(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio)

        resultado = _apurar(sessao, empresa)

        assert resultado.nao_cobertos == {}
        assert not [a for a in resultado.avisos if "NÃO consumiu" in a]

    def test_diferimento_e_medido_com_valor_e_contagem(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        self._com_valor(sessao, escritorio, "valor_diferido_cbs", 1500.0, itens=2)

        resultado = _apurar(sessao, empresa)

        assert resultado.nao_cobertos["diferimento da CBS"] == (3000.0, 2)
        aviso = next(a for a in resultado.avisos if "NÃO consumiu" in a)
        assert "diferimento da CBS: 3000.00 em 2 item(ns)" in aviso

    @pytest.mark.parametrize(
        ("campo", "rotulo"),
        [
            ("valor_credito_presumido_ibs", "crédito presumido do IBS"),
            ("valor_devolucao_ibs_mun", "devolução do IBS municipal"),
            ("valor_ibs_mono", "IBS monofásico"),
            ("valor_cbs_mono_retido", "CBS monofásica retida anteriormente"),
            ("valor_transf_credito_ibs", "transferência de crédito de IBS"),
            ("valor_ajuste_compet_cbs", "ajuste de competência de CBS"),
            ("valor_estorno_credito_ibs", "estorno de crédito de IBS"),
            # As três destinações do diferimento têm rótulos distintos: um
            # rótulo comum esconderia que o benefício é só de uma delas.
            ("valor_diferido_ibs_uf", "diferimento do IBS estadual"),
            ("valor_diferido_ibs_mun", "diferimento do IBS municipal"),
        ],
    )
    def test_cada_campo_tem_o_seu_rotulo(self, sessao, escritorio, campo, rotulo):
        empresa = _empresa(sessao, escritorio)
        self._com_valor(sessao, escritorio, campo, 10.0)

        assert rotulo in _apurar(sessao, empresa).nao_cobertos

    def test_o_valor_medido_nao_entra_no_total(self, sessao, escritorio):
        """Medir é para avisar, não para somar — ninguém sabe o tratamento."""
        empresa = _empresa(sessao, escritorio)
        self._com_valor(sessao, escritorio, "valor_diferido_cbs", 9999.0)

        resultado = _apurar(sessao, empresa)

        assert resultado.nao_cobertos["diferimento da CBS"] == (9999.0, 1)
        assert resultado.total_devido == 0.0, "a nota é de entrada: só crédito"

    def test_a_medicao_vale_para_entrada_e_para_saida(self, sessao, escritorio):
        """Diferimento numa compra também precisa de tratamento próprio."""
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        self._com_valor(sessao, escritorio, "valor_diferido_cbs", 20.0)

        assert "diferimento da CBS" in _apurar(sessao, emitente).nao_cobertos


class TestOsCSTSaoListadosNaoInterpretados:
    """A IT 002/2025 ainda está em revisão e as fontes divergem.

    Codificar a semântica de cada CST a partir de fonte secundária seria o
    mesmo erro que esta suíte vem corrigindo. Listar o que apareceu é honesto
    e útil; interpretar não é.
    """

    def _com_cst(self, sessao, escritorio, cst, **kwargs):
        _importar(sessao, escritorio, **kwargs)
        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        for item in documento.itens:
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo="cst_ibscbs",
                valor_novo=cst,
                origem=ORIGEM_USUARIO,
            )
        sessao.commit()

    def test_tributacao_integral_nao_vira_aviso(self, sessao, escritorio):
        """`000` é o caso comum: avisá-lo seria avisar sobre tudo."""
        empresa = _empresa(sessao, escritorio)
        self._com_cst(sessao, escritorio, "000")

        resultado = _apurar(sessao, empresa)

        assert resultado.cst_encontrados == {}
        assert not [a for a in resultado.avisos if "CST de IBS/CBS" in a]

    def test_outros_cst_sao_listados_com_a_contagem(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        self._com_cst(sessao, escritorio, "620", itens=3)

        resultado = _apurar(sessao, empresa)

        assert resultado.cst_encontrados == {"620": 3}
        aviso = next(a for a in resultado.avisos if "CST de IBS/CBS" in a)
        assert "620 (3 item(ns))" in aviso

    def test_o_valor_do_cst_diferente_continua_somado(self, sessao, escritorio):
        """Listar não é descartar: ninguém sabe o tratamento para descartar."""
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195")
        self._com_cst(sessao, escritorio, "510")

        resultado = _apurar(sessao, emitente)

        assert resultado.cbs.debito == 9.0
        assert resultado.cst_encontrados == {"510": 1}

    def test_cst_vazio_nao_vira_aviso(self, sessao, escritorio):
        """Nota anterior à reforma não traz o grupo, e isso não é anomalia."""
        empresa = _empresa(sessao, escritorio)
        _importar(sessao, escritorio, com_reforma=False)

        assert _apurar(sessao, empresa).cst_encontrados == {}
