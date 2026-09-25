"""Livro Razão e Livro Diário sobre uma ECD trimestral de verdade.

Antes da correção, no razão:

* a coluna de contrapartidas saía sempre vazia — a consulta só trazia as
  partidas da própria conta;
* duas partidas do mesmo lançamento na mesma conta viravam uma (a última);
* o saldo corrente começava em zero, ignorando o saldo inicial do I155, e o
  saldo final nunca batia com o I155;
* as linhas vinham ordenadas pelo número do lançamento **como texto**, antes
  da data: "11" antes de "3";
* o `criterios` de quem chamava era alterado.

No diário, "10" vinha antes de "9" no mesmo dia.
"""

from __future__ import annotations

import datetime
from unittest import mock

import pytest

import src.ecd_importer as importador
from src.db.models import criar_engine, get_session, init_db
from src.ecd_importer import ECDImportService
from src.filters.engine import FilterCriteria
from src.reports.diario import LivroDiario
from src.reports.razao import Razao
from tests.fixtures.multiperiodo import gerar_ecd_multiperiodo


@pytest.fixture
def trimestral(tmp_path):
    engine = criar_engine(":memory:")
    init_db(engine)
    sessao = get_session(engine)
    with mock.patch.object(importador, "emitir", lambda *a, **k: None):
        ecd_id = (
            ECDImportService(sessao)
            .importar(gerar_ecd_multiperiodo(tmp_path / "trimestral.txt"))
            .ecd_id
        )
    yield sessao, ecd_id
    sessao.close()
    engine.dispose()


def _linhas(trimestral, conta, criterios=None):
    sessao, ecd_id = trimestral
    razao = Razao(sessao, ecd_id)
    _ctx, linhas = razao.gerar(conta, criterios)
    return razao, linhas


class TestRazao:
    def test_ordem_cronologica_e_saldo_desde_o_i155(self, trimestral):
        _razao, linhas = _linhas(trimestral, "1.1.02")
        assert [(ln.data.isoformat(), ln.num_lcto, ln.saldo_corrente) for ln in linhas] == [
            ("2024-01-20", "3", 85_000),
            ("2024-01-25", "4", 79_000),
            ("2024-02-15", "6", 59_000),
            ("2024-02-20", "7", 47_000),
            ("2024-02-28", "8", 57_000),
            ("2024-03-10", "11", 52_000),
            ("2024-03-31", "14", 48_000),
        ], (
            "o razão de bancos precisa começar do SI de 50.000 do I155 e seguir a data; "
            "ordenado pelo número como texto, o lançamento 11 (março) vinha antes do 3"
        )

    def test_saldo_final_confere_com_o_i155(self, trimestral):
        razao, linhas = _linhas(trimestral, "1.1.02")
        conferencia = razao.conferir_saldo_final(linhas, 48_000.0)
        assert conferencia["status"] == "OK", conferencia
        assert razao.saldo_inicial == 50_000

    def test_contrapartidas(self, trimestral):
        _razao, linhas = _linhas(trimestral, "1.1.02")
        por_numero = {ln.num_lcto: ln.contrapartidas for ln in linhas}
        assert por_numero["3"] == "1.1.03", "a contrapartida do recebimento é Clientes"
        assert por_numero["4"] == "4.2.02"
        assert por_numero["8"] == "3.1"

    def test_duas_partidas_do_mesmo_lancamento_na_mesma_conta(self, trimestral):
        _razao, linhas = _linhas(trimestral, "4.2.02")
        assert sum(ln.debito for ln in linhas) == 7_000, (
            "o lançamento 4 debita 4.000 (CC1) e 2.000 (CC2) na mesma conta; "
            "contar só a última partida perde 4.000"
        )
        assert sum(ln.credito for ln in linhas) == 7_000
        assert linhas[-1].saldo_corrente == 0, "a conta de resultado termina encerrada"

    def test_periodo_a_partir_do_inicio_de_um_mes(self, trimestral):
        razao, linhas = _linhas(
            trimestral, "1.1.02", FilterCriteria(dt_ini=datetime.date(2024, 2, 1))
        )
        assert razao.saldo_inicial == 79_000, "o saldo inicial de fevereiro no I155"
        assert (linhas[0].num_lcto, linhas[0].saldo_corrente) == ("6", 59_000)

    def test_periodo_a_partir_do_meio_de_um_mes(self, trimestral):
        razao, linhas = _linhas(
            trimestral, "1.1.02", FilterCriteria(dt_ini=datetime.date(2024, 2, 16))
        )
        assert razao.saldo_inicial == 59_000, "SI de fevereiro menos o pagamento de 15/02"
        assert (linhas[0].num_lcto, linhas[0].saldo_corrente) == ("7", 47_000)
        assert linhas[-1].saldo_corrente == 48_000

    def test_nao_altera_os_criterios_de_quem_chama(self, trimestral):
        criterios = FilterCriteria(dt_ini=datetime.date(2024, 1, 1))
        _linhas(trimestral, "1.1.02", criterios)
        assert criterios.cod_cta_exato == [], (
            "o razão gravou a conta nos critérios do chamador; o próximo relatório "
            "com os mesmos critérios sairia filtrado por ela"
        )


class TestDiario:
    def test_ordem_por_data_e_numero(self, trimestral):
        sessao, ecd_id = trimestral
        _ctx, lancamentos, totais = LivroDiario(sessao, ecd_id).gerar()
        assert [lanc.num_lcto for lanc in lancamentos] == [str(n) for n in range(1, 16)], (
            "no mesmo dia (05/03), o lançamento 10 vinha antes do 9: o número era "
            "comparado como texto"
        )
        assert totais["total_debitos"] == totais["total_creditos"] == 227_000


class TestRazaoPelaLinhaDeComando:
    def test_razao_de_bancos(self, tmp_path, capsys):
        from src.cli import main

        banco = str(tmp_path / "razao.db")
        main(["importar-ecd", str(gerar_ecd_multiperiodo(tmp_path / "t.txt")), "--db", banco])
        capsys.readouterr()
        main(["relatorio", "razao", "--conta", "1.1.02", "--db", banco])
        saida = capsys.readouterr().out
        linhas = [linha for linha in saida.splitlines() if linha.startswith("2024-")]
        assert linhas[0].startswith("2024-01-20"), linhas[0]
        assert "1.1.03" in linhas[0], "contrapartida vazia"
        assert linhas[-1].rstrip().endswith("48,000.00"), linhas[-1]
        assert "Saldo anterior" in saida and "50,000.00" in saida
