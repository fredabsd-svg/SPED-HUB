"""Importação da ECD: valor monetário ilegível e o IND_GRANDE_PORTE.

* "1.234,56" (com separador de milhar) e "1 234,56" viravam `None` no parser
  e 0,00 no banco. A escrituração entrava com um débito a menos e nada
  indicava isso — o caso exato da §6.1 (escrituração parcial não existe).
* O importador lia a chave "IND_GRANDE_POR", mas o leiaute (e o parser)
  chamam o campo de "IND_GRANDE_PORTE": a coluna ficava sempre vazia.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest import mock

import pytest

import src.ecd_importer as importador
from src.db.models import ECD, Empresa, criar_engine, get_session, init_db
from src.ecd_importer import ECDImportError, ECDImportService
from src.parsers.ecd import CampoInvalidoError, ECDParser

AMOSTRA = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
LINHA_I155 = "|I155|1.1.2||200000.00|D|500000.00|450000.00|250000.00|D|"


def _amostra_com(tmp_path: Path, trocar: str, por: str) -> Path:
    texto = AMOSTRA.read_text(encoding="utf-8")
    assert texto.count(trocar) == 1
    arquivo = tmp_path / "ecd.txt"
    arquivo.write_text(texto.replace(trocar, por), encoding="utf-8")
    return arquivo


def _linha_de(arquivo: Path, trecho: str) -> int:
    for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
        if trecho in linha:
            return numero
    raise AssertionError(trecho)


@pytest.fixture
def sessao():
    engine = criar_engine(":memory:")
    init_db(engine)
    s = get_session(engine)
    yield s
    s.close()
    engine.dispose()


class TestValorMonetarioIlegivel:
    @pytest.mark.parametrize("valor", ["1.234,56", "1 234,56", "12a,00", "1,234.56"])
    def test_parser_recusa_com_linha_e_campo(self, tmp_path, valor):
        arquivo = _amostra_com(
            tmp_path, LINHA_I155, LINHA_I155.replace("|500000.00|", f"|{valor}|")
        )
        with pytest.raises(CampoInvalidoError) as exc:
            ECDParser().parse_todos(arquivo)
        assert exc.value.campo == "VL_DEB"
        assert exc.value.linha == _linha_de(arquivo, "|I155|1.1.2|")
        assert "VL_DEB" in str(exc.value) and f"linha {exc.value.linha}" in str(exc.value)

    @pytest.mark.parametrize(
        ("valor", "esperado"),
        [("500000.00", 500000.0), ("500000,00", 500000.0), ("-80000.00", -80000.0), ("7", 7.0)],
    )
    def test_ponto_ou_virgula_decimal_continuam_validos(self, tmp_path, valor, esperado):
        arquivo = _amostra_com(
            tmp_path, LINHA_I155, LINHA_I155.replace("|500000.00|", f"|{valor}|")
        )
        i155 = next(
            r for r in ECDParser().parse(arquivo) if r["_reg"] == "I155" and r["COD_CTA"] == "1.1.2"
        )
        assert i155["VL_DEB"] == esperado

    def test_importacao_recusada_sem_gravar_nada(self, sessao, tmp_path):
        arquivo = _amostra_com(
            tmp_path, LINHA_I155, LINHA_I155.replace("|500000.00|", "|1.234,56|")
        )
        with (
            mock.patch.object(importador, "emitir", lambda *a, **k: None),
            pytest.raises(ECDImportError) as exc,
        ):
            ECDImportService(sessao).importar(arquivo)
        mensagem = str(exc.value)
        assert "VL_DEB" in mensagem and f"linha {_linha_de(arquivo, '|I155|1.1.2|')}" in mensagem
        assert sessao.query(ECD).count() == 0, (
            "a ECD entrou com o débito de 1.234,56 gravado como 0,00: escrituração parcial "
            "com aparência de completa"
        )

    def test_pela_linha_de_comando(self, tmp_path, caplog):
        from src.cli import main

        arquivo = _amostra_com(tmp_path, "|I250|1.1.2||50000.00|D|", "|I250|1.1.2||50.000,00|D|")
        banco = str(tmp_path / "recusa.db")
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as saida:
            main(["importar-ecd", str(arquivo), "--db", banco])
        assert saida.value.code == 1
        assert "VL_DC" in caplog.text and "Arquivo recusado" in caplog.text

        engine = criar_engine(banco)
        with get_session(engine) as s:
            assert s.query(ECD).count() == 0
        engine.dispose()


class TestIndicadorDeGrandePorte:
    def test_ind_grande_porte_chega_a_empresa(self, sessao, tmp_path):
        # 0000 alinhado ao leiaute: o 16º campo (IND_GRANDE_PORTE) é 1.
        original = (
            "|0000|LECD|01012024|31122024|EMPRESA EXEMPLO LTDA|00123456000199|SP||1234567"
            "||0|0|1|0|0|E||1|0||"
        )
        alinhado = (
            "|0000|LECD|01012024|31122024|EMPRESA EXEMPLO LTDA|00123456000199|SP||1234567"
            "||0|0|1|0||1|G||N|N|0|0||"
        )
        arquivo = _amostra_com(tmp_path, original, alinhado)
        with mock.patch.object(importador, "emitir", lambda *a, **k: None):
            ECDImportService(sessao).importar(arquivo)
        empresa = sessao.query(Empresa).one()
        assert empresa.ind_grande_por == 1, (
            "o importador procurava a chave IND_GRANDE_POR; o leiaute chama o campo de "
            "IND_GRANDE_PORTE, e a coluna ficava sempre vazia"
        )
        assert empresa.tip_ecd == "G"
