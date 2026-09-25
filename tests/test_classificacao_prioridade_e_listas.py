"""Prioridade que vale mesmo quando a regra já está cumprida, e `em` pela CLI.

Dois defeitos do motor de classificação:

  * **a regra de menor prioridade vencia a de maior** quando o valor da maior
    já era o atual. O motor pulava a regra cumprida *antes* de registrar que
    ela reivindicava o campo, e a de baixo — que ninguém mandou valer ali —
    virava sugestão: "NCM 2203 é CFOP 6102" (prioridade 10) perdia para "NCM
    2203 é CFOP 2102" (prioridade 0), e o item já certo era "corrigido" para
    o errado;
  * **`em` e `nao_em` nunca casavam pela CLI.** A condição chega do terminal
    como texto — `cfop:em:5102,6102` —, e `_lista` a tratava como um valor só
    ("5102,6102"), em que nenhum CFOP está.
"""

from __future__ import annotations

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
from src.documentos import ImportadorDeDocumentos, MotorDeClassificacao
from src.documentos.classificacao import OPERADORES, criar_regra
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"
CLIENTE = "12345678000195"


@pytest.fixture
def banco(tmp_path):
    """Uma VENDA com item de NCM 22030000 e CFOP 6102."""
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
                escritorio_id=1,
            )
        )
        sessao.commit()
        ImportadorDeDocumentos(sessao, escritorio_id=1).importar(
            nfe_xml(emitente_cnpj=CNPJ, destinatario_cnpj=CLIENTE)
        )
        sessao.commit()
    engine.dispose()
    return url


def _regra(banco, nome, se, entao, prioridade="0"):
    assert (
        main(
            [
                "fiscal",
                "regras",
                "--acao-regra",
                "criar",
                "--nome",
                nome,
                "--se",
                se,
                "--entao",
                entao,
                "--prioridade",
                prioridade,
                "--escritorio",
                "1",
                "--db",
                banco,
            ]
        )
        == 0
    )


def _classificar(banco, capsys) -> str:
    capsys.readouterr()
    assert main(["fiscal", "classificar", "--empresa", "1", "--db", banco]) == 0
    return capsys.readouterr().out


class TestPrioridade:
    def test_a_regra_de_cima_cumprida_segura_o_campo(self, banco, capsys):
        _regra(banco, "ALTA", "ncm:22030000", "cfop:6102", prioridade="10")
        _regra(banco, "BAIXA", "ncm:22030000", "cfop:5102", prioridade="0")

        saida = _classificar(banco, capsys)

        assert "BAIXA" not in saida and "0 sugestões" in saida, (
            "a regra de prioridade 0 venceu a de prioridade 10 só porque a de cima "
            "já estava cumprida: o CFOP certo seria trocado pelo errado"
        )

    def test_empate_com_a_regra_cumprida_continua_sendo_conflito(self, banco, capsys):
        """Mesma prioridade, valores diferentes: o motor não escolhe."""
        _regra(banco, "JA-CUMPRIDA", "ncm:22030000", "cfop:6102", prioridade="5")
        _regra(banco, "OUTRA", "ncm:22030000", "cfop:5102", prioridade="5")

        saida = _classificar(banco, capsys)

        assert "CONFLITOS" in saida and "JA-CUMPRIDA" in saida and "OUTRA" in saida
        assert "0 sugestões" in saida

    def test_duas_regras_que_dizem_o_mesmo_nao_disputam(self, sessao_de):
        """Concordar não é conflito — e com a regra cumprida nem há o que sugerir."""
        sessao, documento = sessao_de
        for nome in ("A", "B"):
            criar_regra(
                sessao,
                nome=nome,
                prioridade=5,
                condicoes=[{"campo": "ncm", "valor": "22030000"}],
                acoes=[{"campo": "cfop", "valor": "6102"}],
            )
        resultado = MotorDeClassificacao(sessao).avaliar(documento)

        assert not resultado.conflitos and not resultado.sugestoes

    def test_a_de_baixo_vale_quando_a_de_cima_nao_casa(self, banco, capsys):
        """A reivindicação é de quem CASA — regra que não casa não segura nada."""
        _regra(banco, "ALTA", "ncm:84713012", "cfop:6108", prioridade="10")
        _regra(banco, "BAIXA", "ncm:22030000", "cfop:5102", prioridade="0")

        saida = _classificar(banco, capsys)

        assert "BAIXA" in saida and "1 sugestões" in saida


class TestListas:
    def test_em_pela_linha_de_comando_casa(self, banco, capsys):
        _regra(banco, "INTERESTADUAL", "cfop:em:5102,6102", "ncm:22030001")

        saida = _classificar(banco, capsys)

        assert "INTERESTADUAL" in saida and "1 sugestões" in saida, (
            "a condição `cfop:em:5102,6102` não casou com o CFOP 6102: a lista "
            "digitada no terminal era lida como um valor só"
        )

    def test_nao_em_pela_linha_de_comando(self, banco, capsys):
        _regra(banco, "FORA-DA-LISTA", "cfop:nao_em:5102,6102", "ncm:22030001")

        saida = _classificar(banco, capsys)

        assert "0 sugestões" in saida, "6102 está na lista, e `nao_em` casou mesmo assim"

    @pytest.mark.parametrize(
        ("operador", "valor", "casa"),
        [
            ("em", "5102, 6102", True),
            ("em", "5102,5405", False),
            ("em", ["5102", "6102"], True),
            ("nao_em", "5102,5405", True),
        ],
    )
    def test_operadores_de_lista(self, operador, valor, casa):
        assert OPERADORES[operador]("6102", valor) is casa


@pytest.fixture
def sessao_de(banco):
    with get_session(criar_engine(url=banco)) as sessao:
        documento = sessao.query(DocumentoFiscal).one()
        yield sessao, documento
