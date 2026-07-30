"""Testes da Fase 16 — observabilidade e monitoramento operacional."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.audit import init_audit_service
from src.auth import init_auth
from src.monitoring import MetricsCollector, build_operational_snapshot, normalize_path
from src.ratelimit import init_limiter
from src.version import APP_VERSION


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="sped_fase16_")
    os.close(fd)
    yield path
    for candidate in (path, f"{path}-wal", f"{path}-shm"):
        try:
            os.unlink(candidate)
        except OSError:
            pass


@pytest.fixture
def app_client(db_path):
    os.environ["SPED_HUB_DB"] = db_path
    from src.dashboard.app import app

    init_auth(db_path)
    init_audit_service(db_path)
    init_limiter(db_path)
    client = TestClient(app)
    yield client
    os.environ.pop("SPED_HUB_DB", None)


def register_and_login(client: TestClient, email: str, name: str = "Admin F16") -> None:
    response = client.post(
        "/api/register",
        data={"email": email, "nome": name, "senha": "senha123"},
    )
    assert response.status_code == 200
    response = client.post(
        "/api/login",
        data={"email": email, "senha": "senha123"},
    )
    assert response.status_code == 200


class TestMetricsCollector:
    def test_records_latency_status_and_routes(self):
        collector = MetricsCollector()
        collector.record("GET", "/api/jobs/123", 200, 10.0)
        collector.record("GET", "/api/jobs/456", 500, 50.0)
        collector.record("POST", "/api/upload", 400, 30.0)

        snapshot = collector.snapshot(minutes=60)

        assert snapshot["requests"] == 3
        assert snapshot["errors_5xx"] == 1
        assert snapshot["errors_4xx"] == 1
        assert snapshot["error_rate_pct"] == pytest.approx(33.33, abs=0.01)
        assert snapshot["avg_latency_ms"] == 30.0
        assert snapshot["p50_latency_ms"] == 30.0
        assert snapshot["p95_latency_ms"] == 50.0
        assert snapshot["status_classes"] == {"2xx": 1, "4xx": 1, "5xx": 1}
        assert snapshot["top_routes"][0]["path"] == "/api/jobs/{id}"
        assert snapshot["top_routes"][0]["requests"] == 2

    def test_reset_clears_events(self):
        collector = MetricsCollector()
        collector.record("GET", "/health", 200, 1)
        assert collector.snapshot()["requests"] == 1
        collector.reset()
        assert collector.snapshot()["requests"] == 0

    def test_old_events_are_outside_window(self):
        collector = MetricsCollector()
        collector.record("GET", "/old", 200, 1)
        with collector._lock:
            old = collector._events.pop()
            collector._events.append(
                old.__class__(
                    timestamp=time.time() - 3600,
                    method=old.method,
                    path=old.path,
                    status_code=old.status_code,
                    duration_ms=old.duration_ms,
                )
            )
        assert collector.snapshot(minutes=1)["requests"] == 0

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/api/jobs/123", "/api/jobs/{id}"),
            ("/api/keys/abcdef0123456789", "/api/keys/{token}"),
            ("/api/v1/health", "/api/v1/health"),
        ],
    )
    def test_normalize_path(self, raw, expected):
        assert normalize_path(raw) == expected


class TestOperationalSnapshot:
    def test_snapshot_has_all_components(self, db_path):
        collector = MetricsCollector()
        collector.record("GET", "/api/v1/health", 200, 4.2)

        snapshot = build_operational_snapshot(
            collector,
            db_path=db_path,
            minutes=15,
        )

        assert snapshot["version"] == APP_VERSION
        assert snapshot["http"]["requests"] == 1
        assert snapshot["database"]["status"] == "ok"
        assert snapshot["database"]["companies"] == 0
        assert "cache" in snapshot
        assert "email" in snapshot
        assert "workers" in snapshot
        assert snapshot["process"]["rss_bytes"] > 0
        assert snapshot["process"]["threads"] >= 1


class TestMonitoringEndpoints:
    def test_monitoring_requires_authentication(self, app_client):
        page = app_client.get("/monitoring", follow_redirects=False)
        assert page.status_code == 302
        assert page.headers["location"] == "/login"

        api = app_client.get("/api/monitoring/summary")
        assert api.status_code == 401

    def test_first_user_is_admin_and_can_monitor(self, app_client):
        register_and_login(app_client, "admin@fase16.test")

        page = app_client.get("/monitoring")
        assert page.status_code == 200
        assert "Monitoramento Operacional" in page.text

        response = app_client.get("/api/monitoring/summary?minutes=15")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == APP_VERSION
        assert data["http"]["requests"] >= 1
        assert data["database"]["status"] == "ok"

    def test_non_admin_is_forbidden(self, db_path):
        os.environ["SPED_HUB_DB"] = db_path
        from src.auth import AuthService
        from src.dashboard.app import app

        auth = AuthService(db_path)
        first = auth.registrar("first@fase16.test", "First", "senha123")
        # Pelo administrador: o registro público fecha depois do primeiro
        # usuário (ver tests/test_registro_publico.py).
        second = auth.criar_usuario("second@fase16.test", "Second", "senha123")
        assert first.admin is True
        assert second.admin is False

        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)
        client = TestClient(app)
        login = client.post(
            "/api/login",
            data={"email": "second@fase16.test", "senha": "senha123"},
        )
        assert login.status_code == 200

        assert client.get("/monitoring").status_code == 403
        assert client.get("/api/monitoring/summary").status_code == 403
        os.environ.pop("SPED_HUB_DB", None)

    def test_reset_endpoint_only_resets_http_window(self, app_client):
        register_and_login(app_client, "reset@fase16.test")
        before = app_client.get("/api/monitoring/summary").json()
        assert before["http"]["requests"] > 0

        reset = app_client.post("/api/monitoring/reset")
        assert reset.status_code == 200
        assert reset.json()["status"] == "ok"

        after = app_client.get("/api/monitoring/summary").json()
        # A própria requisição feita depois do reset volta a alimentar o coletor.
        assert after["http"]["requests"] <= 2
        assert after["database"]["status"] == "ok"

    def test_health_reports_central_version(self, app_client):
        response = app_client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["version"] == APP_VERSION


class TestMonitoringTemplate:
    def test_template_and_nav_exist(self):
        root = Path(__file__).parent.parent
        template = root / "src/dashboard/templates/monitoring.html"
        base = root / "src/dashboard/templates/base.html"

        assert template.exists()
        assert "/api/monitoring/summary" in template.read_text()
        assert "/monitoring" in base.read_text()
