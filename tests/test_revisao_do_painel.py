"""Defeitos do painel contábil achados na revisão de setembro de 2026.

Todos pela porta de entrada — `TestClient` sobre a aplicação montada, com
sessão, middleware e escopo de escritório no caminho (§7.1).  O que se
protege aqui:

1. **Nenhum escritório alcança a ECD do outro.**  O middleware só conferia
   o dono de `ecd_id` que passasse em `str.isdigit()`.  `+2`, `2.0` e `2_0`
   não passam — e por isso escapavam da checagem —, mas o FastAPI os
   converte para `2`, e a rota servia a escrituração do vizinho.
2. **Usuário desativado perde a sessão aberta**, não só o próximo login.
3. **O seletor de ECD troca o painel**, **o XLSX é baixado** e **filtro
   inválido é recusado com 400** — os três respondiam 422/500.
4. **Valores no formato brasileiro** nos cartões e no cabeçalho.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import Escritorio, Usuario, criar_engine, get_session, init_db
from src.ecd_importer import ECDImportService
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache

AMOSTRA = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
SENHA = "senha-de-teste"


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios, uma ECD em cada; o usuário comum é do escritório A."""
    referencia = f"sqlite:///{tmp_path / 'painel.db'}"
    monkeypatch.setenv("DATABASE_URL", referencia)
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    reset_settings_cache()

    engine = criar_engine(url=referencia)
    init_db(engine)
    ids: dict = {"referencia": referencia}
    with get_session(engine) as sessao:
        a = Escritorio(nome="Escritório A", slug="a")
        b = Escritorio(nome="Escritório B", slug="b")
        sessao.add_all([a, b])
        sessao.flush()
        ids["escritorio_a"], ids["escritorio_b"] = a.id, b.id
        # O admin primeiro: sem nenhum, o usuário #1 seria promovido — e admin
        # vê todos os escritórios, o que faria o isolamento passar por acaso.
        for email, admin, escritorio_id in (
            ("admin@teste.local", True, None),
            ("usuario@a.local", False, a.id),
        ):
            senha_hash, salt = Usuario.hash_senha(SENHA)
            sessao.add(
                Usuario(
                    email=email,
                    nome=email,
                    senha_hash=senha_hash,
                    salt=salt,
                    admin=admin,
                    escritorio_id=escritorio_id,
                )
            )
        sessao.commit()

    amostra = AMOSTRA.read_text(encoding="utf-8")
    for rotulo, escritorio_id, cnpj in (
        ("a", ids["escritorio_a"], "00123456000199"),
        ("b", ids["escritorio_b"], "11222333000181"),
    ):
        arquivo = tmp_path / f"ecd_{rotulo}.txt"
        arquivo.write_text(
            amostra.replace("00123456000199", cnpj).replace(
                "EMPRESA EXEMPLO LTDA", f"EMPRESA {rotulo.upper()} LTDA"
            ),
            encoding="utf-8",
        )
        with get_session(engine) as sessao:
            resultado = ECDImportService(sessao).importar(arquivo, escritorio_id=escritorio_id)
            ids[f"ecd_{rotulo}"] = resultado.ecd_id

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    cliente = TestClient(app)
    resposta = cliente.post("/api/login", data={"email": "usuario@a.local", "senha": SENHA})
    assert resposta.status_code == 200, resposta.text
    ids["cliente"] = cliente
    return ids


class TestIdDeEcdSoComDigitos:
    @pytest.mark.parametrize("forma", ["+{id}", "{id}.0", "{id}_0", "0{id}.00"])
    @pytest.mark.parametrize("rota", ["/api/notas", "/api/kpis", "/api/balanco", "/api/dre"])
    def test_forma_alternativa_do_id_nao_alcanca_outro_escritorio(self, cenario, rota, forma):
        valor = forma.format(id=cenario["ecd_b"])
        resposta = cenario["cliente"].get(rota, params={"ecd_id": valor})

        assert resposta.status_code in (400, 404), (
            f"{rota}?ecd_id={valor} respondeu {resposta.status_code}: a checagem de dono "
            "deixou passar a ECD de outro escritório"
        )
        assert "EMPRESA B" not in resposta.text

    def test_lista_de_ids_tambem_e_conferida(self, cenario):
        ids = f"{cenario['ecd_a']},+{cenario['ecd_b']}"
        resposta = cenario["cliente"].get("/api/comparar", params={"ecd_ids": ids})

        assert resposta.status_code in (400, 404)
        assert "EMPRESA B" not in resposta.text

    def test_digito_que_nao_e_ascii_nao_derruba_o_servidor(self, cenario):
        """`"²".isdigit()` é verdadeiro e `int("²")` quebra: o middleware dava 500."""
        resposta = cenario["cliente"].get("/api/notas", params={"ecd_id": "²"})

        assert resposta.status_code == 400

    def test_controles_id_do_proprio_escritorio_passa_e_o_do_outro_nao(self, cenario):
        cliente = cenario["cliente"]

        assert cliente.get("/api/kpis", params={"ecd_id": cenario["ecd_a"]}).status_code == 200
        assert cliente.get("/api/kpis", params={"ecd_id": cenario["ecd_b"]}).status_code == 404


class TestUsuarioDesativado:
    def test_perde_a_sessao_que_ja_estava_aberta(self, cenario):
        cliente = cenario["cliente"]
        assert cliente.get("/api/kpis", params={"ecd_id": cenario["ecd_a"]}).status_code == 200

        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            usuario = sessao.query(Usuario).filter_by(email="usuario@a.local").one()
            usuario.ativo = False
            sessao.commit()

        resposta = cliente.get("/api/kpis", params={"ecd_id": cenario["ecd_a"]})
        assert resposta.status_code == 401, (
            "usuário desativado seguiu lendo com a sessão aberta — desligar alguém do "
            "escritório não tirava o acesso dele"
        )
        tela = cliente.get("/", follow_redirects=False)
        assert tela.status_code == 302 and tela.headers["location"] == "/login"


class TestPainel:
    def test_seletor_escolhe_a_ecd_exibida(self, cenario):
        html = cenario["cliente"].get("/", params={"ecd_id": cenario["ecd_a"]}).text

        assert "EMPRESA A LTDA" in html
        assert 'name="ecd_id"' in html, "o seletor não envia a ECD escolhida"

    def test_seletor_nao_abre_ecd_de_outro_escritorio(self, cenario):
        resposta = cenario["cliente"].get("/", params={"ecd_id": cenario["ecd_b"]})

        assert resposta.status_code == 404
        assert "EMPRESA B" not in resposta.text

    def test_valores_no_formato_brasileiro(self, cenario):
        html = cenario["cliente"].get("/", params={"ecd_id": cenario["ecd_a"]}).text

        assert "830.000,00" in html, "o ativo total não saiu no formato brasileiro"
        assert "830000.00" not in html, "sobrou valor com ponto decimal e sem milhar"
        assert "00.123.456/0001-99" in html, "o CNPJ saiu sem máscara"
        assert "01/01/2024 a 31/12/2024" in html, "o período saiu em ISO"
        assert "31,3%" in html, "o percentual saiu com ponto decimal"

    def test_exportacao_nao_depende_de_seletor_invalido(self, cenario):
        """`hx-target="_blank"` não é seletor: o htmx acusava erro e não pedia nada."""
        html = cenario["cliente"].get("/", params={"ecd_id": cenario["ecd_a"]}).text

        assert 'hx-target="_blank"' not in html
        assert f'href="/api/export/pdf?ecd_id={cenario["ecd_a"]}&tipo=balanco"' in html


class TestExportacaoXlsx:
    @pytest.mark.parametrize("tipo,esperado", [("balanco", "CAIXA"), ("dre", "Receita")])
    def test_baixa_uma_planilha_de_verdade(self, cenario, tipo, esperado):
        from openpyxl import load_workbook

        resposta = cenario["cliente"].get(
            "/api/export/xlsx", params={"ecd_id": cenario["ecd_a"], "tipo": tipo}
        )

        assert (
            resposta.status_code == 200
        ), f"exportar {tipo} em XLSX respondeu {resposta.status_code}: {resposta.text[:200]}"
        assert "spreadsheetml" in resposta.headers["content-type"]
        assert "attachment" in resposta.headers["content-disposition"]
        planilha = load_workbook(io.BytesIO(resposta.content))
        textos = " ".join(
            str(celula.value)
            for aba in planilha.worksheets
            for linha in aba.iter_rows()
            for celula in linha
            if celula.value is not None
        )
        assert esperado in textos, f"a planilha de {tipo} veio sem o conteúdo do relatório"

    def test_tipo_desconhecido_e_recusado(self, cenario):
        resposta = cenario["cliente"].get(
            "/api/export/xlsx", params={"ecd_id": cenario["ecd_a"], "tipo": "xyz"}
        )
        assert resposta.status_code == 400


class TestFiltroInvalido:
    @pytest.mark.parametrize(
        "parametro", [{"nivel_ate": "x"}, {"dt_ini": "2024-02-31"}, {"dt_fin": "ontem"}]
    )
    def test_responde_400_com_a_razao(self, cenario, parametro):
        resposta = cenario["cliente"].get(
            "/api/filtros/aplicar", params={"ecd_id": cenario["ecd_a"], **parametro}
        )

        assert resposta.status_code == 400, f"filtro {parametro} derrubou o servidor"
        assert "Filtro inválido" in resposta.text
