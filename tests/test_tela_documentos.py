"""As telas da Central: a lista de documentos e as três camadas de um deles.

A tela do documento é a que responde *"por que este registro saiu assim?"*, e
ela só serve para isso se mostrar as três camadas **separadas**:

  1. o documento original — o XML como chegou, baixado do que foi guardado e
     nunca remontado a partir das colunas;
  2. o tratamento fiscal — o normalizado e o efetivo lado a lado, com a
     correção visível onde os dois diferem, e o histórico de quem mudou o quê;
  3. o que foi efetivamente enviado — em que escriturações a nota entrou, e
     qual delas foi a transmitida.

Uma tela que mostrasse só o valor final desmentiria o modelo de dados: o
sistema guarda as três justamente porque a resposta a uma intimação depende de
saber qual é qual.

O resto do que se protege aqui é o mesmo do cadastro, e pela mesma razão:
**a tela é multiusuário**, e nenhum escritório pode alcançar o documento do
outro — nem para ver, nem para baixar o XML, nem para descobrir que existe.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ORIGEM_USUARIO, ImportadorDeDocumentos, aplicar_ajuste
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache
from tests.fixtures_nfe import nfe_xml

CNPJ_A = "98765432000198"
CNPJ_B = "98765432000180"


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios; o primeiro com uma nota importada de verdade."""
    referencia = f"sqlite:///{tmp_path / 'documentos.db'}"
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

        # O admin primeiro: sem nenhum, o usuário #1 é promovido, e admin vê
        # todos os escritórios — o teste de isolamento passaria por acaso.
        for email, admin, escritorio_id in (
            ("admin@teste.local", True, None),
            ("usuario@a.local", False, ids["escritorio_a"]),
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
            sessao.flush()
        sessao.commit()

    # Uma nota de verdade em cada escritório, pelo importador — montar
    # `DocumentoFiscal` na mão deixaria de exercitar a normalização.
    with get_session(engine) as sessao:
        for rotulo, escritorio in (("a", ids["escritorio_a"]), ("b", ids["escritorio_b"])):
            importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio)
            importador.importar(
                nfe_xml(
                    chave=f"3526071234567800019555001000000{1 if rotulo == 'a' else 2:04d}100000001{7 if rotulo == 'a' else 8}",
                    numero="1" if rotulo == "a" else "2",
                    destinatario_cnpj=CNPJ_A if rotulo == "a" else CNPJ_B,
                    itens=2,
                )
            )
        sessao.commit()
        for rotulo, empresa_id in (("a", ids["empresa_a"]), ("b", ids["empresa_b"])):
            documento = sessao.query(DocumentoFiscal).filter_by(empresa_id=empresa_id).one_or_none()
            ids[f"documento_{rotulo}"] = documento.id

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    cliente = TestClient(app)
    cliente.post("/api/login", data={"email": "usuario@a.local", "senha": "senha-de-teste"})
    ids["cliente"] = cliente
    return ids


def _texto(resposta) -> str:
    """O HTML com espaços normalizados — o template quebra linha onde cabe."""
    return " ".join(resposta.text.split())


def _secao(html: str, nome: str) -> str:
    """Só o trecho de uma seção da tela, até onde a próxima começa.

    Casar contra a página inteira dá falso positivo o tempo todo: o histórico
    mostra o campo, o valor anterior e o novo, então quase toda asserção sobre
    a tabela de camadas passaria mesmo se a tabela sumisse. As seções são
    marcadas com `data-secao` justamente para que o teste possa dizer *onde*
    espera encontrar cada coisa.

    Parar na próxima marca é o que faz isso valer: uma fatia que fosse até o
    fim da página incluiria o histórico de novo, e o falso positivo voltaria
    por outro caminho.
    """
    marca = 'data-secao="'
    trechos = []
    for pedaco in html.split(marca)[1:]:
        rotulo, _, corpo = pedaco.partition('"')
        if rotulo == nome:
            trechos.append(corpo)
    assert trechos, f"a seção {nome!r} não está na página"
    return " ".join(trechos)


def _corrigir(referencia, documento_id, campo, valor, *, no_item=True, motivo="revisão"):
    with get_session(criar_engine(url=referencia)) as sessao:
        documento = sessao.get(DocumentoFiscal, documento_id)
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0] if no_item else None,
            campo=campo,
            valor_novo=valor,
            origem=ORIGEM_USUARIO,
            motivo=motivo,
        )
        sessao.commit()


class TestNenhumEscritorioAlcancaOOutro:
    def test_a_lista_so_traz_as_empresas_do_escritorio(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/documentos"))

        assert "CLIENTE DO A" in html
        assert "CLIENTE DO B" not in html

    def test_listar_empresa_de_outro_escritorio_nao_lista_nada(self, cenario):
        html = _texto(cenario["cliente"].get(f"/fiscal/documentos?empresa={cenario['empresa_b']}"))

        assert "não encontrada" in html

    def test_abrir_documento_de_outro_escritorio_e_404(self, cenario):
        resposta = cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_b']}")

        assert resposta.status_code == 404

    def test_documento_inexistente_responde_igual_ao_alheio(self, cenario):
        """Distinguir os dois diria a qualquer escritório quais ids existem."""
        alheio = cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_b']}")
        inexistente = cenario["cliente"].get("/fiscal/documentos/999999")

        assert alheio.status_code == inexistente.status_code == 404
        assert alheio.text == inexistente.text

    def test_baixar_o_xml_de_outro_escritorio_e_404(self, cenario):
        """O XML é o documento inteiro — vazá-lo é pior que vazar a listagem."""
        resposta = cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_b']}/xml")

        assert resposta.status_code == 404
        assert "nfeProc" not in resposta.text

    def test_sem_login_nao_ha_lista(self, cenario):
        anonimo = TestClient(cenario["cliente"].app)

        resposta = anonimo.get("/fiscal/documentos", follow_redirects=False)

        assert resposta.status_code == 302

    def test_sem_login_nao_ha_documento(self, cenario):
        anonimo = TestClient(cenario["cliente"].app)

        resposta = anonimo.get(
            f"/fiscal/documentos/{cenario['documento_a']}", follow_redirects=False
        )

        assert resposta.status_code == 302

    def test_sem_login_nao_baixa_o_xml(self, cenario):
        anonimo = TestClient(cenario["cliente"].app)

        resposta = anonimo.get(
            f"/fiscal/documentos/{cenario['documento_a']}/xml", follow_redirects=False
        )

        assert resposta.status_code == 302
        assert "nfeProc" not in resposta.text


class TestAListagem:
    def _listar(self, cenario, **extras):
        consulta = "&".join(f"{k}={v}" for k, v in extras.items())
        return _texto(
            cenario["cliente"].get(f"/fiscal/documentos?empresa={cenario['empresa_a']}&{consulta}")
        )

    def test_mostra_o_documento_importado(self, cenario):
        html = self._listar(cenario)

        assert "<strong>1</strong> documento(s)" in html

    def test_o_periodo_recorta(self, cenario):
        """A nota é de 2026-07-30; um recorte anterior não pode trazê-la."""
        dentro = self._listar(cenario, de="2026-07-01", ate="2026-07-31")
        fora = self._listar(cenario, de="2026-01-01", ate="2026-01-31")

        assert "<strong>1</strong> documento(s)" in dentro
        assert "Nenhum documento no recorte" in fora

    def test_cada_extremo_do_periodo_recorta_sozinho(self, cenario):
        """Um recorte só com `de`, e outro só com `ate`.

        Com os dois sempre juntos, um filtro que ignorasse o `de` passaria: o
        `ate` sozinho já bastaria para excluir a nota de julho.
        """
        so_de = self._listar(cenario, de="2026-08-01")
        so_ate = self._listar(cenario, ate="2026-07-01")

        assert "Nenhum documento no recorte" in so_de, "o `de` não recortou"
        assert "Nenhum documento no recorte" in so_ate, "o `ate` não recortou"

    def test_data_ilegivel_nao_derruba_a_tela(self, cenario):
        """Filtro meio digitado não pode virar uma tela que não abre."""
        html = self._listar(cenario, de="30/07/2026")

        assert "<strong>1</strong> documento(s)" in html

    def test_a_nota_corrigida_e_marcada(self, cenario):
        _corrigir(cenario["referencia"], cenario["documento_a"], "cfop", "2102")

        assert "corrigida" in self._listar(cenario)

    def test_a_nota_intocada_nao_e_marcada(self, cenario):
        assert "corrigida" not in self._listar(cenario)

    def test_sem_empresa_escolhida_nao_lista(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/documentos"))

        assert "escolha" in html
        assert "documento(s)" not in html.split("</form>")[-1]


class TestAsTresCamadas:
    @pytest.fixture
    def html(self, cenario):
        return _texto(cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_a']}"))

    def test_a_primeira_camada_e_o_xml_guardado(self, html, cenario):
        assert "Documento original" in html
        assert f"/fiscal/documentos/{cenario['documento_a']}/xml" in html

    def test_o_xml_baixado_e_o_que_chegou(self, cenario):
        """Byte a byte, do que foi guardado — nunca remontado das colunas."""
        resposta = cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_a']}/xml")

        assert resposta.status_code == 200
        assert "attachment" in resposta.headers["content-disposition"]
        # Não basta parecer um XML de NF-e: tem de ser ESTE documento, com o
        # que só existe no arquivo guardado — a chave, o emitente, os itens.
        assert "<nfeProc" in resposta.text
        assert "PRODUTO DE TESTE 2" in resposta.text
        assert "INDUSTRIA EXEMPLO LTDA" in resposta.text
        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            guardado = sessao.get(DocumentoFiscal, cenario["documento_a"]).xml_original
        assert resposta.text == guardado, "o baixado não é byte a byte o guardado"

    def test_a_primeira_camada_mostra_o_hash(self, html):
        """É o que prova que o guardado é o que chegou."""
        assert "Hash" in html

    def test_a_segunda_camada_poe_normalizado_e_efetivo_lado_a_lado(self, html):
        tratamento = _secao(html, "tratamento")

        assert "Normalizado" in tratamento and "Efetivo" in tratamento
        # A nota de teste vale 1.000,00 por item; o valor tem de estar na
        # tabela, senão "Normalizado" é só um cabeçalho de coluna vazia.
        assert "1000" in tratamento or "1.000" in tratamento

    def test_a_terceira_camada_aparece_mesmo_vazia(self, html):
        """Sem escrituração ainda, a seção fica — e diz que está vazia.

        Some-la faria a tela parecer não ter terceira camada, que é justamente
        a que responde "o que você enviou".
        """
        assert "Escriturações que levaram esta nota" in html
        assert "ainda não entrou em nenhuma escrituração" in _secao(html, "escrituracoes")

    def test_os_itens_aparecem(self, html):
        assert "Item 1" in html
        assert "Item 2" in html
        assert "PRODUTO DE TESTE 1" in _secao(html, "item-1")
        assert "PRODUTO DE TESTE 2" in _secao(html, "item-2")


class TestAClassificacaoConferida:
    """A tela aponta o que a SEFAZ recusaria, no item onde está.

    A apuração já apontava, mas ela é do mês inteiro: para achar QUAL nota tem
    o problema seria preciso sair procurando. Aqui o apontamento fica no item,
    ao lado da tabela que mostra o valor.
    """

    def _abrir(self, cenario):
        return _texto(cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_a']}"))

    def test_nota_bem_classificada_nao_mostra_a_secao(self, cenario):
        """Seção que aparece sempre treina quem lê a ignorá-la."""
        assert "Classificação divergente" not in self._abrir(cenario)

    def test_o_par_que_nao_casa_aparece_no_item(self, cenario):
        _corrigir(cenario["referencia"], cenario["documento_a"], "cst_ibscbs", "620")

        secao = _secao(self._abrir(cenario), "classificacao-1")

        assert "pertence ao CST 000" in secao
        assert "declara CST 620" in secao

    def test_o_apontamento_diz_de_quando_e_a_tabela(self, cenario):
        """Sem a data, quem lê não sabe se a divergência é dele ou nossa."""
        _corrigir(cenario["referencia"], cenario["documento_a"], "cst_ibscbs", "620")

        secao = _secao(self._abrir(cenario), "classificacao-1")

        assert "IT 2025.002" in secao
        assert "2026-06-22" in secao

    def test_o_item_sem_problema_nao_ganha_a_secao(self, cenario):
        """A correção é no item 1; o item 2 continua limpo."""
        _corrigir(cenario["referencia"], cenario["documento_a"], "cst_ibscbs", "620")

        html = self._abrir(cenario)

        assert 'data-secao="classificacao-1"' in html
        assert 'data-secao="classificacao-2"' not in html


class TestOQueMudouFicaVisivel:
    def _abrir(self, cenario):
        return _texto(cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_a']}"))

    def test_o_campo_corrigido_e_marcado(self, cenario):
        _corrigir(cenario["referencia"], cenario["documento_a"], "cfop", "2102")

        item = _secao(self._abrir(cenario), "item-1")

        assert "corrigido</span>" in item, "a linha do campo não foi marcada"
        assert "2102" in item

    def test_o_normalizado_continua_a_vista(self, cenario):
        """O valor de origem não some quando alguém corrige.

        É ele que responde "o que veio na nota" — e a resposta a uma intimação
        depende de as duas colunas existirem lado a lado.
        """
        _corrigir(cenario["referencia"], cenario["documento_a"], "cfop", "2102")

        item = _secao(self._abrir(cenario), "item-1")

        assert "6102" in item, "o CFOP original sumiu da tabela de camadas"

    def test_o_historico_diz_quem_mudou_o_que_e_por_que(self, cenario):
        _corrigir(
            cenario["referencia"],
            cenario["documento_a"],
            "cfop",
            "2102",
            motivo="CFOP de entrada em nota de entrada",
        )

        historico = _secao(self._abrir(cenario), "historico")

        assert "CFOP de entrada em nota de entrada" in historico
        assert ORIGEM_USUARIO in historico

    def test_sem_correcao_o_historico_diz_isso(self, cenario):
        assert "Nenhuma correção" in _secao(self._abrir(cenario), "historico")

    def test_campo_corrigido_fora_da_lista_de_revisao_aparece_assim_mesmo(self, cenario):
        """Um ajuste que a tela não mostrasse seria correção invisível.

        `codigo_beneficio` não está entre os campos que se revisa por padrão;
        tendo sido corrigido, ele entra na tabela mesmo assim.
        """
        _corrigir(cenario["referencia"], cenario["documento_a"], "codigo_beneficio", "TO123456")

        item = _secao(self._abrir(cenario), "item-1")

        assert "codigo_beneficio" in item
        assert "TO123456" in item

    def test_correcao_no_cabecalho_aparece_no_cabecalho(self, cenario):
        _corrigir(
            cenario["referencia"],
            cenario["documento_a"],
            "natureza_operacao",
            "DEVOLUCAO",
            no_item=False,
        )

        tratamento = _secao(self._abrir(cenario), "tratamento")

        assert "DEVOLUCAO" in tratamento


class TestATerceiraCamadaComEscrituracao:
    def test_mostra_a_escrituracao_e_se_foi_transmitida(self, cenario):
        """Gerar e marcar como transmitida — o caminho inteiro até a tela."""
        from src.escrituracoes import GeradorEFDICMS, arquivar, marcar_transmitida

        with get_session(criar_engine(url=cenario["referencia"])) as sessao:
            empresa = sessao.get(Empresa, cenario["empresa_a"])
            empresa.ind_perfil, empresa.ind_ativ = "A", "1"
            empresa.ie, empresa.cod_mun = "293456789", "1721000"
            sessao.flush()
            resultado = GeradorEFDICMS(
                sessao,
                empresa=empresa,
                data_inicio=datetime.date(2026, 7, 1),
                data_fim=datetime.date(2026, 7, 31),
            ).gerar()
            escrituracao = arquivar(
                sessao,
                resultado=resultado,
                empresa=empresa,
                tipo="efd_icms",
                data_inicio=datetime.date(2026, 7, 1),
                data_fim=datetime.date(2026, 7, 31),
            )
            sessao.flush()
            marcar_transmitida(sessao, escrituracao, recibo="RECIBO-123")
            sessao.commit()

        html = _texto(cenario["cliente"].get(f"/fiscal/documentos/{cenario['documento_a']}"))

        secao = _secao(html, "escrituracoes")
        assert "efd_icms" in secao
        assert "RECIBO-123" in secao
        assert "ainda não entrou em nenhuma escrituração" not in secao


class TestNavegacao:
    def test_a_tela_esta_no_menu(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/documentos"))

        assert "/fiscal/documentos" in html

    def test_o_numero_leva_ao_documento(self, cenario):
        html = _texto(cenario["cliente"].get(f"/fiscal/documentos?empresa={cenario['empresa_a']}"))

        assert f"/fiscal/documentos/{cenario['documento_a']}" in html
