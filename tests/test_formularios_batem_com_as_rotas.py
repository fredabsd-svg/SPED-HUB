"""Os formulários das telas fiscais enviam o que as rotas leem.

Há uma lacuna que nem os testes de rota nem os de página fecham, e ela é
silenciosa: os testes de rota fazem `POST` com dados montados à mão, e os de
página conferem o HTML renderizado. Se o `name=` de um campo no template
divergir do que a rota lê — `empresa_id` no formulário, `empresa` na rota —,
**os dois passam** e a página real não funciona. Ninguém descobre até alguém
clicar.

O que se faz aqui é o que o navegador faria: ler o formulário **da página**,
descobrir para onde ele aponta e que campos declara, e enviar exatamente
aqueles. Um nome que só existe no template não alcança a rota; um que só
existe na rota não é preenchido. Nos dois casos, o teste cai.

Não substitui teste de navegador — não há JavaScript envolvido aqui, e o que
falta para chegar lá é clique e renderização. Fecha a costura entre as duas
metades que já são testadas em separado.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import (
    Empresa,
    Escritorio,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ImportadorDeDocumentos
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"


class _Formularios(HTMLParser):
    """Os formulários da página: para onde apontam e o que declaram.

    Guarda também o valor de cada `option`, porque um `select` que a rota
    valida contra tabela oficial só é exercitado com um valor que ele mesmo
    oferece — inventar um daria recusa, e a recusa não distingue "campo com
    nome errado" de "valor inválido".
    """

    def __init__(self):
        super().__init__()
        self.formularios: list[dict] = []
        self._atual: dict | None = None
        self._select: str | None = None

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "form":
            self._atual = {
                "action": atributos.get("action", ""),
                "method": (atributos.get("method") or "get").lower(),
                "campos": {},
                "opcoes": {},
            }
        elif self._atual is None:
            return
        elif tag == "input" and atributos.get("name"):
            self._atual["campos"][atributos["name"]] = atributos.get("value", "")
        elif tag == "select" and atributos.get("name"):
            self._select = atributos["name"]
            self._atual["campos"][self._select] = ""
            self._atual["opcoes"][self._select] = []
        elif tag == "option" and self._select:
            valor = atributos.get("value", "")
            if valor:
                self._atual["opcoes"][self._select].append(valor)

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "form" and self._atual is not None:
            self.formularios.append(self._atual)
            self._atual = None


def formularios(html: str) -> list[dict]:
    leitor = _Formularios()
    leitor.feed(html)
    return leitor.formularios


def formulario_que_posta(html: str, action: str) -> dict:
    """O formulário de `POST` que aponta para `action`."""
    achados = [f for f in formularios(html) if f["method"] == "post" and f["action"] == action]
    assert achados, f"a página não tem formulário POST para {action!r}"
    return achados[0]


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    referencia = f"sqlite:///{tmp_path / 'formularios.db'}"
    monkeypatch.setenv("DATABASE_URL", referencia)
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    reset_settings_cache()

    engine = criar_engine(url=referencia)
    init_db(engine)
    ids = {"referencia": referencia}
    with get_session(engine) as sessao:
        escritorio = Escritorio(nome="Escritório", slug="e")
        sessao.add(escritorio)
        sessao.flush()
        ids["escritorio"] = escritorio.id
        empresa = Empresa(
            escritorio_id=escritorio.id,
            cnpj=CNPJ,
            nome="CLIENTE",
            uf="TO",
            ie="293456789",
            cod_mun="1721000",
            ind_perfil="A",
            ind_ativ="1",
        )
        sessao.add(empresa)
        sessao.flush()
        ids["empresa"] = empresa.id
        for email, admin, escritorio_id in (
            ("admin@teste.local", True, None),
            ("usuario@teste.local", False, escritorio.id),
        ):
            senha_hash, salt = Usuario.hash_senha("senha-de-teste")
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

    with get_session(engine) as sessao:
        ImportadorDeDocumentos(sessao, escritorio_id=ids["escritorio"]).importar(nfe_xml())
        sessao.commit()

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    cliente = TestClient(app)
    cliente.post("/api/login", data={"email": "usuario@teste.local", "senha": "senha-de-teste"})
    ids["cliente"] = cliente
    return ids


def _enviar(cliente, formulario: dict, preenchendo: dict | None = None):
    """Envia o formulário como o navegador enviaria.

    Os valores partem do que a **página** declara; `preenchendo` só troca o
    conteúdo dos campos que já existem lá. Acrescentar um nome que o
    formulário não tem seria burlar justamente o que este teste procura.
    """
    dados = dict(formulario["campos"])
    for nome, valor in (preenchendo or {}).items():
        assert nome in dados, f"o formulário não declara o campo {nome!r}"
        dados[nome] = valor
    return cliente.post(formulario["action"], data=dados)


class TestCadastroFiscal:
    def test_o_formulario_grava_pelos_nomes_que_declara(self, cenario):
        cliente = cenario["cliente"]
        pagina = cliente.get(f"/fiscal/cadastro?empresa={cenario['empresa']}").text
        formulario = formulario_que_posta(pagina, "/fiscal/cadastro")

        # Um valor que o próprio `select` oferece, para que a recusa possível
        # seja "nome errado" e não "valor fora da tabela".
        valor = formulario["opcoes"]["cod_inc_trib"][0]
        resposta = _enviar(cliente, formulario, {"cod_inc_trib": valor})

        assert resposta.status_code == 200
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            assert sessao.get(Empresa, cenario["empresa"]).cod_inc_trib == valor

    def test_a_empresa_viaja_no_formulario(self, cenario):
        """Sem ela a rota não sabe em quem gravar — e recusaria."""
        pagina = cenario["cliente"].get(f"/fiscal/cadastro?empresa={cenario['empresa']}").text

        assert "empresa" in formulario_que_posta(pagina, "/fiscal/cadastro")["campos"]


class TestCorrigir:
    def _simulada(self, cenario):
        return (
            cenario["cliente"]
            .get(f"/fiscal/corrigir?empresa={cenario['empresa']}&campo=cfop&valor=2102")
            .text
        )

    def test_o_formulario_de_confirmar_grava(self, cenario):
        formulario = formulario_que_posta(self._simulada(cenario), "/fiscal/corrigir")

        resposta = _enviar(cenario["cliente"], formulario, {"motivo": "pela tela"})

        assert resposta.status_code == 200
        assert "gravada" in " ".join(resposta.text.split())

    def test_o_total_visto_viaja_no_formulario(self, cenario):
        """É a conferência que impede gravar mais do que se aprovou.

        Se o campo sumisse do template, a rota passaria a recusar tudo — e o
        teste de rota, que monta o `esperado` na mão, continuaria passando.
        """
        formulario = formulario_que_posta(self._simulada(cenario), "/fiscal/corrigir")

        assert formulario["campos"]["esperado"] == "1"

    def test_o_formulario_de_desfazer_aponta_para_a_rota_certa(self, cenario):
        formulario = formulario_que_posta(self._simulada(cenario), "/fiscal/desfazer")

        assert "lote" in formulario["campos"]


class TestClassificar:
    def test_o_formulario_de_aplicar_declara_o_que_a_rota_le(self, cenario):
        from src.documentos.classificacao import criar_regra

        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            criar_regra(
                sessao,
                nome="NCM 2203",
                condicoes=[{"campo": "ncm", "operador": "igual", "valor": "22030000"}],
                acoes=[{"campo": "cfop", "valor": "2102"}],
                empresa_id=cenario["empresa"],
            )
            sessao.commit()

        pagina = cenario["cliente"].get(f"/fiscal/classificar?empresa={cenario['empresa']}").text
        formulario = formulario_que_posta(pagina, "/fiscal/classificar")

        resposta = _enviar(cenario["cliente"], formulario)

        assert resposta.status_code == 200
        assert "aplicada" in " ".join(resposta.text.split())


class TestGerar:
    def test_o_formulario_gera_e_arquiva(self, cenario):
        cliente = cenario["cliente"]
        pagina = cliente.get(
            f"/fiscal/gerar?empresa={cenario['empresa']}&tipo=efd_icms"
            "&de=2026-07-01&ate=2026-07-31"
        ).text
        formulario = formulario_que_posta(pagina, "/fiscal/gerar")

        resposta = _enviar(cliente, formulario)

        assert resposta.status_code == 200
        assert "gerada e arquivada" in " ".join(resposta.text.split())

    def test_o_formulario_de_transmitida_declara_a_escrituracao(self, cenario):
        cliente = cenario["cliente"]
        consulta = (
            f"/fiscal/gerar?empresa={cenario['empresa']}&tipo=efd_icms"
            "&de=2026-07-01&ate=2026-07-31"
        )
        formulario = formulario_que_posta(cliente.get(consulta).text, "/fiscal/gerar")
        _enviar(cliente, formulario)

        pagina = cliente.get(consulta).text
        marcar = formulario_que_posta(pagina, "/fiscal/transmitida")

        resposta = _enviar(cliente, marcar, {"recibo": "R-1"})

        assert resposta.status_code == 200
        assert "marcada como transmitida" in " ".join(resposta.text.split())


class TestImportar:
    def test_o_campo_de_arquivo_tem_o_nome_que_a_rota_espera(self, cenario):
        """Aqui o nome do campo é o parâmetro da rota, e não vem do corpo.

        Um `name=` diferente faria o upload chegar vazio, e a tela responderia
        "nenhum arquivo enviado" a quem acabou de escolher trinta.
        """
        pagina = cenario["cliente"].get("/fiscal/importar").text
        formulario = formulario_que_posta(pagina, "/fiscal/importar")

        assert "arquivos" in formulario["campos"]

        resposta = cenario["cliente"].post(
            "/fiscal/importar",
            files=[("arquivos", ("nota.xml", nfe_xml(numero="9"), "text/xml"))],
            data={"politica": formulario["opcoes"]["politica"][0]},
        )

        assert resposta.status_code == 200
        assert "importado(s)" in " ".join(resposta.text.split())


class TestNavegacao:
    def _links_do_menu(self, cliente) -> set[str]:
        import re

        pagina = cliente.get("/fiscal/documentos").text
        assert "</nav>" in pagina, "sem `</nav>` a fatia seria a página inteira"
        menu = pagina.split("</nav>")[0]
        return {
            href for href in re.findall(r'href="(/[^"#]*)"', menu) if not href.startswith("/logout")
        }

    def test_todo_link_do_menu_responde(self, cenario):
        """Um `href` com erro de digitação só aparece quando alguém clica.

        E um link que este usuário não pode abrir também: o menu não pode
        oferecer caminho que devolve 403 — a mensagem fala de permissão, e
        quem clicou não pediu permissão nenhuma, só clicou no que estava lá.
        """
        links = self._links_do_menu(cenario["cliente"])

        assert len(links) >= 8, "o menu perdeu itens"
        for href in sorted(links):
            resposta = cenario["cliente"].get(href)
            assert resposta.status_code == 200, f"{href} respondeu {resposta.status_code}"

    def test_o_menu_do_administrador_tem_mais_coisa(self, cenario):
        """O contraste é o teste: sem ele, esconder de todos também passaria."""
        from src.dashboard.app import app

        admin = TestClient(app)
        admin.post("/api/login", data={"email": "admin@teste.local", "senha": "senha-de-teste"})

        do_admin = self._links_do_menu(admin)
        do_usuario = self._links_do_menu(cenario["cliente"])

        assert "/auditoria" in do_admin
        assert "/auditoria" not in do_usuario
        assert do_usuario < do_admin, "o menu comum deixou de ser um recorte do de admin"

    def test_os_formularios_de_filtro_usam_get(self, cenario):
        """Filtro por `POST` faria o resultado sumir ao recarregar a página,
        e não caberia num link para alguém mandar ao colega."""
        pagina = cenario["cliente"].get("/fiscal/documentos").text

        de_filtro = [f for f in formularios(pagina) if f["action"] == "/fiscal/documentos"]

        assert de_filtro
        assert all(f["method"] == "get" for f in de_filtro)
