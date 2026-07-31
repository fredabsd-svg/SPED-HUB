"""Gerador da EFD-Contribuições.

O que estes testes protegem, além da estrutura:

  * **no regime cumulativo não há crédito** — somar os créditos das entradas
    ali produziria contribuição a menor, num arquivo estruturalmente válido
    que o validador aceita e o Fisco cobra depois, com multa;
  * **o regime é cadastro, não default** — errar nele é o erro caro deste
    arquivo, e não há palpite razoável;
  * **as contagens do bloco 9**, que a base compartilha com o gerador de ICMS.
"""

from __future__ import annotations

import datetime
from collections import Counter

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
from src.escrituracoes import (
    ATIVIDADES_CONTRIBUICOES,
    REGIMES,
    CampoObrigatorioAusente,
    GeradorEFDContribuicoes,
)
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)

NAO_CUMULATIVO = "1"
CUMULATIVO = "2"
AMBOS = "3"

# IND_ATIV do 0000 da EFD-Contribuições — tabela própria, não a da EFD ICMS/IPI.
INDUSTRIAL = "0"
SERVICOS = "1"
COMERCIO = "2"


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'contrib.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def escritorio(sessao):
    e = Escritorio(nome="Teste", slug="teste")
    sessao.add(e)
    sessao.commit()
    return e


def _empresa(
    sessao,
    escritorio,
    *,
    cnpj="98765432000198",
    regime=NAO_CUMULATIVO,
    atividade=COMERCIO,
    ind_ativ_icms="1",
):
    e = Empresa(
        cnpj=cnpj,
        nome="COMERCIO EXEMPLO LTDA",
        uf="TO",
        ie="293456789",
        cod_mun="1721000",
        cod_inc_trib=regime,
        ind_ativ=ind_ativ_icms,
        ind_ativ_contribuicoes=atividade,
        escritorio_id=escritorio.id,
    )
    sessao.add(e)
    sessao.commit()
    return e


def _gerar(sessao, empresa):
    return GeradorEFDContribuicoes(
        sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM
    ).gerar()


def _linhas(resultado) -> list[str]:
    return resultado.texto().replace("\r\n", "\n").rstrip("\n").split("\n")


def _campos(linha: str) -> list[str]:
    """Os campos depois do tipo do registro."""
    return linha.split("|")[2:-1]


def _primeiro(linhas: list[str], tipo: str) -> list[str]:
    for linha in linhas:
        if linha.startswith(f"|{tipo}|"):
            return _campos(linha)
    raise AssertionError(f"registro {tipo} não está no arquivo")


def _numero(campo: str) -> float:
    return float(campo.replace(",", ".")) if campo else 0.0


class TestRegimeObrigatorio:
    """O erro caro deste arquivo, e o que não tem palpite razoável."""

    def test_sem_regime_nao_gera(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio, regime=None)
        with pytest.raises(CampoObrigatorioAusente, match="cod_inc_trib"):
            _gerar(sessao, empresa)

    def test_regime_invalido_e_recusado(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio, regime="9")
        with pytest.raises(CampoObrigatorioAusente):
            _gerar(sessao, empresa)

    def test_a_mensagem_diz_por_que_importa(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio, regime=None)
        with pytest.raises(CampoObrigatorioAusente) as erro:
            _gerar(sessao, empresa)
        assert "crédito" in str(erro.value)

    @pytest.mark.parametrize("regime", sorted(REGIMES))
    def test_todo_regime_conhecido_gera(self, sessao, escritorio, regime):
        empresa = _empresa(sessao, escritorio, regime=regime)
        assert _gerar(sessao, empresa).total_linhas > 0

    def test_o_0110_declara_o_regime(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio, regime=CUMULATIVO)
        assert _primeiro(_linhas(_gerar(sessao, empresa)), "0110")[0] == CUMULATIVO


class TestAtividadeObrigatoria:
    """O outro campo do 0000 que o validador aceita errado."""

    def test_sem_atividade_nao_gera(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio, atividade=None)
        with pytest.raises(CampoObrigatorioAusente, match="ind_ativ_contribuicoes"):
            _gerar(sessao, empresa)

    def test_atividade_fora_da_tabela_e_recusada(self, sessao, escritorio):
        """ "5" não existe nesta tabela — 4 é imobiliária e 9 é outros."""
        empresa = _empresa(sessao, escritorio, atividade="5")
        with pytest.raises(CampoObrigatorioAusente):
            _gerar(sessao, empresa)

    @pytest.mark.parametrize("atividade", sorted(ATIVIDADES_CONTRIBUICOES))
    def test_a_atividade_cadastrada_e_a_que_sai_no_0000(self, sessao, escritorio, atividade):
        empresa = _empresa(sessao, escritorio, atividade=atividade)
        assert _primeiro(_linhas(_gerar(sessao, empresa)), "0000")[12] == atividade

    def test_nao_reaproveita_o_ind_ativ_da_efd_icms(self, sessao, escritorio):
        """As duas escriturações fazem perguntas diferentes com o mesmo nome.

        Uma empresa de comércio responde "1 = outros" na EFD ICMS/IPI, onde a
        tabela é binária. Copiar esse "1" para cá a declararia prestadora de
        serviços — e o validador aceitaria, porque não tem como saber.
        """
        empresa = _empresa(sessao, escritorio, ind_ativ_icms="1", atividade=COMERCIO)

        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "0000")

        assert campos[12] == COMERCIO
        assert campos[12] != empresa.ind_ativ

    def test_sem_natureza_declarada_sai_o_geral_com_aviso(self, sessao, escritorio):
        """Há default razoável aqui, ao contrário do regime e da atividade.

        Exigir a resposta de todo mundo por causa da minoria travaria quem não
        tem o que declarar — mas o silêncio é dito em voz alta.
        """
        empresa = _empresa(sessao, escritorio)

        resultado = _gerar(sessao, empresa)

        assert _primeiro(_linhas(resultado), "0000")[11] == "00"
        aviso = next(a for a in resultado.avisos if "IND_NAT_PJ" in a)
        assert "não declarou a natureza jurídica" in aviso
        assert "fiscal cadastro --ind-nat-pj" in aviso

    def test_natureza_declarada_vai_para_o_arquivo(self, sessao, escritorio):
        """Cooperativa apura por outra regra: declarar errado sai caro."""
        empresa = _empresa(sessao, escritorio)
        empresa.ind_nat_pj = "01"
        sessao.commit()

        resultado = _gerar(sessao, empresa)

        assert _primeiro(_linhas(resultado), "0000")[11] == "01"
        assert not [a for a in resultado.avisos if "não declarou a natureza" in a]

    def test_natureza_fora_da_tabela_nao_e_repassada(self, sessao, escritorio):
        """Valor inválido no cadastro não vira valor inválido no arquivo."""
        empresa = _empresa(sessao, escritorio)
        empresa.ind_nat_pj = "99"
        sessao.commit()

        resultado = _gerar(sessao, empresa)

        assert _primeiro(_linhas(resultado), "0000")[11] == "00"
        assert [a for a in resultado.avisos if "não declarou a natureza" in a]

    @pytest.mark.parametrize("natureza", ["03", "04", "05"])
    def test_natureza_de_scp_avisa_o_registro_que_falta(self, sessao, escritorio, natureza):
        """As três naturezas de SCP exigem o 0035, que este gerador não escreve."""
        empresa = _empresa(sessao, escritorio)
        empresa.ind_nat_pj = natureza
        sessao.commit()

        resultado = _gerar(sessao, empresa)

        assert _primeiro(_linhas(resultado), "0000")[11] == natureza
        assert [a for a in resultado.avisos if "0035" in a]

    def test_natureza_sem_scp_nao_avisa_do_0035(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        empresa.ind_nat_pj = "02"
        sessao.commit()

        assert not [a for a in _gerar(sessao, empresa).avisos if "0035" in a]


class TestCumulativoNaoTemCredito:
    """A distinção que decide o valor da contribuição."""

    def _com_entrada(self, sessao, escritorio, regime):
        """Uma nota de COMPRA: PIS e Cofins destacados, que viram crédito.

        Só viram crédito no regime não cumulativo. No cumulativo a empresa
        paga sobre a receita e não desconta nada das compras.
        """
        empresa = _empresa(sessao, escritorio, regime=regime)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml(itens=2))
        sessao.commit()
        return empresa

    def test_nao_cumulativo_desconta_o_credito(self, sessao, escritorio):
        empresa = self._com_entrada(sessao, escritorio, NAO_CUMULATIVO)

        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "M200")

        assert _numero(campos[1]) == pytest.approx(33.00), "2 itens × 16,50 de PIS"

    def test_cumulativo_nao_desconta_nada(self, sessao, escritorio):
        """Somar crédito aqui geraria contribuição a menor.

        O arquivo passaria no validador — ele não sabe o regime da empresa —,
        e a diferença viria como cobrança com multa.
        """
        empresa = self._com_entrada(sessao, escritorio, CUMULATIVO)

        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "M200")

        assert _numero(campos[1]) == 0.0, "crédito descontado em regime cumulativo"

    def test_cumulativo_avisa_que_nao_descontou(self, sessao, escritorio):
        """Silêncio faria parecer que os créditos foram esquecidos."""
        empresa = self._com_entrada(sessao, escritorio, CUMULATIVO)

        avisos = _gerar(sessao, empresa).avisos

        assert any("cumulativo" in a and "NÃO foram descontados" in a for a in avisos)

    def test_nao_cumulativo_nao_traz_esse_aviso(self, sessao, escritorio):
        empresa = self._com_entrada(sessao, escritorio, NAO_CUMULATIVO)
        assert not any("NÃO foram descontados" in a for a in _gerar(sessao, empresa).avisos)

    def test_cofins_segue_a_mesma_regra(self, sessao, escritorio):
        cumulativo = self._com_entrada(sessao, escritorio, CUMULATIVO)
        assert _numero(_primeiro(_linhas(_gerar(sessao, cumulativo)), "M600")[1]) == 0.0

    def test_regime_ambos_tem_credito(self, sessao, escritorio):
        empresa = self._com_entrada(sessao, escritorio, AMBOS)
        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "M200")
        assert _numero(campos[1]) == pytest.approx(33.00)


class TestApuracao:
    """PIS e Cofins por item na nota: 16,50 e 76,00."""

    def _entrada_e_saida(self, sessao, escritorio, *, itens_saida, itens_entrada):
        """Uma empresa com os dois sentidos no mesmo período.

        Sem os dois o `devido` não é exercitado: com crédito zero, descontar e
        somar dão o mesmo número.
        """
        empresa = _empresa(sessao, escritorio, regime=NAO_CUMULATIVO)
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        # Emitente não cadastrado: a nota é entrada para a nossa empresa.
        importador.importar(nfe_xml(itens=itens_entrada))
        # Nossa empresa emitindo para terceiro não cadastrado: saída.
        importador.importar(
            nfe_xml(
                chave="35260798765432000198550010000000021000000017",
                numero="2",
                emitente_cnpj="98765432000198",
                destinatario_cnpj="11111111000111",
                itens=itens_saida,
            )
        )
        sessao.commit()
        return empresa

    def test_devido_e_o_debito_menos_o_credito(self, sessao, escritorio):
        """Somar em vez de descontar dobraria a contribuição."""
        empresa = self._entrada_e_saida(sessao, escritorio, itens_saida=3, itens_entrada=1)

        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "M200")

        assert _numero(campos[0]) == pytest.approx(49.50), "3 × 16,50 de débito"
        assert _numero(campos[1]) == pytest.approx(16.50), "1 × 16,50 de crédito"
        assert _numero(campos[3]) == pytest.approx(33.00), "49,50 − 16,50"
        assert _numero(campos[6]) == pytest.approx(33.00)

    def test_credito_maior_que_debito_nao_gera_valor_negativo(self, sessao, escritorio):
        """O campo é 'contribuição a recolher'; saldo credor não cabe nele.

        O excedente vira saldo para o período seguinte — que este gerador ainda
        não escritura, e o aviso da apuração diz isso.
        """
        empresa = self._entrada_e_saida(sessao, escritorio, itens_saida=1, itens_entrada=3)

        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "M200")

        assert _numero(campos[0]) == pytest.approx(16.50)
        assert _numero(campos[1]) == pytest.approx(49.50)
        assert _numero(campos[3]) == 0.0, "16,50 − 49,50 não pode sair negativo"

    def test_cofins_tem_valor_proprio(self, sessao, escritorio):
        """Alíquotas diferentes: repetir o PIS no M600 erraria por 4,6 vezes."""
        empresa = self._entrada_e_saida(sessao, escritorio, itens_saida=3, itens_entrada=1)

        linhas = _linhas(_gerar(sessao, empresa))
        pis = _primeiro(linhas, "M200")
        cofins = _primeiro(linhas, "M600")

        assert _numero(cofins[0]) == pytest.approx(228.00), "3 × 76,00"
        assert _numero(cofins[1]) == pytest.approx(76.00)
        assert _numero(cofins[3]) == pytest.approx(152.00)
        assert _numero(cofins[0]) != _numero(pis[0])

    def test_saida_gera_contribuicao(self, sessao, escritorio):
        """Do ponto de vista de quem emitiu a nota."""
        emitente = _empresa(sessao, escritorio, cnpj="12345678000195", regime=NAO_CUMULATIVO)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml(itens=2))
        sessao.commit()

        campos = _primeiro(_linhas(_gerar(sessao, emitente)), "M200")

        assert _numero(campos[0]) == pytest.approx(33.00), "débito de PIS"
        assert _numero(campos[1]) == 0.0, "sem crédito: não há entrada"

    def test_apuracao_avisa_o_que_nao_cobre(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        sessao.commit()

        avisos = _gerar(sessao, empresa).avisos

        assert any("créditos extemporâneos" in a for a in avisos)
        assert any("retenções" in a for a in avisos)

    def test_m200_e_m600_existem(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        sessao.commit()

        linhas = _linhas(_gerar(sessao, empresa))
        assert _primeiro(linhas, "M200")
        assert _primeiro(linhas, "M600")


class TestEstrutura:
    @pytest.fixture
    def com_documento(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml(itens=2))
        sessao.commit()
        return empresa

    def test_versao_do_leiaute(self, sessao, com_documento):
        assert _primeiro(_linhas(_gerar(sessao, com_documento)), "0000")[0] == "006"

    def test_c010_identifica_o_estabelecimento(self, sessao, com_documento):
        campos = _primeiro(_linhas(_gerar(sessao, com_documento)), "C010")
        assert campos[0] == "98765432000198"

    def test_c170_traz_cst_e_valores_de_pis(self, sessao, com_documento):
        linhas = _linhas(_gerar(sessao, com_documento))
        campos = _primeiro(linhas, "C170")
        assert campos[23] == "01", "CST do PIS"
        assert campos[28] == "16,50", "valor do PIS"

    def test_arquivo_sai_do_efetivo(self, sessao, com_documento):
        """O que o operador corrigiu é o que vai para o Fisco."""
        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cst_pis",
            valor_novo="50",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        linhas = _linhas(_gerar(sessao, com_documento))
        csts = [_campos(linha)[23] for linha in linhas if linha.startswith("|C170|")]
        assert csts == ["50", "01"]

    def test_ajuste_no_cabecalho_tambem_chega_no_arquivo(self, sessao, com_documento):
        """A camada efetiva vale para o documento inteiro, não só para os itens."""
        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=None,
            campo="valor_total",
            valor_novo="1234.56",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        assert _primeiro(_linhas(_gerar(sessao, com_documento)), "C100")[10] == "1234,56"

    def test_arquivo_usa_crlf(self, sessao, com_documento):
        """Há validador que recusa o arquivo inteiro com LF, sem dizer por quê."""
        texto = _gerar(sessao, com_documento).texto()

        assert "\r\n" in texto
        assert texto.replace("\r\n", "").count("\n") == 0, "há quebra sem CR"

    def test_periodo_vazio_gera_arquivo_valido(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        resultado = _gerar(sessao, empresa)
        linhas = _linhas(resultado)

        assert _primeiro(linhas, "C001")[0] == "1", "bloco sem dados"
        assert _primeiro(linhas, "M001")[0] == "1"
        assert _primeiro(linhas, "9999")[0] == str(len(linhas))

    def test_linha_comeca_e_termina_com_barra(self, sessao, com_documento):
        for linha in _linhas(_gerar(sessao, com_documento)):
            assert linha.startswith("|") and linha.endswith("|"), linha


class TestContagensDoBloco9:
    """A base é compartilhada com o gerador de ICMS; aqui se confere que vale."""

    @pytest.fixture
    def com_documentos(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        for n in range(1, 4):
            importador.importar(
                nfe_xml(
                    chave=f"3526071234567800019555001000000{n:04d}1000000017",
                    numero=str(n),
                    itens=n,
                )
            )
        sessao.commit()
        return empresa

    def test_cada_9900_bate_com_o_arquivo(self, sessao, com_documentos):
        linhas = _linhas(_gerar(sessao, com_documentos))
        presentes = Counter(linha.split("|")[1] for linha in linhas)
        declarados = {
            _campos(linha)[0]: int(_campos(linha)[1])
            for linha in linhas
            if linha.startswith("|9900|")
        }
        assert declarados == dict(presentes)

    def test_9999_conta_todas_as_linhas(self, sessao, com_documentos):
        linhas = _linhas(_gerar(sessao, com_documentos))
        assert _primeiro(linhas, "9999")[0] == str(len(linhas))

    def test_9990_conta_o_bloco_9_inteiro(self, sessao, com_documentos):
        linhas = _linhas(_gerar(sessao, com_documentos))
        do_bloco_9 = sum(1 for linha in linhas if linha.startswith("|9"))
        assert _primeiro(linhas, "9990")[0] == str(do_bloco_9)

    def test_encerramento_de_bloco_conta_a_si_mesmo(self, sessao, com_documentos):
        linhas = _linhas(_gerar(sessao, com_documentos))
        for bloco, encerramento in (("0", "0990"), ("C", "C990"), ("M", "M990")):
            do_bloco = sum(1 for linha in linhas if linha.startswith(f"|{bloco}"))
            assert _primeiro(linhas, encerramento)[0] == str(do_bloco), encerramento


class TestEscopo:
    def test_documento_de_outra_empresa_fica_de_fora(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        outra = _empresa(sessao, escritorio, cnpj="11111111000111")
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        sessao.commit()

        linhas = _linhas(_gerar(sessao, outra))
        assert not [linha for linha in linhas if linha.startswith("|C100|")]
        assert empresa is not outra

    @pytest.mark.parametrize("borda", ["inicio", "fim"])
    def test_documento_na_borda_do_periodo_entra(self, sessao, escritorio, borda):
        """A nota é de 30/07/2026; o período que começa ou termina nesse dia inclui.

        Um `>` no lugar de `<=` deixaria de fora exatamente as notas do último
        dia do mês — o dia de maior movimento — sem erro nenhum na geração.
        """
        empresa = _empresa(sessao, escritorio)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        sessao.commit()

        emissao = datetime.date(2026, 7, 30)
        inicio, fim = (emissao, FIM) if borda == "inicio" else (INICIO, emissao)
        resultado = GeradorEFDContribuicoes(
            sessao, empresa=empresa, data_inicio=inicio, data_fim=fim
        ).gerar()

        assert [linha for linha in _linhas(resultado) if linha.startswith("|C100|")]

    def test_documento_fora_do_periodo_fica_de_fora(self, sessao, escritorio):
        empresa = _empresa(sessao, escritorio)
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        sessao.commit()

        resultado = GeradorEFDContribuicoes(
            sessao,
            empresa=empresa,
            data_inicio=datetime.date(2026, 8, 1),
            data_fim=datetime.date(2026, 8, 31),
        ).gerar()

        assert not [linha for linha in _linhas(resultado) if linha.startswith("|C100|")]
