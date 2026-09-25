"""Destaques do painel e a cor dos indicadores.

Os destaques são frases sobre os mesmos totais que os cartões mostram — o
balanço fecha? houve lucro, com que margem? —, e por isso cada teste aqui
confere a *conta*, não a presença do texto.  Um destaque que dissesse
"o balanço fecha" para um balanço que não fecha seria pior que nenhum.

Dois defeitos antigos do painel moram aqui porque apareceram ao montar os
destaques:

- **A margem líquida nunca aparecia.** A DRE devolve a receita com o sinal
  contábil (crédito, negativo), e o cartão exigia `receita_bruta > 0`.
- **Endividamento saudável saía em vermelho.** A cor vinha de `tendencia`,
  que no endividamento marca "down" justamente quando ele é baixo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.dashboard.destaques import ALERTA, INFO, OK, gerar_destaques

AMOSTRA = Path(__file__).parent / "fixtures" / "ecd_sample.txt"


@dataclass
class Dados:
    ativo_total: float = 1000.0
    passivo_total: float = 400.0
    pl_total: float = 600.0
    resultado_liquido: float = 100.0
    receita_liquida: float = -1000.0  # como a DRE entrega: crédito negativo
    num_lancamentos: int = 12
    num_contas: int = 30
    ativo_anterior: float | None = None


def _por_titulo(destaques, trecho):
    achados = [d for d in destaques if trecho in d.titulo]
    assert achados, f"nenhum destaque com '{trecho}' em {[d.titulo for d in destaques]}"
    return achados[0]


class TestFechamento:
    def test_balanco_que_fecha(self):
        destaque = gerar_destaques(Dados())[0]

        assert destaque.tipo == OK
        assert destaque.titulo == "O balanço fecha"
        assert "R$ 1.000,00" in destaque.texto

    def test_diferenca_igual_ao_resultado_e_explicada_e_nao_acusada(self):
        """Saldos antes do encerramento: ativo = passivo + PL + resultado."""
        destaque = gerar_destaques(Dados(pl_total=500.0))[0]

        assert destaque.tipo == INFO, "resultado ainda não transferido foi tratado como erro"
        assert "encerramento" in destaque.texto

    def test_balanco_que_nao_fecha_e_alerta_com_a_diferenca(self):
        destaque = gerar_destaques(Dados(pl_total=550.0, resultado_liquido=10.0))[0]

        assert destaque.tipo == ALERTA
        assert destaque.titulo == "O balanço não fecha"
        assert "R$ 50,00" in destaque.texto


class TestResultado:
    def test_lucro_com_margem_positiva_sobre_receita_com_sinal_de_credito(self):
        destaque = _por_titulo(gerar_destaques(Dados()), "Lucro")

        assert destaque.tipo == OK
        assert (
            "margem de 10,0%" in destaque.texto
        ), "a margem saiu com o sinal da receita contábil — lucro com margem negativa"

    def test_prejuizo_e_alerta(self):
        destaque = _por_titulo(
            gerar_destaques(Dados(resultado_liquido=-50.0, pl_total=600.0)), "Prejuízo"
        )

        assert destaque.tipo == ALERTA
        assert "R$ 50,00" in destaque.titulo

    def test_sem_receita_nao_divide_por_zero(self):
        destaque = _por_titulo(gerar_destaques(Dados(receita_liquida=0.0)), "Lucro")

        assert "margem" not in destaque.texto


class TestEstruturaEVariacao:
    def test_pl_negativo_vira_alerta(self):
        destaques = gerar_destaques(Dados(passivo_total=1200.0, pl_total=-200.0))

        assert _por_titulo(destaques, "Patrimônio líquido negativo").tipo == ALERTA

    def test_endividamento_e_a_razao_passivo_sobre_ativo(self):
        assert _por_titulo(gerar_destaques(Dados()), "Endividamento de 40,0%")

    def test_variacao_contra_o_exercicio_anterior(self):
        destaque = _por_titulo(gerar_destaques(Dados(ativo_anterior=800.0)), "Ativo cresceu")

        assert "25,0%" in destaque.titulo
        assert "R$ 800,00" in destaque.texto and "R$ 1.000,00" in destaque.texto

    def test_sem_exercicio_anterior_nao_inventa_variacao(self):
        titulos = [d.titulo for d in gerar_destaques(Dados())]

        assert not any("Ativo cresceu" in t or "Ativo recuou" in t for t in titulos)

    def test_escrituracao_sem_lancamento_e_alerta(self):
        assert _por_titulo(gerar_destaques(Dados(num_lancamentos=0)), "Nenhum").tipo == ALERTA

    def test_milhar_no_formato_brasileiro(self):
        assert _por_titulo(gerar_destaques(Dados(num_lancamentos=12345)), "12.345 lançamentos")


class TestNaTela:
    @pytest.fixture
    def html(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from src.audit import init_audit_service
        from src.auth import init_auth
        from src.db.models import criar_engine, init_db
        from src.ratelimit import init_limiter
        from src.settings import reset_settings_cache

        referencia = f"sqlite:///{tmp_path / 'destaques.db'}"
        monkeypatch.setenv("DATABASE_URL", referencia)
        monkeypatch.delenv("SPED_HUB_DB", raising=False)
        reset_settings_cache()
        init_db(criar_engine(url=referencia))
        init_auth(referencia)
        init_audit_service(referencia)
        init_limiter(referencia)
        from src.dashboard.app import app

        cliente = TestClient(app)
        dados = {"email": "ana@teste.local", "nome": "Ana", "senha": "senha-de-teste"}
        cliente.post("/api/register", data=dados)
        cliente.post("/api/login", data={"email": dados["email"], "senha": dados["senha"]})
        with AMOSTRA.open("rb") as arquivo:
            resposta = cliente.post("/api/upload", files={"file": ("ecd.txt", arquivo)})
        assert resposta.status_code == 200, resposta.text
        return " ".join(cliente.get("/").text.split())

    def test_painel_mostra_os_destaques_da_ecd(self, html):
        assert "Destaques" in html
        assert "O balanço fecha" in html
        assert "Lucro de R$ 180.000,00" in html

    def test_margem_liquida_aparece(self, html):
        assert "Margem Líquida" in html, "o cartão da margem sumiu com receita de sinal credor"
        assert "18,0%" in html

    def test_endividamento_saudavel_nao_sai_em_vermelho(self, html):
        inicio = html.index("Endividamento</h3>")
        cartao = html[inicio : html.index("</article>", inicio)]

        assert (
            "kpi-negativo" not in cartao and "kpi-tendencia-down" not in cartao
        ), "endividamento de 31,3% pintado de vermelho, como se fosse ruim"
