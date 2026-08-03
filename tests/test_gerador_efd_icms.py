"""Gerador da EFD ICMS/IPI a partir dos documentos importados.

O que estes testes protegem:

  * **as contagens do bloco 9** — é onde gerador próprio erra, e errar ali faz
    o validador recusar o arquivo inteiro sem apontar a linha;
  * **o C190 bate com a soma dos C170** — o consolidado divergente invalida a
    nota, e é o segundo erro mais comum;
  * **o arquivo sai do efetivo**, não do XML: o que o operador corrigiu na tela
    é o que vai para o Fisco;
  * **falta de cadastro para a geração** em vez de sair com enquadramento
    errado — o validador aceitaria, e o erro só apareceria em intimação.
"""

from __future__ import annotations

import datetime
from collections import Counter

import pytest

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import (
    ORIGEM_USUARIO,
    ImportadorDeDocumentos,
    aplicar_ajuste,
)
from src.escrituracoes import (
    CampoObrigatorioAusente,
    GeradorEFDICMS,
    formatar_data,
    formatar_valor,
)
from src.escrituracoes.efd_icms import PeriodoSemLeiaute, cod_ver
from src.escrituracoes.leiaute import EFD_ICMS
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'efd.db'}")
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
def com_documento(sessao, empresa):
    ocorrencia = ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
        nfe_xml(itens=2)
    )
    sessao.commit()
    return sessao.get(DocumentoFiscal, ocorrencia.documento_id)


def _importar(sessao, empresa, **kwargs):
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(nfe_xml(**kwargs))
    sessao.commit()


def _gerar(sessao, empresa, **kwargs):
    return GeradorEFDICMS(
        sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM, **kwargs
    ).gerar()


def _linhas(resultado) -> list[str]:
    return resultado.texto().replace("\r\n", "\n").rstrip("\n").split("\n")


def _campos(linha: str) -> list[str]:
    """Os campos DEPOIS do tipo do registro.

    `|C100|0|1|…|` vira `["0", "1", …]`: o tipo fica de fora, senão todo
    índice deste arquivo estaria deslocado em um.
    """
    return linha.split("|")[2:-1]


def _primeiro(linhas: list[str], tipo: str) -> list[str]:
    for linha in linhas:
        if linha.startswith(f"|{tipo}|"):
            return _campos(linha)
    raise AssertionError(f"registro {tipo} não está no arquivo")


class TestFormato:
    def test_valor_usa_virgula_decimal(self):
        assert formatar_valor(1234.5) == "1234,50"
        assert formatar_valor(0.1 + 0.2) == "0,30"

    def test_zero_vira_campo_vazio(self):
        """O leiaute trata ausente e zero igual; `0,00` gera advertência."""
        assert formatar_valor(0) == ""
        assert formatar_valor(None) == ""

    @pytest.mark.parametrize(
        ("valor", "esperado"),
        [("2.665", "2,67"), ("2.685", "2,69"), ("0.125", "0,13")],
    )
    def test_arredondamento_e_o_da_contabilidade(self, valor, esperado):
        """Meio para cima, não para o par.

        O padrão do `Decimal.quantize` — e do `round` do Python — é o do
        banqueiro, que arredonda 2,665 para 2,66. Estes três valores
        discriminam os dois modos; 2,675 não discrimina, porque ali os dois
        dão 2,68.
        """
        from decimal import Decimal

        assert formatar_valor(Decimal(valor)) == esperado

    def test_data_em_ddmmaaaa(self):
        assert formatar_data(datetime.date(2026, 7, 1)) == "01072026"
        assert formatar_data(None) == ""

    def test_linha_comeca_e_termina_com_barra(self, sessao, empresa, com_documento):
        for linha in _linhas(_gerar(sessao, empresa)):
            assert linha.startswith("|") and linha.endswith("|"), linha

    def test_arquivo_usa_crlf(self, sessao, empresa, com_documento):
        """Alguns validadores recusam o arquivo inteiro com LF, sem dizer por quê."""
        texto = _gerar(sessao, empresa).texto()
        assert texto.endswith("\r\n")
        assert "\n" not in texto.replace("\r\n", "")


class TestCadastroObrigatorio:
    """Sem enquadramento, o arquivo sai errado e o validador aceita."""

    def test_sem_perfil_nao_gera(self, sessao, empresa):
        empresa.ind_perfil = None
        sessao.flush()
        with pytest.raises(CampoObrigatorioAusente, match="ind_perfil"):
            _gerar(sessao, empresa)

    def test_sem_indicador_de_atividade_nao_gera(self, sessao, empresa):
        empresa.ind_ativ = None
        sessao.flush()
        with pytest.raises(CampoObrigatorioAusente, match="ind_ativ"):
            _gerar(sessao, empresa)

    def test_sem_inscricao_estadual_nao_gera(self, sessao, empresa):
        empresa.ie = None
        sessao.flush()
        with pytest.raises(CampoObrigatorioAusente, match="ie"):
            _gerar(sessao, empresa)

    def test_perfil_invalido_e_recusado(self, sessao, empresa):
        empresa.ind_perfil = "Z"
        sessao.flush()
        with pytest.raises(CampoObrigatorioAusente):
            _gerar(sessao, empresa)


class TestBloco0:
    def test_identificacao_da_empresa(self, sessao, empresa, com_documento):
        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "0000")
        assert campos[0] == "020", "versão do leiaute — o período é de 2026"
        assert campos[2] == "01072026"
        assert campos[3] == "31072026"
        assert campos[4] == "COMERCIO EXEMPLO LTDA"
        assert campos[5] == "98765432000198"
        assert campos[7] == "TO"
        assert campos[12] == "A"
        assert campos[13] == "1"

    def test_participante_vem_do_documento(self, sessao, empresa, com_documento):
        """Recadastrar o que já está na nota seria pedir para divergir."""
        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "0150")
        assert campos[0] == "12345678000195", "o emitente, porque é entrada"
        assert campos[1] == "INDUSTRIA EXEMPLO LTDA"

    def test_itens_vem_dos_documentos(self, sessao, empresa, com_documento):
        linhas = _linhas(_gerar(sessao, empresa))
        itens = [linha for linha in linhas if linha.startswith("|0200|")]
        assert len(itens) == 2
        assert _campos(itens[0])[0] == "PROD-001"
        assert _campos(itens[0])[6] == "22030000", "NCM"

    def test_item_repetido_entra_uma_vez_so(self, sessao, empresa, com_documento):
        """Dois documentos com o mesmo produto não podem gerar 0200 duplicado."""
        ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
            nfe_xml(chave="35260712345678000195550010000000021000000028", numero="2")
        )
        sessao.commit()

        linhas = _linhas(_gerar(sessao, empresa))
        codigos = [_campos(linha)[0] for linha in linhas if linha.startswith("|0200|")]
        assert codigos == sorted(set(codigos)), f"0200 duplicado: {codigos}"

    def test_mesmo_codigo_com_descricao_diferente_usa_a_primeira(
        self, sessao, empresa, com_documento
    ):
        """Fornecedor muda a descrição do produto entre uma nota e outra.

        O 0200 é UM cadastro por código, e alguma das descrições tem de
        prevalecer. Fica a primeira, em ordem cronológica: sem essa regra a
        escolha dependeria da ordem em que o banco devolveu os documentos, e o
        mesmo período geraria arquivos diferentes.
        """
        outro = nfe_xml(chave="35260712345678000195550010000000021000000028", numero="2").replace(
            b"PRODUTO DE TESTE 1", b"DESCRICAO QUE MUDOU"
        )
        ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(outro)
        sessao.commit()

        linhas = _linhas(_gerar(sessao, empresa))
        do_produto = [_campos(linha) for linha in linhas if linha.startswith("|0200|PROD-001|")]
        assert len(do_produto) == 1, "o mesmo código gerou dois 0200"
        assert do_produto[0][1] == "PRODUTO DE TESTE 1"

    def test_mesmo_participante_com_nome_diferente_usa_o_primeiro(
        self, sessao, empresa, com_documento
    ):
        """Mesma razão: o 0150 é um cadastro por participante."""
        outro = nfe_xml(chave="35260712345678000195550010000000031000000039", numero="3").replace(
            b"INDUSTRIA EXEMPLO LTDA", b"NOME QUE MUDOU LTDA"
        )
        ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(outro)
        sessao.commit()

        linhas = _linhas(_gerar(sessao, empresa))
        do_participante = [
            _campos(linha) for linha in linhas if linha.startswith("|0150|12345678000195|")
        ]
        assert len(do_participante) == 1, "o mesmo CNPJ gerou dois 0150"
        assert do_participante[0][1] == "INDUSTRIA EXEMPLO LTDA"

    def test_unidade_entra_uma_vez_so(self, sessao, empresa, com_documento):
        linhas = _linhas(_gerar(sessao, empresa))
        assert len([linha for linha in linhas if linha.startswith("|0190|")]) == 1


class TestBlocoC:
    def test_c100_traz_a_chave_e_os_totais(self, sessao, empresa, com_documento):
        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "C100")
        assert campos[0] == "0", "IND_OPER: entrada"
        assert campos[1] == "1", "IND_EMIT: terceiros"
        assert campos[7] == com_documento.chave
        assert campos[10] == "2100,00", "valor total"

    def test_documento_cancelado_muda_o_cod_sit(self, sessao, empresa, com_documento):
        com_documento.situacao = "cancelado"
        sessao.flush()
        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "C100")
        assert campos[4] == "02", "COD_SIT de documento cancelado"

    def test_c170_por_item(self, sessao, empresa, com_documento):
        linhas = _linhas(_gerar(sessao, empresa))
        itens = [linha for linha in linhas if linha.startswith("|C170|")]
        assert len(itens) == 2
        campos = _campos(itens[0])
        assert campos[0] == "1", "número do item"
        assert campos[8] == "000", "origem 0 + CST 00"
        assert campos[9] == "6102", "CFOP"

    def test_c190_bate_com_a_soma_dos_c170(self, sessao, empresa, com_documento):
        """O consolidado divergente invalida a nota inteira.

        É o segundo erro mais comum de gerador próprio, atrás só das contagens
        do bloco 9.
        """
        linhas = _linhas(_gerar(sessao, empresa))
        c170 = [_campos(linha) for linha in linhas if linha.startswith("|C170|")]
        c190 = [_campos(linha) for linha in linhas if linha.startswith("|C190|")]

        assert len(c190) == 1, "os dois itens têm mesmo CST, CFOP e alíquota"

        def soma(indice: int) -> float:
            return sum(float(c[indice].replace(",", ".") or 0) for c in c170)

        consolidado = c190[0]
        assert float(consolidado[3].replace(",", ".")) == pytest.approx(soma(5)), "valor"
        assert float(consolidado[4].replace(",", ".")) == pytest.approx(soma(11)), "base"
        assert float(consolidado[5].replace(",", ".")) == pytest.approx(soma(13)), "ICMS"

    def test_cfop_diferente_gera_c190_separado(self, sessao, empresa, com_documento):
        aplicar_ajuste(
            sessao,
            documento=com_documento,
            item=com_documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        linhas = _linhas(_gerar(sessao, empresa))
        c190 = [_campos(linha) for linha in linhas if linha.startswith("|C190|")]
        assert len(c190) == 2
        assert {c[1] for c in c190} == {"2102", "6102"}


class TestArquivoSaiDoEfetivo:
    """O que o operador corrigiu é o que vai para o Fisco."""

    def test_ajuste_de_item_aparece_no_c170(self, sessao, empresa, com_documento):
        aplicar_ajuste(
            sessao,
            documento=com_documento,
            item=com_documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        linhas = _linhas(_gerar(sessao, empresa))
        cfops = [_campos(linha)[9] for linha in linhas if linha.startswith("|C170|")]
        assert cfops == ["2102", "6102"]

    def test_ajuste_de_cabecalho_aparece_no_c100(self, sessao, empresa, com_documento):
        aplicar_ajuste(
            sessao,
            documento=com_documento,
            campo="valor_total",
            valor_novo=1500.0,
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        assert _primeiro(_linhas(_gerar(sessao, empresa)), "C100")[10] == "1500,00"

    def test_o_xml_original_nao_muda(self, sessao, empresa, com_documento):
        aplicar_ajuste(
            sessao,
            documento=com_documento,
            item=com_documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.commit()
        sessao.expire_all()

        assert sessao.get(DocumentoFiscal, com_documento.id).itens[0].cfop == "6102"


class TestBlocoE:
    def test_entrada_vira_credito(self, sessao, empresa, com_documento):
        campos = _primeiro(_linhas(_gerar(sessao, empresa)), "E110")
        assert campos[0] == "", "sem débito: o documento é de entrada"
        assert campos[4] == "360,00", "crédito: 2 itens × 180"

    def test_saida_vira_debito(self, sessao, empresa):
        """A mesma nota, do ponto de vista de quem a emitiu."""
        emitente = Empresa(
            cnpj="12345678000195",
            nome="INDUSTRIA EXEMPLO LTDA",
            uf="SP",
            ie="110042490114",
            cod_mun="3550308",
            ind_perfil="A",
            ind_ativ="0",
            escritorio_id=empresa.escritorio_id,
        )
        sessao.add(emitente)
        sessao.commit()
        ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
            nfe_xml(itens=2)
        )
        sessao.commit()

        campos = _primeiro(_linhas(_gerar(sessao, emitente)), "E110")
        assert campos[0] == "360,00", "débito"
        assert campos[4] == "", "sem crédito"

    def test_apuracao_sem_ajuste_diz_que_e_soma_direta(self, sessao, empresa, com_documento):
        """Silêncio aqui faria alguém transmitir apuração incompleta.

        Esta lista encolheu duas vezes, e nas duas porque o que estava nela
        passou a existir: o saldo credor anterior virou busca na escrituração
        transmitida, e os ajustes da tabela 5.1.1 viraram cadastro. O que
        sobra é dizer que NÃO HÁ ajuste cadastrado — que é a informação certa
        para quem tem benefício fiscal e ainda não o registrou.
        """
        avisos = _gerar(sessao, empresa).avisos
        assert any("não há ajustes de apuração cadastrados" in a for a in avisos)
        assert any("fiscal ajuste" in a for a in avisos)


class TestContagensDoBloco9:
    """Onde gerador próprio erra, e o validador recusa sem apontar a linha."""

    def test_9999_conta_todas_as_linhas(self, sessao, empresa, com_documento):
        resultado = _gerar(sessao, empresa)
        linhas = _linhas(resultado)
        assert _primeiro(linhas, "9999")[0] == str(len(linhas))

    def test_9990_conta_o_bloco_9_inteiro(self, sessao, empresa, com_documento):
        """Inclui o próprio 9990 e o 9999."""
        linhas = _linhas(_gerar(sessao, empresa))
        do_bloco_9 = sum(1 for linha in linhas if linha.startswith("|9"))
        assert _primeiro(linhas, "9990")[0] == str(do_bloco_9)

    def test_cada_9900_bate_com_o_arquivo(self, sessao, empresa, com_documento):
        """Conferido um a um contra o que está no arquivo, não contra o gerador."""
        linhas = _linhas(_gerar(sessao, empresa))
        presentes = Counter(linha.split("|")[1] for linha in linhas)

        declarados = {}
        for linha in linhas:
            if linha.startswith("|9900|"):
                tipo, quantidade = _campos(linha)
                declarados[tipo] = int(quantidade)

        assert declarados == dict(
            presentes
        ), "o 9900 diverge do arquivo — o validador recusaria tudo"

    def test_9900_conta_o_proprio_9900(self, sessao, empresa, com_documento):
        """O erro clássico: esquecer que o bloco 9 também se conta."""
        linhas = _linhas(_gerar(sessao, empresa))
        quantos_9900 = sum(1 for linha in linhas if linha.startswith("|9900|"))

        declarado = None
        for linha in linhas:
            if linha.startswith("|9900|9900|"):
                declarado = int(_campos(linha)[1])
        assert declarado == quantos_9900

    def test_encerramento_de_bloco_conta_a_si_mesmo(self, sessao, empresa, com_documento):
        linhas = _linhas(_gerar(sessao, empresa))
        for bloco, encerramento in (("0", "0990"), ("C", "C990"), ("E", "E990")):
            do_bloco = sum(1 for linha in linhas if linha.startswith(f"|{bloco}"))
            assert _primeiro(linhas, encerramento)[0] == str(
                do_bloco
            ), f"{encerramento} não conta a si mesmo"

    def test_contagens_seguem_certas_com_mais_documentos(self, sessao, empresa, com_documento):
        """A conta não pode depender de haver um documento só."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id)
        for n in range(2, 6):
            importador.importar(
                nfe_xml(
                    chave=f"3526071234567800019555001000000{n:04d}1000000017",
                    numero=str(n),
                    itens=n,
                )
            )
        sessao.commit()

        linhas = _linhas(_gerar(sessao, empresa))
        presentes = Counter(linha.split("|")[1] for linha in linhas)
        declarados = {
            _campos(linha)[0]: int(_campos(linha)[1])
            for linha in linhas
            if linha.startswith("|9900|")
        }
        assert declarados == dict(presentes)
        assert _primeiro(linhas, "9999")[0] == str(len(linhas))


class TestPeriodo:
    def test_documento_fora_do_periodo_fica_de_fora(self, sessao, empresa, com_documento):
        resultado = GeradorEFDICMS(
            sessao,
            empresa=empresa,
            data_inicio=datetime.date(2026, 8, 1),
            data_fim=datetime.date(2026, 8, 31),
        ).gerar()

        linhas = _linhas(resultado)
        assert not [linha for linha in linhas if linha.startswith("|C100|")]
        assert any("nenhum documento" in a for a in resultado.avisos)

    def test_periodo_vazio_ainda_gera_arquivo_valido(self, sessao, empresa):
        """O Fisco espera arquivo mesmo em mês sem movimento."""
        resultado = _gerar(sessao, empresa)
        linhas = _linhas(resultado)

        assert _primeiro(linhas, "0000")[2] == "01072026"
        assert _primeiro(linhas, "C001")[0] == "1", "bloco sem dados"
        assert _primeiro(linhas, "9999")[0] == str(len(linhas))

    def test_documento_de_outra_empresa_fica_de_fora(self, sessao, empresa, com_documento):
        outra = Empresa(
            cnpj="11111111000111",
            nome="OUTRA",
            uf="TO",
            ie="999",
            cod_mun="1721000",
            ind_perfil="A",
            ind_ativ="1",
            escritorio_id=empresa.escritorio_id,
        )
        sessao.add(outra)
        sessao.commit()

        linhas = _linhas(_gerar(sessao, outra))
        assert not [linha for linha in linhas if linha.startswith("|C100|")]


class TestOsTributosDaReformaFicamForaDoArquivo:
    """A EFD ICMS/IPI não leva IBS, CBS nem IS — e diz quanto ficou de fora.

    A decisão é do GT48 da COTEPE, não nossa. A consequência prática chega
    todo mês a partir de agosto de 2026: o total da EFD deixa de bater com o
    total das notas, e a diferença tem exatamente a cara de um defeito do
    gerador. Sem o aviso, quem confere gasta o fechamento procurando um erro
    que não existe.
    """

    def test_o_aviso_diz_quanto_de_cada_tributo_ficou_de_fora(self, sessao, empresa):
        _importar(sessao, empresa, itens=2)

        resultado = _gerar(sessao, empresa)

        aviso = next(a for a in resultado.avisos if "NÃO entram neste arquivo" in a)
        assert "CBS 18.00" in aviso, aviso
        assert "IBS estadual 1.40" in aviso, aviso
        assert "IBS municipal 0.60" in aviso, aviso

    def test_o_aviso_explica_o_vl_doc_contra_o_vl_opr(self, sessao, empresa):
        """É a pergunta que o contador faz ao conferir o arquivo."""
        _importar(sessao, empresa)

        aviso = next(a for a in _gerar(sessao, empresa).avisos if "NÃO entram neste arquivo" in a)

        assert "VL_DOC" in aviso and "VL_OPR" in aviso
        assert "GT48" in aviso

    def test_sem_os_tributos_novos_o_aviso_nao_sai(self, sessao, empresa):
        """Nota anterior à Reforma não gera aviso: seria ruído em todo
        fechamento de período antigo."""
        _importar(sessao, empresa, com_reforma=False)

        resultado = _gerar(sessao, empresa)

        assert not [a for a in resultado.avisos if "NÃO entram neste arquivo" in a]

    def test_o_vl_doc_do_c100_segue_sendo_o_vnf(self, sessao, empresa):
        """O `vNF` não inclui os tributos novos — eles têm o `vNFTot`, à parte.

        Escrever o `vNFTot` no VL_DOC poria na EFD justamente o que o GT48
        decidiu manter fora.
        """
        _importar(sessao, empresa, itens=2)

        resultado = _gerar(sessao, empresa)

        c100 = next(r for r in resultado.registros if r.tipo == "C100")
        posicao = EFD_ICMS["C100"].index("VL_DOC")
        assert c100.campos[posicao] == "2100,00"


class TestVersaoDoLeiaute:
    """O `COD_VER` do 0000 depende do período, e era fixo.

    O validador confere o código contra a data do `DT_FIN` e recusa o arquivo
    inteiro quando ele não vale para o período — "A versão do leiaute não é
    válida para o período informado". Fixo em `018`, todo arquivo de 2025 em
    diante saía recusado, e nada aqui acusava: o arquivo é bem-formado, e a
    recusa só aparece no validador do Fisco.

    Cada faixa vem da capa da Nota Técnica que instituiu o leiaute, em
    "Institui o leiaute válido a partir de".
    """

    @pytest.mark.parametrize(
        "fim,esperado",
        [
            (datetime.date(2024, 12, 31), "018"),
            (datetime.date(2025, 1, 1), "019"),  # NT 2024.001 v1.0
            (datetime.date(2025, 12, 31), "019"),
            (datetime.date(2026, 1, 1), "020"),  # NT 2025.001 v1.0
            (datetime.date(2026, 7, 31), "020"),
        ],
    )
    def test_a_versao_segue_a_data_final_do_periodo(self, fim, esperado):
        assert cod_ver(fim) == esperado

    def test_e_o_dt_fin_que_decide_nao_o_dt_ini(self, sessao, empresa):
        """Período que atravessa a virada do ano usa a versão do fim.

        É contra o `DT_FIN` que o validador confere; escolher pelo início
        daria `019` num arquivo de janeiro de 2026.
        """
        resultado = GeradorEFDICMS(
            sessao,
            empresa=empresa,
            data_inicio=datetime.date(2025, 12, 1),
            data_fim=datetime.date(2026, 1, 31),
        ).gerar()

        campos = _primeiro(_linhas(resultado), "0000")
        assert campos[0] == "020"

    def test_periodo_anterior_ao_leiaute_mais_antigo_levanta(self):
        """Devolver `018` para 2020 repetiria o defeito em menor escala.

        Seria outro código que o validador recusa, escrito com a confiança de
        quem sabe — e sem ninguém para desconfiar.
        """
        with pytest.raises(PeriodoSemLeiaute, match="018"):
            cod_ver(datetime.date(2020, 12, 31))

    def test_a_vespera_do_leiaute_mais_antigo_ainda_levanta(self):
        with pytest.raises(PeriodoSemLeiaute):
            cod_ver(datetime.date(2023, 12, 31))
