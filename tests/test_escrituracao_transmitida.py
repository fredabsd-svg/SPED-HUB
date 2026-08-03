"""Marcar qual escrituração foi de fato entregue ao Fisco.

A terceira camada guardava todas as gerações e não sabia qual delas saiu. Um
mês costuma ter várias — a primeira, a de depois da correção, a que se gerou
só para conferir —, e sem essa marca a camada guarda candidatos, não o
registro efetivamente enviado.

O que estes testes protegem:

  * **nenhuma é marcada sozinha.** Deduzir pela mais recente responderia que
    foi entregue justamente a que se acabou de gerar para olhar;
  * **marcar não se desfaz.** Transmitir é fato do mundo, não estado do
    sistema; apagar a marca apagaria o registro de que aconteceu;
  * **uma segunda entrega original no mesmo período é recusada** — ou o
    arquivo devia ser retificadora, ou a marca anterior está errada. A
    finalidade é lida do `0000` do arquivo que saiu, não do parâmetro de
    geração;
  * **marcar não toca no conteúdo nem no hash**, senão a linha deixa de valer
    como prova.
"""

from __future__ import annotations

import datetime

import pytest

from src.db.models import (
    Empresa,
    Escritorio,
    Escrituracao,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ImportadorDeDocumentos
from src.escrituracoes import (
    GeradorEFDContribuicoes,
    GeradorEFDICMS,
    TransmissaoInvalida,
    arquivar,
    cod_ver,
    marcar_transmitida,
    transmitidas_do_periodo,
)
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'transmitida.db'}")
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


@pytest.fixture
def com_documento(sessao, empresa):
    ImportadorDeDocumentos(sessao, escritorio_id=empresa.escritorio_id).importar(nfe_xml())
    sessao.commit()
    return empresa


def gerar_e_arquivar(sessao, empresa, *, cod_fin="0", tipo="efd_icms") -> Escrituracao:
    """Uma escrituração arquivada, como `fiscal gerar` produz.

    `cod_fin` vai para o `0000`: 0 = original, 1 = retificadora.
    """
    if tipo == "efd_icms":
        gerador = GeradorEFDICMS(
            sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM, cod_fin=cod_fin
        )
    else:
        gerador = GeradorEFDContribuicoes(
            sessao,
            empresa=empresa,
            data_inicio=INICIO,
            data_fim=FIM,
            tipo_escrituracao=cod_fin,
        )
    escrituracao = arquivar(
        sessao,
        resultado=gerador.gerar(),
        empresa=empresa,
        tipo=tipo,
        data_inicio=INICIO,
        data_fim=FIM,
    )
    sessao.commit()
    return escrituracao


# ── Nenhuma é marcada sozinha ──────────────────────────────────────────────


def test_escrituracao_nasce_sem_marca(sessao, com_documento):
    """O sistema não transmite: a informação vem de fora."""
    escrituracao = gerar_e_arquivar(sessao, com_documento)

    assert not escrituracao.transmitida
    assert escrituracao.transmitida_em is None
    assert escrituracao.recibo is None


def test_gerar_de_novo_nao_transfere_a_marca(sessao, com_documento):
    """A segunda geração não herda nada da primeira, nem para menos.

    Marcar a mais recente automaticamente diria que foi entregue justamente a
    que se acabou de gerar para conferir.
    """
    primeira = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, primeira, recibo="R-1")
    sessao.commit()

    segunda = gerar_e_arquivar(sessao, com_documento)

    assert primeira.transmitida
    assert not segunda.transmitida


# ── Marcar ─────────────────────────────────────────────────────────────────


def test_marcar_registra_quando_recibo_e_quem(sessao, com_documento):
    """Quem marcou não é necessariamente quem gerou — são dois campos."""
    usuario = Usuario(email="fiscal@exemplo.com", nome="Fiscal", senha_hash="x", salt="y")
    sessao.add(usuario)
    sessao.commit()

    escrituracao = gerar_e_arquivar(sessao, com_documento)
    quando = datetime.datetime(2026, 8, 14, 10, 30)

    marcar_transmitida(sessao, escrituracao, recibo="ABC-123", quando=quando, usuario_id=usuario.id)
    sessao.commit()

    assert escrituracao.transmitida
    assert escrituracao.transmitida_em == quando
    assert escrituracao.recibo == "ABC-123"
    assert escrituracao.transmitida_por_id == usuario.id
    assert escrituracao.usuario_id is None


def test_marcar_sem_recibo_e_permitido(sessao, com_documento):
    """O recibo às vezes chega depois, e a entrega não deixa de ter ocorrido."""
    escrituracao = gerar_e_arquivar(sessao, com_documento)

    marcar_transmitida(sessao, escrituracao)

    assert escrituracao.transmitida
    assert escrituracao.recibo is None


def test_marcar_nao_toca_no_conteudo_nem_no_hash(sessao, com_documento):
    """A linha continua valendo como prova do que saiu."""
    escrituracao = gerar_e_arquivar(sessao, com_documento)
    conteudo, hash_antes = escrituracao.conteudo, escrituracao.hash_conteudo

    marcar_transmitida(sessao, escrituracao, recibo="R-9")
    sessao.commit()

    assert escrituracao.conteudo == conteudo
    assert escrituracao.hash_conteudo == hash_antes


# ── Marcar não se desfaz ───────────────────────────────────────────────────


def test_marcar_duas_vezes_e_recusado(sessao, com_documento):
    """Transmitir é fato do mundo; remarcar reescreveria o registro dele."""
    escrituracao = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, escrituracao, recibo="R-1")
    sessao.commit()

    with pytest.raises(TransmissaoInvalida, match="já está marcada"):
        marcar_transmitida(sessao, escrituracao, recibo="R-2")


def test_a_recusa_diz_o_que_fazer(sessao, com_documento):
    escrituracao = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, escrituracao)
    sessao.commit()

    with pytest.raises(TransmissaoInvalida, match="retificadora"):
        marcar_transmitida(sessao, escrituracao)


def test_o_recibo_da_primeira_marca_sobrevive_a_tentativa(sessao, com_documento):
    escrituracao = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, escrituracao, recibo="R-1")
    sessao.commit()

    with pytest.raises(TransmissaoInvalida):
        marcar_transmitida(sessao, escrituracao, recibo="R-2")

    assert escrituracao.recibo == "R-1"


# ── A segunda entrega do período ───────────────────────────────────────────


def test_segunda_original_no_mesmo_periodo_e_recusada(sessao, com_documento):
    """Ou o arquivo devia ser retificadora, ou a marca anterior está errada."""
    primeira = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, primeira)
    sessao.commit()

    segunda = gerar_e_arquivar(sessao, com_documento, cod_fin="0")

    with pytest.raises(TransmissaoInvalida, match="ORIGINAL"):
        marcar_transmitida(sessao, segunda)


def test_retificadora_no_mesmo_periodo_e_aceita(sessao, com_documento):
    """É o caminho certo para corrigir o que já foi entregue."""
    primeira = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, primeira)
    sessao.commit()

    retificadora = gerar_e_arquivar(sessao, com_documento, cod_fin="1")
    marcar_transmitida(sessao, retificadora, recibo="R-2")
    sessao.commit()

    assert primeira.transmitida and retificadora.transmitida


def test_a_finalidade_e_lida_do_arquivo_que_saiu(sessao, com_documento):
    """Não do parâmetro de geração: o Fisco recebeu o arquivo, não o parâmetro.

    O `0000` é adulterado depois de arquivado — como se tivesse sido gerado
    assim. A recusa tem de acompanhar o conteúdo.
    """
    primeira = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, primeira)
    sessao.commit()

    # Gerada como retificadora, mas o conteúdo diz original.  A versão do
    # leiaute vem de `cod_ver`, e não escrita à mão: fixa, a substituição
    # deixava de casar quando a versão mudava, e o teste passava a montar um
    # cenário que não existia — sem acusar nada, porque quem falha depois é a
    # asserção, e não a substituição.
    segunda = gerar_e_arquivar(sessao, com_documento, cod_fin="1")
    versao = cod_ver(FIM)
    antes = segunda.conteudo
    segunda.conteudo = antes.replace(f"|0000|{versao}|1|", f"|0000|{versao}|0|", 1)
    assert segunda.conteudo != antes, "o 0000 não foi adulterado; o cenário não existe"
    sessao.flush()

    with pytest.raises(TransmissaoInvalida, match="ORIGINAL"):
        marcar_transmitida(sessao, segunda)


def test_forcar_passa_por_cima_da_recusa(sessao, com_documento):
    """Existe porque o caso legítimo existe: entrega rejeitada e reenviada."""
    primeira = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, primeira)
    sessao.commit()

    segunda = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, segunda, forcar=True)
    sessao.commit()

    assert segunda.transmitida


def test_outro_periodo_nao_disputa_com_este(sessao, com_documento):
    """Julho entregue não impede agosto de ser entregue como original."""
    julho = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, julho)
    sessao.commit()

    agosto = arquivar(
        sessao,
        resultado=GeradorEFDICMS(
            sessao,
            empresa=com_documento,
            data_inicio=datetime.date(2026, 8, 1),
            data_fim=datetime.date(2026, 8, 31),
        ).gerar(),
        empresa=com_documento,
        tipo="efd_icms",
        data_inicio=datetime.date(2026, 8, 1),
        data_fim=datetime.date(2026, 8, 31),
    )
    marcar_transmitida(sessao, agosto)
    sessao.commit()

    assert agosto.transmitida


def test_outra_obrigacao_nao_disputa_com_esta(sessao, com_documento):
    """A EFD ICMS e a EFD-Contribuições do mesmo mês são duas entregas."""
    icms = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, icms)
    sessao.commit()

    contribuicoes = gerar_e_arquivar(sessao, com_documento, tipo="efd_contribuicoes")
    marcar_transmitida(sessao, contribuicoes)
    sessao.commit()

    assert contribuicoes.transmitida


def test_outra_empresa_nao_disputa_com_esta(sessao, com_documento):
    outra = Empresa(
        cnpj="11222333000181",
        nome="OUTRA LTDA",
        uf="TO",
        ie="111111111",
        cod_mun="1721000",
        ind_perfil="A",
        ind_ativ="1",
        escritorio_id=com_documento.escritorio_id,
    )
    sessao.add(outra)
    sessao.commit()

    marcar_transmitida(sessao, gerar_e_arquivar(sessao, com_documento))
    sessao.commit()

    da_outra = gerar_e_arquivar(sessao, outra)
    marcar_transmitida(sessao, da_outra)
    sessao.commit()

    assert da_outra.transmitida


# ── O histórico de entregas do período ─────────────────────────────────────


def test_transmitidas_do_periodo_ignora_as_nao_entregues(sessao, com_documento):
    """Geração que ninguém entregou não é entrega.

    São necessárias TRÊS escriturações para o teste dizer algo: com duas, uma
    consulta sem filtro de transmissão devolveria a mesma lista por acidente,
    já que a única outra linha é justamente a entregue.
    """
    entregue = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, entregue)
    sessao.commit()
    gerar_e_arquivar(sessao, com_documento)  # ninguém entregou esta
    so_gerada = gerar_e_arquivar(sessao, com_documento)

    anteriores = transmitidas_do_periodo(sessao, so_gerada)

    assert [e.id for e in anteriores] == [entregue.id]


def test_transmitidas_do_periodo_nao_conta_a_propria(sessao, com_documento):
    """Senão toda escrituração marcada apareceria como sua própria anterior."""
    entregue = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, entregue)
    sessao.commit()

    assert transmitidas_do_periodo(sessao, entregue) == []


def test_as_entregas_saem_na_ordem_em_que_sairam(sessao, com_documento):
    """A ordem é o que distingue a original da retificadora."""
    primeira = gerar_e_arquivar(sessao, com_documento)
    marcar_transmitida(sessao, primeira, quando=datetime.datetime(2026, 8, 10, 9, 0))
    retificadora = gerar_e_arquivar(sessao, com_documento, cod_fin="1")
    marcar_transmitida(sessao, retificadora, quando=datetime.datetime(2026, 8, 20, 9, 0))
    sessao.commit()

    terceira = gerar_e_arquivar(sessao, com_documento, cod_fin="1")

    assert [e.id for e in transmitidas_do_periodo(sessao, terceira)] == [
        primeira.id,
        retificadora.id,
    ]
