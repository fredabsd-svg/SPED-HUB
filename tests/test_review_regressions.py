"""Regressões da revisão geral anterior à Fase 16."""

from __future__ import annotations

import asyncio
import datetime
import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from src.api import ApiKeyService, validar_requisicao_api
from src.audit import init_audit_service
from src.auth import init_auth
from src.ratelimit import init_limiter
from src.uploads import save_upload


def _slow_progress_handler(payload, update_progress):
    update_progress(42.0, "quarenta e dois por cento")
    time.sleep(0.8)
    return {"ok": True}


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "review.db")


def _prepare_app(db_path: str):
    os.environ["SPED_HUB_DB"] = db_path
    from src.dashboard.app import app

    init_auth(db_path)
    init_audit_service(db_path)
    init_limiter(db_path)
    return app


def _login(client: TestClient, email: str = "review@test.local") -> None:
    response = client.post(
        "/api/register",
        data={"email": email, "nome": "Review User", "senha": "senha123"},
    )
    assert response.status_code == 200
    response = client.post(
        "/api/login",
        data={"email": email, "senha": "senha123"},
    )
    assert response.status_code == 200


class TestDashboardSecurityRegression:
    def test_internal_apis_require_session(self, db_path: str):
        client = TestClient(_prepare_app(db_path))

        for path in ("/api/ecds", "/api/jobs", "/api/cache/stats"):
            response = client.get(path)
            assert response.status_code == 401, path

    def test_internal_api_accepts_authenticated_session(self, db_path: str):
        client = TestClient(_prepare_app(db_path))
        _login(client)

        response = client.get("/api/ecds")
        assert response.status_code == 200
        assert response.json() == []

    def test_graphql_requires_api_key_or_session(self, db_path: str):
        client = TestClient(_prepare_app(db_path))
        query = {"query": "{ health }"}

        response = client.post("/api/v2/graphql", json=query)
        assert response.status_code == 401

        api_key = ApiKeyService(db_path).criar("GraphQL Review")["chave"]
        response = client.post(
            "/api/v2/graphql",
            json=query,
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 200
        assert response.json()["data"]["health"] == "ok"

    def test_dashboard_has_no_duplicate_routes(self, db_path: str):
        app = _prepare_app(db_path)
        routes: list[tuple[str, str]] = []
        for route in app.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if not path:
                continue
            routes.extend((method, path) for method in methods)

        assert len(routes) == len(set(routes))


class TestUploadSecurityRegression:
    def test_filename_traversal_is_never_used_as_path(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SPED_HUB_UPLOAD_DIR", str(tmp_path / "uploads"))
        sentinel = tmp_path / "escape.txt"
        upload = UploadFile(
            file=__import__("io").BytesIO(b"conteudo seguro"),
            filename="../escape.txt",
            headers=Headers({"content-type": "text/plain"}),
        )

        saved = asyncio.run(save_upload(upload, (".txt",)))
        try:
            assert saved.original_name == "escape.txt"
            assert saved.path.parent == tmp_path / "uploads"
            assert saved.path.name != "escape.txt"
            assert not sentinel.exists()
            assert saved.path.read_bytes() == b"conteudo seguro"
        finally:
            saved.path.unlink(missing_ok=True)

    def test_upload_limit_removes_partial_file(self, tmp_path: Path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        monkeypatch.setenv("SPED_HUB_UPLOAD_DIR", str(upload_dir))
        monkeypatch.setenv("SPED_HUB_MAX_UPLOAD_BYTES", "4")
        upload = UploadFile(
            file=__import__("io").BytesIO(b"12345"),
            filename="grande.txt",
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(save_upload(upload, (".txt",)))

        assert exc.value.status_code == 413
        assert list(upload_dir.iterdir()) == []


class TestApiKeyExpiryRegression:
    def test_future_expiring_key_is_accepted_with_sqlite(self, db_path: str):
        _prepare_app(db_path)
        key = ApiKeyService(db_path).criar("Temporaria", dias_expiracao=1)["chave"]

        class RequestStub:
            headers = {"X-API-Key": key}
            cookies = {}
            state = type("State", (), {})()
            url = type("URL", (), {"path": "/api/v1/empresas"})()
            method = "GET"
            client = None

        record = asyncio.run(validar_requisicao_api(RequestStub(), db_path))
        assert record.ativo is True

    def test_expired_key_is_rejected_with_sqlite(self, db_path: str):
        _prepare_app(db_path)
        result = ApiKeyService(db_path).criar("Expirada", dias_expiracao=1)

        from src.db.models import ApiKey, criar_engine, get_session

        session = get_session(criar_engine(db_path))
        try:
            record = session.get(ApiKey, result["id"])
            record.expira_em = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
            session.commit()
        finally:
            session.close()

        class RequestStub:
            headers = {"X-API-Key": result["chave"]}
            cookies = {}
            state = type("State", (), {})()
            url = type("URL", (), {"path": "/api/v1/empresas"})()
            method = "GET"
            client = None

        with pytest.raises(HTTPException) as exc:
            asyncio.run(validar_requisicao_api(RequestStub(), db_path))
        assert exc.value.status_code == 403
        assert "expirada" in exc.value.detail.lower()


class TestRuntimeServicesRegression:
    def test_cached_prefix_can_be_invalidated(self, monkeypatch):
        import src.cache as cache_module
        from src.cache import CacheService, cached

        service = CacheService(max_entries=10)
        monkeypatch.setattr(cache_module, "_cache_service", service)
        calls = 0

        @cached(ttl=60, prefix="dashboard:")
        def calculate(value):
            nonlocal calls
            calls += 1
            return value * 2

        assert calculate(3) == 6
        assert calculate(3) == 6
        assert calls == 1
        assert service.invalidate_prefix("dashboard:") == 1
        assert calculate(3) == 6
        assert calls == 2

    def test_email_failure_is_recorded(self, monkeypatch):
        from src.email_service import EmailService

        service = EmailService(modo="smtp")

        def fail(_message):
            raise RuntimeError("SMTP indisponível")

        monkeypatch.setattr(service, "_smtp_send", fail)
        message = service.enviar("destino@test.local", "Falha", "Corpo", async_mode=False)

        assert message.status == "falha"
        assert service.stats()["falhas"] == 1
        history = service.historico()
        assert len(history) == 1
        assert history[0]["status"] == "falha"
        assert "SMTP indisponível" in history[0]["erro"]

    def test_email_smtp_includes_attachments(self, monkeypatch):
        import src.email_service as email_module
        from src.email_service import EmailService

        sent = []

        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self):
                pass

            def login(self, user, password):
                pass

            def send_message(self, message):
                sent.append(message)

        monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
        service = EmailService(modo="smtp")
        message = service.enviar(
            "destino@test.local",
            "Relatório",
            "Segue relatório",
            anexos=[{"nome": "../relatorio.pdf", "dados": b"%PDF-review"}],
            async_mode=False,
        )

        assert message.status == "enviado"
        attachments = [part for part in sent[0].walk() if part.get_filename()]
        assert [part.get_filename() for part in attachments] == ["relatorio.pdf"]
        assert attachments[0].get_payload(decode=True) == b"%PDF-review"

    def test_worker_exposes_running_progress_before_completion(self):
        from src.worker_queue import WorkerQueue

        queue = WorkerQueue(num_workers=1)
        queue.register_handler("slow", _slow_progress_handler)
        queue.start()
        try:
            task_id = queue.enqueue("slow")
            observed = None
            deadline = time.time() + 4
            while time.time() < deadline:
                queue.process_results()
                status = queue.status(task_id)
                if status["status"] == "running" and status["progresso"] == 42.0:
                    observed = status
                    break
                time.sleep(0.05)

            assert observed is not None
            assert observed["mensagem"] == "quarenta e dois por cento"
            assert queue.active_count() == 1
            assert queue.pending_count() == 0

            deadline = time.time() + 4
            while time.time() < deadline:
                queue.process_results()
                if queue.status(task_id)["status"] == "completed":
                    break
                time.sleep(0.05)
            assert queue.status(task_id)["status"] == "completed"
        finally:
            queue.shutdown()


class TestWebhookSecurityRegression:
    def test_webhook_rejects_local_and_private_targets(self, db_path: str):
        from src.webhooks import WebhookService

        service = WebhookService(db_path)
        invalid_urls = (
            "http://example.com/hook",
            "https://localhost/hook",
            "https://127.0.0.1/hook",
            "https://10.0.0.1/hook",
            "https://[::1]/hook",
        )
        for url in invalid_urls:
            with pytest.raises(ValueError):
                service.registrar(url=url, eventos=["ecd.importada"])

    def test_manual_retry_really_dispatches_payload(self, db_path: str):
        import json

        from src.db.models import (
            WebhookDelivery,
            criar_engine,
            get_session,
            init_db,
        )
        from src.webhooks import WebhookService

        service = WebhookService(db_path)
        webhook = service.registrar(
            url="https://example.com/hook",
            eventos=["ecd.importada"],
        )
        engine = criar_engine(db_path)
        init_db(engine)
        session = get_session(engine)
        try:
            delivery = WebhookDelivery(
                webhook_id=webhook.id,
                evento="ecd.importada",
                status="failed",
                request_body=json.dumps(
                    {
                        "evento": "ecd.importada",
                        "dados": {"ecd_id": 42},
                        "timestamp": "2026-01-01T00:00:00+00:00",
                    }
                ),
                tentativa=3,
            )
            session.add(delivery)
            session.commit()
        finally:
            session.close()

        seen = []

        async def fake_send(target, event):
            seen.append((target.id, event.tipo, event.dados))
            return True

        service._enviar_com_retry = fake_send
        result = asyncio.run(service.retry_failed(webhook_id=webhook.id))

        assert result == {
            "reenviados": 1,
            "sucessos": 1,
            "falhas": 0,
            "total_falhas": 1,
        }
        assert seen == [(webhook.id, "ecd.importada", {"ecd_id": 42})]


class TestStreamingImportRegression:
    def test_import_service_persists_fixture_in_one_parser_pass(self, tmp_path: Path):
        from src.db.models import (
            ECD,
            Lancamento,
            Partida,
            PlanoConta,
            criar_engine,
            get_session,
            init_db,
        )
        from src.ecd_importer import ECDImportService
        from src.parsers.ecd import ECDParser

        fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"

        class CountingParser(ECDParser):
            calls = 0

            def parse(self, path):
                self.calls += 1
                yield from super().parse(path)

        engine = criar_engine(str(tmp_path / "stream.db"))
        init_db(engine)
        session = get_session(engine)
        parser = CountingParser()
        try:
            result = ECDImportService(session, parser=parser).importar(fixture)
            assert parser.calls == 1
            assert result.contas == 23
            assert result.lancamentos == 9
            assert result.partidas == 18
            assert session.query(ECD).count() == 1
            assert session.query(PlanoConta).count() == 23
            assert session.query(Lancamento).count() == 9
            assert session.query(Partida).count() == 18
        finally:
            session.close()

    def test_efd_and_ecf_summaries_do_not_call_parse_todos(self, tmp_path: Path):
        from src.parsers.ecf import ECFParser
        from src.parsers.efd import EFDParser

        efd = tmp_path / "sample.efd"
        efd.write_text("|0000|A|B|01012024|31122024|CNPJ|EMPRESA|X|\n|M100|X|10,00|\n")
        ecf = tmp_path / "sample.ecf"
        ecf.write_text("|0000|A|B|01012024|31122024|CNPJ|EMPRESA|X|\n|N630|X|Y|10,00|\n")

        for parser, path in ((EFDParser(), efd), (ECFParser(), ecf)):
            parser.parse_todos = lambda _path: (_ for _ in ()).throw(
                AssertionError("extrair_resumo materializou todos os registros")
            )
            summary = parser.extrair_resumo(path)
            assert summary["total_registros"] == 2
