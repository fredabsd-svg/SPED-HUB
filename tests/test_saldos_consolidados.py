"""Saldos de ECD real: vários I150, I155 só em analítica, centro de custo.

O código assumia um único I150 e sintéticas com I155 próprio. Nenhuma das
duas coisas vale numa ECD real: o manual diz que o COD_CTA do I155 é o
"código da conta analítica", e a escrituração traz um I150 por mês. O
resultado, antes da correção:

* o balancete somava o saldo inicial e o final dos três meses (caixa com
  SI 30.000 em vez de 10.000);
* o balanço mostrava ATIVO e ATIVO CIRCULANTE zerados;
* os totais do balancete somavam só o menor nível presente (820.000 de
  débitos na amostra, contra 2.980.000);
* dois I155/I355 da mesma conta em centros de custo diferentes se
  sobrescreviam: ficava o último;
* +100 em janeiro e −100 em fevereiro se anulavam, e a validação dizia OK.

Os números esperados foram calculados à mão a partir da tabela de
lançamentos de `tests/fixtures/multiperiodo.py`.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from src.db.models import Mapeamento, criar_engine, get_session, init_db
from src.ecd_importer import ECDImportService
from src.filters.engine import FilterCriteria
from src.reports.balancete import Balancete
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.validators.integridade import ValidadorIntegridade
from tests.fixtures.multiperiodo import gerar_ecd_multiperiodo

AMOSTRA = Path(__file__).parent / "fixtures" / "ecd_sample.txt"


@pytest.fixture
def sessao():
    engine = criar_engine(":memory:")
    init_db(engine)
    s = get_session(engine)
    yield s
    s.close()
    engine.dispose()


def _importar(sessao, arquivo: Path) -> int:
    # Banco em memória: o webhook procuraria assinantes numa conexão nova,
    # sem as tabelas, e só poluiria o log.
    from unittest import mock

    import src.ecd_importer as importador

    with mock.patch.object(importador, "emitir", lambda *a, **k: None):
        return ECDImportService(sessao).importar(arquivo).ecd_id


def _ecd_texto(tmp_path: Path, nome: str, linhas: list[str]) -> Path:
    arquivo = tmp_path / nome
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return arquivo


@pytest.fixture
def trimestral(sessao, tmp_path) -> int:
    return _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "trimestral.txt"))


@pytest.fixture
def com_anterior(sessao, tmp_path) -> tuple[int, int]:
    """A mesma empresa em 2023 (valores em dobro) e em 2024."""
    anterior = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "ant.txt", ano=2023, fator=2))
    atual = _importar(sessao, gerar_ecd_multiperiodo(tmp_path / "atual.txt"))
    return anterior, atual


def _por_conta(linhas):
    return {ln.cod_cta: ln for ln in linhas}


# ── Balancete ──────────────────────────────────────────────────────────────


class TestBalanceteMultiPeriodo:
    def test_saldo_inicial_do_primeiro_mes_e_final_do_ultimo(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar()
        caixa = _por_conta(linhas)["1.1.01"]
        assert (caixa.saldo_inicial, caixa.debitos, caixa.creditos, caixa.saldo_final) == (
            10_000,
            8_000,
            0,
            18_000,
        ), (
            f"caixa com SI {caixa.saldo_inicial:,.2f} e SF {caixa.saldo_final:,.2f}: somar os "
            "saldos dos três I150 multiplica o saldo pelo número de meses"
        )
        bancos = _por_conta(linhas)["1.1.02"]
        assert (bancos.saldo_inicial, bancos.debitos, bancos.creditos, bancos.saldo_final) == (
            50_000,
            45_000,
            47_000,
            48_000,
        )

    def test_conta_que_so_aparece_no_ultimo_mes(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar()
        depreciacao = _por_conta(linhas)["4.2.03"]
        assert (depreciacao.saldo_inicial, depreciacao.debitos, depreciacao.creditos) == (
            0,
            2_000,
            2_000,
        )
        assert depreciacao.saldo_final == 0

    def test_centros_de_custo_da_mesma_conta_se_somam(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar()
        administrativas = _por_conta(linhas)["4.2.02"]
        assert (administrativas.debitos, administrativas.creditos) == (
            7_000,
            7_000,
        ), "CC1 (5.000) e CC2 (2.000) são partes do mesmo saldo, não versões dele"

    def test_sinteticas_agregam_as_analiticas(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar()
        conta = _por_conta(linhas)
        circulante = conta["1.1"]
        assert (
            circulante.saldo_inicial,
            circulante.debitos,
            circulante.creditos,
            circulante.saldo_final,
        ) == (110_000, 111_000, 100_000, 121_000), "ATIVO CIRCULANTE sem o saldo das analíticas"
        assert conta["1"].saldo_final == 221_000
        assert conta["2"].saldo_final == -64_000
        assert conta["3"].saldo_final == -157_000
        assert conta["4"].debitos == conta["4"].creditos == 75_000

    def test_ordem_do_plano(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar()
        codigos = [ln.cod_cta for ln in linhas]
        assert codigos[:3] == ["1", "1.1", "1.1.01"]
        assert codigos.index("1.2") > codigos.index("1.1.04")

    def test_totais_somam_cada_valor_uma_vez(self, sessao, trimestral):
        balancete = Balancete(sessao, trimestral)
        _, linhas = balancete.gerar()
        assert balancete.totais(linhas) == {
            "saldo_inicial": 0.0,
            "debitos": 227_000.0,
            "creditos": 227_000.0,
            "saldo_final": 0.0,
        }, "a soma dos débitos das 15 partidas do trimestre é 227.000"

    @pytest.mark.parametrize(
        "criterios",
        [
            pytest.param(FilterCriteria(ind_cta=["A"]), id="so-analiticas"),
            pytest.param(FilterCriteria(nivel_ate=1), id="so-topo"),
            pytest.param(FilterCriteria(nivel_ate=2), id="ate-nivel-2"),
        ],
    )
    def test_totais_nao_dependem_do_recorte(self, sessao, trimestral, criterios):
        """Analíticas em níveis 2 e 3 misturadas: o menor nível não cobre o resto."""
        balancete = Balancete(sessao, trimestral)
        _, linhas = balancete.gerar(criterios)
        totais = balancete.totais(linhas)
        assert totais["debitos"] == 227_000.0, (
            f"total de débitos {totais['debitos']:,.2f} com {criterios.to_dict()}: somar só o "
            "menor nível presente deixa de fora as analíticas mais fundas"
        )

    def test_sintetica_aparece_com_filtro_de_nivel(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar(nivel_max=1)
        assert {ln.cod_cta: ln.saldo_final for ln in linhas} == {
            "1": 221_000,
            "2": -64_000,
            "3": -157_000,
            "4": 0,
        }

    def test_conferencia_fecha(self, sessao, trimestral):
        balancete = Balancete(sessao, trimestral)
        _, linhas = balancete.gerar()
        assert balancete.conferir(linhas)["status"] == "OK"

    def test_periodo_escolhido_usa_os_meses_de_dentro(self, sessao, trimestral):
        _, linhas = Balancete(sessao, trimestral).gerar(
            FilterCriteria(dt_ini=datetime.date(2024, 2, 1), dt_fin=datetime.date(2024, 3, 31))
        )
        bancos = _por_conta(linhas)["1.1.02"]
        assert (bancos.saldo_inicial, bancos.saldo_final) == (79_000, 48_000)


class TestBalanceteDaAmostra:
    def test_total_de_debitos_inclui_as_analiticas_de_nivel_3(self, sessao):
        balancete = Balancete(sessao, _importar(sessao, AMOSTRA))
        _, linhas = balancete.gerar()
        assert balancete.totais(linhas)["debitos"] == 2_980_000.0, (
            "os débitos das 15 analíticas somam 2.980.000; somar só o nível 2 (o menor "
            "presente quando as sintéticas não tinham saldo) dava 820.000"
        )


# ── Balanço ────────────────────────────────────────────────────────────────


class TestBalanco:
    def test_sinteticas_com_saldo(self, sessao, trimestral):
        _, grupos, totais = BalancoPatrimonial(sessao, trimestral).gerar()
        ativo = {ln.cod_cta: ln.saldo_atual for ln in grupos["ativo"]}
        assert ativo["1"] == 221_000, "ATIVO saía 0,00: a sintética não tem I155 próprio"
        assert ativo["1.1"] == 121_000
        assert ativo["1.2"] == 100_000
        assert (totais["ativo"], totais["passivo"], totais["pl"]) == (221_000, 64_000, 157_000)
        assert totais["diferenca"] == 0

    def test_amostra_mantem_os_totais(self, sessao):
        _, grupos, totais = BalancoPatrimonial(sessao, _importar(sessao, AMOSTRA)).gerar()
        assert (totais["ativo"], totais["passivo"], totais["pl"]) == (830_000, 260_000, 570_000)
        ativo = {ln.cod_cta: ln.saldo_atual for ln in grupos["ativo"]}
        assert (ativo["1"], ativo["1.1"], ativo["1.2"]) == (830_000, 580_000, 250_000)

    def test_filtro_de_nivel_nao_muda_o_total(self, sessao, trimestral):
        _, grupos, totais = BalancoPatrimonial(sessao, trimestral).gerar(
            FilterCriteria(nivel_ate=2)
        )
        assert (
            totais["ativo"] == 221_000
        ), "com nível até 2 só sobravam as analíticas de nível 2 (capital e lucros)"
        assert max(ln.nivel for ln in grupos["ativo"]) == 2

    def test_conta_inexistente_nao_mostra_o_balanco_inteiro(self, sessao):
        _, grupos, totais = BalancoPatrimonial(sessao, _importar(sessao, AMOSTRA)).gerar(
            FilterCriteria(cod_cta_exato=["X"])
        )
        assert totais["ativo"] == 0 and not grupos["ativo"]

    def test_comparativo_usa_o_saldo_final_do_ano_anterior(self, sessao, com_anterior):
        _anterior, atual = com_anterior
        _, grupos, totais = BalancoPatrimonial(sessao, atual).gerar()
        ativo = {ln.cod_cta: ln for ln in grupos["ativo"]}
        assert ativo["1.1.01"].saldo_anterior == 36_000
        assert ativo["1"].saldo_anterior == 442_000
        assert totais["ativo_anterior"] == 442_000
        assert totais["tem_anterior"] is True

    def test_centro_de_custo_no_ativo(self, sessao, tmp_path):
        arquivo = _ecd_texto(
            tmp_path,
            "ccus.txt",
            [
                "|0000|LECD|01012024|31122024|EMPRESA CC LTDA|00123456000199|SP||1234567"
                "||0|0|1|0|0|E||1|0||",
                "|I001|0|",
                "|I010|G|009|",
                "|I050|01012024|01|S|1|1||ATIVO|",
                "|I050|01012024|01|A|2|1.1|1|CAIXA|",
                "|I050|01012024|03|S|1|3||PL|",
                "|I050|01012024|03|A|2|3.1|3|CAPITAL SOCIAL|",
                "|I100|01012024|CC1|LOJA 1|",
                "|I100|01012024|CC2|LOJA 2|",
                "|I150|01012024|31122024|",
                "|I155|1.1|CC1|600,00|D|0,00|0,00|600,00|D|",
                "|I155|1.1|CC2|400,00|D|0,00|0,00|400,00|D|",
                "|I155|3.1||1000,00|C|0,00|0,00|1000,00|C|",
                "|I990|0|",
            ],
        )
        ecd = _importar(sessao, arquivo)
        _, _grupos, totais = BalancoPatrimonial(sessao, ecd).gerar()
        assert (
            totais["ativo"] == 1_000
        ), f"ativo {totais['ativo']:,.2f}: o caixa da loja 2 sobrescrevia o da loja 1"
        erros = [
            i
            for i in ValidadorIntegridade(sessao, ecd)._validar_ativo_passivo_pl()
            if i.tipo == "balanco_nao_fecha"
        ]
        assert not erros, f"a validação acusou um balanço que fecha: {erros}"


class TestPublicacao:
    def test_aglutinacao_em_sintetica_e_analitica_nao_dobra(self, sessao, tmp_path):
        """O I052 costuma vir na sintética e nas filhas com o mesmo código."""
        texto = gerar_ecd_multiperiodo(tmp_path / "base.txt").read_text(encoding="utf-8")
        linhas = []
        for linha in texto.splitlines():
            linhas.append(linha)
            partes = linha.split("|")
            if partes[1] == "I050" and partes[6] in {"1.1", "1.1.01", "1.1.02", "1.1.03"}:
                linhas.append("|I052||J100|")
            if partes[1] == "I050" and partes[6] == "1.1.04":
                linhas.append("|I052||J100|")
        ecd = _importar(sessao, _ecd_texto(tmp_path, "agl.txt", linhas))
        _, grupos, totais = BalancoPatrimonial(sessao, ecd).gerar_publicacao()
        circulante = [ln for ln in grupos["ativo"] if ln.cod_cta == "J100"]
        assert (
            circulante and circulante[0].saldo_atual == 121_000
        ), "o J100 está na sintética 1.1 e nas quatro analíticas: somar todas dobra o valor"
        assert totais["ativo"] == 121_000


# ── DRE ────────────────────────────────────────────────────────────────────


class TestDRE:
    def test_resultado_soma_os_centros_de_custo(self, sessao, trimestral):
        _, linhas, totais = DRE(sessao, trimestral).gerar()
        assert (
            totais["resultado_liquido"] == 21_000
        ), f"resultado {totais['resultado_liquido']:,.2f}: o I355 de CC2 sobrescrevia o de CC1"
        despesas = next(ln for ln in linhas if "Despesas Operacionais" in ln.descricao)
        assert despesas.valor_atual == -9_000

    def test_receita_bruta_no_total_tem_o_sinal_da_dre(self, sessao, trimestral):
        _, _linhas, totais = DRE(sessao, trimestral).gerar()
        assert totais["receita_bruta"] == 48_000, (
            "receita bruta saía -48.000 (sinal interno do crédito): o painel nunca mostrava "
            "a margem líquida, que só aparece com receita positiva"
        )

    def test_periodo_do_filtro_vale_para_o_i355(self, sessao, trimestral):
        _, _linhas, totais = DRE(sessao, trimestral).gerar(
            FilterCriteria(dt_ini=datetime.date(2024, 1, 1), dt_fin=datetime.date(2024, 2, 29))
        )
        assert (
            totais["resultado_liquido"] == 0
        ), "o cabeçalho dizia janeiro a fevereiro e a DRE mostrava o I355 de 31/03"

    def test_mapeamento_de_sintetica_leva_as_filhas(self, sessao, trimestral):
        from src.db.models import ECD

        empresa_id = sessao.get(ECD, trimestral).empresa_id
        sessao.add_all(
            [
                Mapeamento(
                    empresa_id=empresa_id,
                    tipo="dre",
                    cod_cta="4.1",
                    categoria="receita_bruta",
                    ordem=1,
                ),
                Mapeamento(
                    empresa_id=empresa_id, tipo="dre", cod_cta="4.2", categoria="custos", ordem=2
                ),
            ]
        )
        sessao.commit()
        _, linhas, totais = DRE(sessao, trimestral).gerar()
        valores = {ln.descricao: ln.valor_atual for ln in linhas}
        assert valores["Receita Operacional Bruta"] == 48_000
        assert valores["(-) Custos"] == -27_000, "a sintética 4.2 mapeada ficava zerada"
        assert totais["resultado_liquido"] == 21_000

    def test_comparativo(self, sessao, com_anterior):
        _anterior, atual = com_anterior
        _, linhas, _totais = DRE(sessao, atual).gerar()
        assert linhas[-1].valor_atual == 21_000
        assert linhas[-1].valor_anterior == 42_000


# ── Validações ─────────────────────────────────────────────────────────────


class TestValidacaoPorPeriodo:
    def test_erros_que_se_anulam_entre_meses_sao_acusados(self, sessao, tmp_path):
        arquivo = _ecd_texto(
            tmp_path,
            "anula.txt",
            [
                "|0000|LECD|01012024|29022024|EMPRESA MENSAL LTDA|00123456000199|SP||1234567"
                "||0|0|1|0|0|E||1|0||",
                "|I001|0|",
                "|I010|G|009|",
                "|I050|01012024|01|S|1|1||ATIVO|",
                "|I050|01012024|01|A|2|1.1|1|CAIXA|",
                "|I150|01012024|31012024|",
                "|I155|1.1||1000,00|D|0,00|0,00|1100,00|D|",
                "|I150|01022024|29022024|",
                "|I155|1.1||1100,00|D|0,00|0,00|1000,00|D|",
                "|I990|0|",
            ],
        )
        ecd = _importar(sessao, arquivo)
        inconsistencias = ValidadorIntegridade(sessao, ecd)._validar_saldo_si_d_c_sf()
        periodos = sorted(i.detalhes["dt_ini"] for i in inconsistencias)
        assert periodos == ["2024-01-01", "2024-02-01"], (
            f"inconsistências por período: {periodos}. +100 em janeiro e −100 em fevereiro "
            "se anulavam na soma dos meses e a validação dizia OK"
        )

    def test_escrituracao_trimestral_consistente_nao_tem_erro(self, sessao, trimestral):
        from unittest import mock

        with mock.patch("src.webhooks.emitir"):
            inconsistencias = ValidadorIntegridade(sessao, trimestral).validar_todas()
        assert [i for i in inconsistencias if i.severidade == "erro"] == []
        assert inconsistencias == [], [i.descricao for i in inconsistencias]


# ── Porta de entrada ───────────────────────────────────────────────────────


class TestPelaLinhaDeComando:
    @pytest.fixture
    def banco(self, tmp_path) -> str:
        from src.cli import main

        caminho = str(tmp_path / "trimestral.db")
        main(["importar-ecd", str(gerar_ecd_multiperiodo(tmp_path / "t.txt")), "--db", caminho])
        return caminho

    def test_balancete_trimestral(self, banco, capsys):
        from src.cli import main

        capsys.readouterr()
        main(["relatorio", "balancete", "--db", banco])
        saida = capsys.readouterr().out
        caixa = next(linha for linha in saida.splitlines() if "CAIXA" in linha)
        assert "10,000.00" in caixa and "18,000.00" in caixa, caixa
        assert "30,000.00" not in caixa, "saldo inicial somado nos três meses"
        assert "Conferência: OK" in saida

    def test_balanco_mostra_a_sintetica(self, banco, capsys):
        from src.cli import main

        capsys.readouterr()
        main(["relatorio", "balanco", "--db", banco])
        saida = capsys.readouterr().out
        ativo = next(linha for linha in saida.splitlines() if linha.strip().startswith("1 "))
        assert "221,000.00" in ativo, f"linha do ATIVO: {ativo!r}"
        assert "Ativo = 221,000.00  |  Passivo + PL = 221,000.00" in saida


class TestPainel:
    """O painel da amostra continua com ativo 830.000, PL 570.000, resultado 180.000."""

    @pytest.fixture
    def cliente(self, tmp_path):
        import os

        from fastapi.testclient import TestClient

        from src.auth import init_auth
        from src.cli import main

        caminho = str(tmp_path / "painel.db")
        main(["importar-ecd", str(AMOSTRA), "--db", caminho])
        os.environ["SPED_HUB_DB"] = caminho
        from src.dashboard.app import app

        init_auth(caminho)
        cliente = TestClient(app)
        assert (
            cliente.post(
                "/api/register",
                data={"email": "saldos@test.local", "nome": "Saldos", "senha": "senha123"},
            ).status_code
            == 200
        )
        assert (
            cliente.post(
                "/api/login", data={"email": "saldos@test.local", "senha": "senha123"}
            ).status_code
            == 200
        )
        return cliente

    def test_kpis(self, cliente):
        html = cliente.get("/api/kpis", params={"ecd_id": 1}).text
        for valor in ("R$ 830000.00", "R$ 570000.00", "R$ 180000.00"):
            assert valor in html, f"o KPI {valor} sumiu do painel"
        assert "Margem Líquida" in html and "18.0%" in html, (
            "a margem líquida (180.000 / 1.000.000) não aparecia: a receita bruta chegava "
            "negativa ao painel"
        )

    def test_composicao_do_ativo_nao_dobra(self, cliente):
        composicao = cliente.get("/api/graficos", params={"ecd_id": 1}).json()["composicao"]
        assert sum(composicao["valores"]) == pytest.approx(830_000), composicao
        assert dict(zip(composicao["labels"], composicao["valores"], strict=True)) == {
            "ATIVO CIRCULANTE": 580_000,
            "ATIVO NAO CIRCULANTE": 250_000,
        }
