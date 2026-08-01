"""As telas de classificar e corrigir — as duas que gravam em massa.

Elas têm a mesma forma, e ela não é acidental: **mostrar, e só depois gravar**.
É a garantia que os motores dão por dentro e que a linha de comando dá com
`--aplicar`/`--confirmar`; invertê-la na tela, que é por onde a maioria vai
passar, desfaria a proteção justamente na porta mais usada.

Três coisas aqui não existem na linha de comando, e é sobre elas que a maior
parte destes testes está:

  * **a segunda etapa re-simula e confere o total contra o que a tela mostrou.**
    Entre ver e confirmar cabe uma importação, outra pessoa corrigindo, uma
    regra nova. Sem a conferência, alguém aprovaria trinta mudanças e gravaria
    trezentas — e o lote reversível não ajuda quem não percebeu;
  * **desfazer é escopado por escritório.** `desfazer_lote` não conhece
    escritório, e na CLI isso está certo: lá quem roda já tem o banco na mão.
    Aqui não, e um lote é uma string opaca — mas "opaca" não é controle de
    acesso;
  * **quem gravou fica registrado.** A tela sabe quem está logado; a linha de
    comando não sabe.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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
from src.documentos import ORIGEM_REGRA, ORIGEM_USUARIO, ImportadorDeDocumentos
from src.documentos.classificacao import criar_regra
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache
from tests.fixtures_nfe import nfe_xml

CNPJ_A = "98765432000198"
CNPJ_B = "98765432000180"


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios, uma nota de dois itens em cada."""
    referencia = f"sqlite:///{tmp_path / 'massa.db'}"
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
                escritorio_id=escritorio.id, cnpj=cnpj, nome=f"CLIENTE DO {rotulo.upper()}", uf="TO"
            )
            sessao.add(empresa)
            sessao.flush()
            ids[f"empresa_{rotulo}"] = empresa.id

        # Admin primeiro: sem nenhum, o usuário #1 é promovido, e admin vê
        # todos os escritórios — o isolamento passaria por acaso.
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
            sessao.flush()
            if email == "usuario@a.local":
                ids["usuario_a"] = sessao.execute(
                    select(Usuario.id).where(Usuario.email == email)
                ).scalar_one()
        sessao.commit()

    with get_session(engine) as sessao:
        for rotulo, escritorio in (("a", ids["escritorio_a"]), ("b", ids["escritorio_b"])):
            ImportadorDeDocumentos(sessao, escritorio_id=escritorio).importar(
                nfe_xml(
                    chave=f"352607123456780001955500100000{1 if rotulo == 'a' else 2:05d}100000001{7 if rotulo == 'a' else 8}",
                    numero="1" if rotulo == "a" else "2",
                    destinatario_cnpj=CNPJ_A if rotulo == "a" else CNPJ_B,
                    itens=2,
                )
            )
        sessao.commit()
        for rotulo, empresa_id in (("a", ids["empresa_a"]), ("b", ids["empresa_b"])):
            ids[f"documento_{rotulo}"] = (
                sessao.execute(
                    select(DocumentoFiscal.id).where(DocumentoFiscal.empresa_id == empresa_id)
                )
                .scalars()
                .one()
            )

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    def entrar(email):
        cliente = TestClient(app)
        cliente.post("/api/login", data={"email": email, "senha": "senha-de-teste"})
        return cliente

    ids["cliente"] = entrar("usuario@a.local")
    ids["cliente_b"] = entrar("usuario@b.local")
    ids["app"] = app
    return ids


def _texto(resposta) -> str:
    return " ".join(resposta.text.split())


def _secao(html: str, nome: str) -> str:
    """Só o trecho de uma seção, até onde a próxima começa."""
    trechos = []
    for pedaco in html.split('data-secao="')[1:]:
        rotulo, _, corpo = pedaco.partition('"')
        if rotulo == nome:
            trechos.append(corpo)
    assert trechos, f"a seção {nome!r} não está na página"
    return " ".join(trechos)


def _tem_secao(html: str, nome: str) -> bool:
    return f'data-secao="{nome}"' in html


def _ajustes(referencia, **filtros) -> list[AjusteFiscal]:
    with get_session(criar_engine(url=referencia)) as sessao:
        consulta = select(AjusteFiscal)
        for campo, valor in filtros.items():
            consulta = consulta.where(getattr(AjusteFiscal, campo) == valor)
        return list(sessao.execute(consulta).scalars().all())


def _regra(referencia, empresa_id):
    """Uma regra que propõe CFOP 2102 para todo item com NCM 2203."""
    with get_session(criar_engine(url=referencia)) as sessao:
        criar_regra(
            sessao,
            nome="NCM 2203 é entrada interestadual",
            condicoes=[{"campo": "ncm", "operador": "igual", "valor": "22030000"}],
            acoes=[{"campo": "cfop", "valor": "2102"}],
            empresa_id=empresa_id,
        )
        sessao.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Classificar
# ═══════════════════════════════════════════════════════════════════════════


class TestClassificarNaoGravaSozinho:
    def test_ver_nao_grava(self, cenario):
        _regra(cenario["referencia"], cenario["empresa_a"])

        resposta = cenario["cliente"].get(f"/fiscal/classificar?empresa={cenario['empresa_a']}")

        assert "cfop" in _secao(_texto(resposta), "sugestoes")
        assert _ajustes(cenario["referencia"]) == [], "gravou só de mostrar"

    def test_aplicar_grava(self, cenario):
        _regra(cenario["referencia"], cenario["empresa_a"])
        cliente = cenario["cliente"]
        visto = _texto(cliente.get(f"/fiscal/classificar?empresa={cenario['empresa_a']}"))
        total = _esperado(visto)

        resposta = cliente.post(
            "/fiscal/classificar",
            data={"empresa": cenario["empresa_a"], "esperado": total},
        )

        assert resposta.status_code == 200
        assert len(_ajustes(cenario["referencia"])) == 2, "dois itens com NCM 2203"

    def test_o_que_a_regra_aplica_tem_origem_regra(self, cenario):
        """O histórico tem de distinguir o que a regra propôs do que a pessoa
        decidiu — senão "por que este campo está assim?" perde a resposta."""
        _regra(cenario["referencia"], cenario["empresa_a"])
        cliente = cenario["cliente"]
        total = _esperado(
            _texto(cliente.get(f"/fiscal/classificar?empresa={cenario['empresa_a']}"))
        )

        cliente.post(
            "/fiscal/classificar", data={"empresa": cenario["empresa_a"], "esperado": total}
        )

        assert all(a.origem == ORIGEM_REGRA for a in _ajustes(cenario["referencia"]))

    def test_quem_aplicou_a_regra_fica_registrado(self, cenario):
        """Origem `regra` diz *o quê* propôs; o usuário diz *quem* mandou.

        As duas coisas são necessárias: a regra explica o valor, e a pessoa
        responde por ter aceitado a proposta num lote inteiro.
        """
        _regra(cenario["referencia"], cenario["empresa_a"])
        cliente = cenario["cliente"]
        total = _esperado(
            _texto(cliente.get(f"/fiscal/classificar?empresa={cenario['empresa_a']}"))
        )

        cliente.post(
            "/fiscal/classificar", data={"empresa": cenario["empresa_a"], "esperado": total}
        )

        ajustes = _ajustes(cenario["referencia"])
        assert ajustes
        assert all(a.usuario_id == cenario["usuario_a"] for a in ajustes)

    def test_o_lote_aparece_para_poder_desfazer(self, cenario):
        _regra(cenario["referencia"], cenario["empresa_a"])
        cliente = cenario["cliente"]
        total = _esperado(
            _texto(cliente.get(f"/fiscal/classificar?empresa={cenario['empresa_a']}"))
        )

        resposta = cliente.post(
            "/fiscal/classificar", data={"empresa": cenario["empresa_a"], "esperado": total}
        )

        assert "aplicada" in _secao(_texto(resposta), "gravado")

    def test_sem_regra_nao_propoe_nada(self, cenario):
        html = _texto(cenario["cliente"].get(f"/fiscal/classificar?empresa={cenario['empresa_a']}"))

        assert "Nenhuma regra propõe nada" in html


class TestOTotalVistoEConferido:
    """A garantia que a linha de comando não tem."""

    def test_total_diferente_do_visto_nao_grava(self, cenario):
        _regra(cenario["referencia"], cenario["empresa_a"])

        resposta = cenario["cliente"].post(
            "/fiscal/classificar",
            data={"empresa": cenario["empresa_a"], "esperado": "1"},  # a tela mostraria 2
        )

        assert resposta.status_code == 400
        assert "A tela mostrava 1 e agora há 2" in _secao(_texto(resposta), "erro")
        assert _ajustes(cenario["referencia"]) == []

    def test_sem_o_total_nao_grava(self, cenario):
        """Um POST forjado sem o campo não pode virar gravação silenciosa."""
        _regra(cenario["referencia"], cenario["empresa_a"])

        resposta = cenario["cliente"].post(
            "/fiscal/classificar", data={"empresa": cenario["empresa_a"]}
        )

        assert resposta.status_code == 400
        assert _ajustes(cenario["referencia"]) == []

    def test_o_mesmo_vale_para_corrigir(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_a"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": "99",
            },
        )

        assert resposta.status_code == 400
        assert "Nada foi gravado" in _secao(_texto(resposta), "erro")
        assert _ajustes(cenario["referencia"]) == []


def _esperado(html: str) -> str:
    """O total que a tela pôs no formulário, para o teste devolvê-lo igual."""
    marca = 'name="esperado" value="'
    assert marca in html, "a tela não mandou o total que mostrou"
    return html.split(marca)[1].split('"')[0]


# ═══════════════════════════════════════════════════════════════════════════
# Corrigir
# ═══════════════════════════════════════════════════════════════════════════


class TestCorrigirSimulaPrimeiro:
    def _simular(self, cenario, **extras):
        pedido = {"empresa": cenario["empresa_a"], "campo": "cfop", "valor": "2102", **extras}
        consulta = "&".join(f"{k}={v}" for k, v in pedido.items())
        return cenario["cliente"].get(f"/fiscal/corrigir?{consulta}")

    def test_simular_nao_grava(self, cenario):
        resposta = self._simular(cenario)

        assert "mudança(s)" in _secao(_texto(resposta), "simulacao")
        assert _ajustes(cenario["referencia"]) == [], "gravou só de simular"

    def test_a_simulacao_conta_o_que_mudaria(self, cenario):
        html = _secao(_texto(self._simular(cenario)), "simulacao")

        assert "<strong>1</strong> documento(s)" in html
        assert "<strong>2</strong> item(ns)" in html

    def test_confirmar_grava(self, cenario):
        total = _esperado(_texto(self._simular(cenario)))

        resposta = cenario["cliente"].post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_a"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": total,
                "motivo": "CFOP errado na origem",
            },
        )

        assert resposta.status_code == 200
        assert len(_ajustes(cenario["referencia"])) == 2

    def test_o_motivo_vai_para_o_historico(self, cenario):
        total = _esperado(_texto(self._simular(cenario)))

        cenario["cliente"].post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_a"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": total,
                "motivo": "CFOP errado na origem",
            },
        )

        assert all(a.motivo == "CFOP errado na origem" for a in _ajustes(cenario["referencia"]))

    def test_quem_gravou_fica_registrado(self, cenario):
        """A tela sabe quem está logado; a linha de comando não sabe."""
        total = _esperado(_texto(self._simular(cenario)))

        cenario["cliente"].post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_a"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": total,
            },
        )

        assert all(a.usuario_id == cenario["usuario_a"] for a in _ajustes(cenario["referencia"]))

    def test_o_ajuste_gravado_tem_origem_usuario(self, cenario):
        total = _esperado(_texto(self._simular(cenario)))

        cenario["cliente"].post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_a"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": total,
            },
        )

        assert all(a.origem == ORIGEM_USUARIO for a in _ajustes(cenario["referencia"]))

    def test_campo_inexistente_e_erro_e_nao_traceback(self, cenario):
        resposta = self._simular(cenario, campo="nao_existe")

        assert resposta.status_code == 400
        assert "não existe em documento nem em item" in _secao(_texto(resposta), "erro")

    def test_valor_numerico_e_convertido_antes_de_simular(self, cenario):
        """Sem converter, o impacto sairia R$ 0,00.

        A diferença entre `1000.0` e `"2000"` não é numérica, e o impacto é
        justamente o número que decide se a alteração passa. A base do item é
        1.000,00; levá-la a 2.000,00 em dois itens são 2.000,00 de impacto.
        """
        html = _secao(_texto(self._simular(cenario, campo="base_icms", valor="2000")), "simulacao")

        assert "impacto <strong>2.000,00</strong>" in html

    def test_sem_campo_nao_simula(self, cenario):
        resposta = cenario["cliente"].get(f"/fiscal/corrigir?empresa={cenario['empresa_a']}")

        assert not _tem_secao(_texto(resposta), "simulacao")


class TestDesfazer:
    def _gravar_um_lote(self, cenario):
        cliente = cenario["cliente"]
        total = _esperado(
            _texto(
                cliente.get(
                    f"/fiscal/corrigir?empresa={cenario['empresa_a']}&campo=cfop&valor=2102"
                )
            )
        )
        resposta = cliente.post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_a"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": total,
            },
        )
        return _ajustes(cenario["referencia"])[0].lote, resposta

    def test_desfaz_o_lote_inteiro(self, cenario):
        lote, _ = self._gravar_um_lote(cenario)

        resposta = cenario["cliente"].post("/fiscal/desfazer", data={"lote": lote})

        assert "desfeito(s)" in _secao(_texto(resposta), "desfeito")
        assert _ajustes(cenario["referencia"]) == []

    def test_lote_de_outro_escritorio_nao_e_desfeito(self, cenario):
        """`desfazer_lote` não conhece escritório; a tela tem de conhecer.

        O lote é uma string opaca — mas "opaca" não é controle de acesso.
        """
        lote, _ = self._gravar_um_lote(cenario)

        resposta = cenario["cliente_b"].post("/fiscal/desfazer", data={"lote": lote})

        assert resposta.status_code == 400
        assert len(_ajustes(cenario["referencia"])) == 2, "o outro escritório desfez o lote"

    def test_a_recusa_nao_conta_que_o_lote_existe(self, cenario):
        lote, _ = self._gravar_um_lote(cenario)

        alheio = _texto(cenario["cliente_b"].post("/fiscal/desfazer", data={"lote": lote}))
        inventado = _texto(
            cenario["cliente_b"].post("/fiscal/desfazer", data={"lote": "nao-existe"})
        )

        assert "não encontrado" in _secao(alheio, "erro")
        assert "não encontrado" in _secao(inventado, "erro")

    def test_lote_vazio_e_recusado_dizendo_o_que_falta(self, cenario):
        """Lote vazio no `delete` apagaria os ajustes avulsos.

        E a recusa diz *o que fazer* — "Lote  não encontrado", com o buraco no
        meio da frase, manda procurar um lote que a pessoa só esqueceu de
        digitar.
        """
        self._gravar_um_lote(cenario)

        resposta = cenario["cliente"].post("/fiscal/desfazer", data={"lote": ""})

        assert resposta.status_code == 400
        assert "Informe o lote" in _secao(_texto(resposta), "erro")
        assert len(_ajustes(cenario["referencia"])) == 2, "apagou os ajustes de outro lote"


class TestNenhumEscritorioAlcancaOOutro:
    def test_classificar_nao_ve_empresa_alheia(self, cenario):
        html = _texto(cenario["cliente"].get(f"/fiscal/classificar?empresa={cenario['empresa_b']}"))

        assert "não encontrada" in _secao(html, "erro")

    def test_classificar_nao_aplica_em_empresa_alheia(self, cenario):
        _regra(cenario["referencia"], cenario["empresa_b"])

        resposta = cenario["cliente"].post(
            "/fiscal/classificar", data={"empresa": cenario["empresa_b"], "esperado": "2"}
        )

        assert resposta.status_code == 400
        assert _ajustes(cenario["referencia"]) == []

    def test_corrigir_nao_ve_empresa_alheia(self, cenario):
        html = _texto(
            cenario["cliente"].get(
                f"/fiscal/corrigir?empresa={cenario['empresa_b']}&campo=cfop&valor=2102"
            )
        )

        assert "não encontrada" in _secao(html, "erro")

    def test_corrigir_nao_grava_em_empresa_alheia(self, cenario):
        resposta = cenario["cliente"].post(
            "/fiscal/corrigir",
            data={
                "empresa": cenario["empresa_b"],
                "campo": "cfop",
                "valor": "2102",
                "esperado": "2",
            },
        )

        assert resposta.status_code == 400
        assert _ajustes(cenario["referencia"]) == []

    @pytest.mark.parametrize(
        ("metodo", "rota"),
        [
            ("get", "/fiscal/classificar"),
            ("get", "/fiscal/corrigir"),
            ("post", "/fiscal/classificar"),
            ("post", "/fiscal/corrigir"),
            ("post", "/fiscal/desfazer"),
        ],
    )
    def test_anonimo_nao_alcanca_nada(self, cenario, metodo, rota):
        anonimo = TestClient(cenario["app"])

        resposta = getattr(anonimo, metodo)(rota, follow_redirects=False)

        assert resposta.status_code == 302
        assert _ajustes(cenario["referencia"]) == []


class TestNavegacao:
    def test_as_duas_telas_estao_no_menu(self, cenario):
        html = _texto(cenario["cliente"].get("/fiscal/corrigir"))

        assert "/fiscal/classificar" in html
        assert "/fiscal/corrigir" in html
