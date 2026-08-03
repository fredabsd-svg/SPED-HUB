"""Os campos de cada registro nas posições que o leiaute manda.

Estes testes existem por causa de um defeito real: o `C100` saía sem o
`IND_FRT`. Como esse é o campo 17, logo depois do `VL_MERC`, os doze valores
seguintes ocupavam a posição do vizinho — o valor do frete no campo do
indicador do frete, a base do ICMS em "outras despesas", e assim até o fim da
linha. A suíte inteira passava: nenhum teste olhava para a **posição**, só
para a presença dos números.

Por isso o que se protege aqui não é "o C100 contém 1000,00", e sim "o campo
`VL_MERC` do C100 vale 1000,00". A diferença entre as duas frases é a diferença
entre um arquivo aceito e um arquivo em que todo valor está na gaveta errada.

O segundo mecanismo é `GeradorBase._add` conferir cada linha contra
`src/escrituracoes/leiaute.py`. Assim o defeito não volta por outra porta:
acrescentar campo ao gerador sem acrescentá-lo ao leiaute para na hora.
"""

from __future__ import annotations

import datetime
import re

import pytest

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ImportadorDeDocumentos
from src.escrituracoes import GeradorEFDContribuicoes, GeradorEFDICMS
from src.escrituracoes.base import GeradorBase
from src.escrituracoes.efd_icms import VERSOES_DO_LEIAUTE
from src.escrituracoes.leiaute import (
    CONTRIBUICOES_VERIFICADO_CONTRA,
    CONTRIBUICOES_VERIFICADO_EM,
    EFD_CONTRIBUICOES,
    EFD_ICMS,
    LEIAUTE_CONFERIDO,
    POR_QUE_E_COMUM,
    VERIFICADO_CONTRA,
    VERIFICADO_EM,
    CamposEmDesacordo,
    RegistroForaDoLeiaute,
    conferir,
)
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'leiaute.db'}")
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
        ind_ativ_contribuicoes="2",
        cod_inc_trib="1",
        escritorio_id=escritorio.id,
    )
    sessao.add(e)
    sessao.commit()
    return e


def importar(sessao, empresa, **kwargs) -> DocumentoFiscal:
    ocorrencia = ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(
        nfe_xml(**kwargs)
    )
    sessao.commit()
    return sessao.get(DocumentoFiscal, ocorrencia.documento_id)


def gerar_icms(sessao, empresa):
    return GeradorEFDICMS(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM).gerar()


def gerar_contribuicoes(sessao, empresa):
    return GeradorEFDContribuicoes(
        sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM
    ).gerar()


def campo(resultado, tipo: str, nome: str, leiaute: dict) -> str:
    """O valor de um campo pelo NOME, resolvendo a posição pelo leiaute.

    Ler por nome é o ponto: um teste que lê por índice fixo continua passando
    quando o gerador e o leiaute se deslocam juntos, que é o erro que se quer
    impedir aqui.
    """
    registro = next(r for r in resultado.registros if r.tipo == tipo)
    return registro.campos[leiaute[tipo].index(nome)]


# ── O defeito que originou tudo ────────────────────────────────────────────


def test_ind_frt_fica_entre_o_vl_merc_e_o_vl_frt(sessao, empresa):
    """A posição exata em que o campo faltava.

    Com frete de 30,00 e modalidade 1, o campo 16 (`IND_FRT`) tem de valer "1"
    e o 17 (`VL_FRT`) "30,00". Sem o `IND_FRT`, o "30,00" cai no campo 16 — e
    o teste que só procurasse "30,00" na linha não veria diferença nenhuma.
    """
    importar(sessao, empresa, mod_frete="1", valor_frete=30.0)
    resultado = gerar_icms(sessao, empresa)

    assert campo(resultado, "C100", "IND_FRT", EFD_ICMS) == "1"
    assert campo(resultado, "C100", "VL_FRT", EFD_ICMS) == "30,00"


def test_os_valores_do_c100_ficam_cada_um_no_seu_campo(sessao, empresa):
    """Todo valor do cabeçalho lido pelo nome do campo, não pela posição.

    Um deslocamento de uma casa só é visível assim: cada número é distinto dos
    demais de propósito, para que trocar dois de lugar mude o resultado.
    """
    importar(sessao, empresa, mod_frete="2", valor_frete=30.0)
    resultado = gerar_icms(sessao, empresa)
    lido = {nome: campo(resultado, "C100", nome, EFD_ICMS) for nome in EFD_ICMS["C100"]}

    assert lido["VL_MERC"] == "1000,00"
    assert lido["IND_FRT"] == "2"
    assert lido["VL_FRT"] == "30,00"
    assert lido["VL_SEG"] == ""
    assert lido["VL_OUT_DA"] == ""
    assert lido["VL_BC_ICMS"] == "1000,00"
    assert lido["VL_ICMS"] == "180,00"
    assert lido["VL_IPI"] == "50,00"
    assert lido["VL_PIS"] == "16,50"
    assert lido["VL_COFINS"] == "76,00"


def test_o_mesmo_deslocamento_valia_para_a_efd_contribuicoes(sessao, empresa):
    """O C100 é o mesmo registro nas duas escriturações — e faltava nas duas."""
    importar(sessao, empresa, mod_frete="0", valor_frete=42.0)
    resultado = gerar_contribuicoes(sessao, empresa)

    assert campo(resultado, "C100", "IND_FRT", EFD_CONTRIBUICOES) == "0"
    assert campo(resultado, "C100", "VL_FRT", EFD_CONTRIBUICOES) == "42,00"
    assert campo(resultado, "C100", "VL_PIS", EFD_CONTRIBUICOES) == "16,50"


def test_c170_termina_no_vl_abat_nt(sessao, empresa):
    """O campo 38 do C170, que também faltava — no fim, mas faltava."""
    importar(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    c170 = next(r for r in resultado.registros if r.tipo == "C170")

    assert len(c170.campos) == len(EFD_ICMS["C170"])
    assert EFD_ICMS["C170"][-1] == "VL_ABAT_NT"


def test_e110_tem_o_deb_esp(sessao, empresa):
    """O campo 14 do E110.

    Não é decorativo: a soma das obrigações a recolher é conferida contra
    `VL_ICMS_RECOLHER` + `DEB_ESP`, e o campo ausente muda a contagem da linha.
    """
    importar(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)
    e110 = next(r for r in resultado.registros if r.tipo == "E110")

    # A nota do fixture é entrada para esta empresa: o ICMS é crédito, e o
    # saldo credor sai no campo 13 — logo antes do que faltava.
    assert len(e110.campos) == 14
    assert campo(resultado, "E110", "VL_TOT_CREDITOS", EFD_ICMS) == "180,00"
    assert campo(resultado, "E110", "VL_SLD_CREDOR_TRANSPORTAR", EFD_ICMS) == "180,00"
    assert campo(resultado, "E110", "DEB_ESP", EFD_ICMS) == "0,00"


# ── O mecanismo que impede a volta ─────────────────────────────────────────


@pytest.mark.parametrize(
    "gerar,leiaute",
    [(gerar_icms, EFD_ICMS), (gerar_contribuicoes, EFD_CONTRIBUICOES)],
    ids=["efd_icms", "efd_contribuicoes"],
)
def test_todo_registro_gerado_bate_com_o_leiaute(sessao, empresa, gerar, leiaute):
    """Nenhuma linha do arquivo sai com contagem diferente da declarada.

    Vale para os registros de estrutura também — o `9900` e o `9990` contam
    tanto quanto o `C100`, e é do bloco 9 que o validador mais reclama.
    """
    importar(sessao, empresa, itens=2, mod_frete="9")
    resultado = gerar(sessao, empresa)

    assert resultado.registros
    for registro in resultado.registros:
        assert registro.tipo in leiaute, f"{registro.tipo} não está no leiaute"
        assert len(registro.campos) == len(leiaute[registro.tipo]), registro.tipo


def test_registro_que_nao_esta_no_leiaute_e_recusado():
    with pytest.raises(RegistroForaDoLeiaute, match="C500"):
        conferir(EFD_ICMS, "C500", ["a", "b"])


def test_campo_a_menos_para_na_hora_dizendo_qual():
    """A mensagem nomeia o que falta — a pergunta seguinte é sempre essa."""
    campos = ["x"] * (len(EFD_ICMS["C100"]) - 1)
    with pytest.raises(CamposEmDesacordo, match="VL_COFINS_ST"):
        conferir(EFD_ICMS, "C100", campos)


def test_campo_a_mais_tambem_para():
    campos = ["x"] * (len(EFD_ICMS["E110"]) + 1)
    with pytest.raises(CamposEmDesacordo, match="sobram 1"):
        conferir(EFD_ICMS, "E110", campos)


def test_gerador_sem_leiaute_declarado_nao_escreve_nada():
    """`LEIAUTE` vazio é o padrão, e o padrão recusa.

    Um gerador novo que esquecesse de declarar o leiaute passaria a escrever
    sem conferência nenhuma, que é exatamente o estado anterior.
    """
    with pytest.raises(RegistroForaDoLeiaute):
        GeradorBase()._add("0000", "018")


def test_o_0000_das_duas_escrituracoes_nao_e_o_mesmo():
    """Mesmo nome, leiautes diferentes — como o `IND_ATIV`.

    Compartilhar a definição faria uma das duas sair errada, e a que saísse
    errada seria aceita pelo validador da outra.
    """
    assert EFD_ICMS["0000"] != EFD_CONTRIBUICOES["0000"]
    assert "COD_FIN" in EFD_ICMS["0000"]
    assert "TIPO_ESCRIT" in EFD_CONTRIBUICOES["0000"]


def test_o_c100_e_o_mesmo_nas_duas(sessao):
    """Onde o leiaute É o mesmo, a definição é uma só — senão viram duas."""
    assert EFD_ICMS["C100"] is EFD_CONTRIBUICOES["C100"]
    assert EFD_ICMS["C170"] is EFD_CONTRIBUICOES["C170"]


# ── A modalidade do frete: repasse, não dedução ────────────────────────────


def test_a_modalidade_vem_do_xml_e_chega_ao_banco(sessao, empresa):
    documento = importar(sessao, empresa, mod_frete="3", valor_frete=15.0)
    assert documento.modalidade_frete == "3"


def test_documento_sem_transp_fica_com_a_modalidade_nula(sessao, empresa):
    documento = importar(sessao, empresa)
    assert documento.modalidade_frete is None


def test_sem_modalidade_e_sem_frete_sai_9_e_nao_avisa(sessao, empresa):
    """9 é o único código possível quando não há frete: não há o que avisar."""
    importar(sessao, empresa)
    resultado = gerar_icms(sessao, empresa)

    assert campo(resultado, "C100", "IND_FRT", EFD_ICMS) == "9"
    assert not [a for a in resultado.avisos if "IND_FRT" in a]


def test_sem_modalidade_mas_com_frete_avisa_nomeando_o_documento(sessao, empresa):
    """Aqui o 9 é uma afirmação falsa — e o aviso diz qual documento corrigir."""
    importar(sessao, empresa, numero="777", valor_frete=25.0)
    resultado = gerar_icms(sessao, empresa)

    avisos = [a for a in resultado.avisos if "IND_FRT" in a]
    assert len(avisos) == 1
    assert "777" in avisos[0]


def test_o_aviso_do_frete_sai_uma_vez_com_todos_os_documentos(sessao, empresa):
    """Um aviso por documento afogaria os outros num fechamento de verdade."""
    importar(sessao, empresa, numero="10", chave="1" * 44, valor_frete=5.0)
    importar(sessao, empresa, numero="11", chave="2" * 44, valor_frete=7.0)
    resultado = gerar_icms(sessao, empresa)

    avisos = [a for a in resultado.avisos if "IND_FRT" in a]
    assert len(avisos) == 1
    assert "10" in avisos[0] and "11" in avisos[0]


def test_modalidade_fora_da_tabela_nao_e_repassada(sessao, empresa):
    """`modFrete` inválido no XML não vira `IND_FRT` inválido no arquivo.

    O grupo `transp` vem de quem emitiu a nota, e não há por que confiar nele
    mais do que na ausência dele.
    """
    documento = importar(sessao, empresa, mod_frete="7", valor_frete=9.0)
    assert documento.modalidade_frete == "7"

    resultado = gerar_icms(sessao, empresa)
    assert campo(resultado, "C100", "IND_FRT", EFD_ICMS) == "9"
    assert [a for a in resultado.avisos if "IND_FRT" in a]


def test_gerar_duas_vezes_nao_soma_os_avisos_da_primeira(sessao, empresa):
    """O segundo arquivo não pode acusar documento que já foi acusado."""
    importar(sessao, empresa, numero="55", valor_frete=12.0)
    gerador = GeradorEFDICMS(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM)

    primeira = gerador.gerar()
    segunda = gerador.gerar()

    assert len([a for a in primeira.avisos if "IND_FRT" in a]) == 1
    (aviso,) = [a for a in segunda.avisos if "IND_FRT" in a]
    # O documento aparece uma vez, não duas: o estado da primeira geração não
    # sobrevive para a segunda.
    assert aviso.count("55") == 1


class TestProcedencia:
    """§8.1 — o leiaute embutido diz de qual versão veio e quando foi conferido.

    Estas tabelas são cópia de documento de terceiro. Sem a versão declarada,
    ninguém sabe se elas descrevem o leiaute que o arquivo diz declarar — e a
    divergência entre as duas coisas é justamente o defeito que fez todo
    arquivo de 2025 sair recusado.
    """

    def test_a_versao_conferida_e_a_mais_nova_do_gerador(self):
        """A trava que liga a tabela de versões à conferência dos registros.

        Acrescentar o leiaute 021 em `VERSOES_DO_LEIAUTE` é dizer que o
        arquivo passa a declarar 021. Se os registros aqui continuam sendo os
        do 020, o arquivo declara uma coisa e é outra — e o validador recusa
        sem dizer qual campo. Reconferir os registros é trabalho humano, e
        este teste é o que o torna obrigatório.
        """
        mais_nova = VERSOES_DO_LEIAUTE[0][1]

        assert LEIAUTE_CONFERIDO == mais_nova, (
            f"o gerador declara o leiaute {mais_nova} e os registros foram conferidos "
            f"contra o {LEIAUTE_CONFERIDO}. Reconfira os registros contra a Nota "
            "Técnica nova e atualize LEIAUTE_CONFERIDO/VERIFICADO_CONTRA/VERIFICADO_EM"
        )

    def test_a_procedencia_das_duas_obrigacoes_esta_preenchida(self):
        """Cada obrigação tem leiaute próprio, e por isso procedência própria.

        A primeira conferência cobriu só a EFD ICMS/IPI, e o módulo passou a
        declarar uma procedência que valia para metade do que ele continha.
        """
        assert "Guia Prático" in CONTRIBUICOES_VERIFICADO_CONTRA
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", CONTRIBUICOES_VERIFICADO_EM)

    def test_a_procedencia_esta_preenchida(self):
        assert re.fullmatch(r"\d{3}", LEIAUTE_CONFERIDO)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", VERIFICADO_EM)
        assert "NT" in VERIFICADO_CONTRA and LEIAUTE_CONFERIDO in VERIFICADO_CONTRA


class TestOsDoisLeiautesDoMesmoRegistro:
    """Registro de mesmo nome com leiaute diferente nas duas obrigações.

    `conferir` compara o gerador com a NOSSA tabela — nunca a nossa tabela com
    o documento oficial. Um campo a mais aqui passa por toda a suíte e só é
    recusado pelo validador do Fisco. Por isso as diferenças conhecidas entre
    as duas obrigações ficam pinadas uma a uma: elas são o que `_COMUNS` não
    pode engolir.
    """

    def test_o_0200_da_efd_contribuicoes_nao_tem_cest(self):
        """O campo não existe nesta obrigação.

        A palavra "CEST" não aparece uma única vez nas 433 páginas do Guia
        Prático da EFD-Contribuições. Compartilhado com o da EFD ICMS/IPI —
        onde ele é o campo 13 —, o gerador escrevia doze valores onde o
        validador espera onze.
        """
        assert "CEST" not in EFD_CONTRIBUICOES["0200"]
        assert len(EFD_CONTRIBUICOES["0200"]) == 11
        assert EFD_CONTRIBUICOES["0200"][-1] == "ALIQ_ICMS"

    def test_o_0200_da_efd_icms_termina_no_cest(self):
        assert EFD_ICMS["0200"][-1] == "CEST"
        assert len(EFD_ICMS["0200"]) == 12

    def test_os_dois_0200_compartilham_o_comeco_e_divergem_no_fim(self):
        """O prefixo igual é o que faz o engano parecer inofensivo."""
        contribuicoes = EFD_CONTRIBUICOES["0200"]

        assert EFD_ICMS["0200"][: len(contribuicoes)] == contribuicoes
        assert EFD_ICMS["0200"] != contribuicoes

    def test_o_0200_nao_esta_entre_os_comuns(self):
        """Se voltar para `_COMUNS`, as duas obrigações voltam a divergir do
        oficial em silêncio — foi exatamente assim que o defeito entrou."""
        from src.escrituracoes import leiaute

        assert "0200" not in leiaute._COMUNS

    def test_todo_registro_comum_tem_o_motivo_declarado(self):
        """Estar em `_COMUNS` é afirmação, não conveniência.

        O `0200` foi para lá por parecer igual — o começo dos dois é
        idêntico — e divergia no último campo. Exigir o motivo escrito é o
        que transforma "parece o mesmo" em "foi conferido nos dois".
        """
        from src.escrituracoes import leiaute

        assert sorted(leiaute._COMUNS) == sorted(POR_QUE_E_COMUM), (
            "acrescentar registro a `_COMUNS` exige declarar em `POR_QUE_E_COMUM` "
            "por que ele é o mesmo nas duas obrigações — conferido nos dois "
            "documentos, ou delegado por um deles ao outro"
        )
        assert all(motivo.strip() for motivo in POR_QUE_E_COMUM.values())

    def test_o_c100_e_comum_porque_o_guia_delega(self):
        """Não é semelhança nossa: é o que o documento manda.

        O Guia da EFD-Contribuições não traz tabela de campos para o C100 —
        ele diz que a estrutura é a do Leiaute da EFD ICMS/IPI, instituído
        pelo Ato COTEPE/ICMS nº 9.
        """
        for registro in ("C100", "C170"):
            assert "DELEGA" in POR_QUE_E_COMUM[registro]
            assert EFD_ICMS[registro] == EFD_CONTRIBUICOES[registro]

    def test_o_0000_tambem_difere(self):
        """O caso que já era conhecido, pinado junto para não voltar."""
        assert EFD_ICMS["0000"] != EFD_CONTRIBUICOES["0000"]
