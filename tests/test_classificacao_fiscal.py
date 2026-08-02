"""Motor de classificação: a regra sugere, ninguém aplica em silêncio.

O que estes testes protegem:

  * **sugerir e aplicar são passos separados** — avaliar não toca no banco, e
    uma classificação errada aplicada sobre um mês inteiro só se descobre na
    malha fina;
  * **conflito é denunciado, não resolvido no sorteio** — duas regras de mesma
    prioridade no mesmo campo fariam a mesma importação dar resultados
    diferentes;
  * **a regra lê o efetivo**, não o normalizado, senão a segunda regra
    classificaria em cima de um valor que a primeira já mudou;
  * **regra fiscal tem vigência** — a que valia até dezembro não pode alcançar
    documento de janeiro.
"""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy import select

from src.db.models import (
    AjusteFiscal,
    DocumentoFiscal,
    Empresa,
    Escritorio,
    RegraFiscal,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import (
    ORIGEM_REGRA,
    ORIGEM_USUARIO,
    ImportadorDeDocumentos,
    MotorDeClassificacao,
    RegraInvalida,
    aplicar,
    aplicar_ajuste,
    criar_regra,
    desfazer_lote,
    efetivo,
    regras_aplicaveis,
)
from tests.fixtures_nfe import nfe_xml


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'classificacao.db'}")
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
    ocorrencia = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(
        nfe_xml(itens=2)
    )
    sessao.commit()
    return {
        "escritorio": escritorio,
        "empresa": empresa,
        "documento": sessao.get(DocumentoFiscal, ocorrencia.documento_id),
    }


def _regra_cfop(sessao, escritorio, **kwargs):
    padrao = dict(
        nome="revenda-interestadual",
        descricao="NCM de bebida em operação interestadual é revenda",
        condicoes=[
            {"campo": "ncm", "operador": "comeca_com", "valor": "2203"},
            {"campo": "cfop", "operador": "igual", "valor": "6102"},
        ],
        acoes=[{"campo": "cfop", "valor": "6404"}],
        escritorio_id=escritorio.id,
    )
    padrao.update(kwargs)
    return criar_regra(sessao, **padrao)


class TestAvaliar:
    def test_regra_que_casa_produz_sugestao(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"])

        resultado = MotorDeClassificacao(sessao).avaliar(cenario["documento"])

        assert resultado.total == 2, "os dois itens casam"
        sugestao = resultado.sugestoes[0]
        assert sugestao.campo == "cfop"
        assert sugestao.valor_anterior == "6102"
        assert sugestao.valor_sugerido == "6404"
        assert sugestao.regra_nome == "revenda-interestadual"
        assert "revenda" in sugestao.justificativa

    def test_avaliar_nao_toca_no_banco(self, sessao, cenario):
        """Sugerir é proposta; gravar é decisão de quem lê."""
        _regra_cfop(sessao, cenario["escritorio"])

        MotorDeClassificacao(sessao).avaliar(cenario["documento"])

        assert not sessao.execute(select(AjusteFiscal)).scalars().all()
        assert efetivo(sessao, cenario["documento"]).item(1)["cfop"] == "6102"

    def test_regra_que_nao_casa_nao_sugere(self, sessao, cenario):
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            condicoes=[{"campo": "ncm", "operador": "comeca_com", "valor": "8471"}],
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_todas_as_condicoes_precisam_casar(self, sessao, cenario):
        """São E, não OU: a segunda condição sozinha derruba a regra."""
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            condicoes=[
                {"campo": "ncm", "operador": "comeca_com", "valor": "2203"},
                {"campo": "cfop", "operador": "igual", "valor": "5102"},
            ],
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_sugestao_igual_ao_valor_atual_e_descartada(self, sessao, cenario):
        """Sugerir o que já está lá poluiria a tela de revisão."""
        _regra_cfop(sessao, cenario["escritorio"], acoes=[{"campo": "cfop", "valor": "6102"}])
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_regra_sobre_campo_do_cabecalho(self, sessao, cenario):
        criar_regra(
            sessao,
            nome="devolucao",
            condicoes=[{"campo": "emitente_uf", "operador": "igual", "valor": "SP"}],
            acoes=[{"campo": "natureza_operacao", "valor": "DEVOLUCAO"}],
            escritorio_id=cenario["escritorio"].id,
        )
        resultado = MotorDeClassificacao(sessao).avaliar(cenario["documento"])

        do_cabecalho = [s for s in resultado.sugestoes if s.item_id is None]
        assert len(do_cabecalho) == 1
        assert do_cabecalho[0].valor_sugerido == "DEVOLUCAO"

    def test_regra_inativa_e_ignorada(self, sessao, cenario):
        regra = _regra_cfop(sessao, cenario["escritorio"])
        regra.ativa = False
        sessao.flush()
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_le_o_efetivo_e_nao_o_normalizado(self, sessao, cenario):
        """A segunda regra vê o que a primeira decidiu.

        Sem isto, uma regra classificaria em cima de um valor que já não vale,
        e a ordem das regras deixaria de significar o que aparenta.
        """
        documento = cenario["documento"]
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="5102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            condicoes=[{"campo": "cfop", "operador": "igual", "valor": "5102"}],
            acoes=[{"campo": "cfop", "valor": "5405"}],
        )

        resultado = MotorDeClassificacao(sessao).avaliar(documento)

        assert resultado.total == 1, "só o item já ajustado casa"
        assert resultado.sugestoes[0].valor_anterior == "5102"


class TestOperadores:
    @pytest.mark.parametrize(
        ("operador", "valor", "casa"),
        [
            ("igual", "22030000", True),
            ("igual", "84713012", False),
            ("diferente", "84713012", True),
            ("em", ["22030000", "22084000"], True),
            ("em", ["84713012"], False),
            ("nao_em", ["84713012"], True),
            ("comeca_com", "2203", True),
            ("comeca_com", "8471", False),
            ("contem", "0300", True),
            ("preenchido", None, True),
            ("vazio", None, False),
        ],
    )
    def test_operador(self, sessao, cenario, operador, valor, casa):
        criar_regra(
            sessao,
            nome=f"teste-{operador}",
            condicoes=[{"campo": "ncm", "operador": operador, "valor": valor}],
            acoes=[{"campo": "cfop", "valor": "6404"}],
            escritorio_id=cenario["escritorio"].id,
        )
        total = MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total
        assert (total > 0) is casa

    def test_operadores_numericos(self, sessao, cenario):
        criar_regra(
            sessao,
            nome="acima-de-500",
            condicoes=[{"campo": "valor_total", "operador": "maior_que", "valor": 500}],
            acoes=[{"campo": "cfop", "valor": "6404"}],
            escritorio_id=cenario["escritorio"].id,
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 2

    def test_campo_vazio_e_alcancavel(self, sessao, cenario):
        """Preencher o que falta é metade do saneamento (§12.3)."""
        criar_regra(
            sessao,
            nome="preencher-codigo-servico",
            condicoes=[{"campo": "codigo_servico", "operador": "vazio"}],
            acoes=[{"campo": "codigo_servico", "valor": "0000"}],
            escritorio_id=cenario["escritorio"].id,
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 2


class TestPrioridadeEConflito:
    def test_maior_prioridade_vence(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"], nome="generica", prioridade=1)
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            nome="especifica",
            prioridade=10,
            acoes=[{"campo": "cfop", "valor": "6108"}],
        )

        resultado = MotorDeClassificacao(sessao).avaliar(cenario["documento"])

        assert not resultado.conflitos
        assert {s.valor_sugerido for s in resultado.sugestoes} == {"6108"}
        assert {s.regra_nome for s in resultado.sugestoes} == {"especifica"}

    def test_empate_no_mesmo_campo_vira_conflito(self, sessao, cenario):
        """Escolher por sorteio faria a mesma importação variar."""
        _regra_cfop(sessao, cenario["escritorio"], nome="regra-a", prioridade=5)
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            nome="regra-b",
            prioridade=5,
            acoes=[{"campo": "cfop", "valor": "6108"}],
        )

        resultado = MotorDeClassificacao(sessao).avaliar(cenario["documento"])

        assert resultado.total == 0, "nenhuma sugestão sai de um empate"
        assert len(resultado.conflitos) == 2
        conflito = resultado.conflitos[0]
        assert conflito.campo == "cfop"
        assert conflito.regras == ["regra-a", "regra-b"]
        assert "regra-a" in str(conflito) and "5" in str(conflito)

    def test_empate_em_campos_diferentes_nao_e_conflito(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"], nome="do-cfop", prioridade=5)
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            nome="do-ncm",
            prioridade=5,
            acoes=[{"campo": "ncm", "valor": "22084000"}],
        )

        resultado = MotorDeClassificacao(sessao).avaliar(cenario["documento"])

        assert not resultado.conflitos
        assert {s.campo for s in resultado.sugestoes} == {"cfop", "ncm"}


class TestVigencia:
    def test_regra_futura_nao_alcanca_documento_antigo(self, sessao, cenario):
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            vigencia_inicio=datetime.date(2027, 1, 1),
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_regra_encerrada_nao_alcanca_documento_novo(self, sessao, cenario):
        """A que valia até dezembro não pode classificar janeiro."""
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            vigencia_fim=datetime.date(2026, 6, 30),
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_regra_dentro_da_vigencia_vale(self, sessao, cenario):
        _regra_cfop(
            sessao,
            cenario["escritorio"],
            vigencia_inicio=datetime.date(2026, 1, 1),
            vigencia_fim=datetime.date(2026, 12, 31),
        )
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 2


class TestEscopo:
    def test_regra_de_outro_escritorio_nao_alcanca(self, sessao, cenario):
        outro = Escritorio(nome="Outro", slug="outro")
        sessao.add(outro)
        sessao.commit()
        _regra_cfop(sessao, outro)

        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_regra_de_outra_empresa_nao_alcanca(self, sessao, cenario):
        outra = Empresa(cnpj="11111111000111", nome="Outra", escritorio_id=cenario["escritorio"].id)
        sessao.add(outra)
        sessao.commit()
        _regra_cfop(sessao, cenario["escritorio"], empresa_id=outra.id)

        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0

    def test_regra_da_propria_empresa_alcanca(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"], empresa_id=cenario["documento"].empresa_id)
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 2

    def test_obrigacao_filtra(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"], obrigacao="efd_contribuicoes")

        assert (
            MotorDeClassificacao(sessao, obrigacao="efd_icms").avaliar(cenario["documento"]).total
            == 0
        )
        assert (
            MotorDeClassificacao(sessao, obrigacao="efd_contribuicoes")
            .avaliar(cenario["documento"])
            .total
            == 2
        )

    def test_ordenadas_da_maior_prioridade_para_a_menor(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"], nome="baixa", prioridade=1)
        _regra_cfop(sessao, cenario["escritorio"], nome="alta", prioridade=9)

        nomes = [r.nome for r in regras_aplicaveis(sessao, cenario["documento"])]
        assert nomes == ["alta", "baixa"]


class TestAplicar:
    def test_sugestao_aceita_vira_ajuste_de_origem_regra(self, sessao, cenario):
        documento = cenario["documento"]
        _regra_cfop(sessao, cenario["escritorio"])
        resultado = MotorDeClassificacao(sessao).avaliar(documento)

        aplicar(sessao, documento, resultado.sugestoes)

        ajustes = sessao.execute(select(AjusteFiscal)).scalars().all()
        assert len(ajustes) == 2
        assert all(a.origem == ORIGEM_REGRA for a in ajustes)
        assert all(a.regra == "revenda-interestadual" for a in ajustes)
        assert efetivo(sessao, documento).item(1)["cfop"] == "6404"

    def test_o_normalizado_segue_intacto(self, sessao, cenario):
        documento = cenario["documento"]
        _regra_cfop(sessao, cenario["escritorio"])
        aplicar(sessao, documento, MotorDeClassificacao(sessao).avaliar(documento).sugestoes)
        sessao.commit()
        sessao.expire_all()

        assert sessao.get(DocumentoFiscal, documento.id).itens[0].cfop == "6102"

    def test_classificacao_inteira_se_desfaz_por_lote(self, sessao, cenario):
        """Se as regras estavam erradas, o mês volta ao que era."""
        documento = cenario["documento"]
        _regra_cfop(sessao, cenario["escritorio"])
        lote = aplicar(sessao, documento, MotorDeClassificacao(sessao).avaliar(documento).sugestoes)
        assert efetivo(sessao, documento).item(1)["cfop"] == "6404"

        assert desfazer_lote(sessao, lote) == 2
        assert efetivo(sessao, documento).item(1)["cfop"] == "6102"

    def test_aplicar_parcialmente(self, sessao, cenario):
        """A tela deixa aceitar umas e recusar outras."""
        documento = cenario["documento"]
        _regra_cfop(sessao, cenario["escritorio"])
        sugestoes = MotorDeClassificacao(sessao).avaliar(documento).sugestoes

        aplicar(sessao, documento, [sugestoes[0]])

        visao = efetivo(sessao, documento)
        assert visao.item(1)["cfop"] == "6404"
        assert visao.item(2)["cfop"] == "6102"


class TestImpacto:
    def test_campo_numerico_tem_impacto_em_reais(self, sessao, cenario):
        criar_regra(
            sessao,
            nome="corrigir-base",
            condicoes=[{"campo": "cfop", "operador": "igual", "valor": "6102"}],
            acoes=[{"campo": "base_icms", "valor": 900.0}],
            escritorio_id=cenario["escritorio"].id,
        )
        sugestao = MotorDeClassificacao(sessao).avaliar(cenario["documento"]).sugestoes[0]
        assert sugestao.impacto == pytest.approx(-100.0)

    def test_campo_de_texto_nao_tem_impacto(self, sessao, cenario):
        """Trocar CFOP não muda valor nenhum, e fingir que muda confundiria."""
        _regra_cfop(sessao, cenario["escritorio"])
        assert (
            MotorDeClassificacao(sessao).avaliar(cenario["documento"]).sugestoes[0].impacto is None
        )

    def test_confianca_vem_da_regra(self, sessao, cenario):
        _regra_cfop(sessao, cenario["escritorio"], confianca=0.6)
        assert (
            MotorDeClassificacao(sessao).avaliar(cenario["documento"]).sugestoes[0].confianca == 0.6
        )


class TestValidacao:
    """Regra quebrada é recusada ao salvar, não descoberta no fechamento."""

    def test_operador_inexistente(self, sessao, cenario):
        with pytest.raises(RegraInvalida, match="operador"):
            criar_regra(
                sessao,
                nome="x",
                condicoes=[{"campo": "ncm", "operador": "parecido_com", "valor": "2203"}],
                acoes=[{"campo": "cfop", "valor": "6404"}],
                escritorio_id=cenario["escritorio"].id,
            )

    def test_regra_sem_acao(self, sessao, cenario):
        with pytest.raises(RegraInvalida, match="sem ação"):
            criar_regra(
                sessao,
                nome="x",
                condicoes=[{"campo": "ncm", "operador": "igual", "valor": "2203"}],
                acoes=[],
                escritorio_id=cenario["escritorio"].id,
            )

    def test_condicao_sem_campo(self, sessao, cenario):
        with pytest.raises(RegraInvalida, match="sem campo"):
            criar_regra(
                sessao,
                nome="x",
                condicoes=[{"operador": "igual", "valor": "2203"}],
                acoes=[{"campo": "cfop", "valor": "6404"}],
                escritorio_id=cenario["escritorio"].id,
            )

    def test_acao_com_codigo_da_reforma_inventado(self, sessao, cenario):
        """Uma regra escreve em TODO documento que casar com ela.

        Aceitar `999999` aqui é aceitá-lo mil vezes, com origem `regra`, sem
        que ninguém tenha digitado nenhuma delas — e a origem `regra` é
        justamente a que ninguém revisa item a item.
        """
        with pytest.raises(RegraInvalida, match="não está na tabela oficial"):
            criar_regra(
                sessao,
                nome="x",
                condicoes=[{"campo": "ncm", "operador": "igual", "valor": "22030000"}],
                acoes=[{"campo": "class_trib_ibscbs", "valor": "999999"}],
                escritorio_id=cenario["escritorio"].id,
            )

    def test_acao_com_cst_da_reforma_inventado(self, sessao, cenario):
        with pytest.raises(RegraInvalida, match="não está na tabela oficial"):
            criar_regra(
                sessao,
                nome="x",
                condicoes=[{"campo": "ncm", "operador": "igual", "valor": "22030000"}],
                acoes=[{"campo": "cst_ibscbs", "valor": "999"}],
                escritorio_id=cenario["escritorio"].id,
            )

    def test_acao_com_codigo_que_existe_e_aceita(self, sessao, cenario):
        regra = criar_regra(
            sessao,
            nome="monofásico de combustível",
            condicoes=[{"campo": "ncm", "operador": "comeca_com", "valor": "2710"}],
            acoes=[
                {"campo": "cst_ibscbs", "valor": "620"},
                {"campo": "class_trib_ibscbs", "valor": "620001"},
            ],
            escritorio_id=cenario["escritorio"].id,
        )

        assert regra.id is not None

    def test_a_conferencia_nao_alcanca_a_condicao(self, sessao, cenario):
        """Filtrar por um código que não existe é consulta que não acha nada.

        Recusar aqui impediria de procurar exatamente o que se quer achar:
        as notas que vieram com um código errado da origem.
        """
        regra = criar_regra(
            sessao,
            nome="caça ao código errado",
            condicoes=[{"campo": "class_trib_ibscbs", "operador": "igual", "valor": "999999"}],
            acoes=[{"campo": "cfop", "valor": "6404"}],
            escritorio_id=cenario["escritorio"].id,
        )

        assert regra.id is not None

    def test_json_invalido_no_banco(self, sessao, cenario):
        regra = RegraFiscal(
            nome="corrompida",
            condicoes="{isto não é json",
            acoes="[]",
            escritorio_id=cenario["escritorio"].id,
        )
        with pytest.raises(RegraInvalida, match="JSON"):
            from src.documentos import validar_regra

            validar_regra(regra)

    def test_condicoes_e_acoes_ficam_como_json(self, sessao, cenario):
        regra = _regra_cfop(sessao, cenario["escritorio"])
        assert json.loads(regra.condicoes)[0]["campo"] == "ncm"
        assert json.loads(regra.acoes) == [{"campo": "cfop", "valor": "6404"}]

    def test_condicao_nao_e_expressao_avaliada(self, sessao, cenario):
        """O valor da condição é comparado, nunca executado.

        Um DSL avaliado transformaria a tabela de regras em superfície de
        execução de código no servidor: quem escrevesse nela rodaria o que
        quisesse.
        """
        criar_regra(
            sessao,
            nome="tentativa",
            condicoes=[
                {"campo": "ncm", "operador": "igual", "valor": "__import__('os').system('id')"}
            ],
            acoes=[{"campo": "cfop", "valor": "6404"}],
            escritorio_id=cenario["escritorio"].id,
        )
        # Não casa e não executa: é só um texto que não bate com o NCM.
        assert MotorDeClassificacao(sessao).avaliar(cenario["documento"]).total == 0
