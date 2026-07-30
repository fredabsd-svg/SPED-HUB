"""Poder da API Key (Fase 34).

A revisão do aplicativo encontrou uma cadeia de escalonamento alcançável com a
chave que se entrega a um integrador terceiro. Demonstrada antes do conserto:

    empresas visíveis para a chave do escritório A: ['Cliente do A', 'Cliente do B']
    criar nova chave:      200 → PERMITIDO
    listar chaves:         PERMITIDO — vê 3 chave(s)
    revogar chave alheia:  200 → PERMITIDO

Estava registrado em `docs/modules/api.md` como lacuna — *"não tem escopo por
chave: qualquer API Key válida acessa tudo, inclusive criar e revogar outras
chaves"*. A frase lê como funcionalidade que falta. O que ela descreve é
administração total da instância na mão de quem recebe a chave, incluindo
elevar a própria cota de rate limit e derrubar as integrações do dono.

Duas mudanças fecham isso:

1. **Administrar é sessão, não chave.** As rotas de `/api-keys*` (e as de cota)
   passaram a exigir administrador logado no dashboard.
2. **A chave ganhou dono.** Chave com escritório lê só o dele. Chave sem
   escritório (`NULL`) segue lendo tudo — é o comportamento de toda chave criada
   antes da coluna existir, e invalidá-las quebraria produção.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from src.api import ApiKeyService
from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import (
    ECD,
    Empresa,
    Escritorio,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.ratelimit import init_limiter
from src.settings import reset_settings_cache

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Dois escritórios, cada um com empresa e ECD, e chaves de cada tipo."""
    referencia = f"sqlite:///{tmp_path / 'escopo.db'}"
    monkeypatch.setenv("DATABASE_URL", referencia)
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    reset_settings_cache()

    engine = criar_engine(url=referencia)
    init_db(engine)
    import datetime

    with get_session(engine) as sessao:
        a = Escritorio(nome="Escritório A", slug="a")
        b = Escritorio(nome="Escritório B", slug="b")
        sessao.add_all([a, b])
        sessao.flush()
        ids = {"escritorio_a": a.id, "escritorio_b": b.id}
        for rotulo, escritorio in (("a", a), ("b", b)):
            empresa = Empresa(
                escritorio_id=escritorio.id,
                cnpj=f"1111111100011{rotulo == 'b' and 2 or 1}",
                nome=f"Cliente do {rotulo.upper()}",
            )
            sessao.add(empresa)
            sessao.flush()
            ecd = ECD(
                empresa_id=empresa.id,
                leiaute="9",
                dt_ini=datetime.date(2024, 1, 1),
                dt_fin=datetime.date(2024, 12, 31),
                importado_em=datetime.datetime.now(datetime.UTC),
            )
            sessao.add(ecd)
            sessao.flush()
            ids[f"ecd_{rotulo}"] = ecd.id
            ids[f"empresa_{rotulo}"] = empresa.id
        senha_hash, salt = Usuario.hash_senha("senha-admin")
        sessao.add(
            Usuario(
                email="admin@escritorio.local",
                nome="Admin",
                senha_hash=senha_hash,
                salt=salt,
                admin=True,
            )
        )
        sessao.commit()

    servico = ApiKeyService(referencia)
    ids["chave_de_a"] = servico.criar("Integrador do A", escritorio_id=ids["escritorio_a"])["chave"]
    ids["chave_global"] = servico.criar("Chave legada, sem dono")["chave"]
    ids["referencia"] = referencia

    init_auth(referencia)
    init_audit_service(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    ids["cliente"] = TestClient(app)
    return ids


def _nomes(resposta) -> list[str]:
    dados = resposta.json()
    return sorted(x["nome"] for x in (dados.get("dados") or dados))


# ═══════════════════════════════════════════════════════════════════════════
# 1. A chave com dono não atravessa escritório
# ═══════════════════════════════════════════════════════════════════════════


class TestEscopoDeLeitura:
    def test_chave_do_a_nao_ve_empresa_do_b(self, cenario):
        """O defeito demonstrado na revisão."""
        resposta = cenario["cliente"].get(
            "/api/v1/empresas", headers={"X-API-Key": cenario["chave_de_a"]}
        )

        assert resposta.status_code == 200
        assert _nomes(resposta) == ["Cliente do A"]

    def test_o_total_tambem_e_escopado(self, cenario):
        """Total sem escopo revelaria quantas empresas o vizinho tem."""
        resposta = cenario["cliente"].get(
            "/api/v1/empresas", headers={"X-API-Key": cenario["chave_de_a"]}
        )

        assert resposta.json()["total"] == 1

    def test_chave_do_a_nao_ve_ecd_do_b_na_listagem(self, cenario):
        resposta = cenario["cliente"].get(
            "/api/v1/ecds", headers={"X-API-Key": cenario["chave_de_a"]}
        )

        assert resposta.status_code == 200
        corpo = resposta.json()
        assert corpo["total"] == 1
        assert all("do A" in linha["empresa_nome"] for linha in corpo["dados"])

    def test_chave_legada_sem_dono_continua_vendo_tudo(self, cenario):
        """Invalidar chave existente quebraria integração em produção."""
        resposta = cenario["cliente"].get(
            "/api/v1/empresas", headers={"X-API-Key": cenario["chave_global"]}
        )

        assert _nomes(resposta) == ["Cliente do A", "Cliente do B"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. Escopar a listagem sem escopar o detalhe seria cosmético
# ═══════════════════════════════════════════════════════════════════════════


ROTAS_DE_ECD = [
    "",
    "/balanco",
    "/dre",
    "/dfc",
    "/diario",
    "/kpis",
    "/notas",
    "/validar",
]


class TestEscopoPorId:
    @pytest.mark.parametrize("sufixo", ROTAS_DE_ECD)
    def test_chave_do_a_nao_alcanca_ecd_do_b_por_id(self, cenario, sufixo):
        """Quem quisesse a escrituração do vizinho pediria o id direto."""
        resposta = cenario["cliente"].get(
            f"/api/v1/ecds/{cenario['ecd_b']}{sufixo}",
            headers={"X-API-Key": cenario["chave_de_a"]},
        )

        assert resposta.status_code == 404, (
            f"/ecds/{{id}}{sufixo} entregou a ECD de outro escritório "
            f"(status {resposta.status_code})"
        )

    @pytest.mark.parametrize("sufixo", ROTAS_DE_ECD)
    def test_a_propria_ecd_continua_acessivel(self, cenario, sufixo):
        """A proteção não pode fechar o acesso legítimo."""
        resposta = cenario["cliente"].get(
            f"/api/v1/ecds/{cenario['ecd_a']}{sufixo}",
            headers={"X-API-Key": cenario["chave_de_a"]},
        )

        assert resposta.status_code == 200, f"/ecds/{{id}}{sufixo} bloqueou a própria ECD"

    def test_responde_404_e_nao_403(self, cenario):
        """403 confirmaria que a ECD existe e é de outro — já é informação."""
        resposta = cenario["cliente"].get(
            f"/api/v1/ecds/{cenario['ecd_b']}", headers={"X-API-Key": cenario["chave_de_a"]}
        )

        assert resposta.status_code == 404
        assert "não encontrada" in resposta.json()["detail"].lower()

    def test_ecd_inexistente_responde_igual(self, cenario):
        """Mesma resposta para "não existe" e "é de outro"."""
        do_outro = cenario["cliente"].get(
            f"/api/v1/ecds/{cenario['ecd_b']}", headers={"X-API-Key": cenario["chave_de_a"]}
        )
        inexistente = cenario["cliente"].get(
            "/api/v1/ecds/999999", headers={"X-API-Key": cenario["chave_de_a"]}
        )

        assert do_outro.status_code == inexistente.status_code
        assert do_outro.json() == inexistente.json()

    def test_toda_rota_de_ecd_passa_pela_dependencia(self):
        """A trava estrutural: são nove rotas, e a décima esqueceria.

        Verificação por AST, não por requisição: um teste por rota só cobre as
        rotas que existem hoje.
        """
        fonte = (REPO / "src" / "api" / "routes.py").read_text("utf-8")
        arvore = ast.parse(fonte)
        sem_escopo = []
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorador in no.decorator_list:
                if not isinstance(decorador, ast.Call) or not decorador.args:
                    continue
                alvo = decorador.args[0]
                if not isinstance(alvo, ast.Constant) or "{ecd_id}" not in str(alvo.value):
                    continue
                corpo = ast.get_source_segment(fonte, no) or ""
                assinatura = corpo[: corpo.index("):") + 2] if "):" in corpo else corpo
                if "Depends(ecd_autorizada)" not in assinatura:
                    sem_escopo.append(f"{alvo.value} ({no.name})")
        assert not sem_escopo, (
            f"rota com /{{ecd_id}} sem `Depends(ecd_autorizada)`: {sem_escopo} — "
            "ela entrega a escrituração de qualquer escritório a quem souber o id"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Administrar a instância não é trabalho de integração
# ═══════════════════════════════════════════════════════════════════════════


ROTAS_DE_ADMINISTRACAO = [
    ("get", "/api/v1/api-keys", None),
    ("post", "/api/v1/api-keys", {"nome": "chave criada pelo integrador"}),
    ("delete", "/api/v1/api-keys/1", None),
    ("get", "/api/v1/api-keys/1/rate-limit", None),
    ("put", "/api/v1/api-keys/1/rate-limit", {"limite": 999999, "janela": 1}),
    ("delete", "/api/v1/api-keys/1/rate-limit", None),
    ("get", "/api/v1/api-keys/1/rate-limit/status", None),
]


class TestAdministracaoExigeSessao:
    @pytest.mark.parametrize("metodo,rota,corpo", ROTAS_DE_ADMINISTRACAO)
    def test_api_key_nao_administra(self, cenario, metodo, rota, corpo):
        """A cadeia de escalonamento inteira, rota por rota."""
        chamada = getattr(cenario["cliente"], metodo)
        kwargs = {"headers": {"X-API-Key": cenario["chave_global"]}}
        if corpo is not None:
            kwargs["json"] = corpo

        resposta = chamada(rota, **kwargs)

        assert resposta.status_code == 401, (
            f"{metodo.upper()} {rota} aceitou API Key — quem recebe a chave "
            "administra a instância (status {resposta.status_code})"
        )

    def test_chave_com_dono_tambem_nao_administra(self, cenario):
        """Ter dono não é permissão de administrar."""
        resposta = cenario["cliente"].post(
            "/api/v1/api-keys",
            json={"nome": "tentativa"},
            headers={"X-API-Key": cenario["chave_de_a"]},
        )

        assert resposta.status_code == 401

    def test_admin_de_sessao_administra(self, cenario):
        """A proteção não pode fechar o caminho legítimo."""
        cliente = cenario["cliente"]
        cliente.post("/api/login", data={"email": "admin@escritorio.local", "senha": "senha-admin"})

        resposta = cliente.get("/api/v1/api-keys")

        assert resposta.status_code == 200
        assert resposta.json()["total"] >= 2

    def test_usuario_comum_nao_administra(self, cenario):
        cliente = cenario["cliente"]
        # Pelo administrador, não pelo `/api/register`: o registro público
        # fecha depois do primeiro usuário (ver tests/test_registro_publico.py).
        from src.auth import AuthService

        AuthService(db_path=cenario["referencia"]).criar_usuario(
            email="comum@escritorio.local", nome="Comum", senha="senha123"
        )
        cliente.post("/api/login", data={"email": "comum@escritorio.local", "senha": "senha123"})

        resposta = cliente.get("/api/v1/api-keys")

        assert resposta.status_code == 403

    def test_toda_rota_de_api_key_exige_sessao(self):
        """Trava estrutural, pelo mesmo motivo da anterior."""
        fonte = (REPO / "src" / "api" / "routes.py").read_text("utf-8")
        arvore = ast.parse(fonte)
        desprotegidas = []
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorador in no.decorator_list:
                if not isinstance(decorador, ast.Call) or not decorador.args:
                    continue
                alvo = decorador.args[0]
                if not isinstance(alvo, ast.Constant) or "api-keys" not in str(alvo.value):
                    continue
                corpo = ast.get_source_segment(fonte, no) or ""
                if "requer_admin_de_sessao" not in corpo[:600]:
                    desprotegidas.append(f"{alvo.value} ({no.name})")
        assert not desprotegidas, (
            f"rota de /api-keys sem `requer_admin_de_sessao`: {desprotegidas} — "
            "uma API Key administraria a instância por ela"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. O dono da chave chega ao banco
# ═══════════════════════════════════════════════════════════════════════════


class TestDonoDaChave:
    def test_chave_criada_com_dono_guarda_o_dono(self, cenario):
        servico = ApiKeyService(cenario["referencia"])
        criada = servico.criar("Com dono", escritorio_id=cenario["escritorio_b"])

        assert criada["escritorio_id"] == cenario["escritorio_b"]

    def test_chave_criada_sem_dono_fica_nula(self, cenario):
        """O default preserva o comportamento das chaves existentes."""
        criada = ApiKeyService(cenario["referencia"]).criar("Sem dono")

        assert criada["escritorio_id"] is None

    def test_coluna_existe_no_schema_migrado(self, cenario):
        engine = criar_engine(url=cenario["referencia"])
        try:
            colunas = {c["name"] for c in sqlalchemy.inspect(engine).get_columns("api_keys")}
        finally:
            engine.dispose()

        assert "escritorio_id" in colunas
