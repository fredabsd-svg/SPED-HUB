"""Nota cancelada e nota denegada não são venda — e não entram na apuração.

O defeito: a situação do documento só decidia o `COD_SIT` do C100. A nota
cancelada (cStat 101) e a denegada (cStat 110) saíam com C170/C190 e somavam
no E110, no M200 e na apuração do IBS/CBS como se fossem venda — uma venda
autorizada, uma cancelada e uma denegada apuravam o triplo do ICMS devido.

O que o Guia Prático da EFD ICMS/IPI 3.2.2 manda (registro C100):

  * **Exceção 1**: documento cancelado (02) preenche **somente** REG, IND_OPER,
    IND_EMIT, COD_MOD, COD_SIT, SER, NUM_DOC e CHV_NFE; os demais campos saem
    vazios e **não se informam registros filhos**;
  * **denegado**: "a partir de janeiro de 2023, os códigos de situação 04 (NF-e
    denegada) e 05 (numeração inutilizada) serão descontinuados", e "não
    deverão ser informados os documentos fiscais eletrônicos denegados". O
    código 04 que o pedido original sugeria é justamente o que o validador
    recusa desde 2023 — a nota denegada não entra no arquivo.

Os testes passam pela porta de entrada (`sped-hub fiscal gerar`), porque é o
arquivo em disco que vai para o Fisco (§7.1).
"""

from __future__ import annotations

import datetime

import pytest

from src.cli import main
from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.escrituracoes import ApuracaoIBSCBS
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES, EFD_ICMS
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"
CLIENTE = "12345678000195"

AUTORIZADA = "35260798765432000198550010000000011000000011"
CANCELADA = "35260798765432000198550010000000021000000012"
DENEGADA = "35260798765432000198550010000000031000000013"


@pytest.fixture
def banco(tmp_path):
    url = f"sqlite:///{tmp_path / 'fiscal.db'}"
    engine = criar_engine(url=url)
    init_db(engine)
    with get_session(engine) as sessao:
        sessao.add(Escritorio(nome="Teste", slug="teste"))
        sessao.commit()
        sessao.add(
            Empresa(
                cnpj=CNPJ,
                nome="COMERCIO EXEMPLO LTDA",
                uf="TO",
                ie="293456789",
                cod_mun="1721000",
                ind_perfil="A",
                ind_ativ="1",
                ind_ativ_contribuicoes="2",
                cod_inc_trib="1",
                escritorio_id=1,
            )
        )
        sessao.commit()
    engine.dispose()
    return url


@pytest.fixture
def tres_vendas(banco, tmp_path):
    """Uma venda autorizada, uma cancelada e uma denegada — do mesmo jeito."""
    pasta = tmp_path / "xml"
    pasta.mkdir()
    for chave, numero, c_stat in (
        (AUTORIZADA, "1", "100"),
        (CANCELADA, "2", "101"),
        (DENEGADA, "3", "110"),
    ):
        (pasta / f"nfe{numero}.xml").write_bytes(
            nfe_xml(
                chave=chave,
                numero=numero,
                emitente_cnpj=CNPJ,
                destinatario_cnpj=CLIENTE,
                c_stat=c_stat,
            )
        )
    assert main(["fiscal", "importar", str(pasta), "--escritorio", "1", "--db", banco]) == 0
    return banco


def _gerar(banco, destino, tipo="efd_icms"):
    codigo = main(
        [
            "fiscal",
            "gerar",
            "--empresa",
            "1",
            "--de",
            "2026-07-01",
            "--ate",
            "2026-07-31",
            "--tipo",
            tipo,
            "--saida",
            str(destino),
            "--db",
            banco,
        ]
    )
    assert codigo == 0
    return destino.read_bytes().decode("latin-1").replace("\r\n", "\n").rstrip("\n").split("\n")


def _registros(linhas: list[str], tipo: str) -> list[list[str]]:
    return [linha.split("|")[2:-1] for linha in linhas if linha.startswith(f"|{tipo}|")]


def _campo(campos: list[str], tipo: str, nome: str, leiaute: dict) -> str:
    return campos[leiaute[tipo].index(nome)]


def _filhos_de(linhas: list[str], chave: str) -> list[str]:
    """Os registros que vêm depois do C100 da chave, até o próximo C100."""
    filhos: list[str] = []
    dentro = False
    for linha in linhas:
        if linha.startswith("|C100|"):
            dentro = chave in linha
            continue
        if dentro and linha[1:5] in {"C170", "C190"}:
            filhos.append(linha)
        elif dentro and not linha.startswith("|C1"):
            dentro = False
    return filhos


class TestEFDICMS:
    def test_a_apuracao_so_leva_a_venda_autorizada(self, tres_vendas, tmp_path):
        linhas = _gerar(tres_vendas, tmp_path / "efd.txt")
        (e110,) = _registros(linhas, "E110")

        assert _campo(e110, "E110", "VL_TOT_DEBITOS", EFD_ICMS) == "180,00", (
            "a nota cancelada e a denegada entraram no débito do E110: o arquivo "
            "apura ICMS de venda que não houve, e a empresa recolhe o triplo"
        )
        assert _campo(e110, "E110", "VL_ICMS_RECOLHER", EFD_ICMS) == "180,00"

    def test_a_cancelada_sai_so_com_os_campos_da_excecao_1(self, tres_vendas, tmp_path):
        linhas = _gerar(tres_vendas, tmp_path / "efd.txt")
        (cancelada,) = [c for c in _registros(linhas, "C100") if CANCELADA in c]

        preenchidos = {
            nome for nome, valor in zip(EFD_ICMS["C100"], cancelada, strict=True) if valor
        }
        assert preenchidos == {
            "IND_OPER",
            "IND_EMIT",
            "COD_MOD",
            "COD_SIT",
            "SER",
            "NUM_DOC",
            "CHV_NFE",
        }, (
            "C100 de nota cancelada com valor, data ou participante: o Guia (Exceção 1 "
            "do C100) manda deixar vazio tudo além destes campos, e o validador recusa"
        )
        assert _campo(cancelada, "C100", "COD_SIT", EFD_ICMS) == "02"
        assert not _filhos_de(linhas, CANCELADA), (
            "nota cancelada saiu com C170/C190: registro filho de documento cancelado é "
            "vedado pela Exceção 1 do C100, e o C190 é o que alimenta o E110"
        )

    def test_a_denegada_nao_entra_no_arquivo(self, tres_vendas, tmp_path):
        linhas = _gerar(tres_vendas, tmp_path / "efd.txt")

        assert not [linha for linha in linhas if DENEGADA in linha], (
            "a nota denegada entrou no arquivo: desde 01/2023 o COD_SIT 04 foi "
            "descontinuado e o Guia manda não informar documento denegado"
        )
        assert all(
            _campo(c, "C100", "COD_SIT", EFD_ICMS) != "04" for c in _registros(linhas, "C100")
        )

    def test_quem_so_aparece_em_nota_cancelada_nao_vira_participante(self, banco, tmp_path):
        """O 0150 só pode listar quem algum registro referencia (Guia, 0150)."""
        pasta = tmp_path / "xml"
        pasta.mkdir()
        (pasta / "cancelada.xml").write_bytes(
            nfe_xml(
                chave=CANCELADA,
                numero="2",
                emitente_cnpj=CNPJ,
                destinatario_cnpj=CLIENTE,
                c_stat="101",
            )
        )
        main(["fiscal", "importar", str(pasta), "--escritorio", "1", "--db", banco])

        linhas = _gerar(banco, tmp_path / "efd.txt")

        assert not _registros(linhas, "0150"), (
            "o cliente da nota cancelada virou 0150 sem nenhum C100 que o cite: "
            "o validador recusa participante sem referência"
        )

    def test_o_aviso_nomeia_a_denegada(self, tres_vendas, tmp_path, capsys):
        _gerar(tres_vendas, tmp_path / "efd.txt")
        saida = capsys.readouterr().out
        assert (
            "denegad" in saida and "3" in saida
        ), "a nota denegada sumiu do arquivo sem que ninguém fosse avisado"

    def test_o_espelho_diz_que_a_nota_e_cancelada(self, tres_vendas, capsys):
        """Sem dizer por quê, ela apareceria como uma nota de valor zero."""
        codigo = main(
            [
                "fiscal",
                "espelho",
                "--empresa",
                "1",
                "--de",
                "2026-07-01",
                "--ate",
                "2026-07-31",
                "--db",
                tres_vendas,
            ]
        )
        saida = capsys.readouterr().out

        assert codigo == 0, "a nota cancelada fez o espelho acusar divergência"
        (linha,) = [linha for linha in saida.splitlines() if "nº 2 " in linha]
        assert "CANCELADO" in linha


class TestEFDContribuicoes:
    def test_o_m200_so_leva_a_venda_autorizada(self, tres_vendas, tmp_path):
        linhas = _gerar(tres_vendas, tmp_path / "contrib.txt", tipo="efd_contribuicoes")
        (m200,) = _registros(linhas, "M200")

        assert _campo(m200, "M200", "VL_TOT_CONT_NC_PER", EFD_CONTRIBUICOES) == "16,50", (
            "a nota cancelada e a denegada entraram no PIS do M200: contribuição "
            "sobre receita que não existiu"
        )

    def test_a_cancelada_nao_leva_c170(self, tres_vendas, tmp_path):
        linhas = _gerar(tres_vendas, tmp_path / "contrib.txt", tipo="efd_contribuicoes")

        assert not _filhos_de(linhas, CANCELADA), (
            "o Guia da EFD-Contribuições (C100) diz que nota cancelada não leva " "registros filhos"
        )
        assert not [linha for linha in linhas if DENEGADA in linha]


class TestApuracaoDaReforma:
    def test_cbs_e_ibs_so_da_venda_autorizada(self, tres_vendas):
        with get_session(criar_engine(url=tres_vendas)) as sessao:
            empresa = sessao.get(Empresa, 1)
            resultado = ApuracaoIBSCBS(
                sessao,
                empresa=empresa,
                data_inicio=datetime.date(2026, 7, 1),
                data_fim=datetime.date(2026, 7, 31),
            ).apurar()

        assert resultado.cbs.debito == pytest.approx(9.00), (
            "a CBS da nota cancelada e da denegada entrou no débito: tributo sobre "
            "operação que não aconteceu"
        )
        assert resultado.ibs_uf.debito == pytest.approx(0.70)
        assert resultado.documentos == 1

    def test_a_situacao_corrigida_na_tela_vale(self, tres_vendas):
        """A situação é lida da camada efetiva, como todo o resto."""
        from src.documentos import ORIGEM_USUARIO, aplicar_ajuste

        with get_session(criar_engine(url=tres_vendas)) as sessao:
            documento = (
                sessao.query(DocumentoFiscal).filter(DocumentoFiscal.chave == AUTORIZADA).one()
            )
            aplicar_ajuste(
                sessao,
                documento=documento,
                campo="situacao",
                valor_novo="cancelado",
                origem=ORIGEM_USUARIO,
            )
            sessao.commit()
            resultado = ApuracaoIBSCBS(
                sessao,
                empresa=sessao.get(Empresa, 1),
                data_inicio=datetime.date(2026, 7, 1),
                data_fim=datetime.date(2026, 7, 31),
            ).apurar()

        assert resultado.cbs.debito == 0.0


def test_importador_ainda_guarda_as_tres(tres_vendas):
    """Ficar fora do arquivo não é ficar fora da Central: o XML é a prova."""
    with get_session(criar_engine(url=tres_vendas)) as sessao:
        situacoes = {d.chave: d.situacao for d in sessao.query(DocumentoFiscal).all()}
    assert situacoes == {
        AUTORIZADA: "autorizado",
        CANCELADA: "cancelado",
        DENEGADA: "denegado",
    }
