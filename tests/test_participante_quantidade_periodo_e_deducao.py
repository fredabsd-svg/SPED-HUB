"""Quatro defeitos menores do arquivo, cada um recusado ou apurado errado.

* **0150 com o município errado.** O `COD_MUN` do participante vinha do
  `cMunFG` — o município do fato gerador, que numa venda é o **da empresa**,
  não o do cliente —, e o `COD_PAIS` e o `END`, obrigatórios, saíam vazios.
  O validador confere a IE do participante contra a UF do `COD_MUN`: toda
  venda interestadual saía com o participante no estado errado;
* **QTD com duas casas.** O C170 aceita cinco (N - 05); `0,004` virava vazio
  e `2,12345` virava `2,12`;
* **período invertido ou de mais de um mês** era aceito pela CLI e
  arquivado. A EFD "tem periodicidade mensal", e o `DT_FIN` "deve pertencer
  ao mesmo mês/ano" do `DT_INI` (Guia Prático da EFD ICMS/IPI 3.2.2, 0000);
* **dedução maior que o saldo devedor sumia.** O `VL_SLD_CREDOR_TRANSPORTAR`
  do E110 só recebia o saldo credor da apuração; a dedução que passava do
  devedor não ia para lugar nenhum. O Guia (E110, campos 13 e 14) manda
  levá-la ao saldo credor a transportar.
"""

from __future__ import annotations

import datetime

import pytest

from src.cli import main
from src.db.models import (
    Empresa,
    Escritorio,
    Escrituracao,
    criar_engine,
    get_session,
    init_db,
)
from src.escrituracoes import base, criar_ajuste
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES, EFD_ICMS
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"
CLIENTE = "12345678000195"
INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


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


def _importar(banco, tmp_path, conteudo: bytes, nome="nfe.xml"):
    xml = tmp_path / nome
    xml.write_bytes(conteudo)
    assert main(["fiscal", "importar", str(xml), "--escritorio", "1", "--db", banco]) == 0


def _gerar(banco, destino, *, tipo="efd_icms", de="2026-07-01", ate="2026-07-31"):
    return main(
        [
            "fiscal",
            "gerar",
            "--empresa",
            "1",
            "--de",
            de,
            "--ate",
            ate,
            "--tipo",
            tipo,
            "--saida",
            str(destino),
            "--db",
            banco,
        ]
    )


def _registros(destino, tipo, leiaute) -> list[dict[str, str]]:
    linhas = destino.read_bytes().decode("latin-1").split("\r\n")
    return [
        dict(zip(leiaute[tipo], linha.split("|")[2:-1], strict=True))
        for linha in linhas
        if linha.startswith(f"|{tipo}|")
    ]


class TestParticipante:
    @pytest.mark.parametrize(
        ("tipo", "leiaute"),
        [("efd_icms", EFD_ICMS), ("efd_contribuicoes", EFD_CONTRIBUICOES)],
        ids=["efd_icms", "efd_contribuicoes"],
    )
    def test_numa_venda_o_municipio_e_o_do_cliente(self, banco, tmp_path, tipo, leiaute):
        _importar(banco, tmp_path, nfe_xml(emitente_cnpj=CNPJ, destinatario_cnpj=CLIENTE))
        destino = tmp_path / "sped.txt"
        assert _gerar(banco, destino, tipo=tipo) == 0

        (participante,) = _registros(destino, "0150", leiaute)
        assert participante["COD_PART"] == CLIENTE
        assert participante["COD_MUN"] == "1721000", (
            "o 0150 do cliente saiu com o município do fato gerador (o da empresa): "
            "o validador confere a IE do participante contra a UF do COD_MUN"
        )

    def test_pais_e_endereco_obrigatorios(self, banco, tmp_path):
        _importar(banco, tmp_path, nfe_xml(emitente_cnpj=CNPJ, destinatario_cnpj=CLIENTE))
        destino = tmp_path / "efd.txt"
        _gerar(banco, destino)

        (participante,) = _registros(destino, "0150", EFD_ICMS)
        assert (
            participante["COD_PAIS"] == "1058"
        ), "COD_PAIS vazio: o Guia manda informar inclusive para o Brasil (1058)"
        assert participante["END"] == "AVENIDA JK", "END é obrigatório (O) no 0150"
        assert participante["NUM"] == "1500"
        assert participante["COMPL"] == "SALA 2"
        assert participante["BAIRRO"] == "PLANO DIRETOR SUL"

    def test_numa_entrada_o_endereco_e_o_do_fornecedor(self, banco, tmp_path):
        _importar(banco, tmp_path, nfe_xml())
        destino = tmp_path / "efd.txt"
        _gerar(banco, destino)

        (participante,) = _registros(destino, "0150", EFD_ICMS)
        assert participante["COD_PART"] == CLIENTE  # o emitente do fixture
        assert (participante["COD_MUN"], participante["END"]) == ("3550308", "RUA DAS INDUSTRIAS")


class TestQuantidade:
    @pytest.mark.parametrize(
        ("valor", "esperado"),
        [
            (0.004, "0,004"),
            (2.12345, "2,12345"),
            (10, "10,00"),
            (1.5, "1,50"),
            (2.123456, "2,12346"),
            (0, "0,00"),
            (None, "0,00"),
        ],
    )
    def test_ate_cinco_casas(self, valor, esperado):
        assert base.formatar_quantidade(valor) == esperado

    def test_a_quantidade_fracionada_chega_ao_c170(self, banco, tmp_path):
        _importar(
            banco,
            tmp_path,
            nfe_xml().decode().replace("<qCom>10.0000</qCom>", "<qCom>0.0040</qCom>").encode(),
        )
        destino = tmp_path / "efd.txt"
        _gerar(banco, destino)

        (item,) = _registros(destino, "C170", EFD_ICMS)
        assert item["QTD"] == "0,004", (
            "a quantidade de 0,004 saiu vazia: QTD é obrigatório no C170 e aceita "
            "cinco casas decimais"
        )


class TestPeriodo:
    @pytest.mark.parametrize(
        ("de", "ate"),
        [("2026-07-31", "2026-07-01"), ("2026-07-01", "2026-08-31"), ("2026-06-15", "2026-07-14")],
        ids=["invertido", "dois-meses", "atravessa-o-mes"],
    )
    def test_periodo_que_o_leiaute_nao_admite_e_recusado(self, banco, tmp_path, capsys, de, ate):
        codigo = _gerar(banco, tmp_path / "efd.txt", de=de, ate=ate)

        assert codigo == 1, (
            f"a CLI gerou e arquivou o período {de} a {ate}: o validador recusa DT_FIN "
            "fora do mês do DT_INI, e o arquivo ficou no histórico como se valesse"
        )
        assert "período" in capsys.readouterr().out
        with get_session(criar_engine(url=banco)) as sessao:
            assert not sessao.query(Escrituracao).all(), "o período inválido foi arquivado"

    def test_fracao_de_mes_continua_valendo(self, banco, tmp_path):
        """Início ou encerramento de atividade: o Guia admite a fração."""
        assert _gerar(banco, tmp_path / "efd.txt", de="2026-07-10", ate="2026-07-31") == 0

    def test_ajuste_de_apuracao_tambem_recusa(self, banco, capsys):
        codigo = main(
            [
                "fiscal",
                "ajuste",
                "--empresa",
                "1",
                "--de",
                "2026-07-31",
                "--ate",
                "2026-07-01",
                "--codigo",
                "TO020001",
                "--valor",
                "10",
                "--db",
                banco,
            ]
        )
        assert codigo == 1

    def test_espelho_tambem_recusa(self, banco, capsys):
        codigo = main(
            ["fiscal", "espelho", "--empresa", "1", "--de", "2026-07-01", "--ate", "2026-09-30"]
            + ["--db", banco]
        )
        assert codigo == 1


class TestDeducao:
    def test_deducao_maior_que_o_devedor_vai_para_o_saldo_credor(self, banco, tmp_path, capsys):
        # Venda com 180,00 de débito e 200,00 de dedução (quarta posição 4).
        _importar(banco, tmp_path, nfe_xml(emitente_cnpj=CNPJ, destinatario_cnpj=CLIENTE))
        with get_session(criar_engine(url=banco)) as sessao:
            criar_ajuste(
                sessao,
                empresa=sessao.get(Empresa, 1),
                data_inicio=INICIO,
                data_fim=FIM,
                cod_aj="TO040001",
                valor=200.0,
            )
            sessao.commit()
        destino = tmp_path / "efd.txt"
        _gerar(banco, destino)

        (e110,) = _registros(destino, "E110", EFD_ICMS)
        assert e110["VL_SLD_APURADO"] == "180,00"
        assert e110["VL_TOT_DED"] == "200,00"
        assert e110["VL_ICMS_RECOLHER"] == "0,00"
        assert e110["VL_SLD_CREDOR_TRANSPORTAR"] == "20,00", (
            "os 20,00 de dedução acima do saldo devedor sumiram: o Guia (E110, campos "
            "13 e 14) manda levá-los ao saldo credor a transportar"
        )
        assert "dedu" in capsys.readouterr().out.lower()

    def test_com_saldo_credor_a_deducao_se_soma_a_ele(self, banco, tmp_path):
        """Campo 11: o valor absoluto da expressão, "adicionado ao total das deduções"."""
        _importar(banco, tmp_path, nfe_xml())  # entrada: 180,00 de crédito
        with get_session(criar_engine(url=banco)) as sessao:
            criar_ajuste(
                sessao,
                empresa=sessao.get(Empresa, 1),
                data_inicio=INICIO,
                data_fim=FIM,
                cod_aj="TO040001",
                valor=50.0,
            )
            sessao.commit()
        destino = tmp_path / "efd.txt"
        _gerar(banco, destino)

        (e110,) = _registros(destino, "E110", EFD_ICMS)
        assert e110["VL_SLD_APURADO"] == "0,00"
        assert e110["VL_SLD_CREDOR_TRANSPORTAR"] == "230,00"
