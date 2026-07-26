"""Testes da Fase 13 — Rate Limiting, Logs de Auditoria e E2E.

Cobre:
  1. Rate Limiting — RateLimiter, RateLimitService, RateLimitConfig
  2. Logs de Auditoria — AuditService, AuditLog, filtros, estatísticas
  3. Testes E2E — fluxo completo via FastAPI TestClient (dashboard, API, auditoria)
"""

import datetime
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from src.db.models import (
    ApiKey,
    AuditLog,
    ECD,
    Empresa,
    Escritorio,
    Lancamento,
    Partida,
    PlanoConta,
    RateLimitConfig,
    SaldoPeriodico,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.db.repository import Repository
from src.ratelimit import (
    RateLimiter,
    RateLimitService,
    RateLimitInfo,
    DEFAULT_LIMITE,
    DEFAULT_JANELA,
    get_limiter,
    init_limiter,
)
from src.audit import AuditService, get_audit_service, init_audit_service
from src.api import ApiKeyService, gerar_api_key, _hash_key


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path():
    """Cria banco temporário para cada teste."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="sped_test_fase13_")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def session(db_path):
    engine = criar_engine(db_path)
    init_db(engine)
    sess = get_session(engine)
    yield sess
    sess.close()


@pytest.fixture
def api_key_record(db_path):
    """Cria uma API Key no banco e retorna o registro."""
    svc = ApiKeyService(db_path)
    result = svc.criar(nome="Test Key")
    engine = criar_engine(db_path)
    init_db(engine)
    sess = get_session(engine)
    try:
        key = sess.execute(
            select(ApiKey).where(ApiKey.id == result["id"])
        ).scalar_one()
        return key, result["chave"]
    finally:
        sess.close()


# ═══════════════════════════════════════════════════════════════════════════
# Testes: Rate Limiting
# ═══════════════════════════════════════════════════════════════════════════


class TestRateLimiter:
    """Testes do RateLimiter com janela deslizante."""

    def test_primeira_requisicao_permitida(self, db_path, api_key_record):
        """Primeira requisição é sempre permitida."""
        key, _ = api_key_record
        limiter = RateLimiter(db_path)
        permitido, info = limiter.verificar(key.id)
        assert permitido is True
        assert info.limite_excedido is False
        assert info.requisicoes_restantes == DEFAULT_LIMITE - 1

    def test_limite_padrao_sem_config(self, db_path, api_key_record):
        """Sem configuração, usa o default (100/60s)."""
        key, _ = api_key_record
        limiter = RateLimiter(db_path)
        _, info = limiter.verificar(key.id)
        assert info.limite == DEFAULT_LIMITE
        assert info.janela == DEFAULT_JANELA

    def test_bloqueia_apos_exceder_limite(self, db_path, api_key_record):
        """Bloqueia após exceder o limite configurado."""
        key, _ = api_key_record

        # Configura limite baixo: 3 req / 60s
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=3, janela=60)

        limiter = RateLimiter(db_path)

        # 3 requisições permitidas
        for i in range(3):
            permitido, info = limiter.verificar(key.id)
            assert permitido is True, f"Requisição {i+1} deveria ser permitida"

        # 4ª deve ser bloqueada
        permitido, info = limiter.verificar(key.id)
        assert permitido is False
        assert info.limite_excedido is True
        assert info.requisicoes_restantes == 0

    def test_reset_apos_janela(self, db_path, api_key_record):
        """Contador reseta após a janela expirar."""
        key, _ = api_key_record

        # Configura janela muito curta: 2 req / 1s
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=2, janela=1)

        limiter = RateLimiter(db_path)

        # 2 requisições permitidas
        assert limiter.verificar(key.id)[0] is True
        assert limiter.verificar(key.id)[0] is True
        # 3ª bloqueada
        assert limiter.verificar(key.id)[0] is False

        # Espera a janela expirar
        time.sleep(1.2)

        # Após reset, deve permitir novamente
        permitido, info = limiter.verificar(key.id)
        assert permitido is True
        assert info.requisicoes_restantes == 1

    def test_get_info_sem_incrementar(self, db_path, api_key_record):
        """get_info retorna status sem incrementar contador."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=5, janela=60)

        limiter = RateLimiter(db_path)

        # Faz 2 requisições
        limiter.verificar(key.id)
        limiter.verificar(key.id)

        # get_info não incrementa
        info = limiter.get_info(key.id)
        assert info.requisicoes_restantes == 3  # 5 - 2 = 3

        # Confirma que não incrementou
        info2 = limiter.get_info(key.id)
        assert info2.requisicoes_restantes == 3

    def test_reset_manual(self, db_path, api_key_record):
        """Reset manual limpa o contador."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=2, janela=60)

        limiter = RateLimiter(db_path)
        limiter.verificar(key.id)
        limiter.verificar(key.id)
        assert limiter.verificar(key.id)[0] is False

        # Reset
        limiter.reset(key.id)
        assert limiter.verificar(key.id)[0] is True

    def test_isolamento_por_api_key(self, db_path):
        """Cada API Key tem seu próprio contador."""
        svc = ApiKeyService(db_path)
        r1 = svc.criar(nome="Key A")
        r2 = svc.criar(nome="Key B")

        rl_svc = RateLimitService(db_path)
        rl_svc.configurar(r1["id"], limite=2, janela=60)
        rl_svc.configurar(r2["id"], limite=2, janela=60)

        limiter = RateLimiter(db_path)

        # Esgota Key A
        limiter.verificar(r1["id"])
        limiter.verificar(r1["id"])
        assert limiter.verificar(r1["id"])[0] is False

        # Key B ainda tem crédito
        assert limiter.verificar(r2["id"])[0] is True


class TestRateLimitService:
    """Testes do serviço de configuração de rate limit."""

    def test_configurar_novo(self, db_path, api_key_record):
        """Configura rate limit para uma API Key."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        result = svc.configurar(key.id, limite=50, janela=120)

        assert result["api_key_id"] == key.id
        assert result["limite"] == 50
        assert result["janela"] == 120
        assert result["id"] > 0

    def test_configurar_atualizar_existente(self, db_path, api_key_record):
        """Atualiza configuração existente."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=100, janela=60)
        result = svc.configurar(key.id, limite=200, janela=30)

        assert result["limite"] == 200
        assert result["janela"] == 30

    def test_obter_config(self, db_path, api_key_record):
        """Obtém configuração de rate limit."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=30, janela=45)

        config = svc.obter(key.id)
        assert config is not None
        assert config["limite"] == 30
        assert config["janela"] == 45

    def test_obter_inexistente(self, db_path, api_key_record):
        """Obtém None quando não há configuração."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        assert svc.obter(key.id) is None

    def test_remover_config(self, db_path, api_key_record):
        """Remove configuração de rate limit."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        svc.configurar(key.id, limite=50, janela=60)

        assert svc.remover(key.id) is True
        assert svc.obter(key.id) is None

    def test_remover_inexistente(self, db_path, api_key_record):
        """Remover configuração inexistente retorna False."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        assert svc.remover(key.id) is False

    def test_listar_configs(self, db_path):
        """Lista todas as configurações."""
        svc_api = ApiKeyService(db_path)
        k1 = svc_api.criar(nome="K1")
        k2 = svc_api.criar(nome="K2")

        svc = RateLimitService(db_path)
        svc.configurar(k1["id"], limite=100, janela=60)
        svc.configurar(k2["id"], limite=200, janela=30)

        configs = svc.listar()
        assert len(configs) == 2

    def test_validacao_limite_invalido(self, db_path, api_key_record):
        """Limite inválido levanta ValueError."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        with pytest.raises(ValueError):
            svc.configurar(key.id, limite=0, janela=60)

    def test_validacao_janela_invalida(self, db_path, api_key_record):
        """Janela inválida levanta ValueError."""
        key, _ = api_key_record
        svc = RateLimitService(db_path)
        with pytest.raises(ValueError):
            svc.configurar(key.id, limite=100, janela=0)


class TestRateLimitModel:
    """Testes do modelo RateLimitConfig."""

    def test_criar_modelo(self, session, api_key_record):
        """Cria registro de RateLimitConfig no banco."""
        key, _ = api_key_record
        config = RateLimitConfig(
            api_key_id=key.id,
            limite=500,
            janela=300,
        )
        session.add(config)
        session.commit()

        assert config.id > 0
        assert config.limite == 500
        assert config.janela == 300

    def test_unique_api_key_id(self, session, api_key_record):
        """api_key_id deve ser único."""
        key, _ = api_key_record
        c1 = RateLimitConfig(api_key_id=key.id, limite=100, janela=60)
        session.add(c1)
        session.commit()

        c2 = RateLimitConfig(api_key_id=key.id, limite=200, janela=30)
        session.add(c2)
        with pytest.raises(Exception):
            session.commit()
        session.rollback()


# ═══════════════════════════════════════════════════════════════════════════
# Testes: Logs de Auditoria
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditService:
    """Testes do AuditService."""

    def test_registrar_basico(self, db_path):
        """Registra um log de auditoria básico."""
        svc = AuditService(db_path)
        log = svc.registrar(
            acao="auth.login",
            recurso="/login",
            usuario_email="user@test.com",
            metodo="POST",
            ip="192.168.1.1",
            status_code=200,
        )
        assert log.id > 0
        assert log.acao == "auth.login"
        assert log.recurso == "/login"
        assert log.ip == "192.168.1.1"

    def test_registrar_com_detalhes(self, db_path):
        """Registra log com detalhes em JSON."""
        svc = AuditService(db_path)
        log = svc.registrar(
            acao="ecd.upload",
            recurso="ECD #1",
            detalhes={"contas": 100, "lancamentos": 500},
        )
        assert log.get_detalhes() == {"contas": 100, "lancamentos": 500}

    def test_registrar_com_usuario(self, db_path):
        """Registra log vinculado a usuário."""
        # Cria usuário
        from src.auth import AuthService
        auth = AuthService(db_path)
        usuario = auth.registrar(email="test@audit.com", nome="Test", senha="senha123")

        svc = AuditService(db_path)
        log = svc.registrar(
            acao="auth.login",
            recurso="Login",
            usuario_id=usuario.id,
            usuario_email=usuario.email,
        )
        assert log.usuario_id == usuario.id
        assert log.usuario_email == "test@audit.com"

    def test_listar_sem_filtro(self, db_path):
        """Lista todos os logs sem filtro."""
        svc = AuditService(db_path)
        for i in range(5):
            svc.registrar(acao="api.acesso", recurso=f"/api/v1/test/{i}")

        logs = svc.listar()
        assert len(logs) == 5

    def test_listar_filtro_acao(self, db_path):
        """Filtra logs por ação."""
        svc = AuditService(db_path)
        svc.registrar(acao="auth.login", recurso="/login")
        svc.registrar(acao="ecd.upload", recurso="ECD #1")
        svc.registrar(acao="auth.login", recurso="/login2")

        logs = svc.listar(acao="auth.login")
        assert len(logs) == 2
        assert all(l["acao"] == "auth.login" for l in logs)

    def test_listar_filtro_usuario(self, db_path):
        """Filtra logs por usuário."""
        svc = AuditService(db_path)
        svc.registrar(acao="test", recurso="r1", usuario_id=1)
        svc.registrar(acao="test", recurso="r2", usuario_id=2)
        svc.registrar(acao="test", recurso="r3", usuario_id=1)

        logs = svc.listar(usuario_id=1)
        assert len(logs) == 2
        assert all(l["usuario_id"] == 1 for l in logs)

    def test_listar_paginacao(self, db_path):
        """Testa paginação na listagem."""
        svc = AuditService(db_path)
        for i in range(10):
            svc.registrar(acao="test", recurso=f"r{i}")

        pagina1 = svc.listar(limite=5, offset=0)
        pagina2 = svc.listar(limite=5, offset=5)
        assert len(pagina1) == 5
        assert len(pagina2) == 5
        # Não deve haver sobreposição
        ids1 = {l["id"] for l in pagina1}
        ids2 = {l["id"] for l in pagina2}
        assert ids1.isdisjoint(ids2)

    def test_contar(self, db_path):
        """Conta total de logs."""
        svc = AuditService(db_path)
        svc.registrar(acao="a", recurso="r1")
        svc.registrar(acao="b", recurso="r2")
        svc.registrar(acao="a", recurso="r3")

        assert svc.contar() == 3
        assert svc.contar(acao="a") == 2
        assert svc.contar(acao="b") == 1
        assert svc.contar(acao="c") == 0

    def test_estatisticas(self, db_path):
        """Gera estatísticas agregadas."""
        svc = AuditService(db_path)
        svc.registrar(acao="auth.login", recurso="/login", status_code=200)
        svc.registrar(acao="auth.login", recurso="/login", status_code=401)
        svc.registrar(acao="ecd.upload", recurso="ECD #1", status_code=200)
        svc.registrar(acao="api.acesso", recurso="/api/v1/empresas", status_code=200, ip="10.0.0.1")
        svc.registrar(acao="api.acesso", recurso="/api/v1/empresas", status_code=200, ip="10.0.0.2")

        stats = svc.estatisticas(horas=24)
        assert stats["total_eventos"] == 5
        assert stats["por_acao"]["auth.login"] == 2
        assert stats["por_acao"]["ecd.upload"] == 1
        assert stats["erros"] == 1  # 401
        assert stats["ips_unicos"] == 2

    def test_limpar_antigos(self, db_path):
        """Remove logs antigos."""
        svc = AuditService(db_path)

        # Cria log antigo (modifica data diretamente no banco)
        log = svc.registrar(acao="old", recurso="old")
        engine = criar_engine(db_path)
        init_db(engine)
        sess = get_session(engine)
        try:
            db_log = sess.get(AuditLog, log.id)
            db_log.criado_em = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100)
            sess.commit()
        finally:
            sess.close()

        # Cria log recente
        svc.registrar(acao="new", recurso="new")

        # Limpa logs > 30 dias
        removidos = svc.limpar_antigos(dias=30)
        assert removidos == 1

        # Verifica que só ficou o recente
        logs = svc.listar()
        assert len(logs) == 1
        assert logs[0]["acao"] == "new"

    def test_get_set_detalhes(self, db_path):
        """Testa serialização de detalhes."""
        svc = AuditService(db_path)
        log = svc.registrar(
            acao="test",
            recurso="test",
            detalhes={"key": "value", "number": 42, "nested": {"a": 1}},
        )

        detalhes = log.get_detalhes()
        assert detalhes["key"] == "value"
        assert detalhes["number"] == 42
        assert detalhes["nested"]["a"] == 1


class TestAuditModel:
    """Testes do modelo AuditLog."""

    def test_criar_modelo(self, session):
        """Cria registro de AuditLog diretamente."""
        log = AuditLog(
            acao="api.acesso",
            recurso="/api/v1/empresas",
            metodo="GET",
            ip="127.0.0.1",
            status_code=200,
        )
        session.add(log)
        session.commit()

        assert log.id > 0
        assert log.acao == "api.acesso"
        assert log.criado_em is not None

    def test_detalhes_json(self, session):
        """Testa serialização JSON dos detalhes."""
        log = AuditLog(acao="test", recurso="test")
        log.set_detalhes({"foo": "bar", "num": 123})
        session.add(log)
        session.commit()

        assert log.get_detalhes() == {"foo": "bar", "num": 123}

    def test_detalhes_vazio(self, session):
        """Detalhes vazios retornam dict vazio."""
        log = AuditLog(acao="test", recurso="test")
        session.add(log)
        session.commit()

        assert log.get_detalhes() == {}

    def test_detalhes_json_invalido(self, session):
        """Detalhes com JSON inválido retornam dict vazio."""
        log = AuditLog(acao="test", recurso="test", detalhes="not json{")
        session.add(log)
        session.commit()

        assert log.get_detalhes() == {}


# ═══════════════════════════════════════════════════════════════════════════
# Testes: E2E — Fluxo completo via FastAPI TestClient
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EAuth:
    """Testes E2E de autenticação."""

    def test_registro_e_login(self, db_path):
        """Fluxo completo: registro → login → acesso → logout."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app

        # Inicializa serviços com o DB de teste
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        client = TestClient(app)

        # Registro
        resp = client.post("/api/register", data={
            "email": "e2e@test.com",
            "nome": "E2E User",
            "senha": "senha123",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Login
        resp = client.post("/api/login", data={
            "email": "e2e@test.com",
            "senha": "senha123",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Acessa dashboard
        resp = client.get("/")
        assert resp.status_code == 200

        # Logout
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code == 302

        # Após logout, dashboard redireciona
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302

        del os.environ["SPED_HUB_DB"]

    def test_login_falho_registra_auditoria(self, db_path):
        """Login falho é registrado na auditoria."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        client = TestClient(app)

        # Login com senha errada
        resp = client.post("/api/login", data={
            "email": "naoexiste@test.com",
            "senha": "errada",
        })
        assert resp.status_code == 401

        # Verifica auditoria
        audit = AuditService(db_path)
        logs = audit.listar(acao="auth.login")
        assert len(logs) >= 1
        assert logs[0]["status_code"] == 401

        del os.environ["SPED_HUB_DB"]


class TestE2EAPI:
    """Testes E2E da API REST com API Key."""

    def test_api_com_api_key(self, db_path):
        """Acesso à API com API Key válida."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        # Cria API Key
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="E2E Key")
        chave = result["chave"]

        client = TestClient(app)

        # Health check (não precisa de auth)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["version"] == "0.14.0"

        # Lista empresas (precisa de API Key)
        resp = client.get("/api/v1/empresas", headers={"X-API-Key": chave})
        assert resp.status_code == 200
        assert "dados" in resp.json()

        # Sem API Key → 401
        resp = client.get("/api/v1/empresas")
        assert resp.status_code == 401

        # API Key inválida → 403
        resp = client.get("/api/v1/empresas", headers={"X-API-Key": "spd_invalida"})
        assert resp.status_code == 403

        del os.environ["SPED_HUB_DB"]

    def test_api_rate_limit_429(self, db_path):
        """API retorna 429 quando rate limit é excedido."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        # Cria API Key e configura rate limit baixo
        svc_api = ApiKeyService(db_path)
        result = svc_api.criar(nome="Rate Limited Key")
        chave = result["chave"]
        key_id = result["id"]

        svc_rl = RateLimitService(db_path)
        svc_rl.configurar(key_id, limite=2, janela=60)

        client = TestClient(app)

        # 2 requisições permitidas
        resp1 = client.get("/api/v1/empresas", headers={"X-API-Key": chave})
        assert resp1.status_code == 200

        resp2 = client.get("/api/v1/empresas", headers={"X-API-Key": chave})
        assert resp2.status_code == 200

        # 3ª deve retornar 429
        resp3 = client.get("/api/v1/empresas", headers={"X-API-Key": chave})
        assert resp3.status_code == 429
        assert "Rate limit" in resp3.json()["detail"]

        # Verifica headers
        assert resp3.headers.get("X-RateLimit-Limit") == "2"
        assert resp3.headers.get("X-RateLimit-Remaining") == "0"

        del os.environ["SPED_HUB_DB"]

    def test_api_auditoria_registrada(self, db_path):
        """Acessos à API são registrados na auditoria."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        # Cria API Key
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="Audit Test Key")
        chave = result["chave"]
        key_id = result["id"]

        client = TestClient(app)

        # Faz algumas requisições
        client.get("/api/v1/empresas", headers={"X-API-Key": chave})
        client.get("/api/v1/ecds", headers={"X-API-Key": chave})

        # Verifica auditoria
        audit = AuditService(db_path)
        logs = audit.listar()
        # Pelo menos os eventos de criação da API key + acessos
        assert len(logs) >= 1

        # Verifica que há log de criação de API Key
        apikey_logs = audit.listar(acao="apikey.create")
        assert len(apikey_logs) >= 1

        del os.environ["SPED_HUB_DB"]


class TestE2EAuditDashboard:
    """Testes E2E do dashboard de auditoria."""

    def test_pagina_auditoria_requer_auth(self, db_path):
        """Página de auditoria requer autenticação."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        client = TestClient(app)

        # Sem auth → redirect
        resp = client.get("/auditoria", follow_redirects=False)
        assert resp.status_code == 302

        del os.environ["SPED_HUB_DB"]

    def test_api_audit_logs_requer_auth(self, db_path):
        """Endpoint de logs de auditoria requer autenticação."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        client = TestClient(app)

        # Sem auth → 401
        resp = client.get("/api/audit/logs")
        assert resp.status_code == 401

        del os.environ["SPED_HUB_DB"]

    def test_fluxo_auditoria_completo(self, db_path):
        """Fluxo completo: login → acessa auditoria → vê logs."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        client = TestClient(app)

        # Registro + Login
        client.post("/api/register", data={
            "email": "audit@test.com",
            "nome": "Audit User",
            "senha": "senha123",
        })
        client.post("/api/login", data={
            "email": "audit@test.com",
            "senha": "senha123",
        })

        # Acessa página de auditoria
        resp = client.get("/auditoria")
        assert resp.status_code == 200
        assert "Auditoria" in resp.text

        # Busca logs via API
        resp = client.get("/api/audit/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "dados" in data
        # Pelo menos o log de login
        assert data["total"] >= 1

        # Busca estatísticas
        resp = client.get("/api/audit/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert "total_eventos" in stats
        assert stats["total_eventos"] >= 1

        del os.environ["SPED_HUB_DB"]


class TestE2ERateLimitConfig:
    """Testes E2E de configuração de rate limit via API."""

    def test_configurar_rate_limit_via_api(self, db_path):
        """Configura rate limit via endpoint da API."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        # Cria API Key
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="RL Config Key")
        chave = result["chave"]
        key_id = result["id"]

        client = TestClient(app)

        # Obtém config atual (default)
        resp = client.get(f"/api/v1/api-keys/{key_id}/rate-limit", headers={"X-API-Key": chave})
        assert resp.status_code == 200
        assert resp.json()["default"] is True

        # Configura rate limit
        resp = client.put(
            f"/api/v1/api-keys/{key_id}/rate-limit",
            headers={"X-API-Key": chave},
            json={"limite": 50, "janela": 120},
        )
        assert resp.status_code == 200
        assert resp.json()["limite"] == 50
        assert resp.json()["janela"] == 120

        # Obtém config atualizada
        resp = client.get(f"/api/v1/api-keys/{key_id}/rate-limit", headers={"X-API-Key": chave})
        assert resp.status_code == 200
        assert resp.json()["limite"] == 50
        assert resp.json()["default"] is False

        # Status do rate limit
        resp = client.get(f"/api/v1/api-keys/{key_id}/rate-limit/status", headers={"X-API-Key": chave})
        assert resp.status_code == 200
        assert resp.json()["limite"] == 50
        assert resp.json()["requisicoes_restantes"] <= 50

        del os.environ["SPED_HUB_DB"]

    def test_remover_rate_limit_via_api(self, db_path):
        """Remove configuração de rate limit via API."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        svc = ApiKeyService(db_path)
        result = svc.criar(nome="RL Remove Key")
        chave = result["chave"]
        key_id = result["id"]

        # Configura
        rl_svc = RateLimitService(db_path)
        rl_svc.configurar(key_id, limite=30, janela=60)

        client = TestClient(app)

        # Remove
        resp = client.delete(f"/api/v1/api-keys/{key_id}/rate-limit", headers={"X-API-Key": chave})
        assert resp.status_code == 200

        # Volta ao default
        resp = client.get(f"/api/v1/api-keys/{key_id}/rate-limit", headers={"X-API-Key": chave})
        assert resp.json()["default"] is True

        del os.environ["SPED_HUB_DB"]
