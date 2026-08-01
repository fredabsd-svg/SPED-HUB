"""A tela de gerar: o espelho, o arquivo e a marca de entrega.

Três coisas que esta tela não pode afrouxar, porque são o motivo de a terceira
camada existir:

  * **gerar sempre arquiva.** Não há prévia que grave em disco. A prévia é o
    espelho, que é prosa e não arquivo transmissível — ninguém o entrega por
    engano. Um arquivo que sai do sistema sem deixar registro é exatamente o
    buraco que a terceira camada fecha;
  * **o que se baixa é o guardado, não um recém-gerado.** Gerar de novo
    produziria um arquivo parecido e possivelmente diferente, porque a camada
    efetiva pode ter mudado. "O que você entregou" é outra pergunta;
  * **marcar a transmissão não se desfaz**, e segunda entrega original do
    mesmo período é recusada.

E, como em toda tela desta suíte, nenhum escritório alcança o do outro — aqui
inclusive para baixar o arquivo, que é a escrituração inteira.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import (
    Empresa,
    Escritorio,
    Escrituracao,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ImportadorDeDocumentos
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache
from tests.fixtures_nfe import nfe_xml

CNPJ_A = "98765432000198"
CNPJ_B = "98765432000180"
PERIODO = {"de": "2026-07-01", "ate": "2026-07-31"}


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios com empresa apta a gerar e uma nota importada."""
    referencia = f"sqlite:///{tmp_path / 'gerar.db'}"
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
        ids["escritorio_a"], ids["escritorio_b"] = a.id, b.id
        for rotulo, escritorio, cnpj in (("a", a, CNPJ_A), ("b", b, CNPJ_B)):
            empresa = Empresa(
                escritorio_id=escritorio.id,
                cnpj=cnpj,
                nome=f"CLIENTE DO {rotulo.upper()}",
                uf="TO",
                ie="293456789",
                cod_mun="1721000",
                ind_perfil="A",
                ind_ativ="1",
                ind_ativ_contribuicoes="2",
                cod_inc_trib="1",
            )
            sessao.add(empresa)
            sessao.flush()
            ids[f"empresa_{rotulo}"] = empresa.id

        for email, admin, escritorio_id in (
            ("admin@teste.local", True, None),
            ("usuario@a.local", False, ids["escritorio_a"]),
            ("usuario@b.local", False, ids["escritorio_b"]),
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
        for rotulo, escritorio in (("a", ids["escritorio_a"]), ("b", ids["escritorio_b"])):
            ImportadorDeDocumentos(sessao, escritorio_id=escritorio).importar(
                nfe_xml(
                    chave=f"35260712345678000195550010000000{1 if rotulo == 'a' else 2:04d}1000000017",
                    numero="1" if rotulo == "a" else "2",
                    destinatario_cnpj=CNPJ_A if rotulo == "a" else CNPJ_B,
                )
            )
        sessao.commit()

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    def entrar(email):
        cliente = TestClient(app)
        cliente.post("/api/login", data={"email": email, "senha": "senha-de-teste"})
        return cliente

    ids["app"] = app
    ids["cliente"] = entrar("usuario@a.local")
    ids["cliente_b"] = entrar("usuario@b.local")
    return ids


def _texto(resposta) -> str:
    return " ".join(resposta.text.split())


def _secao(html: str, nome: str) -> str:
    trechos = []
    for pedaco in html.split('data-secao="')[1:]:
        rotulo, _, corpo = pedaco.partition('"')
        if rotulo == nome:
            trechos.append(corpo)
    assert trechos, f"a seção {nome!r} não está na página"
    return " ".join(trechos)


def _tem_secao(html: str, nome: str) -> bool:
    return f'data-secao="{nome}"' in html


def _espelhar(cenario, cliente=None, **extras):
    pedido = {"empresa": cenario["empresa_a"], "tipo": "efd_icms", **PERIODO, **extras}
    consulta = "&".join(f"{k}={v}" for k, v in pedido.items())
    return (cliente or cenario["cliente"]).get(f"/fiscal/gerar?{consulta}")


def _gerar(cenario, cliente=None, **extras):
    dados = {"empresa": cenario["empresa_a"], "tipo": "efd_icms", **PERIODO, **extras}
    return (cliente or cenario["cliente"]).post("/fiscal/gerar", data=dados)


def _quantas(referencia) -> int:
    with get_session(criar_engine(url=referencia)) as sessao:
        return sessao.execute(select(func.count()).select_from(Escrituracao)).scalar_one()


def _escrituracoes(referencia) -> list[Escrituracao]:
    with get_session(criar_engine(url=referencia)) as sessao:
        return list(sessao.execute(select(Escrituracao).order_by(Escrituracao.id)).scalars().all())


class TestOEspelhoNaoGera:
    """A prévia é prosa; não escreve arquivo nem cria escrituração."""

    def test_ver_o_espelho_nao_arquiva(self, cenario):
        resposta = _espelhar(cenario)

        assert resposta.status_code == 200
        assert _tem_secao(_texto(resposta), "espelho")
        assert _quantas(cenario["referencia"]) == 0, "o espelho arquivou"

    def test_o_espelho_traz_as_conferencias(self, cenario):
        """São as perguntas que o validador faria, respondidas antes dele."""
        secao = _secao(_texto(_espelhar(cenario)), "espelho")

        assert "Conferências" in secao
        assert "✓" in secao

    def test_o_espelho_traz_o_texto_inteiro(self, cenario):
        secao = _secao(_texto(_espelhar(cenario)), "espelho")

        assert "ESPELHO" in secao

    def test_sem_periodo_nao_ha_espelho(self, cenario):
        resposta = cenario["cliente"].get(f"/fiscal/gerar?empresa={cenario['empresa_a']}")

        assert not _tem_secao(_texto(resposta), "espelho")

    def test_periodo_invertido_e_recusado(self, cenario):
        resposta = _espelhar(cenario, de="2026-07-31", ate="2026-07-01")

        assert resposta.status_code == 400
        assert "anterior ao começo" in _secao(_texto(resposta), "erro")

    def test_data_pela_metade_e_recusada_e_nao_derruba(self, cenario):
        """Meio período digitado é o caso comum, e não pode virar erro 500.

        Sem a recusa, a data ilegível vira `None` e a comparação com a outra
        ponta levanta — a tela morre no lugar de dizer o que falta.
        """
        resposta = _espelhar(cenario, ate="trinta-e-um")

        assert resposta.status_code == 400
        assert "período inteiro" in _secao(_texto(resposta), "erro")

    def test_gerar_com_data_pela_metade_nao_arquiva(self, cenario):
        resposta = _gerar(cenario, de="")

        assert resposta.status_code == 400
        assert _quantas(cenario["referencia"]) == 0

    def test_obrigacao_desconhecida_e_recusada(self, cenario):
        resposta = _espelhar(cenario, tipo="efd_inventada")

        assert resposta.status_code == 400
        assert "desconhecida" in _secao(_texto(resposta), "erro")
        assert not _tem_secao(_texto(resposta), "espelho")

    def test_gerar_obrigacao_desconhecida_nao_arquiva(self, cenario):
        resposta = _gerar(cenario, tipo="efd_inventada")

        assert resposta.status_code == 400
        assert _quantas(cenario["referencia"]) == 0

    def test_cadastro_faltando_e_erro_e_nao_traceback(self, cenario):
        """Sem o cadastro fiscal o gerador recusa — e a tela diz o quê falta."""
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            sessao.get(Empresa, cenario["empresa_a"]).ind_perfil = None
            sessao.commit()

        resposta = _espelhar(cenario)

        assert resposta.status_code == 400
        assert "ind_perfil" in _secao(_texto(resposta), "erro")
        assert _quantas(cenario["referencia"]) == 0


class TestGerarSempreArquiva:
    def test_gerar_cria_a_escrituracao(self, cenario):
        resposta = _gerar(cenario)

        assert resposta.status_code == 200
        assert _quantas(cenario["referencia"]) == 1
        assert "gerada e arquivada" in _secao(_texto(resposta), "gerada")

    def test_quem_gerou_fica_registrado(self, cenario):
        _gerar(cenario)

        assert _escrituracoes(cenario["referencia"])[0].usuario_id is not None

    def test_gerar_de_novo_cria_outra(self, cenario):
        """O histórico de tentativas é informação real, não sujeira."""
        _gerar(cenario)
        _gerar(cenario)

        assert _quantas(cenario["referencia"]) == 2

    def test_o_arquivo_baixado_e_o_guardado(self, cenario):
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]

        resposta = cenario["cliente"].get(f"/fiscal/escrituracoes/{escrituracao.id}/arquivo")

        assert resposta.status_code == 200
        assert resposta.text == escrituracao.conteudo
        assert "attachment" in resposta.headers["content-disposition"]

    def test_o_arquivo_sai_com_crlf(self, cenario):
        """O validador recusa o arquivo inteiro se a quebra de linha mudar."""
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]

        resposta = cenario["cliente"].get(f"/fiscal/escrituracoes/{escrituracao.id}/arquivo")

        assert b"\r\n" in resposta.content

    def test_periodo_sem_cadastro_nao_arquiva_nada(self, cenario):
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            sessao.get(Empresa, cenario["empresa_a"]).ind_perfil = None
            sessao.commit()

        resposta = _gerar(cenario)

        assert resposta.status_code == 400
        assert _quantas(cenario["referencia"]) == 0


class TestMarcarATransmissao:
    def _gerar_e_marcar(self, cenario, **extras):
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]
        resposta = cenario["cliente"].post(
            "/fiscal/transmitida",
            data={"escrituracao": escrituracao.id, "recibo": "RECIBO-1", **extras},
        )
        return escrituracao.id, resposta

    def test_marca_e_guarda_o_recibo(self, cenario):
        escrituracao_id, resposta = self._gerar_e_marcar(cenario)

        assert resposta.status_code == 200
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            marcada = sessao.get(Escrituracao, escrituracao_id)
        assert marcada.transmitida_em is not None
        assert marcada.recibo == "RECIBO-1"

    def test_quem_marcou_fica_registrado(self, cenario):
        escrituracao_id, _ = self._gerar_e_marcar(cenario)

        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            assert sessao.get(Escrituracao, escrituracao_id).transmitida_por_id is not None

    def test_segunda_original_do_mesmo_periodo_e_recusada(self, cenario):
        """Duas entregas originais do mesmo período não existem no mundo real."""
        self._gerar_e_marcar(cenario)
        _gerar(cenario)
        segunda = _escrituracoes(cenario["referencia"])[1]

        resposta = cenario["cliente"].post(
            "/fiscal/transmitida", data={"escrituracao": segunda.id, "recibo": "RECIBO-2"}
        )

        assert resposta.status_code == 400
        assert _tem_secao(_texto(resposta), "erro")
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            assert sessao.get(Escrituracao, segunda.id).transmitida_em is None

    def test_a_tela_avisa_que_a_marca_nao_se_desfaz(self, cenario):
        _gerar(cenario)

        html = _texto(_espelhar(cenario))

        assert "não se desfaz" in _secao(html, "escrituracoes")

    def test_escrituracao_inexistente_e_recusada(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/transmitida", data={"escrituracao": "9999", "recibo": "X"}
        )

        assert resposta.status_code == 400
        assert "não encontrada" in _secao(_texto(resposta), "erro")


class TestNenhumEscritorioAlcancaOOutro:
    def test_espelho_de_empresa_alheia_e_recusado(self, cenario):
        resposta = _espelhar(cenario, empresa=cenario["empresa_b"])

        assert resposta.status_code == 400
        assert "não encontrada" in _secao(_texto(resposta), "erro")

    def test_gerar_para_empresa_alheia_nao_arquiva(self, cenario):
        resposta = _gerar(cenario, empresa=cenario["empresa_b"])

        assert resposta.status_code == 400
        assert _quantas(cenario["referencia"]) == 0

    def test_baixar_arquivo_alheio_e_404(self, cenario):
        """O arquivo é a escrituração inteira do outro escritório."""
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]

        resposta = cenario["cliente_b"].get(f"/fiscal/escrituracoes/{escrituracao.id}/arquivo")

        assert resposta.status_code == 404
        assert "|0000|" not in resposta.text

    def test_marcar_transmissao_alheia_e_recusado(self, cenario):
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]

        resposta = cenario["cliente_b"].post(
            "/fiscal/transmitida", data={"escrituracao": escrituracao.id, "recibo": "X"}
        )

        assert resposta.status_code == 400
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            assert sessao.get(Escrituracao, escrituracao.id).transmitida_em is None

    def test_a_recusa_nao_conta_que_a_escrituracao_existe(self, cenario):
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]

        alheia = _texto(
            cenario["cliente_b"].post("/fiscal/transmitida", data={"escrituracao": escrituracao.id})
        )
        inexistente = _texto(
            cenario["cliente_b"].post("/fiscal/transmitida", data={"escrituracao": "9999"})
        )

        assert "não encontrada" in _secao(alheia, "erro")
        assert "não encontrada" in _secao(inexistente, "erro")

    @pytest.mark.parametrize(
        ("metodo", "rota"),
        [("get", "/fiscal/gerar"), ("post", "/fiscal/gerar"), ("post", "/fiscal/transmitida")],
    )
    def test_anonimo_nao_alcanca(self, cenario, metodo, rota):
        anonimo = TestClient(cenario["app"])

        resposta = getattr(anonimo, metodo)(rota, follow_redirects=False)

        assert resposta.status_code == 302
        assert _quantas(cenario["referencia"]) == 0

    def test_anonimo_nao_baixa_arquivo(self, cenario):
        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]
        anonimo = TestClient(cenario["app"])

        resposta = anonimo.get(
            f"/fiscal/escrituracoes/{escrituracao.id}/arquivo", follow_redirects=False
        )

        assert resposta.status_code == 302
        assert "|0000|" not in resposta.text


class TestOFluxoInteiro:
    def test_espelho_gerar_baixar_e_marcar(self, cenario):
        """A cadeia toda pela tela, na ordem em que ela acontece."""
        assert _tem_secao(_texto(_espelhar(cenario)), "espelho")

        _gerar(cenario)
        escrituracao = _escrituracoes(cenario["referencia"])[0]

        arquivo = cenario["cliente"].get(f"/fiscal/escrituracoes/{escrituracao.id}/arquivo")
        assert arquivo.text.startswith("|0000|")

        cenario["cliente"].post(
            "/fiscal/transmitida", data={"escrituracao": escrituracao.id, "recibo": "R-9"}
        )

        # E a nota passa a mostrar, na sua própria tela, em que arquivo entrou.
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            documento_id = (
                sessao.execute(select(Escrituracao).where(Escrituracao.id == escrituracao.id))
                .scalar_one()
                .documentos[0]
                .documento_id
            )
        html = _texto(cenario["cliente"].get(f"/fiscal/documentos/{documento_id}"))

        assert "R-9" in html

    def test_a_tela_esta_no_menu(self, cenario):
        assert "/fiscal/gerar" in _texto(cenario["cliente"].get("/fiscal/gerar"))


class TestPeriodo:
    def test_periodo_sem_documento_ainda_gera(self, cenario):
        """Mês sem nota tem EFD igual — e sem arquivo o Fisco cobra a omissão."""
        resposta = _gerar(cenario, de="2026-01-01", ate="2026-01-31")

        assert resposta.status_code == 200
        assert _quantas(cenario["referencia"]) == 1

    def test_o_periodo_recorta_o_espelho(self, cenario):
        dentro = _secao(_texto(_espelhar(cenario)), "espelho")
        fora = _secao(_texto(_espelhar(cenario, de="2026-01-01", ate="2026-01-31")), "espelho")

        assert "<strong>1</strong> documento(s)" in dentro
        assert "<strong>0</strong> documento(s)" in fora


def test_data_de_hoje_nao_e_usada_no_periodo(cenario):
    """O período vem do formulário, e só dele.

    Um padrão silencioso de "mês atual" faria a tela gerar um período que
    ninguém pediu, e o arquivo sairia certo para o mês errado.
    """
    resposta = cenario["cliente"].get(f"/fiscal/gerar?empresa={cenario['empresa_a']}&tipo=efd_icms")

    assert not _tem_secao(_texto(resposta), "espelho")
    assert (
        str(datetime.date.today().year) not in _secao(_texto(resposta), "erro")
        if _tem_secao(_texto(resposta), "erro")
        else True
    )
