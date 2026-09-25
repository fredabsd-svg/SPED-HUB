"""A EFD-Contribuições naquilo em que o Guia Prático não deixa dúvida.

Guia Prático da EFD-Contribuições, versão 1.35:

  * **M200/M600** — todos os treze campos têm "S" na coluna de obrigatório, e
    o próprio Guia diz, campo a campo, que no regime que não se aplica "o
    valor do campo deverá ser igual a 0". Vazio não é zero para quem valida;
  * **M200, campo 03** — "Validação: o somatório dos campos VL_TOT_CRED_DESC e
    VL_TOT_CRED_DESC_ANT deve ser menor ou igual ao valor do campo
    VL_TOT_CONT_NC_PER". O gerador descontava todo o crédito do mês, e com
    compra maior que venda o M200 dizia ter descontado mais do que devia;
  * **C010, campo 03** — `IND_ESCRI` só aceita 1 (consolidado, C180/C190) ou 2
    (individualizado, C100/C170). Saía "0";
  * **0110, campo 05** — `IND_REG_CUM` é o critério de escrituração de quem
    está "exclusivamente no regime cumulativo (COD_INC_TRIB = 2)", e "9" é o
    da escrituração detalhada nos blocos A, C, D e F — que é o que este
    arquivo é (não há F500 nem F550);
  * **C100, campo 13** — `IND_PGTO` é obrigatório ("S") e saía vazio. A EFD
    ICMS/IPI já usava `_ind_pgto`; o C100 é o mesmo registro nas duas, e o
    Guia da EFD-Contribuições delega a ela.
"""

from __future__ import annotations

import pytest

from src.cli import main
from src.db.models import Empresa, Escritorio, criar_engine, get_session, init_db
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"
CLIENTE = "12345678000195"


def _banco(tmp_path, regime: str) -> str:
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
                cod_inc_trib=regime,
                escritorio_id=1,
            )
        )
        sessao.commit()
    engine.dispose()
    return url


@pytest.fixture
def nao_cumulativo(tmp_path):
    return _banco(tmp_path, "1")


@pytest.fixture
def cumulativo(tmp_path):
    return _banco(tmp_path, "2")


def _importar(banco, tmp_path, *conteudos: bytes):
    pasta = tmp_path / "xml"
    pasta.mkdir(exist_ok=True)
    for n, conteudo in enumerate(conteudos):
        (pasta / f"nfe{n}.xml").write_bytes(conteudo)
    assert main(["fiscal", "importar", str(pasta), "--escritorio", "1", "--db", banco]) == 0


def _venda(numero="1", chave="35260798765432000198550010000000011000000011", **extras):
    return nfe_xml(
        chave=chave, numero=numero, emitente_cnpj=CNPJ, destinatario_cnpj=CLIENTE, **extras
    )


def _gerar(banco, tmp_path) -> dict[str, list[dict[str, str]]]:
    destino = tmp_path / "contrib.txt"
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
            "efd_contribuicoes",
            "--saida",
            str(destino),
            "--db",
            banco,
        ]
    )
    assert codigo == 0
    registros: dict[str, list[dict[str, str]]] = {}
    for linha in destino.read_bytes().decode("latin-1").split("\r\n"):
        if not linha:
            continue
        tipo, *campos = linha.split("|")[1:-1]
        registros.setdefault(tipo, []).append(
            dict(zip(EFD_CONTRIBUICOES[tipo], campos, strict=True))
        )
    return registros


class TestM200:
    @pytest.mark.parametrize("regime", ["1", "2"], ids=["nao-cumulativo", "cumulativo"])
    def test_nenhum_campo_sai_vazio(self, tmp_path, regime):
        banco = _banco(tmp_path, regime)
        _importar(banco, tmp_path, _venda())

        registros = _gerar(banco, tmp_path)

        for tipo in ("M200", "M600"):
            (consolidacao,) = registros[tipo]
            vazios = [nome for nome, valor in consolidacao.items() if valor == ""]
            assert not vazios, (
                f"{tipo} com campos obrigatórios vazios ({', '.join(vazios)}): o Guia "
                "marca os treze como obrigatórios e manda informar 0 no regime que não "
                "se aplica"
            )

    def test_o_credito_descontado_nao_passa_da_contribuicao(self, nao_cumulativo, tmp_path):
        """Uma venda (PIS 16,50) e três itens comprados (PIS 49,50)."""
        _importar(nao_cumulativo, tmp_path, _venda(), nfe_xml(itens=3))

        (m200,) = _gerar(nao_cumulativo, tmp_path)["M200"]

        assert m200["VL_TOT_CONT_NC_PER"] == "16,50"
        assert m200["VL_TOT_CRED_DESC"] == "16,50", (
            "o M200 descontou 49,50 de crédito contra 16,50 de contribuição: o Guia "
            "(M200, campo 03) exige crédito descontado menor ou igual à contribuição"
        )
        assert m200["VL_TOT_CONT_NC_DEV"] == "0,00"
        assert m200["VL_CONT_NC_REC"] == "0,00"
        assert m200["VL_TOT_CONT_REC"] == "0,00"

    def test_a_sobra_de_credito_e_avisada(self, nao_cumulativo, tmp_path, capsys):
        """O que não foi descontado não pode sumir calado: é saldo para depois."""
        _importar(nao_cumulativo, tmp_path, _venda(), nfe_xml(itens=3))
        _gerar(nao_cumulativo, tmp_path)

        saida = capsys.readouterr().out
        assert (
            "33,00" in saida and "M100" in saida
        ), "33,00 de crédito de PIS ficaram sem desconto e ninguém foi avisado"

    def test_com_contribuicao_maior_o_credito_entra_inteiro(self, nao_cumulativo, tmp_path):
        _importar(
            nao_cumulativo,
            tmp_path,
            _venda(itens=3),
            nfe_xml(),  # uma compra: 16,50 de crédito
        )
        (m200,) = _gerar(nao_cumulativo, tmp_path)["M200"]

        assert m200["VL_TOT_CRED_DESC"] == "16,50"
        assert m200["VL_TOT_CONT_NC_DEV"] == "33,00"
        assert m200["VL_TOT_CONT_REC"] == "33,00"

    def test_cumulativo_leva_a_contribuicao_nos_campos_do_cumulativo(self, cumulativo, tmp_path):
        _importar(cumulativo, tmp_path, _venda())

        (m200,) = _gerar(cumulativo, tmp_path)["M200"]

        assert m200["VL_TOT_CONT_NC_PER"] == "0,00"
        assert m200["VL_TOT_CONT_CUM_PER"] == "16,50"
        assert m200["VL_CONT_CUM_REC"] == "16,50"
        assert m200["VL_TOT_CONT_REC"] == "16,50"


class TestC010E0110:
    def test_ind_escri_e_o_do_c100(self, nao_cumulativo, tmp_path):
        _importar(nao_cumulativo, tmp_path, _venda())

        (c010,) = _gerar(nao_cumulativo, tmp_path)["C010"]

        assert c010["IND_ESCRI"] == "2", (
            "IND_ESCRI fora dos valores válidos (1 ou 2): este arquivo apura pelo "
            "registro individualizado, C100 e C170"
        )

    def test_cumulativo_declara_a_escrituracao_detalhada(self, cumulativo, tmp_path):
        _importar(cumulativo, tmp_path, _venda())

        (r0110,) = _gerar(cumulativo, tmp_path)["0110"]

        assert r0110["IND_REG_CUM"] == "9", (
            "o 0110 de quem está só no cumulativo saiu sem o critério de escrituração; "
            "o arquivo é a escrituração detalhada no bloco C, que é o código 9"
        )

    def test_nao_cumulativo_nao_leva_ind_reg_cum(self, nao_cumulativo, tmp_path):
        _importar(nao_cumulativo, tmp_path, _venda())

        (r0110,) = _gerar(nao_cumulativo, tmp_path)["0110"]

        assert r0110["IND_REG_CUM"] == ""


class TestIndicadorDePagamento:
    def test_o_ind_pgto_vem_do_documento(self, nao_cumulativo, tmp_path):
        _importar(nao_cumulativo, tmp_path, _venda(ind_pag="1"))

        (c100,) = _gerar(nao_cumulativo, tmp_path)["C100"]

        assert c100["IND_PGTO"] == "1", (
            "IND_PGTO vazio no C100 da EFD-Contribuições: o campo é obrigatório e o "
            "validador recusa"
        )

    def test_sem_indpag_sai_2_com_aviso(self, nao_cumulativo, tmp_path, capsys):
        _importar(nao_cumulativo, tmp_path, _venda(numero="44"))

        (c100,) = _gerar(nao_cumulativo, tmp_path)["C100"]

        assert c100["IND_PGTO"] == "2"
        saida = capsys.readouterr().out
        assert "IND_PGTO" in saida and "44" in saida
