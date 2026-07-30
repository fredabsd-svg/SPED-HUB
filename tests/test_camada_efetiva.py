"""A terceira camada: o normalizado mais os ajustes, calculado na hora.

O que estes testes protegem:

  * **o normalizado nunca muda** — ajustar não reescreve o que veio do XML,
    e é isso que permite comparar, auditar e reverter;
  * **desfazer um lote basta** para o documento voltar ao que era, porque não
    há valor a restaurar;
  * **o tipo sobrevive à viagem** — o ajuste é gravado como texto, e um CFOP
    que volta `float` ou um valor que volta `str` quebraria a apuração em
    silêncio;
  * **a ordem decide** — vale o ajuste mais recente, não o primeiro.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from src.db.models import (
    AjusteFiscal,
    DocumentoFiscal,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import (
    ORIGEM_REGRA,
    ORIGEM_USUARIO,
    CampoInexistente,
    ImportadorDeDocumentos,
    OrigemInvalida,
    aplicar_ajuste,
    desfazer_lote,
    efetivo,
    historico,
    novo_lote,
    valor_efetivo,
)
from tests.fixtures_nfe import nfe_xml


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'efetivo.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def documento(sessao) -> DocumentoFiscal:
    escritorio = Escritorio(nome="Teste", slug="teste")
    sessao.add(escritorio)
    sessao.commit()
    ocorrencia = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(
        nfe_xml(itens=2)
    )
    sessao.commit()
    return sessao.get(DocumentoFiscal, ocorrencia.documento_id)


class TestValorEfetivo:
    def test_sem_ajuste_vale_o_normalizado(self, documento):
        item = documento.itens[0]
        assert valor_efetivo(item, "cfop") == "6102"

    def test_com_ajuste_vale_o_ajuste(self, sessao, documento):
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert valor_efetivo(item, "cfop", ajustes) == "6404"

    def test_vale_o_mais_recente(self, sessao, documento):
        """Ajustes se empilham; o último é o que sai."""
        item = documento.itens[0]
        for cfop in ("6404", "6108", "5102"):
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo="cfop",
                valor_novo=cfop,
                origem=ORIGEM_USUARIO,
            )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert valor_efetivo(item, "cfop", ajustes) == "5102"

    def test_ordem_e_por_criado_em_e_nao_pela_ordem_da_lista(self, sessao, documento):
        """O banco não promete ordem, e o Postgres não a dá de graça.

        Se o efetivo dependesse da ordem em que as linhas voltaram da
        consulta, o mesmo documento sairia diferente entre SQLite e Postgres —
        e ninguém desconfiaria.
        """
        import datetime as _dt

        item = documento.itens[0]
        base = _dt.datetime(2026, 7, 30, 12, 0, 0)
        antigo = AjusteFiscal(
            documento_id=documento.id,
            item_id=item.id,
            campo="cfop",
            valor_novo="1111",
            origem=ORIGEM_USUARIO,
            criado_em=base,
        )
        recente = AjusteFiscal(
            documento_id=documento.id,
            item_id=item.id,
            campo="cfop",
            valor_novo="9999",
            origem=ORIGEM_USUARIO,
            criado_em=base + _dt.timedelta(hours=1),
        )
        sessao.add_all([antigo, recente])
        sessao.flush()

        # Embaralhado de propósito: o recente primeiro.
        assert valor_efetivo(item, "cfop", [recente, antigo]) == "9999"
        assert valor_efetivo(item, "cfop", [antigo, recente]) == "9999"

    def test_desempate_por_id_quando_o_instante_e_o_mesmo(self, sessao, documento):
        """Um lote de alteração em massa nasce todo no mesmo instante.

        Sem o desempate, qual dos ajustes vale passa a depender da ordem em
        que o banco devolveu as linhas — e o resultado da massa vira loteria.
        """
        import datetime as _dt

        item = documento.itens[0]
        instante = _dt.datetime(2026, 7, 30, 12, 0, 0)
        primeiro = AjusteFiscal(
            documento_id=documento.id,
            item_id=item.id,
            campo="cfop",
            valor_novo="1111",
            origem=ORIGEM_USUARIO,
            criado_em=instante,
        )
        sessao.add(primeiro)
        sessao.flush()
        segundo = AjusteFiscal(
            documento_id=documento.id,
            item_id=item.id,
            campo="cfop",
            valor_novo="2222",
            origem=ORIGEM_USUARIO,
            criado_em=instante,
        )
        sessao.add(segundo)
        sessao.flush()

        assert segundo.id > primeiro.id
        # Embaralhado: o de id maior vem primeiro na lista.
        assert valor_efetivo(item, "cfop", [segundo, primeiro]) == "2222"

    def test_ajuste_de_outro_campo_nao_interfere(self, sessao, documento):
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="ncm",
            valor_novo="84713012",
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert valor_efetivo(item, "cfop", ajustes) == "6102"
        assert valor_efetivo(item, "ncm", ajustes) == "84713012"

    def test_campo_inexistente_falha_cedo(self, documento):
        """Ajuste com nome errado ficaria gravado e nunca chegaria ao SPED."""
        with pytest.raises(CampoInexistente, match="cfopp"):
            valor_efetivo(documento.itens[0], "cfopp")

    def test_o_normalizado_nunca_muda(self, sessao, documento):
        """É o que permite comparar, auditar e reverter."""
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_USUARIO,
        )
        sessao.commit()
        sessao.expire_all()

        recarregado = sessao.get(DocumentoFiscal, documento.id)
        assert recarregado.itens[0].cfop == "6102", "o ajuste reescreveu o normalizado"


class TestTipos:
    """O ajuste é texto no banco; o tipo tem de voltar inteiro."""

    def test_float_volta_float(self, sessao, documento):
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="valor_icms",
            valor_novo=200.50,
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        efetivado = valor_efetivo(item, "valor_icms", ajustes)
        assert efetivado == 200.50
        assert isinstance(efetivado, float), "voltou como texto — a apuração somaria errado"

    def test_int_volta_int(self, sessao, documento):
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="numero_item",
            valor_novo=7,
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert valor_efetivo(item, "numero_item", ajustes) == 7

    def test_data_volta_data(self, sessao, documento):
        aplicar_ajuste(
            sessao,
            documento=documento,
            campo="data_emissao",
            valor_novo=datetime.date(2026, 8, 15),
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        efetivado = valor_efetivo(documento, "data_emissao", ajustes)
        assert efetivado == datetime.date(2026, 8, 15)
        assert isinstance(efetivado, datetime.date)

    def test_limpar_campo_e_diferente_de_nao_ajustar(self, sessao, documento):
        """A linha do ajuste existir é o sinal; nulo quer dizer "limpou"."""
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cest",
            valor_novo=None,
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert item.cest == "0300100", "o normalizado segue lá"
        assert valor_efetivo(item, "cest", ajustes) is None

    def test_valor_corrompido_nao_derruba_a_geracao(self, sessao, documento):
        """Um ajuste ilegível não pode impedir o mês inteiro de sair."""
        item = documento.itens[0]
        sessao.add(
            AjusteFiscal(
                documento_id=documento.id,
                item_id=item.id,
                campo="valor_icms",
                valor_novo="não é número",
                origem=ORIGEM_USUARIO,
            )
        )
        sessao.flush()
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert valor_efetivo(item, "valor_icms", ajustes) == "não é número"


class TestAplicarAjuste:
    def test_guarda_o_valor_anterior(self, sessao, documento):
        item = documento.itens[0]
        ajuste = aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_USUARIO,
            motivo="revenda interestadual",
        )
        assert ajuste.valor_anterior == "6102"
        assert ajuste.valor_novo == "6404"
        assert ajuste.motivo == "revenda interestadual"

    def test_anterior_e_o_efetivo_e_nao_o_normalizado(self, sessao, documento):
        """O segundo ajuste parte de onde o primeiro deixou."""
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_USUARIO,
        )
        segundo = aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="5102",
            origem=ORIGEM_USUARIO,
        )
        assert segundo.valor_anterior == "6404"

    def test_ajuste_sem_efeito_nao_e_gravado(self, sessao, documento):
        """Poluiria o histórico e faria a simulação relatar impacto que não há."""
        item = documento.itens[0]
        assert (
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo="cfop",
                valor_novo="6102",
                origem=ORIGEM_USUARIO,
            )
            is None
        )
        assert not sessao.execute(select(AjusteFiscal)).scalars().all()

    def test_origem_separa_sugestao_de_decisao(self, sessao, documento):
        item = documento.itens[0]
        sugerido = aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_REGRA,
            regra="revenda-interestadual",
        )
        assert sugerido.origem == ORIGEM_REGRA
        assert sugerido.regra == "revenda-interestadual"

    def test_origem_invalida_e_recusada(self, sessao, documento):
        with pytest.raises(OrigemInvalida):
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo="cfop",
                valor_novo="6404",
                origem="magia",
            )

    def test_campo_inexistente_e_recusado(self, sessao, documento):
        with pytest.raises(CampoInexistente):
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo="nao_existe",
                valor_novo="x",
                origem=ORIGEM_USUARIO,
            )

    def test_ajuste_de_cabecalho_e_de_item_nao_se_misturam(self, sessao, documento):
        aplicar_ajuste(
            sessao,
            documento=documento,
            campo="natureza_operacao",
            valor_novo="DEVOLUCAO",
            origem=ORIGEM_USUARIO,
        )
        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert ajustes[0].item_id is None


class TestVisaoEfetiva:
    def test_documento_inteiro_com_ajustes_aplicados(self, sessao, documento):
        aplicar_ajuste(
            sessao,
            documento=documento,
            campo="natureza_operacao",
            valor_novo="DEVOLUCAO DE VENDA",
            origem=ORIGEM_USUARIO,
        )
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="6202",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        visao = efetivo(sessao, documento)
        assert visao.valores["natureza_operacao"] == "DEVOLUCAO DE VENDA"
        assert visao.item(1)["cfop"] == "6202"
        assert visao.item(2)["cfop"] == "6102", "o item não ajustado não muda"

    def test_aponta_o_que_foi_alterado(self, sessao, documento):
        """A tela precisa distinguir o que veio do XML do que alguém mexeu."""
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[1],
            campo="ncm",
            valor_novo="84713012",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        visao = efetivo(sessao, documento)
        assert visao.alterado
        assert visao.campos_alterados == set()
        assert visao.itens_alterados == {2: {"ncm"}}

    def test_documento_intocado_nao_se_diz_alterado(self, sessao, documento):
        visao = efetivo(sessao, documento)
        assert not visao.alterado
        assert visao.valores["numero"] == "1"

    def test_uma_consulta_por_documento(self, sessao, documento):
        """Consultar por campo daria centenas de milhares de consultas no mês."""
        for campo, valor in (("cfop", "6202"), ("ncm", "84713012"), ("cest", "2100100")):
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo=campo,
                valor_novo=valor,
                origem=ORIGEM_USUARIO,
            )
        sessao.flush()
        sessao.expire_all()

        consultas = []
        from sqlalchemy import event

        engine = sessao.get_bind()
        registrar = lambda *a, **k: consultas.append(a[2])  # noqa: E731
        event.listen(engine, "before_cursor_execute", registrar)
        try:
            visao = efetivo(sessao, sessao.get(DocumentoFiscal, documento.id))
            assert visao.item(1)["cfop"] == "6202"
        finally:
            event.remove(engine, "before_cursor_execute", registrar)

        de_ajustes = [c for c in consultas if "ajustes_fiscais" in c.lower()]
        assert len(de_ajustes) == 1, f"{len(de_ajustes)} consultas à tabela de ajustes"

    def test_item_inexistente(self, sessao, documento):
        with pytest.raises(KeyError):
            efetivo(sessao, documento).item(99)


class TestReversao:
    def test_desfazer_lote_volta_tudo(self, sessao, documento):
        """Não há valor a restaurar: o normalizado nunca foi tocado."""
        lote = novo_lote()
        for item in documento.itens:
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=item,
                campo="cfop",
                valor_novo="6404",
                origem=ORIGEM_USUARIO,
                lote=lote,
            )
        sessao.flush()
        assert efetivo(sessao, documento).item(1)["cfop"] == "6404"

        removidos = desfazer_lote(sessao, lote)

        assert removidos == 2
        assert efetivo(sessao, documento).item(1)["cfop"] == "6102"
        assert not efetivo(sessao, documento).alterado

    def test_desfazer_preserva_os_outros_lotes(self, sessao, documento):
        primeiro, segundo = novo_lote(), novo_lote()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_USUARIO,
            lote=primeiro,
        )
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[1],
            campo="ncm",
            valor_novo="84713012",
            origem=ORIGEM_USUARIO,
            lote=segundo,
        )
        sessao.flush()

        desfazer_lote(sessao, primeiro)

        visao = efetivo(sessao, documento)
        assert visao.item(1)["cfop"] == "6102"
        assert visao.item(2)["ncm"] == "84713012", "o outro lote foi junto"

    def test_lote_vazio_e_recusado(self, sessao, documento):
        """Apagaria todo ajuste avulso do banco."""
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()
        with pytest.raises(ValueError, match="lote"):
            desfazer_lote(sessao, "")
        assert sessao.execute(select(AjusteFiscal)).scalars().all()

    def test_desfazer_lote_inexistente_nao_estoura(self, sessao, documento):
        assert desfazer_lote(sessao, "nao-existe") == 0

    def test_lotes_sao_distintos(self):
        assert novo_lote() != novo_lote()


class TestHistorico:
    def test_conta_a_historia_do_campo_em_ordem(self, sessao, documento):
        """ "Por que este registro saiu assim?" se responde aqui."""
        item = documento.itens[0]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="6404",
            origem=ORIGEM_REGRA,
            regra="revenda",
        )
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="cfop",
            valor_novo="5102",
            origem=ORIGEM_USUARIO,
            motivo="operação interna",
        )
        sessao.flush()

        # Um ajuste de OUTRO campo, para o filtro ter o que excluir.
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=item,
            campo="ncm",
            valor_novo="84713012",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        linha = historico(sessao, documento, "cfop")
        assert [(a.valor_anterior, a.valor_novo, a.origem) for a in linha] == [
            ("6102", "6404", ORIGEM_REGRA),
            ("6404", "5102", ORIGEM_USUARIO),
        ], "o histórico de cfop trouxe ajuste de outro campo"

    def test_historico_do_documento_inteiro(self, sessao, documento):
        aplicar_ajuste(
            sessao,
            documento=documento,
            campo="natureza_operacao",
            valor_novo="DEVOLUCAO",
            origem=ORIGEM_USUARIO,
        )
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="6202",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()
        assert len(historico(sessao, documento)) == 2

    def test_documento_sem_ajuste_tem_historico_vazio(self, sessao, documento):
        assert historico(sessao, documento) == []
