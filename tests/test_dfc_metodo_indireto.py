"""DFC pelo método indireto, conferida contra a variação do caixa.

A DFC anterior estava errada em todas as linhas:

* usava o saldo **final** de cada conta, não a variação do período;
* aumento de ativo saía como entrada de caixa (sinal trocado);
* o lucro líquido nunca era mapeado — a linha era sempre zero;
* os subtotais acumulavam as seções anteriores (o de financiamento somava
  operações e investimento);
* nada conferia o total com a variação do caixa.

Método indireto (NBC TG 03): lucro líquido + itens sem efeito no caixa
(depreciação = variação da depreciação acumulada) ± variação do capital de
giro (aumento de ativo operacional = saída; de passivo = entrada) = FCO;
FCI = −variação do imobilizado bruto, intangível e investimentos; FCF =
variação de empréstimos + variação do capital − lucros distribuídos. O
total precisa ser a variação de caixa e equivalentes (SF do último mês −
SI do primeiro); quando não é, a diferença aparece.

Números da escrituração trimestral de `tests/fixtures/multiperiodo.py`,
calculados à mão:

    lucro 21.000; depreciação +2.000; clientes 30.000→35.000 (−5.000);
    estoques 20.000→20.000 (0); fornecedores 25.000→23.000 (−2.000);
    impostos a recolher 5.000→6.000 (+1.000)                → FCO 17.000
    imobilizado 100.000→112.000                             → FCI −12.000
    capital +10.000; empréstimos 40.000→35.000 (−5.000);
    lucros acumulados 30.000→47.000 com lucro de 21.000 (−4.000) → FCF 1.000
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
    "Lucro Líquido do Exercício": 21_000,
    "(+) Depreciação e Amortização": 2_000,
    "(+/-) Variação em Contas a Receber": -5_000,
    "(+/-) Variação em Estoques": 0,
    "(+/-) Variação em Fornecedores": -2_000,
    "(+/-) Variação em Obrigações Fiscais": 1_000,
    "(+/-) Outros Ajustes Operacionais": 0,
    "= Caixa Gerado nas Operações": 17_000,
    "(-) Aquisição de Imobilizado": -12_000,
    "(+) Venda de Imobilizado": 0,
    "(-) Aquisição de Intangível": 0,
    "(+/-) Outros Investimentos": 0,
    "= Caixa das Atividades de Investimento": -12_000,
    "(+) Aumento de Capital": 10_000,
    "(+/-) Empréstimos e Financiamentos": -5_000,
    "(-) Distribuição de Lucros": -4_000,
    "= Caixa das Atividades de Financiamento": 1_000,
    "= Variação Líquida de Caixa": 6_000,
    "Caixa e equivalentes no início do período": 60_000,
    "Caixa e equivalentes no fim do período": 66_000,
    "Variação de caixa nos saldos (fim − início)": 6_000,
    "Diferença não conciliada (fluxos − saldos)": 0,
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
    return {ln.descricao: getattr(ln, campo) for ln in linhas if ln.tipo != "section"}


class TestTrimestre:
    @pytest.fixture
    def dfc(self, sessao, tmp_path):
        ecd = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "t.txt"))
        return DFC(sessao, ecd).gerar()

    def test_cada_linha(self, dfc):
        _ctx, linhas, _totais = dfc
        valores = _valores(linhas)
        divergentes = {
            descricao: (valores.get(descricao), esperado)
            for descricao, esperado in ESPERADO_TRIMESTRE.items()
            if valores.get(descricao) != pytest.approx(esperado, abs=0.005)
        }
        assert not divergentes, f"linhas da DFC (obtido, esperado): {divergentes}"

    def test_lucro_liquido_vem_do_resultado(self, dfc):
        _ctx, linhas, totais = dfc
        assert (
            _valores(linhas)["Lucro Líquido do Exercício"] == 21_000
        ), "a linha do lucro líquido nunca era mapeada e saía zero"
        assert totais["lucro_liquido"] == 21_000

    def test_aumento_de_ativo_e_saida_de_caixa(self, dfc):
        _ctx, linhas, _totais = dfc
        valores = _valores(linhas)
        assert (
            valores["(+/-) Variação em Contas a Receber"] < 0
        ), "clientes subiu 5.000: dinheiro que não entrou; o sinal saía trocado"
        assert (
            valores["(-) Aquisição de Imobilizado"] == -12_000
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
        assert _valores(linhas, "valor_anterior") == _valores(
            linhas_do_anterior
        ), "a coluna do período anterior precisa ser a DFC do ano anterior"
        assert _valores(linhas, "valor_anterior")["= Variação Líquida de Caixa"] == 12_000
        assert totais["variacao_caixa_anterior"] == totais_do_anterior["variacao_caixa"]
        assert totais["diferenca_conciliacao_anterior"] == 0


class TestAmostra:
    """A amostra não fecha na abertura, e a DFC diz isso em vez de esconder."""

    def test_linhas_e_diferenca(self, sessao):
        _ctx, linhas, totais = DFC(sessao, _importar(sessao, AMOSTRA)).gerar()
        valores = _valores(linhas)
        assert valores["Lucro Líquido do Exercício"] == 180_000
        assert valores["(+) Depreciação e Amortização"] == 20_000
        assert valores["(+/-) Variação em Contas a Receber"] == -100_000
        assert valores["(+/-) Variação em Fornecedores"] == 50_000
        assert valores["(+/-) Variação em Obrigações Fiscais"] == 10_000
        assert totais["operacional"] == 160_000
        assert totais["investimento"] == -50_000
        assert totais["financiamento"] == -10_000
        assert totais["variacao_caixa"] == 100_000
        assert totais["variacao_caixa_saldos"] == 80_000, "caixa 50→80 mil e bancos 200→250 mil"
        assert totais["diferenca_conciliacao"] == 20_000, (
            "o saldo inicial da amostra não fecha (ativo 620.000 contra passivo + PL "
            "600.000): a DFC não pode conciliar, e a diferença precisa aparecer"
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

        grafico = cliente.get("/api/graficos", params={"ecd_id": 1}).json()["dfc"]
        assert grafico["totais"]["variacao_caixa"] == 6_000
