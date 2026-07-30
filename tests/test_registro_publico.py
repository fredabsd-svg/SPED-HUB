"""Quem consegue criar conta numa instalação do escritório.

`/register` era aberto, sem restrição nenhuma, e é o único caminho que existe
para criar usuário. Numa instalação nova isso entrega a escrituração fiscal
inteira a qualquer um que alcance o servidor:

  1. o contador cria a primeira conta e vira administrador — sem escritório,
     porque o registro não tem onde informar um;
  2. ele importa as ECDs dos clientes, e as empresas herdam o escritório dele:
     nenhum;
  3. um estranho acha a URL e se registra. Também fica sem escritório;
  4. o escopo casa "sem escritório" com "sem escritório" — e a conta nova vê
     todas as empresas e abre todas as escriturações.

O escopo em si está certo: numa instalação de escritório único, todos são do
mesmo grupo. O que estava errado é **quem entra nesse grupo**. E o deploy
documentado publica as portas 80 e 443 com domínio e Let's Encrypt, então
"qualquer um que alcance o servidor" quer dizer a internet.

O registro segue aberto enquanto não existe nenhum usuário — é o único jeito de
criar o primeiro administrador. Depois disso fecha, e novos usuários são criados
por `sped-hub usuario criar`, por quem já administra o servidor.
"""

from __future__ import annotations

import datetime
import pathlib

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.auth import AuthService, aplicar_escopo_empresas, usuario_pode_acessar_ecd
from src.cli import main
from src.db.models import ECD, Empresa, Escritorio, criar_engine, init_db

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def instalacao(tmp_path, monkeypatch):
    """Uma instalação nova, com banco próprio."""
    caminho = tmp_path / "escritorio.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{caminho}")
    engine = criar_engine(url=f"sqlite:///{caminho}")
    init_db(engine)
    return engine, str(caminho)


def _cliente_importado(engine, usuario) -> int:
    """Reproduz o que a importação faz: a empresa herda o escritório de quem sobe."""
    with Session(engine) as sessao:
        empresa = Empresa(
            cnpj="11222333000181", nome="Cliente Ltda", escritorio_id=usuario.escritorio_id
        )
        sessao.add(empresa)
        sessao.commit()
        ecd = ECD(
            empresa_id=empresa.id,
            leiaute="9.00",
            dt_ini=datetime.date(2024, 1, 1),
            dt_fin=datetime.date(2024, 12, 31),
            hash_arquivo="h1",
            nome_arquivo="cliente.txt",
        )
        sessao.add(ecd)
        sessao.commit()
        return ecd.id


class TestPrimeiroUsuario:
    def test_primeiro_registro_e_permitido_e_vira_admin(self, instalacao):
        """Sem isto não há como começar: é o bootstrap do administrador."""
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)

        contador = auth.registrar(
            email="contador@escritorio.com.br", nome="Contador", senha="senha123"
        )

        assert contador.admin is True

    def test_registro_publico_fecha_depois_do_primeiro(self, instalacao):
        """A porta pela qual um estranho entrava."""
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")

        with pytest.raises(ValueError) as erro:
            auth.registrar(email="qualquer@gmail.com", nome="Fulano", senha="senha123")

        assert "administrador" in str(erro.value).lower()

    def test_estranho_nao_chega_a_existir(self, instalacao):
        """Ponta a ponta: o contador importa, e o estranho não passa da porta."""
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        contador = auth.registrar(
            email="contador@escritorio.com.br", nome="Contador", senha="senha123"
        )
        ecd_id = _cliente_importado(engine, contador)

        with pytest.raises(ValueError):
            auth.registrar(email="qualquer@gmail.com", nome="Fulano", senha="senha123")

        with pytest.raises(ValueError):
            auth.login(email="qualquer@gmail.com", senha="senha123")
        with Session(engine) as sessao:
            assert sessao.get(ECD, ecd_id) is not None, "a ECD do cliente segue lá"

    def test_sem_escritorio_enxerga_o_grupo_sem_escritorio(self, instalacao):
        """Isto é o escopo funcionando, e é por isso que a porta tem de fechar.

        Numa instalação de escritório único ninguém tem `escritorio_id`: nem o
        contador, nem as empresas que ele importa. Todo usuário sem escritório
        pertence, corretamente, ao mesmo grupo. O defeito nunca esteve aqui —
        estava em qualquer pessoa poder entrar nesse grupo pelo `/register`.

        O teste existe para que ninguém "conserte" o escopo achando que o
        problema era ele.
        """
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        contador = auth.registrar(
            email="contador@escritorio.com.br", nome="Contador", senha="senha123"
        )
        ecd_id = _cliente_importado(engine, contador)
        colega = auth.criar_usuario(
            email="colega@escritorio.com.br", nome="Colega", senha="senha123"
        )

        assert colega.escritorio_id is None
        with Session(engine) as sessao:
            visiveis = (
                sessao.execute(aplicar_escopo_empresas(select(Empresa), colega)).scalars().all()
            )
            assert [e.nome for e in visiveis] == ["Cliente Ltda"]
            assert usuario_pode_acessar_ecd(sessao, colega, ecd_id) is True


class TestCriacaoPeloAdministrador:
    """Fechar o registro sem alternativa deixaria o escritório sem crescer."""

    def test_admin_cria_usuario(self, instalacao):
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")

        colega = auth.criar_usuario(
            email="colega@escritorio.com.br", nome="Colega", senha="senha123"
        )

        assert colega.id is not None
        assert colega.admin is False, "usuário criado pelo admin não vira admin sozinho"
        usuario, token, *_ = auth.login(email="colega@escritorio.com.br", senha="senha123")
        assert usuario.id == colega.id and token

    def test_admin_pode_criar_outro_admin(self, instalacao):
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")

        socio = auth.criar_usuario(
            email="socio@escritorio.com.br", nome="Sócio", senha="senha123", admin=True
        )

        assert socio.admin is True

    def test_criacao_pelo_admin_aceita_escritorio(self, instalacao):
        """É o que separa um escritório do outro na mesma instância."""
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")
        with Session(engine) as sessao:
            filial = Escritorio(nome="Filial Norte", slug="filial-norte")
            sessao.add(filial)
            sessao.commit()
            filial_id = filial.id

        usuario = auth.criar_usuario(
            email="filial@escritorio.com.br",
            nome="Filial",
            senha="senha123",
            escritorio_id=filial_id,
        )

        assert usuario.escritorio_id == filial_id

    def test_email_repetido_e_recusado(self, instalacao):
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")

        with pytest.raises(ValueError, match="cadastrado"):
            auth.criar_usuario(email="contador@escritorio.com.br", nome="Outro", senha="senha123")

    def test_senha_curta_e_recusada(self, instalacao):
        """A rota web exige 6 caracteres; a criação pelo admin não pode ser frouxa."""
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")

        with pytest.raises(ValueError, match="6"):
            auth.criar_usuario(email="curto@escritorio.com.br", nome="Curto", senha="123")


class TestRegistroAbertoPorConfiguracao:
    """Auto-serviço continua possível, mas por escolha explícita."""

    def test_variavel_reabre_o_registro(self, instalacao, monkeypatch):
        engine, caminho = instalacao
        monkeypatch.setenv("SPED_HUB_REGISTRO_ABERTO", "true")
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")

        segundo = auth.registrar(email="colega@escritorio.com.br", nome="Colega", senha="senha123")

        assert segundo.id is not None
        assert segundo.admin is False

    @pytest.mark.parametrize("valor", ["false", "False", "0", "no"])
    def test_desligar_pela_variavel_realmente_desliga(self, instalacao, monkeypatch, valor):
        """Sem coerção booleana, a string "false" é verdadeira em Python.

        O operador que escreve `SPED_HUB_REGISTRO_ABERTO=false` para fechar a
        porta a deixaria escancarada — exatamente ao contrário do que pediu.
        """
        engine, caminho = instalacao
        monkeypatch.setenv("SPED_HUB_REGISTRO_ABERTO", valor)
        from src.settings import get_settings

        assert get_settings().registro_aberto is False

        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")
        with pytest.raises(ValueError, match="fechado"):
            auth.registrar(email="qualquer@gmail.com", nome="Fulano", senha="senha123")

    def test_o_padrao_e_fechado(self, monkeypatch):
        """Padrão inseguro é o que produziu o problema; o padrão tem de ser fechado."""
        monkeypatch.delenv("SPED_HUB_REGISTRO_ABERTO", raising=False)
        from src.settings import get_settings

        assert get_settings().registro_aberto is False


class TestComandoDeUsuario:
    """`sped-hub usuario` é a alternativa ao registro público fechado."""

    def test_criar_pelo_cli(self, instalacao, capsys):
        engine, caminho = instalacao
        AuthService(db_path=caminho).registrar(
            email="contador@escritorio.com.br", nome="Contador", senha="senha123"
        )

        codigo = main(
            [
                "usuario",
                "criar",
                "--email",
                "colega@escritorio.com.br",
                "--nome",
                "Colega",
                "--senha",
                "senha123",
                "--db",
                caminho,
            ]
        )

        assert codigo == 0
        assert "colega@escritorio.com.br" in capsys.readouterr().out
        usuario, _token, *_ = AuthService(db_path=caminho).login(
            email="colega@escritorio.com.br", senha="senha123"
        )
        assert usuario.admin is False

    def test_criar_admin_pelo_cli(self, instalacao, capsys):
        engine, caminho = instalacao
        AuthService(db_path=caminho).registrar(
            email="contador@escritorio.com.br", nome="Contador", senha="senha123"
        )

        codigo = main(
            [
                "usuario",
                "criar",
                "--email",
                "socio@escritorio.com.br",
                "--nome",
                "Sócio",
                "--senha",
                "senha123",
                "--admin",
                "--db",
                caminho,
            ]
        )

        assert codigo == 0
        assert "administrador" in capsys.readouterr().out

    def test_email_repetido_devolve_codigo_de_erro(self, instalacao, capsys):
        """Script de provisionamento precisa saber que falhou."""
        engine, caminho = instalacao
        AuthService(db_path=caminho).registrar(
            email="contador@escritorio.com.br", nome="Contador", senha="senha123"
        )

        codigo = main(
            [
                "usuario",
                "criar",
                "--email",
                "contador@escritorio.com.br",
                "--nome",
                "Outro",
                "--senha",
                "senha123",
                "--db",
                caminho,
            ]
        )

        assert codigo == 1
        assert "ERRO" in capsys.readouterr().out

    def test_faltando_email_nao_estoura(self, instalacao, capsys):
        engine, caminho = instalacao

        codigo = main(["usuario", "criar", "--nome", "Sem E-mail", "--db", caminho])

        assert codigo == 1
        assert "--email" in capsys.readouterr().out

    def test_listar_mostra_papel_e_escritorio(self, instalacao, capsys):
        engine, caminho = instalacao
        auth = AuthService(db_path=caminho)
        auth.registrar(email="contador@escritorio.com.br", nome="Contador", senha="senha123")
        auth.criar_usuario(email="colega@escritorio.com.br", nome="Colega", senha="senha123")

        codigo = main(["usuario", "listar", "--db", caminho])

        saida = capsys.readouterr().out
        assert codigo == 0
        assert "contador@escritorio.com.br" in saida
        assert "colega@escritorio.com.br" in saida
        assert "sim" in saida and "não" in saida, "não distingue administrador"

    def test_listar_banco_vazio_orienta(self, instalacao, capsys):
        """Instalação nova: dizer onde se cria o primeiro é o que importa."""
        engine, caminho = instalacao

        codigo = main(["usuario", "listar", "--db", caminho])

        assert codigo == 0
        assert "/register" in capsys.readouterr().out


class TestRotaWebDeRegistro:
    """A porta pública em si — é por ela que um estranho chegava."""

    @pytest.fixture
    def cliente(self, instalacao, monkeypatch):
        from fastapi.testclient import TestClient

        engine, caminho = instalacao
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "*")
        import src.dashboard.app as app_mod

        monkeypatch.setattr(app_mod, "get_auth", lambda: AuthService(db_path=caminho))
        return TestClient(app_mod.app), caminho

    def _registrar(self, cliente, email):
        return cliente.post(
            "/api/register",
            data={"email": email, "nome": "Fulano", "senha": "senha123"},
        )

    def test_primeiro_registro_pela_web_funciona(self, cliente):
        client, _ = cliente
        assert self._registrar(client, "contador@escritorio.com.br").status_code == 200

    def test_segundo_registro_pela_web_e_recusado_com_403(self, cliente):
        """403 e não 400: é recusa de permissão, não formulário mal preenchido."""
        client, _ = cliente
        self._registrar(client, "contador@escritorio.com.br")

        resposta = self._registrar(client, "qualquer@gmail.com")

        assert resposta.status_code == 403
        assert "fechado" in resposta.json()["mensagem"].lower()

    def test_conta_recusada_nao_e_criada(self, cliente):
        """O que importa não é o código HTTP, é o estranho não existir."""
        client, caminho = cliente
        self._registrar(client, "contador@escritorio.com.br")
        self._registrar(client, "qualquer@gmail.com")

        with pytest.raises(ValueError):
            AuthService(db_path=caminho).login(email="qualquer@gmail.com", senha="senha123")

    def test_email_repetido_continua_400(self, cliente):
        """Regressão: a recusa por permissão não pode engolir a de validação."""
        client, _ = cliente
        self._registrar(client, "contador@escritorio.com.br")
        import os

        os.environ["SPED_HUB_REGISTRO_ABERTO"] = "true"
        try:
            resposta = self._registrar(client, "contador@escritorio.com.br")
        finally:
            del os.environ["SPED_HUB_REGISTRO_ABERTO"]

        assert resposta.status_code == 400
        assert "cadastrado" in resposta.json()["mensagem"].lower()


class TestMensagemDeErroChegaNaTela:
    """O alerta de erro era montado e descartado.

    O `htmx:beforeSwap` do `base.html` transforma `{"status":"erro",...}` num
    `.alert-error` legível — e nunca aparecia: em resposta 4xx o HTMX não faz
    swap sem `shouldSwap`, e o ramo só mexia em `isError`, que apenas silencia
    o log do console. Verificado no navegador, antes do conserto:

        LOGIN COM SENHA ERRADA      -> texto do #login-result: ''
        REGISTRO COM E-MAIL EM USO  -> texto do #register-result: ''

    Quem errava a senha via a tela parada, sem explicação. O
    `tests/test_e2e_playwright.py` cobre no navegador; aqui fica a trava
    barata, que roda sem Chromium.
    """

    @pytest.fixture
    def script(self) -> str:
        base = (REPO_ROOT / "src" / "dashboard" / "templates" / "base.html").read_text("utf-8")
        inicio = base.index('if (corpo && corpo.status === "erro"')
        return base[inicio : inicio + 800]

    def test_ramo_de_erro_libera_o_swap(self, script):
        assert "shouldSwap = true" in script, (
            "sem `shouldSwap`, o HTMX descarta o alerta em resposta 4xx e a " "tela fica parada"
        )

    def test_a_mensagem_e_escapada(self, script):
        """Ela vem do servidor e é injetada como HTML."""
        assert "escapar(corpo.mensagem)" in script
