"""A tela do cadastro fiscal — os cinco códigos sem os quais não se gera EFD.

Até aqui esses campos só podiam ser preenchidos por `sped-hub fiscal cadastro`
ou escrevendo no banco, o que na prática deixava a geração de EFD fora do
alcance de quem escritura. A tela existe para isso, e traz junto o risco que a
linha de comando não tem: **ela é multiusuário**.

O que estes testes protegem, em ordem de gravidade:

  * **nenhum escritório alcança a empresa do outro** — nem para ler, nem para
    gravar, nem para descobrir que ela existe. É a única falha aqui que não dá
    para desfazer depois;
  * **a validação é a mesma da linha de comando** — vem do domínio. Uma
    segunda cópia da tabela na tela divergiria da primeira no primeiro ato
    normativo, e a tela é onde ninguém iria conferir;
  * **campo em branco não apaga o que estava lá** — o formulário manda os
    cinco sempre, e quem vem corrigir um não deve zerar os outros quatro;
  * **recusa não grava nada** — nem os campos válidos que vieram junto.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import Empresa, Escritorio, Usuario, criar_engine, get_session, init_db
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios, uma empresa em cada, um usuário no primeiro."""
    referencia = f"sqlite:///{tmp_path / 'cadastro.db'}"
    monkeypatch.setenv("DATABASE_URL", referencia)
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    reset_settings_cache()

    engine = criar_engine(url=referencia)
    init_db(engine)
    ids = {"referencia": referencia}
    with get_session(engine) as sessao:
        a = Escritorio(nome="Escritório A", slug="a")
        b = Escritorio(nome="Escritório B", slug="b")
        sessao.add_all([a, b])
        sessao.flush()
        for rotulo, escritorio in (("a", a), ("b", b)):
            empresa = Empresa(
                escritorio_id=escritorio.id,
                cnpj=f"1111111100019{'1' if rotulo == 'a' else '2'}",
                nome=f"CLIENTE DO {rotulo.upper()}",
                uf="TO",
            )
            sessao.add(empresa)
            sessao.flush()
            ids[f"empresa_{rotulo}"] = empresa.id
            ids[f"escritorio_{rotulo}"] = escritorio.id

        # O admin vem PRIMEIRO de propósito: sem nenhum, o sistema promove o
        # usuário #1 a administrador — e admin enxerga todos os escritórios,
        # o que faria o teste de isolamento passar por acaso.
        senha_hash, salt = Usuario.hash_senha("senha-do-admin")
        sessao.add(
            Usuario(
                email="admin@teste.local",
                nome="Admin",
                senha_hash=senha_hash,
                salt=salt,
                admin=True,
            )
        )
        sessao.flush()
        senha_hash, salt = Usuario.hash_senha("senha-do-a")
        sessao.add(
            Usuario(
                email="usuario@a.local",
                nome="Do A",
                senha_hash=senha_hash,
                salt=salt,
                escritorio_id=ids["escritorio_a"],
            )
        )
        sessao.commit()

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    cliente = TestClient(app)
    cliente.post("/api/login", data={"email": "usuario@a.local", "senha": "senha-do-a"})
    ids["cliente"] = cliente
    return ids


def _texto(resposta) -> str:
    """O HTML com os espaços normalizados.

    O template quebra linha onde cabe, e uma frase da tela pode chegar partida
    em duas. Casar contra o HTML cru faria o teste depender de onde a linha foi
    quebrada — e falhar em quem só reindentou o template.
    """
    return " ".join(resposta.text.split())


def _empresa(referencia, empresa_id) -> Empresa:
    with get_session(criar_engine(url=referencia)) as sessao:
        return sessao.get(Empresa, empresa_id)


class TestNenhumEscritorioAlcancaOOutro:
    """A falha que não dá para desfazer: gravar no cadastro de outro."""

    def test_a_lista_so_traz_as_empresas_do_escritorio(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/cadastro"))

        assert "CLIENTE DO A" in html
        assert "CLIENTE DO B" not in html

    def test_abrir_empresa_de_outro_escritorio_nao_mostra_o_cadastro(self, cenario):
        resposta = cenario["cliente"].get(f"/fiscal/cadastro?empresa={cenario['empresa_b']}")

        assert "CLIENTE DO B" not in resposta.text
        assert "c-ind_perfil" not in resposta.text, "montou o formulário da empresa alheia"

    def test_a_recusa_nao_conta_que_a_empresa_existe(self, cenario):
        """A mesma mensagem para alheia e para inexistente.

        Distinguir as duas transformaria a tela num oráculo de quais ids
        existem no banco — de graça, para qualquer escritório.
        """
        alheia = _texto(cenario["cliente"].get(f"/fiscal/cadastro?empresa={cenario['empresa_b']}"))
        inexistente = _texto(cenario["cliente"].get("/fiscal/cadastro?empresa=9999"))

        assert "não encontrada" in alheia
        assert "não encontrada" in inexistente

    def test_gravar_em_empresa_de_outro_escritorio_nao_grava(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_b"], "ind_perfil": "A"},
        )

        assert resposta.status_code == 400
        assert _empresa(cenario["referencia"], cenario["empresa_b"]).ind_perfil is None

    def test_sem_login_nao_ha_tela(self, cenario):
        anonimo = TestClient(cenario["cliente"].app)

        resposta = anonimo.get("/fiscal/cadastro", follow_redirects=False)

        assert resposta.status_code == 302
        assert resposta.headers["location"] == "/login"

    def test_sem_login_nao_grava(self, cenario):
        anonimo = TestClient(cenario["cliente"].app)

        anonimo.post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "A"},
            follow_redirects=False,
        )

        assert _empresa(cenario["referencia"], cenario["empresa_a"]).ind_perfil is None


class TestGravar:
    def test_grava_o_campo_informado(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "A"},
        )

        assert resposta.status_code == 200
        assert _empresa(cenario["referencia"], cenario["empresa_a"]).ind_perfil == "A"

    def test_diz_que_gravou(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "A"},
        )

        assert "Cadastro gravado" in _texto(resposta)

    def test_campo_em_branco_nao_apaga_o_que_estava(self, cenario):
        """O formulário manda os cinco sempre.

        Se branco significasse "apagar", vir corrigir o `cod_inc_trib` zeraria
        o perfil e a atividade de tabela — e a empresa deixaria de poder gerar
        sem que ninguém tivesse pedido isso.
        """
        cliente = cenario["cliente"]
        cliente.post("/fiscal/cadastro", data={"empresa": cenario["empresa_a"], "ind_perfil": "A"})

        cliente.post(
            "/fiscal/cadastro",
            data={
                "empresa": cenario["empresa_a"],
                "ind_perfil": "",
                "cod_inc_trib": "1",
            },
        )

        empresa = _empresa(cenario["referencia"], cenario["empresa_a"])
        assert empresa.ind_perfil == "A", "o branco apagou o que já estava gravado"
        assert empresa.cod_inc_trib == "1"

    def test_gravar_sem_escolher_nada_nao_diz_que_gravou(self, cenario):
        """Todos os campos em "não alterar" é um formulário que não muda nada.

        Dizer "Cadastro gravado" aí ensina que a mensagem não quer dizer nada,
        e a próxima vez que ela aparecer — merecidamente — ninguém acredita.
        """
        resposta = cenario["cliente"].post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "", "ind_ativ": ""},
        )

        assert resposta.status_code == 200
        assert "Cadastro gravado" not in _texto(resposta)

    def test_grava_varios_campos_de_uma_vez(self, cenario):
        cenario["cliente"].post(
            "/fiscal/cadastro",
            data={
                "empresa": cenario["empresa_a"],
                "ind_perfil": "A",
                "ind_ativ": "1",
                "cod_inc_trib": "1",
            },
        )

        empresa = _empresa(cenario["referencia"], cenario["empresa_a"])
        assert (empresa.ind_perfil, empresa.ind_ativ, empresa.cod_inc_trib) == ("A", "1", "1")

    def test_sem_empresa_no_formulario_e_recusado(self, cenario):
        resposta = cenario["cliente"].post("/fiscal/cadastro", data={"ind_perfil": "A"})

        assert resposta.status_code == 400


class TestValorForaDaTabela:
    """A validação é a do domínio, e a recusa mostra a tabela inteira."""

    def test_valor_invalido_nao_grava(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "Z"},
        )

        assert resposta.status_code == 400
        assert _empresa(cenario["referencia"], cenario["empresa_a"]).ind_perfil is None

    def test_a_recusa_mostra_os_valores_validos(self, cenario):
        """Quem errou o código não sabe qual é o certo.

        Uma recusa que só diz "inválido" manda procurar no Guia Prático o que
        o programa já tem na memória.
        """
        resposta = cenario["cliente"].post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "Z"},
        )

        texto = _texto(resposta)
        assert "Os válidos são" in texto
        assert "A = " in texto

    def test_recusa_nao_grava_nem_os_campos_validos_que_vieram_junto(self, cenario):
        """Meio cadastro gravado é pior que nenhum.

        A empresa passaria a parecer pronta para uma obrigação e não para a
        outra, sem que ninguém tivesse decidido isso.
        """
        cenario["cliente"].post(
            "/fiscal/cadastro",
            data={
                "empresa": cenario["empresa_a"],
                "ind_perfil": "A",
                "ind_ativ": "9",  # fora da tabela
            },
        )

        empresa = _empresa(cenario["referencia"], cenario["empresa_a"])
        assert empresa.ind_perfil is None, "gravou o campo válido de um formulário recusado"
        assert empresa.ind_ativ is None


class TestOQueATelaMostra:
    def test_mostra_o_que_falta_para_cada_obrigacao(self, cenario):
        html = _texto(cenario["cliente"].get(f"/fiscal/cadastro?empresa={cenario['empresa_a']}"))

        assert "falta ind_perfil, ind_ativ" in html
        assert "falta cod_inc_trib, ind_ativ_contribuicoes" in html

    def test_obrigacao_pronta_aparece_como_pronta(self, cenario):
        """Não basta sumir da lista: quem lê precisa VER que está pronta."""
        cliente = cenario["cliente"]
        cliente.post(
            "/fiscal/cadastro",
            data={"empresa": cenario["empresa_a"], "ind_perfil": "A", "ind_ativ": "1"},
        )

        html = _texto(cliente.get(f"/fiscal/cadastro?empresa={cenario['empresa_a']}"))

        assert "pronta para gerar" in html

    def test_as_duas_tabelas_de_atividade_aparecem_separadas(self, cenario):
        """`IND_ATIV` tem tabela DIFERENTE em cada obrigação.

        Foi o engano que o cadastro pela CLI existe para evitar; a tela não
        pode reintroduzi-lo mostrando um campo só.
        """
        html = _texto(cenario["cliente"].get(f"/fiscal/cadastro?empresa={cenario['empresa_a']}"))

        assert "ind_ativ" in html
        assert "ind_ativ_contribuicoes" in html
        assert "tabela DIFERENTE" in html

    def test_valor_gravado_fora_da_tabela_e_denunciado(self, cenario):
        """Cadastro escrito direto no banco, antes de existir validação.

        Mostrar como "vazio" esconderia o problema; mostrar o valor com o
        aviso é o que leva alguém a corrigi-lo.
        """
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            empresa = sessao.get(Empresa, cenario["empresa_a"])
            empresa.ind_perfil = "Z"
            sessao.commit()

        html = _texto(cenario["cliente"].get(f"/fiscal/cadastro?empresa={cenario['empresa_a']}"))

        assert "não está na tabela oficial" in html

    def test_sem_empresa_escolhida_nao_ha_formulario(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/cadastro"))

        assert "Gravar" not in html
        assert "escolha" in html

    def test_a_tela_esta_na_navegacao(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/cadastro"))

        assert "/fiscal/cadastro" in html
