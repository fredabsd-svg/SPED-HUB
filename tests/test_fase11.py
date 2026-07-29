"""Testes da Fase 11 — Dashboard de Webhooks, Drag & Drop Layout, Multi-Tenancy."""

import asyncio
import datetime
import json
import os
import tempfile
from pathlib import Path

import pytest

from src.db.models import (
    Empresa,
    Escritorio,
    WebhookDelivery,
    WebhookRegistration,
    criar_engine,
    get_session,
    init_db,
)
from src.webhooks import EVENTOS_DISPONIVEIS, WebhookEvent, WebhookService

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_fase11():
    """Banco temporário limpo para testes da Fase 11."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    os.environ["SPED_HUB_DB"] = db_path

    engine = criar_engine(db_path)
    init_db(engine)

    yield db_path

    os.unlink(db_path)


@pytest.fixture
def db_com_escritorio():
    """Banco com escritório e empresa para testes multi-tenancy."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    os.environ["SPED_HUB_DB"] = db_path

    engine = criar_engine(db_path)
    init_db(engine)
    session = get_session(engine)

    esc = Escritorio(nome="Escritório Teste", slug="teste")
    session.add(esc)
    session.commit()

    emp = Empresa(cnpj="12345678000199", nome="Empresa Teste", escritorio_id=esc.id)
    session.add(emp)
    session.commit()

    empresa_id = emp.id
    escritorio_id = esc.id
    session.close()

    yield db_path, empresa_id, escritorio_id

    os.unlink(db_path)


# ── Testes: Webhook Delivery Model ───────────────────────────────────────────


class TestWebhookDeliveryModel:

    def test_criar_delivery(self, db_fase11):
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            wh = WebhookRegistration(
                url="https://example.com/hook",
                eventos=json.dumps(["ecd.importada"]),
                descricao="Teste",
            )
            session.add(wh)
            session.commit()

            delivery = WebhookDelivery(
                webhook_id=wh.id,
                evento="ecd.importada",
                status="pending",
                tentativa=1,
            )
            session.add(delivery)
            session.commit()

            assert delivery.id is not None
            assert delivery.status == "pending"
            assert delivery.webhook_id == wh.id
        finally:
            session.close()

    def test_delivery_status_flow(self, db_fase11):
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            wh = WebhookRegistration(
                url="https://example.com/hook",
                eventos=json.dumps(["ecd.importada"]),
            )
            session.add(wh)
            session.commit()

            d = WebhookDelivery(
                webhook_id=wh.id,
                evento="ecd.importada",
                status="pending",
                tentativa=1,
            )
            session.add(d)
            session.commit()

            # Transições de status
            d.status = "retrying"
            session.commit()
            assert d.status == "retrying"

            d.status = "success"
            d.status_code = 200
            d.concluido_em = datetime.datetime.now(datetime.UTC)
            session.commit()
            assert d.status == "success"
            assert d.status_code == 200
            assert d.concluido_em is not None
        finally:
            session.close()


# ── Testes: Webhook Service — Retry e Tracking ───────────────────────────────


class TestWebhookServiceFase11:

    def test_registrar_com_max_retries(self, db_fase11):
        svc = WebhookService(db_fase11)
        wh = svc.registrar(
            url="https://example.com/hook",
            eventos=["ecd.importada"],
            max_retries=5,
        )
        assert wh.max_retries == 5

    def test_get_deliveries(self, db_fase11):
        svc = WebhookService(db_fase11)
        wh = svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])

        # Cria algumas deliveries manualmente
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            for i in range(3):
                d = WebhookDelivery(
                    webhook_id=wh.id,
                    evento="ecd.importada",
                    status="success" if i < 2 else "failed",
                    tentativa=1,
                )
                session.add(d)
            session.commit()
        finally:
            session.close()

        deliveries = svc.get_deliveries(webhook_id=wh.id)
        assert len(deliveries) == 3

        # Filtro por status
        failed = svc.get_deliveries(webhook_id=wh.id, status="failed")
        assert len(failed) == 1

    def test_get_dashboard_stats(self, db_fase11):
        svc = WebhookService(db_fase11)
        svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])
        svc.registrar(url="https://b.com/hook", eventos=["ecd.validada"], ativo=False)

        stats = svc.get_dashboard_stats()
        assert stats["total_webhooks"] == 2
        assert stats["webhooks_ativos"] == 1
        assert "taxa_sucesso" in stats
        assert "deliveries_24h" in stats

    def test_retry_failed(self, db_fase11):
        svc = WebhookService(db_fase11)
        wh = svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])

        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            d = WebhookDelivery(
                webhook_id=wh.id,
                evento="ecd.importada",
                status="failed",
                tentativa=3,
                error_message="Timeout",
            )
            session.add(d)
            session.commit()
        finally:
            session.close()

        async def fake_send(webhook, event):
            return True

        svc._enviar_com_retry = fake_send
        result = asyncio.run(svc.retry_failed(webhook_id=wh.id))
        assert result["reenviados"] == 1
        assert result["sucessos"] == 1
        assert result["total_falhas"] == 1

    def test_webhook_total_falhas(self, db_fase11):
        """Verifica que total_falhas é rastreável."""
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            wh = WebhookRegistration(
                url="https://example.com/hook",
                eventos=json.dumps(["ecd.importada"]),
                total_falhas=5,
            )
            session.add(wh)
            session.commit()
            assert wh.total_falhas == 5
        finally:
            session.close()


# ── Testes: Multi-Tenancy ────────────────────────────────────────────────────


class TestMultiTenancy:

    def test_criar_escritorio(self, db_fase11):
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            esc = Escritorio(nome="Escritório Central", slug="central")
            session.add(esc)
            session.commit()
            assert esc.id is not None
            assert esc.slug == "central"
            assert esc.ativo is True
        finally:
            session.close()

    def test_escritorio_slug_unico(self, db_fase11):
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            esc1 = Escritorio(nome="A", slug="alpha")
            session.add(esc1)
            session.commit()

            esc2 = Escritorio(nome="B", slug="alpha")
            session.add(esc2)
            with pytest.raises(Exception):
                session.commit()
        finally:
            session.rollback()
            session.close()

    def test_empresa_com_escritorio(self, db_com_escritorio):
        db_path, empresa_id, escritorio_id = db_com_escritorio
        engine = criar_engine(db_path)
        session = get_session(engine)
        try:
            emp = session.get(Empresa, empresa_id)
            assert emp is not None
            assert emp.escritorio_id == escritorio_id
            assert emp.escritorio.slug == "teste"
        finally:
            session.close()

    def test_empresa_sem_escritorio(self, db_fase11):
        """Empresa sem escritório deve funcionar (retrocompatibilidade)."""
        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            emp = Empresa(cnpj="99999999000199", nome="Sem Escritório")
            session.add(emp)
            session.commit()
            assert emp.id is not None
            assert emp.escritorio_id is None
        finally:
            session.close()

    def test_usuario_com_escritorio(self, db_com_escritorio):
        db_path, empresa_id, escritorio_id = db_com_escritorio
        from src.db.models import Usuario

        engine = criar_engine(db_path)
        session = get_session(engine)
        try:
            hash_senha, salt = Usuario.hash_senha("123456")
            user = Usuario(
                email="teste@escritorio.com",
                nome="Usuário Teste",
                senha_hash=hash_senha,
                salt=salt,
                escritorio_id=escritorio_id,
            )
            session.add(user)
            session.commit()
            assert user.escritorio_id == escritorio_id
            assert user.escritorio.slug == "teste"
        finally:
            session.close()


# ── Testes: API Webhook Dashboard ────────────────────────────────────────────


class TestAPIWebhookDashboard:

    def test_dashboard_stats_endpoint(self, db_fase11):
        import asyncio

        from src.api.routes import webhook_dashboard_stats

        svc = WebhookService(db_fase11)
        svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])

        result = asyncio.run(webhook_dashboard_stats(api_key="test"))
        assert "total_webhooks" in result
        assert result["total_webhooks"] == 1

    def test_deliveries_endpoint(self, db_fase11):
        import asyncio

        from src.api.routes import listar_deliveries

        svc = WebhookService(db_fase11)
        wh = svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])

        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            d = WebhookDelivery(
                webhook_id=wh.id,
                evento="ecd.importada",
                status="success",
                tentativa=1,
            )
            session.add(d)
            session.commit()
        finally:
            session.close()

        result = asyncio.run(listar_deliveries(api_key="test"))
        assert result["total"] >= 1

    def test_retry_endpoint(self, db_fase11, monkeypatch):
        from src.api.routes import retry_webhooks

        async def fake_send(self, webhook, event):
            return True

        monkeypatch.setattr(WebhookService, "_enviar_com_retry", fake_send)

        svc = WebhookService(db_fase11)
        wh = svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])

        engine = criar_engine(db_fase11)
        init_db(engine)
        session = get_session(engine)
        try:
            d = WebhookDelivery(
                webhook_id=wh.id,
                evento="ecd.importada",
                status="failed",
                tentativa=3,
            )
            session.add(d)
            session.commit()
        finally:
            session.close()

        result = asyncio.run(retry_webhooks(api_key="test"))
        assert result["status"] == "ok"
        assert result["reenviados"] == 1


# ── Testes: Templates Fase 11 ────────────────────────────────────────────────


class TestTemplatesFase11:

    def test_webhooks_template_existe(self):
        template = (
            Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "webhooks.html"
        )
        assert template.exists()

    def test_webhooks_template_tem_chartjs(self):
        template = (
            Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "webhooks.html"
        )
        content = template.read_text()
        assert "chart.js" in content.lower()
        assert "alpinejs" in content.lower()

    def test_layout_template_tem_sortablejs(self):
        template = Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "layout.html"
        content = template.read_text()
        assert "sortablejs" in content.lower()
        assert "Sortable(" in content
        assert "drag-handle" in content

    def test_navbar_tem_webhooks(self):
        """Verifica que os templates com navbar própria têm link para Webhooks."""
        # dashboard.html e upload.html estendem base.html — verificar base.html + templates standalone
        templates_to_check = ["base.html", "comparar.html", "layout.html"]
        for tpl_name in templates_to_check:
            template = Path(__file__).parent.parent / "src" / "dashboard" / "templates" / tpl_name
            content = template.read_text()
            assert "/webhooks" in content, f"{tpl_name} não tem link para /webhooks"


# ── Testes: Webhook Service — Eventos ────────────────────────────────────────


class TestWebhookEventosFase11:

    def test_eventos_disponiveis_completos(self):
        assert len(EVENTOS_DISPONIVEIS) == 3
        assert "ecd.importada" in EVENTOS_DISPONIVEIS
        assert "ecd.validada" in EVENTOS_DISPONIVEIS
        assert "relatorio.gerado" in EVENTOS_DISPONIVEIS

    def test_webhook_event_dataclass(self):
        evt = WebhookEvent(
            tipo="relatorio.gerado",
            dados={"tipo": "balanco", "ecd_id": 42},
        )
        assert evt.tipo == "relatorio.gerado"
        assert evt.dados["ecd_id"] == 42
        assert evt.timestamp is not None
