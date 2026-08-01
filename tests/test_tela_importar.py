"""A tela de importar XML — a porta de entrada da Central.

O que ela tem de diferente das outras telas fiscais: aqui não existe "empresa
escolhida". O documento **traz** a empresa dentro dele, pelo CNPJ do emitente
ou do destinatário, e o que decide de quem ele é vem do usuário logado.

Daí o teste mais importante deste arquivo: **o escritório vem do usuário,
nunca do formulário**. Nas outras telas, mandar um id alheio é recusado porque
o escopo não alcança; aqui não haveria nada a recusar — um campo escondido
seria aceito e o documento nasceria no acervo de outro escritório, sem que
ninguém precisasse sequer enxergá-lo.

O resto protege o que a linha de comando já garantia e que a tela não pode
perder: a política padrão é **ignorar** (reimportar a mesma pasta é rotina, e
substituir por engano apagaria as correções já feitas), e a rejeição aparece
com o motivo — sucesso em silêncio está certo, rejeição em silêncio não.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import (
    AjusteFiscal,
    DocumentoFiscal,
    Empresa,
    Escritorio,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ORIGEM_USUARIO, aplicar_ajuste
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache
from tests.fixtures_nfe import nfe_xml

CNPJ_A = "98765432000198"
CNPJ_B = "98765432000180"
CHAVE = "35260712345678000195550010000000011000000017"


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios com empresa cadastrada, e nenhum documento ainda."""
    referencia = f"sqlite:///{tmp_path / 'importar.db'}"
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
            )
            sessao.add(empresa)
            sessao.flush()
            ids[f"empresa_{rotulo}"] = empresa.id

        # Admin primeiro: sem nenhum, o usuário #1 é promovido.
        for email, admin, escritorio_id in (
            ("admin@teste.local", True, None),
            ("usuario@a.local", False, ids["escritorio_a"]),
            ("usuario@b.local", False, ids["escritorio_b"]),
            ("avulso@teste.local", False, None),
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
    ids["cliente_avulso"] = entrar("avulso@teste.local")
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


def _enviar(cliente, *arquivos, politica=None, seguir=True):
    """Manda os arquivos como o navegador mandaria.

    `seguir=False` para ver o 302 do anônimo: seguindo o redirecionamento a
    resposta vira o 200 da tela de login, e o teste de "não pode entrar"
    passaria vendo a página de entrada.
    """
    dados = {"politica": politica} if politica else {}
    return cliente.post(
        "/fiscal/importar",
        files=[("arquivos", (nome, conteudo, "text/xml")) for nome, conteudo in arquivos],
        data=dados,
        follow_redirects=seguir,
    )


def _documentos(referencia) -> list[DocumentoFiscal]:
    with get_session(criar_engine(url=referencia)) as sessao:
        return list(sessao.execute(select(DocumentoFiscal)).scalars().all())


def _quantos(referencia, modelo=DocumentoFiscal) -> int:
    with get_session(criar_engine(url=referencia)) as sessao:
        return sessao.execute(select(func.count()).select_from(modelo)).scalar_one()


class TestOEscritorioVemDoUsuario:
    """A falha que só existe aqui: plantar nota no acervo alheio."""

    def test_o_documento_nasce_no_escritorio_de_quem_enviou(self, cenario):
        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        documento = _documentos(cenario["referencia"])[0]
        assert documento.escritorio_id == cenario["escritorio_a"]

    def test_escritorio_no_formulario_e_ignorado(self, cenario):
        """Um campo escondido não pode decidir de quem é o documento.

        Nas outras telas, mandar um id alheio é recusado porque o escopo não
        alcança. Aqui não haveria nada a recusar: o documento simplesmente
        nasceria do outro lado.
        """
        cenario["cliente"].post(
            "/fiscal/importar",
            files=[("arquivos", ("nota.xml", nfe_xml(), "text/xml"))],
            data={"escritorio": str(cenario["escritorio_b"]), "escritorio_id": "2"},
        )

        documento = _documentos(cenario["referencia"])[0]
        assert documento.escritorio_id == cenario["escritorio_a"]

    def test_usuario_sem_escritorio_nao_importa(self, cenario):
        """Documento sem dono é documento que ninguém alcança depois."""
        resposta = _enviar(cenario["cliente_avulso"], ("nota.xml", nfe_xml()))

        assert resposta.status_code == 400
        assert "não está ligado a nenhum escritório" in _secao(_texto(resposta), "erro")
        assert _quantos(cenario["referencia"]) == 0

    def test_o_importado_por_um_nao_aparece_para_o_outro(self, cenario):
        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        html = _texto(cenario["cliente_b"].get("/fiscal/documentos"))

        assert "CLIENTE DO A" not in html

    def test_anonimo_nao_importa(self, cenario):
        anonimo = TestClient(cenario["app"])

        resposta = _enviar(anonimo, ("nota.xml", nfe_xml()), seguir=False)

        assert resposta.status_code == 302
        assert _quantos(cenario["referencia"]) == 0


class TestImportar:
    def test_importa_e_conta(self, cenario):
        resposta = _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        assert resposta.status_code == 200
        assert "<strong>1</strong> importado(s)" in _secao(_texto(resposta), "resultado")
        assert _quantos(cenario["referencia"]) == 1

    def test_varios_de_uma_vez(self, cenario):
        resposta = _enviar(
            cenario["cliente"],
            ("um.xml", nfe_xml(chave=CHAVE, numero="1")),
            ("dois.xml", nfe_xml(chave=CHAVE[:-3] + "999", numero="2")),
        )

        assert "<strong>2</strong> importado(s)" in _secao(_texto(resposta), "resultado")
        assert _quantos(cenario["referencia"]) == 2

    def test_o_xml_guardado_e_o_enviado(self, cenario):
        """A primeira camada só vale se for byte a byte o que chegou."""
        enviado = nfe_xml()

        _enviar(cenario["cliente"], ("nota.xml", enviado))

        assert _documentos(cenario["referencia"])[0].xml_original == enviado.decode()

    def test_nenhum_arquivo_e_recusado(self, cenario):
        resposta = cenario["cliente"].post("/fiscal/importar", files=[])

        assert resposta.status_code == 400
        assert "Nenhum arquivo enviado" in _secao(_texto(resposta), "erro")

    def test_arquivo_que_nao_e_xml_e_recusado_pelo_nome(self, cenario):
        resposta = _enviar(cenario["cliente"], ("planilha.xlsx", b"PK\x03\x04qualquer coisa"))

        assert resposta.status_code == 400
        assert "só XML" in _secao(_texto(resposta), "erro")
        assert _quantos(cenario["referencia"]) == 0

    def test_xml_que_nao_e_nota_vira_rejeicao_com_motivo(self, cenario):
        """Rejeição em silêncio é o que faz alguém fechar o mês sem a nota."""
        resposta = _enviar(cenario["cliente"], ("coisa.xml", b"<outra><coisa/></outra>"))

        secao = _secao(_texto(resposta), "resultado")
        assert "<strong>1</strong> rejeitado(s)" in secao
        assert "coisa.xml" in secao
        assert _quantos(cenario["referencia"]) == 0

    def test_arquivo_vazio_e_recusado(self, cenario):
        resposta = _enviar(cenario["cliente"], ("vazio.xml", b""))

        assert resposta.status_code == 400
        assert "está vazio" in _secao(_texto(resposta), "erro")

    def test_arquivo_grande_demais_e_recusado(self, cenario, monkeypatch):
        monkeypatch.setattr("src.dashboard.app.max_upload_bytes", lambda: 64)

        resposta = _enviar(cenario["cliente"], ("grande.xml", nfe_xml()))

        assert resposta.status_code == 400
        assert "passa do limite" in _secao(_texto(resposta), "erro")
        assert _quantos(cenario["referencia"]) == 0

    def test_o_nome_do_arquivo_nao_vira_caminho(self, cenario):
        """`../../etc/x.xml` é nome, não caminho — e nem chega a disco."""
        resposta = _enviar(cenario["cliente"], ("../../etc/passwd.xml", nfe_xml()))

        assert resposta.status_code == 200
        assert _quantos(cenario["referencia"]) == 1

    def test_o_nome_saneado_e_o_que_volta_para_a_tela(self, cenario):
        """O nome vem do cliente e é devolvido no relatório de rejeições.

        Nada aqui chega a disco, então o caminho não atravessa nada — mas o
        que o cliente mandou volta escrito na página, e devolver
        `../../etc/passwd.xml` inteiro é ecoar entrada não tratada de volta
        para quem olha.
        """
        resposta = _enviar(cenario["cliente"], ("../../etc/passwd.xml", b"<nao><e/></nao>"))

        secao = _secao(_texto(resposta), "resultado")
        assert "passwd.xml" in secao
        assert "../" not in secao and "etc/passwd" not in secao


class TestPoliticaDeDuplicidade:
    def test_o_padrao_e_ignorar(self, cenario):
        """Reimportar a mesma pasta é rotina; não pode mudar nada."""
        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        resposta = _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        assert "<strong>1</strong> duplicado(s)" in _secao(_texto(resposta), "resultado")
        assert _quantos(cenario["referencia"]) == 1

    def test_ignorar_preserva_as_correcoes_ja_feitas(self, cenario):
        """É o motivo de o padrão ser esse, e não "substituir"."""
        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            documento = sessao.execute(select(DocumentoFiscal)).scalars().one()
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo="cfop",
                valor_novo="2102",
                origem=ORIGEM_USUARIO,
            )
            sessao.commit()

        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        assert _quantos(cenario["referencia"], AjusteFiscal) == 1, "a reimportação apagou o ajuste"

    def test_substituir_troca_o_documento(self, cenario):
        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        resposta = _enviar(cenario["cliente"], ("nota.xml", nfe_xml()), politica="substituir")

        assert "<strong>1</strong> substituído(s)" in _secao(_texto(resposta), "resultado")

    def test_a_tela_avisa_que_substituir_descarta_correcoes(self, cenario):
        """Quem escolhe a opção tem de saber o que ela custa."""
        html = _texto(cenario["cliente"].get("/fiscal/importar"))

        assert "descarta as correções" in html

    def test_recusar_transforma_duplicata_em_rejeicao(self, cenario):
        _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        resposta = _enviar(cenario["cliente"], ("nota.xml", nfe_xml()), politica="erro")

        assert "<strong>1</strong> rejeitado(s)" in _secao(_texto(resposta), "resultado")

    def test_politica_desconhecida_e_recusada(self, cenario):
        resposta = _enviar(cenario["cliente"], ("nota.xml", nfe_xml()), politica="apagar-tudo")

        assert resposta.status_code == 400
        assert "desconhecida" in _secao(_texto(resposta), "erro")
        assert _quantos(cenario["referencia"]) == 0


class TestNavegacao:
    def test_a_tela_esta_no_menu(self, cenario):
        assert "/fiscal/importar" in _texto(cenario["cliente"].get("/fiscal/importar"))

    def test_leva_aos_documentos_depois_de_importar(self, cenario):
        resposta = _enviar(cenario["cliente"], ("nota.xml", nfe_xml()))

        assert "/fiscal/documentos" in _secao(_texto(resposta), "resultado")
