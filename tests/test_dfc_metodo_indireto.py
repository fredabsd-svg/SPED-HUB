"""DFC pelos métodos indireto e direto, conferida contra a variação do caixa.

A DFC anterior estava errada em todas as linhas:

* usava o saldo **final** de cada conta, não a variação do período;
* aumento de ativo saía como entrada de caixa (sinal trocado);
* o lucro líquido nunca era mapeado — a linha era sempre zero;
* os subtotais acumulavam as seções anteriores (o de financiamento somava
  operações e investimento);
* nada conferia o total com a variação do caixa.

Desde a correção da ECD real, os dois métodos saem dos **lançamentos** que
movimentam caixa e equivalentes (ver `src/reports/dfc.py`): o direto reparte
o que entrou e saiu entre as contrapartidas; o indireto parte do lucro e o
ajusta pelo que não teve caixa. Investimento e financiamento são os mesmos
nos dois.

Números da escrituração trimestral de `tests/fixtures/multiperiodo.py`,
calculados à mão:

    Indireto: lucro 21.000; depreciação +2.000; clientes 30.000→35.000
    (−5.000); estoques 20.000→20.000 (0); fornecedores 25.000→23.000
    (−2.000); impostos a recolher 5.000→6.000 (+1.000) → FCO 17.000
    Direto: recebimentos de clientes 35.000 + 8.000 (venda à vista);
    pagamentos a fornecedores 20.000 + 6.000 (despesas) → FCO 17.000
    imobilizado comprado à vista 12.000                     → FCI −12.000
    capital +10.000; empréstimos −5.000; lucros distribuídos −4.000 → FCF 1.000
    total 6.000 = caixa 10.000→18.000 + bancos 50.000→48.000
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

import src.ecd_importer as importador
from src.db.models import criar_engine, get_session, init_db
from src.ecd_importer import ECDImportService
from src.reports.dfc import DFC
from tests.fixtures.multiperiodo import gerar_ecd_multiperiodo

AMOSTRA = Path(__file__).parent / "fixtures" / "ecd_sample.txt"

ESPERADO_TRIMESTRE = {
    "Lucro (prejuízo) líquido do período": 21_000,
    "(+) Depreciação, amortização e exaustão": 2_000,
    "(Aumento) redução em clientes": -5_000,
    "Aumento (redução) em fornecedores": -2_000,
    "Aumento (redução) em obrigações tributárias": 1_000,
    "= Caixa líquido das atividades operacionais": 17_000,
    "Aquisição de imobilizado": -12_000,
    "= Caixa líquido das atividades de investimento": -12_000,
    "Integralização (restituição) de capital": 10_000,
    "Amortização de empréstimos e financiamentos": -5_000,
    "Lucros e dividendos distribuídos": -4_000,
    "= Caixa líquido das atividades de financiamento": 1_000,
    "= Aumento (redução) líquido de caixa e equivalentes": 6_000,
    "Caixa e equivalentes no início do período": 60_000,
    "Caixa e equivalentes no fim do período": 66_000,
    "Variação de caixa nos saldos (fim − início)": 6_000,
    "Diferença não conciliada (fluxos − saldos)": 0,
}

ESPERADO_DIRETO = {
    "Recebimentos de clientes": 43_000,
    "Pagamentos a fornecedores de mercadorias e serviços": -26_000,
    "= Caixa líquido das atividades operacionais": 17_000,
}


@pytest.fixture
def sessao():
    engine = criar_engine(":memory:")
    init_db(engine)
    s = get_session(engine)
    yield s
    s.close()
    engine.dispose()


def _importar(sessao, arquivo: Path) -> int:
    with mock.patch.object(importador, "emitir", lambda *a, **k: None):
        return ECDImportService(sessao).importar(arquivo).ecd_id


def _valores(linhas, campo="valor") -> dict[str, float]:
    return {
        ln.descricao: getattr(ln, campo) for ln in linhas if ln.tipo not in ("section", "grupo")
    }


class TestTrimestre:
    @pytest.fixture
    def dfc(self, sessao, tmp_path):
        ecd = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "t.txt"))
        return DFC(sessao, ecd).gerar()

    def test_cada_linha(self, dfc):
        _ctx, linhas, _totais = dfc
        valores = _valores(linhas)
        assert valores == pytest.approx(
            ESPERADO_TRIMESTRE
        ), "linhas do método indireto (linha zerada não aparece)"

    def test_metodo_direto(self, sessao, tmp_path):
        ecd = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "d.txt"))
        _ctx, linhas, totais = DFC(sessao, ecd).gerar(metodo="direto")
        valores = _valores(linhas)
        assert {k: valores[k] for k in ESPERADO_DIRETO} == ESPERADO_DIRETO
        assert totais["operacional_direto"] == totais["operacional_indireto"] == 17_000

    def test_lucro_liquido_vem_do_resultado(self, dfc):
        _ctx, linhas, totais = dfc
        assert (
            _valores(linhas)["Lucro (prejuízo) líquido do período"] == 21_000
        ), "a linha do lucro líquido nunca era mapeada e saía zero"
        assert totais["lucro_liquido"] == 21_000 == totais["lucro_dre"]

    def test_aumento_de_ativo_e_saida_de_caixa(self, dfc):
        _ctx, linhas, _totais = dfc
        valores = _valores(linhas)
        assert (
            valores["(Aumento) redução em clientes"] < 0
        ), "clientes subiu 5.000: dinheiro que não entrou; o sinal saía trocado"
        assert (
            valores["Aquisição de imobilizado"] == -12_000
        ), "compra de 12.000 no trimestre; o saldo final (112.000) não é fluxo"

    def test_subtotais_por_secao(self, dfc):
        _ctx, _linhas, totais = dfc
        assert (totais["operacional"], totais["investimento"], totais["financiamento"]) == (
            17_000,
            -12_000,
            1_000,
        ), "o subtotal de financiamento acumulava operações e investimento"

    def test_conciliacao_com_o_caixa(self, dfc):
        _ctx, _linhas, totais = dfc
        assert totais["variacao_caixa"] == 6_000
        assert (totais["caixa_inicial"], totais["caixa_final"]) == (60_000, 66_000)
        assert totais["variacao_caixa_saldos"] == 6_000
        assert totais["diferenca_conciliacao"] == 0
        assert totais["conciliado"] is True

    def test_comparativo_usa_o_mesmo_metodo(self, sessao, tmp_path):
        """O ano anterior (valores em dobro) calculado pelo mesmo caminho."""
        anterior = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "a.txt", ano=2023, fator=2))
        atual = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "b.txt"))
        _ctx, linhas, totais = DFC(sessao, atual).gerar()
        _ctx, linhas_do_anterior, totais_do_anterior = DFC(sessao, anterior).gerar()

        assert totais["tem_anterior"] is True
        coluna_anterior = _valores(linhas, "valor_anterior")
        do_anterior = _valores(linhas_do_anterior)
        assert {
            k: coluna_anterior[k] for k in do_anterior
        } == do_anterior, "a coluna do período anterior precisa ser a DFC do ano anterior"
        assert coluna_anterior["= Aumento (redução) líquido de caixa e equivalentes"] == 12_000
        assert totais["variacao_caixa_anterior"] == totais_do_anterior["variacao_caixa"]
        assert totais["diferenca_conciliacao_anterior"] == 0


class TestAmostra:
    """Os lançamentos da amostra não explicam os saldos, e a DFC diz isso."""

    def test_linhas_e_diferenca(self, sessao):
        _ctx, linhas, totais = DFC(sessao, _importar(sessao, AMOSTRA)).gerar(metodo="direto")
        valores = _valores(linhas)
        assert valores["Recebimentos de clientes"] == 250_000
        assert valores["Pagamentos a fornecedores de mercadorias e serviços"] == -50_000
        assert valores["Pagamentos a empregados e encargos sociais"] == -60_000
        assert totais["operacional"] == 140_000
        assert totais["investimento"] == -40_000
        assert totais["financiamento"] == 0
        assert totais["variacao_caixa"] == 100_000
        assert totais["variacao_caixa_saldos"] == 80_000, "caixa 50→80 mil e bancos 200→250 mil"
        assert totais["diferenca_conciliacao"] == 20_000, (
            "os lançamentos das contas de caixa somam 100.000 e o I155 diz 80.000: a DFC "
            "não pode conciliar, e a diferença precisa aparecer"
        )
        assert totais["conciliado"] is False
        assert valores["Diferença não conciliada (fluxos − saldos)"] == 20_000

    def test_pdf_avisa_que_nao_concilia(self, sessao):
        from src.reports.export_engine import ExportEngine, WhiteLabel

        ctx, linhas, totais = DFC(sessao, _importar(sessao, AMOSTRA)).gerar()
        html = ExportEngine().render_html(
            "dfc.html", ctx, WhiteLabel(), linhas=linhas, totais=totais
        )
        assert "não conciliam" in html and "20.000,00" in html
        assert (
            "difere do" in html
        ), "o lucro dos lançamentos da amostra não é o do I355, e o PDF avisa"


class TestPelaTela:
    def test_dfc_no_painel(self, tmp_path):
        import os

        from fastapi.testclient import TestClient

        from src.auth import init_auth
        from src.cli import main

        caminho = str(tmp_path / "dfc.db")
        main(["importar-ecd", str(gerar_ecd_multiperiodo(tmp_path / "t.txt")), "--db", caminho])
        os.environ["SPED_HUB_DB"] = caminho
        from src.dashboard.app import app

        init_auth(caminho)
        cliente = TestClient(app)
        dados = {"email": "dfc@test.local", "nome": "DFC", "senha": "senha123"}
        assert cliente.post("/api/register", data=dados).status_code == 200
        del dados["nome"]
        assert cliente.post("/api/login", data=dados).status_code == 200

        html = cliente.get("/api/dfc", params={"ecd_id": 1}).text
        assert "17.000,00" in html, "caixa gerado nas operações"
        assert "(12.000,00)" in html, "aquisição de imobilizado"
        assert "Diferença não conciliada" in html
        assert "Recebimentos de clientes" in html, "o painel abre no método direto"

        grafico = cliente.get("/api/graficos", params={"ecd_id": 1}).json()["dfc"]
        assert grafico["totais"]["variacao_caixa"] == 6_000
