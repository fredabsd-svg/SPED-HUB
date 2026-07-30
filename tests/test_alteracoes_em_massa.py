"""Alterações em massa: selecionar, simular, comparar, confirmar.

O que estes testes protegem:

  * **simular não muda nada** — o §13 exige ver o impacto antes de confirmar,
    e uma massa aplicada sem revisão alcança um mês inteiro de uma vez;
  * **o filtro trabalha sobre o efetivo** — um item já classificado tem de
    aparecer pelo valor novo, senão a segunda passada de saneamento não
    enxerga o que a primeira fez;
  * **seleção sem filtro é recusada** — pegaria a base inteira;
  * **as incompatibilidades barram**: CFOP de saída em documento de entrada,
    NCM com dígito faltando, documento cancelado;
  * **tudo se desfaz por lote**.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from src.db.models import (
    AjusteFiscal,
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import (
    ORIGEM_USUARIO,
    Alteracao,
    AlteracaoInvalida,
    Filtro,
    ImportadorDeDocumentos,
    Selecao,
    SelecaoVazia,
    aplicar_ajuste,
    confirmar,
    desfazer_lote,
    efetivo,
    simular,
)
from tests.fixtures_nfe import nfe_xml

CHAVE_A = "35260712345678000195550010000000011000000017"
CHAVE_B = "35260712345678000195550010000000021000000028"


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'massa.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def cenario(sessao):
    escritorio = Escritorio(nome="Teste", slug="teste")
    sessao.add(escritorio)
    sessao.commit()
    empresa = Empresa(cnpj="98765432000198", nome="Cliente", escritorio_id=escritorio.id)
    sessao.add(empresa)
    sessao.commit()

    importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
    a = importador.importar(nfe_xml(chave=CHAVE_A, itens=2))
    b = importador.importar(nfe_xml(chave=CHAVE_B, numero="2", itens=1))
    sessao.commit()
    return {
        "escritorio": escritorio,
        "empresa": empresa,
        "a": sessao.get(DocumentoFiscal, a.documento_id),
        "b": sessao.get(DocumentoFiscal, b.documento_id),
        "selecao": Selecao(
            escritorio_id=escritorio.id,
            filtros=[Filtro("cfop", "igual", "6102")],
        ),
    }


class TestSelecao:
    def test_selecao_sem_filtro_e_recusada(self, sessao, cenario):
        """Alcançaria todos os documentos do escritório de uma vez."""
        vazia = Selecao(escritorio_id=cenario["escritorio"].id)
        with pytest.raises(SelecaoVazia, match="sem filtro"):
            vazia.documentos(sessao)

    def test_periodo_recorta(self, sessao, cenario):
        selecao = Selecao(
            escritorio_id=cenario["escritorio"].id,
            data_inicio=datetime.date(2027, 1, 1),
        )
        assert selecao.documentos(sessao) == []

    def test_empresa_recorta(self, sessao, cenario):
        outra = Empresa(cnpj="11111111000111", nome="Outra", escritorio_id=cenario["escritorio"].id)
        sessao.add(outra)
        sessao.commit()
        selecao = Selecao(escritorio_id=cenario["escritorio"].id, empresa_id=outra.id)
        assert selecao.documentos(sessao) == []

    def test_escritorio_isola(self, sessao, cenario):
        outro = Escritorio(nome="Outro", slug="outro")
        sessao.add(outro)
        sessao.commit()
        selecao = Selecao(escritorio_id=outro.id, filtros=[Filtro("cfop", "igual", "6102")])
        assert selecao.documentos(sessao) == []

    def test_operador_inexistente_e_recusado(self):
        with pytest.raises(AlteracaoInvalida, match="operador"):
            Filtro("cfop", "parecido_com", "6102")


class TestSimulacao:
    def test_simular_nao_toca_no_banco(self, sessao, cenario):
        """É o passo que existe para ser lido antes de confirmar."""
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6404")])

        assert simulacao.total_mudancas == 3, "dois itens de A e um de B"
        assert not sessao.execute(select(AjusteFiscal)).scalars().all()
        assert efetivo(sessao, cenario["a"]).item(1)["cfop"] == "6102"

    def test_itens_afetados_nao_conta_o_cabecalho(self, sessao, cenario):
        """Uma alteração que pega cabeçalho E item não pode inflar a conta.

        `natureza_operacao` é do documento e `cfop` é do item: se o cabeçalho
        entrasse na contagem de itens, a tela diria ao operador que ele está
        mexendo em mais itens do que de fato mexe.
        """
        simulacao = simular(
            sessao,
            cenario["selecao"],
            [Alteracao("natureza_operacao", "COMPRA"), Alteracao("cfop", "2102")],
        )

        do_cabecalho = [m for m in simulacao.mudancas if m.item_id is None]
        assert do_cabecalho, "a alteração de cabeçalho não entrou"
        assert simulacao.itens_afetados == 3, "só os itens contam como itens"
        assert simulacao.documentos_afetados == 2

    def test_conta_documentos_e_itens(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6404")])
        assert simulacao.documentos_afetados == 2
        assert simulacao.itens_afetados == 3

    def test_mudanca_traz_o_antes_e_o_depois(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6404")])
        mudanca = simulacao.mudancas[0]
        assert mudanca.valor_anterior == "6102"
        assert mudanca.valor_novo == "6404"
        assert mudanca.chave in (CHAVE_A, CHAVE_B)

    def test_impacto_em_reais(self, sessao, cenario):
        """O número que decide se a alteração passa."""
        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id, filtros=[Filtro("cfop", "igual", "6102")]
            ),
            [Alteracao("base_icms", 900.0)],
        )
        assert simulacao.impacto_total == pytest.approx(-300.0), "3 itens × −100"

    def test_troca_de_texto_nao_tem_impacto(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6404")])
        assert simulacao.impacto_total == 0.0

    def test_valor_igual_ao_atual_nao_vira_mudanca(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6102")])
        assert simulacao.total_mudancas == 0

    def test_resumo_por_campo(self, sessao, cenario):
        simulacao = simular(
            sessao,
            cenario["selecao"],
            [Alteracao("cfop", "6404"), Alteracao("cst_pis", "50")],
        )
        assert simulacao.por_campo() == {"cfop": 3, "cst_pis": 3}
        assert simulacao.to_dict()["total_mudancas"] == 6

    def test_sem_alteracao_configurada(self, sessao, cenario):
        with pytest.raises(AlteracaoInvalida, match="nenhuma alteração"):
            simular(sessao, cenario["selecao"], [])


class TestNiveis:
    """Cabeçalho e item convivem, e confundi-los estraga o documento."""

    def test_filtro_de_item_alcanca_o_cabecalho(self, sessao, cenario):
        """ "Documentos que tenham item com NCM 2203, mudar a natureza."

        O filtro é de item e o campo alterado é de cabeçalho. Sem isto o
        cabeçalho nunca casaria, porque não tem a coluna `ncm` — e essa
        combinação, que o §11.2 pede, seria impossível.
        """
        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[Filtro("ncm", "comeca_com", "2203")],
            ),
            [Alteracao("natureza_operacao", "COMPRA PARA REVENDA")],
        )

        assert simulacao.total_mudancas == 2, "um por documento"
        assert all(m.item_id is None for m in simulacao.mudancas)

    def test_filtro_de_item_que_nenhum_item_satisfaz_nao_alcanca(self, sessao, cenario):
        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[Filtro("ncm", "comeca_com", "8471")],
            ),
            [Alteracao("natureza_operacao", "COMPRA")],
        )
        assert simulacao.total_mudancas == 0

    def test_campo_que_existe_nos_dois_niveis_so_atinge_o_item(self, sessao, cenario):
        """`base_icms` é parcela no item e TOTAL no documento.

        Sobrescrever o total com o valor de uma parcela produziria um
        documento cujo cabeçalho não bate com a soma dos itens — e ninguém
        pede isso ao mandar "alterar a base de cálculo". Ajustar o total é
        recálculo, não substituição.
        """
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("base_icms", 900.0)])

        assert all(
            m.item_id is not None for m in simulacao.mudancas
        ), "o total do documento foi sobrescrito com o valor de um item"
        assert simulacao.impacto_total == pytest.approx(-300.0), "3 itens × −100"


class TestFiltroSobreOEfetivo:
    def test_item_ja_ajustado_aparece_pelo_valor_novo(self, sessao, cenario):
        """A segunda passada precisa enxergar o que a primeira fez.

        Filtrar o normalizado faria o item classificado sumir do recorte, e o
        saneamento em duas etapas — que é como se trabalha de verdade — não
        funcionaria.
        """
        documento = cenario["a"]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="5405",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[Filtro("cfop", "igual", "5405")],
            ),
            [Alteracao("cfop", "5102")],
        )

        assert simulacao.total_mudancas == 1
        assert simulacao.mudancas[0].valor_anterior == "5405"

    def test_item_ajustado_some_do_filtro_pelo_valor_antigo(self, sessao, cenario):
        documento = cenario["a"]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="5405",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[Filtro("cfop", "igual", "6102")],
            ),
            [Alteracao("cfop", "6404")],
        )

        assert simulacao.total_mudancas == 2, "o item ajustado saiu do recorte"


class TestFiltrosCombinados:
    def test_dois_filtros_sao_E(self, sessao, cenario):
        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[
                    Filtro("cfop", "igual", "6102"),
                    Filtro("ncm", "comeca_com", "8471"),
                ],
            ),
            [Alteracao("cfop", "6404")],
        )
        assert simulacao.total_mudancas == 0

    def test_filtro_de_escopo_com_filtro_de_conteudo(self, sessao, cenario):
        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[
                    Filtro("numero", "igual", "2"),
                    Filtro("ncm", "comeca_com", "2203"),
                ],
            ),
            [Alteracao("cfop", "6404")],
        )
        assert simulacao.documentos_afetados == 1
        assert simulacao.mudancas[0].chave == CHAVE_B

    def test_faixa_de_valores(self, sessao, cenario):
        simulacao = simular(
            sessao,
            Selecao(
                escritorio_id=cenario["escritorio"].id,
                filtros=[Filtro("valor_total", "maior_que", 500)],
            ),
            [Alteracao("natureza_operacao", "REVENDA")],
        )
        assert simulacao.total_mudancas == 2, "só o cabeçalho dos dois documentos"


class TestPreencherVazios:
    def test_apenas_vazios_nao_sobrescreve(self, sessao, cenario):
        """A operação mais comum do saneamento, e a mais perigosa de errar."""
        simulacao = simular(
            sessao, cenario["selecao"], [Alteracao("cfop", "5949", apenas_vazios=True)]
        )
        assert simulacao.total_mudancas == 0, "o CFOP já estava preenchido"

    def test_apenas_vazios_preenche_o_que_falta(self, sessao, cenario):
        simulacao = simular(
            sessao,
            cenario["selecao"],
            [Alteracao("codigo_servico", "0000", apenas_vazios=True)],
        )
        assert simulacao.total_mudancas == 3


class TestProtecoes:
    def test_cfop_de_saida_em_documento_de_entrada(self, sessao, cenario):
        """O caso real mais comum, e o que esta proteção existe para pegar.

        A nota de compra chega com o CFOP do fornecedor (6102, saída dele).
        Quem escritura a entrada tem de trocar por um 2xxx. Deixar um 5xxx ou
        6xxx passar produziria escrituração que o validador do Fisco rejeita.
        """
        assert cenario["a"].sentido == "entrada"

        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6404")])

        assert simulacao.impedida
        assert any("é de saída, e o documento é de entrada" in a.problema for a in simulacao.avisos)

    def test_cfop_com_formato_errado(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "64")])
        assert simulacao.impedida
        assert any("quatro dígitos" in a.problema for a in simulacao.avisos)

    def test_ncm_com_formato_errado(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("ncm", "2203")])
        assert simulacao.impedida
        assert any("oito dígitos" in a.problema for a in simulacao.avisos)

    def test_cest_com_formato_errado(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cest", "30010")])
        assert simulacao.impedida

    def test_cst_do_ibscbs_com_formato_errado(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cst_ibscbs", "00")])
        assert simulacao.impedida
        assert any("três dígitos" in a.problema for a in simulacao.avisos)

    def test_documento_cancelado_e_impeditivo(self, sessao, cenario):
        """Alterar nota cancelada gera arquivo que o validador rejeita."""
        cenario["a"].situacao = "cancelado"
        sessao.flush()

        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "6404")])

        assert simulacao.impedida
        assert any("cancelado" in a.problema for a in simulacao.avisos)

    def test_cfop_de_entrada_em_documento_de_entrada_passa(self, sessao, cenario):
        """2102 é o CFOP que a escrituração da compra realmente usa."""
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "2102")])
        assert not simulacao.impedida
        assert not simulacao.avisos

    def test_aviso_se_descreve(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("ncm", "2203")])
        texto = str(simulacao.avisos[0])
        assert "IMPEDITIVO" in texto and "ncm" in texto


class TestConfirmar:
    def test_confirmar_grava_o_que_a_simulacao_previu(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "2102")])

        lote = confirmar(sessao, simulacao, motivo="revisão do fechamento")

        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert len(ajustes) == 3
        assert all(a.lote == lote for a in ajustes)
        assert all(a.origem == ORIGEM_USUARIO for a in ajustes)
        assert all(a.motivo == "revisão do fechamento" for a in ajustes)
        assert efetivo(sessao, cenario["a"]).item(1)["cfop"] == "2102"

    def test_o_normalizado_segue_intacto(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "2102")])
        confirmar(sessao, simulacao)
        sessao.commit()
        sessao.expire_all()

        assert sessao.get(DocumentoFiscal, cenario["a"].id).itens[0].cfop == "6102"

    def test_massa_inteira_se_desfaz(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("cfop", "2102")])
        lote = confirmar(sessao, simulacao)

        assert desfazer_lote(sessao, lote) == 3
        assert efetivo(sessao, cenario["a"]).item(1)["cfop"] == "6102"

    def test_impeditivo_barra_a_confirmacao(self, sessao, cenario):
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("ncm", "2203")])
        with pytest.raises(AlteracaoInvalida, match="impeditivo"):
            confirmar(sessao, simulacao)
        assert not sessao.execute(select(AjusteFiscal)).scalars().all()

    def test_forcar_passa_por_cima_de_proposito(self, sessao, cenario):
        """A saída existe, mas tem de ser escolhida."""
        simulacao = simular(sessao, cenario["selecao"], [Alteracao("ncm", "2203")])

        lote = confirmar(sessao, simulacao, forcar=True)

        assert len(sessao.execute(select(AjusteFiscal)).scalars().all()) == 3
        assert desfazer_lote(sessao, lote) == 3

    def test_dois_lotes_sao_independentes(self, sessao, cenario):
        primeiro = confirmar(
            sessao, simular(sessao, cenario["selecao"], [Alteracao("cfop", "2102")])
        )
        segundo = confirmar(
            sessao,
            simular(
                sessao,
                Selecao(
                    escritorio_id=cenario["escritorio"].id,
                    filtros=[Filtro("cfop", "igual", "2102")],
                ),
                [Alteracao("cst_pis", "50")],
            ),
        )

        desfazer_lote(sessao, segundo)

        visao = efetivo(sessao, cenario["a"])
        assert visao.item(1)["cfop"] == "2102", "o primeiro lote continua"
        assert visao.item(1)["cst_pis"] == "01"
        assert primeiro != segundo
