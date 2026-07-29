"""Observabilidade em processo para a Fase 16.

Coleta métricas HTTP em janela móvel e agrega indicadores operacionais do
banco, cache, fila, e-mail e webhooks. Não armazena query strings nem dados do
usuário nas séries de requisição.
"""

from __future__ import annotations

import datetime
import math
import os
import resource
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from src.db.models import (
    ECD,
    AsyncJob,
    AuditLog,
    Empresa,
    WebhookDelivery,
    criar_engine,
    get_session,
    init_db,
)
from src.version import APP_VERSION


@dataclass(frozen=True)
class RequestMetric:
    timestamp: float
    method: str
    path: str
    status_code: int
    duration_ms: float


class MetricsCollector:
    """Coletor thread-safe de métricas HTTP em janela limitada."""

    def __init__(self, max_events: int = 20_000):
        self._events: deque[RequestMetric] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self.started_at = time.time()

    def record(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        normalized_path = normalize_path(path)
        event = RequestMetric(
            timestamp=time.time(),
            method=method.upper(),
            path=normalized_path,
            status_code=int(status_code),
            duration_ms=max(0.0, float(duration_ms)),
        )
        with self._lock:
            self._events.append(event)

    def snapshot(self, minutes: int = 60) -> dict:
        minutes = max(1, min(int(minutes), 24 * 60))
        now = time.time()
        cutoff = now - minutes * 60
        with self._lock:
            events = [event for event in self._events if event.timestamp >= cutoff]

        total = len(events)
        errors = sum(event.status_code >= 500 for event in events)
        client_errors = sum(400 <= event.status_code < 500 for event in events)
        durations = sorted(event.duration_ms for event in events)
        status_classes = Counter(f"{event.status_code // 100}xx" for event in events)
        routes: dict[tuple[str, str], list[RequestMetric]] = {}
        for event in events:
            routes.setdefault((event.method, event.path), []).append(event)

        top_routes = []
        for (method, path), route_events in routes.items():
            route_durations = sorted(event.duration_ms for event in route_events)
            route_errors = sum(event.status_code >= 500 for event in route_events)
            top_routes.append(
                {
                    "method": method,
                    "path": path,
                    "requests": len(route_events),
                    "avg_ms": round(sum(route_durations) / len(route_durations), 2),
                    "p95_ms": round(percentile(route_durations, 0.95), 2),
                    "errors": route_errors,
                }
            )
        top_routes.sort(key=lambda item: (-item["requests"], -item["p95_ms"]))

        return {
            "window_minutes": minutes,
            "requests": total,
            "requests_per_minute": round(total / minutes, 2),
            "errors_5xx": errors,
            "errors_4xx": client_errors,
            "error_rate_pct": round(errors / total * 100, 2) if total else 0.0,
            "avg_latency_ms": round(sum(durations) / total, 2) if total else 0.0,
            "p50_latency_ms": round(percentile(durations, 0.50), 2),
            "p95_latency_ms": round(percentile(durations, 0.95), 2),
            "p99_latency_ms": round(percentile(durations, 0.99), 2),
            "status_classes": dict(sorted(status_classes.items())),
            "top_routes": top_routes[:10],
            "uptime_seconds": round(now - self.started_at, 1),
        }

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
        self.started_at = time.time()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
    return values[index]


def normalize_path(path: str) -> str:
    """Reduz cardinalidade sem registrar identificadores em métricas."""
    parts = []
    for part in (path or "/").split("/"):
        if part.isdigit():
            parts.append("{id}")
        elif len(part) >= 16 and all(character in "0123456789abcdefABCDEF" for character in part):
            parts.append("{token}")
        else:
            parts.append(part)
    normalized = "/".join(parts)
    return normalized or "/"


def _database_metrics(db_path: str, minutes: int) -> dict:
    engine = criar_engine(db_path)
    init_db(engine)
    session = get_session(engine)
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes)
    try:
        companies = session.execute(select(func.count(Empresa.id))).scalar() or 0
        ecds = session.execute(select(func.count(ECD.id))).scalar() or 0
        active_jobs = (
            session.execute(
                select(func.count(AsyncJob.id)).where(
                    AsyncJob.status.in_(["pending", "processing"])
                )
            ).scalar()
            or 0
        )
        failed_jobs = (
            session.execute(
                select(func.count(AsyncJob.id)).where(
                    AsyncJob.status == "failed",
                    AsyncJob.criado_em >= cutoff,
                )
            ).scalar()
            or 0
        )
        failed_webhooks = (
            session.execute(
                select(func.count(WebhookDelivery.id)).where(
                    WebhookDelivery.status == "failed",
                    WebhookDelivery.criado_em >= cutoff,
                )
            ).scalar()
            or 0
        )
        audit_events = (
            session.execute(
                select(func.count(AuditLog.id)).where(AuditLog.criado_em >= cutoff)
            ).scalar()
            or 0
        )
        audit_errors = (
            session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.criado_em >= cutoff,
                    AuditLog.status_code >= 400,
                )
            ).scalar()
            or 0
        )
        return {
            "status": "ok",
            "companies": companies,
            "ecds": ecds,
            "active_jobs": active_jobs,
            "failed_jobs": failed_jobs,
            "failed_webhooks": failed_webhooks,
            "audit_events": audit_events,
            "audit_errors": audit_errors,
            "size_bytes": _database_size(db_path),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        session.close()
        engine.dispose()


def _database_size(db_path: str) -> int:
    if db_path == ":memory:":
        return 0
    base = Path(db_path)
    return sum(
        candidate.stat().st_size
        for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm"))
        if candidate.exists()
    )


def _process_metrics() -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # Linux reports KiB; macOS reports bytes. The project deploy target is Linux.
    rss_bytes = (
        int(usage.ru_maxrss * 1024) if sys.platform.startswith("linux") else int(usage.ru_maxrss)
    )
    return {
        "rss_bytes": rss_bytes,
        "threads": threading.active_count(),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
    }


def build_operational_snapshot(
    collector: MetricsCollector,
    *,
    db_path: str,
    minutes: int = 60,
) -> dict:
    """Agrega métricas HTTP e dos serviços sem depender de rede externa."""
    from src.cache import get_cache
    from src.email_service import get_email_service
    from src.worker_queue import get_worker_queue

    queue = get_worker_queue()
    worker_metrics = {
        "status": "running" if queue else "not_initialized",
        "pending": queue.pending_count() if queue else 0,
        "active": queue.active_count() if queue else 0,
        "total_tasks": len(queue.list_tasks()) if queue else 0,
    }
    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "version": APP_VERSION,
        "http": collector.snapshot(minutes),
        "database": _database_metrics(db_path, minutes),
        "cache": get_cache().stats(),
        "email": get_email_service().stats(),
        "workers": worker_metrics,
        "process": _process_metrics(),
    }


metrics_collector = MetricsCollector()
