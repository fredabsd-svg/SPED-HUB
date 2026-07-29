"""Módulo de Jobs Assíncronos — Fase 14.

Importação assíncrona de ECDs grandes com tracking de progresso.
Suporta polling via GET /api/jobs/{id} e notificação via callback.

Modelos:
  AsyncJob — tracking de jobs (status, progresso, resultado, erro)
  AsyncJobService — CRUD de jobs, polling, limpeza

Fluxo:
  1. POST /api/upload-async → cria AsyncJob (status=pending), inicia background task
  2. Background task processa ECD, atualiza progresso (0-100%)
  3. GET /api/jobs/{id} → retorna status, progresso, resultado ou erro
  4. Jobs concluídos expiram após 24h (limpeza automática)
"""

import datetime
import json
import logging
import threading
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.db.models import AsyncJob, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.async_jobs")


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobInfo:
    """Resumo de um job para resposta da API."""

    id: int
    status: str
    progresso: float
    tipo: str
    mensagem: str
    resultado: dict | None = None
    erro: str | None = None
    criado_em: str | None = None
    concluido_em: str | None = None


class AsyncJobService:
    """Serviço de gerenciamento de jobs assíncronos."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path
        self._live_progress: dict[int, tuple[float, str]] = {}
        self._live_lock = threading.Lock()

    def _get_session(self) -> Session:
        engine = criar_engine(self.db_path)
        init_db(engine)
        return get_session(engine)

    def criar(self, tipo: str, parametros: dict | None = None) -> AsyncJob:
        """Cria um novo job com status pending."""
        session = self._get_session()
        try:
            job = AsyncJob(
                tipo=tipo,
                status=JobStatus.PENDING.value,
                progresso=0.0,
                parametros=json.dumps(parametros) if parametros else None,
                mensagem="Aguardando processamento...",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            logger.info("Job #%d criado: %s", job.id, tipo)
            return job
        finally:
            session.close()

    def atualizar_progresso(
        self,
        job_id: int,
        progresso: float,
        mensagem: str = "",
        *,
        persistir: bool = True,
    ):
        """Atualiza progresso persistido ou um overlay seguro em memória."""
        normalized = min(100.0, max(0.0, progresso))
        with self._live_lock:
            self._live_progress[job_id] = (normalized, mensagem)

        if not persistir:
            return

        session = self._get_session()
        try:
            job = session.get(AsyncJob, job_id)
            if job:
                job.progresso = normalized
                if mensagem:
                    job.mensagem = mensagem
                if job.status == JobStatus.PENDING.value:
                    job.status = JobStatus.PROCESSING.value
                session.commit()
        finally:
            session.close()

    def concluir(self, job_id: int, resultado: dict | None = None):
        """Marca job como concluído."""
        session = self._get_session()
        try:
            job = session.get(AsyncJob, job_id)
            if job:
                job.status = JobStatus.COMPLETED.value
                job.progresso = 100.0
                job.mensagem = "Processamento concluído com sucesso"
                job.resultado = json.dumps(resultado) if resultado else None
                job.concluido_em = datetime.datetime.now(datetime.UTC)
                session.commit()
                with self._live_lock:
                    self._live_progress.pop(job_id, None)
                logger.info("Job #%d concluído", job_id)
        finally:
            session.close()

    def falhar(self, job_id: int, erro: str):
        """Marca job como falho."""
        session = self._get_session()
        try:
            job = session.get(AsyncJob, job_id)
            if job:
                job.status = JobStatus.FAILED.value
                job.erro = erro
                job.mensagem = f"Falha: {erro[:200]}"
                job.concluido_em = datetime.datetime.now(datetime.UTC)
                session.commit()
                with self._live_lock:
                    self._live_progress.pop(job_id, None)
                logger.error("Job #%d falhou: %s", job_id, erro)
        finally:
            session.close()

    @staticmethod
    def _parametros(job: AsyncJob) -> dict:
        if not job.parametros:
            return {}
        try:
            parsed = json.loads(job.parametros)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @classmethod
    def _pode_acessar(cls, job: AsyncJob, usuario_id: int | None, admin: bool) -> bool:
        if usuario_id is None or admin:
            return True
        return cls._parametros(job).get("usuario_id") == usuario_id

    def obter(
        self, job_id: int, usuario_id: int | None = None, admin: bool = False
    ) -> JobInfo | None:
        """Obtém informações de um job, incluindo progresso ainda não persistido."""
        session = self._get_session()
        try:
            job = session.get(AsyncJob, job_id)
            if not job or not self._pode_acessar(job, usuario_id, admin):
                return None
            info = self._to_info(job)
        finally:
            session.close()

        with self._live_lock:
            live = self._live_progress.get(job_id)
        if live and info.status not in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
            info.status = JobStatus.PROCESSING.value
            info.progresso, live_message = live
            if live_message:
                info.mensagem = live_message
        return info

    def listar(
        self,
        status: str | None = None,
        limite: int = 20,
        usuario_id: int | None = None,
        admin: bool = False,
    ) -> list[JobInfo]:
        """Lista jobs com filtro opcional, incluindo progresso em memória."""
        session = self._get_session()
        try:
            jobs = (
                session.execute(select(AsyncJob).order_by(desc(AsyncJob.criado_em)).limit(limite))
                .scalars()
                .all()
            )
            infos = [
                self._to_info(job) for job in jobs if self._pode_acessar(job, usuario_id, admin)
            ]
        finally:
            session.close()

        with self._live_lock:
            live_snapshot = dict(self._live_progress)
        for info in infos:
            live = live_snapshot.get(info.id)
            if live and info.status not in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                info.status = JobStatus.PROCESSING.value
                info.progresso, live_message = live
                if live_message:
                    info.mensagem = live_message
        if status:
            infos = [info for info in infos if info.status == status]
        return infos

    def limpar_antigos(self, horas: int = 24) -> int:
        """Remove jobs concluídos/falhos mais antigos que N horas."""
        session = self._get_session()
        try:
            corte = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=horas)
            result = (
                session.execute(
                    select(func.count(AsyncJob.id)).where(
                        AsyncJob.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value]),
                        AsyncJob.concluido_em < corte,
                    )
                ).scalar()
                or 0
            )

            from sqlalchemy import delete as _delete

            session.execute(
                _delete(AsyncJob).where(
                    AsyncJob.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value]),
                    AsyncJob.concluido_em < corte,
                )
            )
            session.commit()
            logger.info("Limpeza de jobs: %d removidos (> %dh)", result, horas)
            return result
        finally:
            session.close()

    def _to_info(self, job: AsyncJob) -> JobInfo:
        resultado = None
        if job.resultado:
            try:
                resultado = json.loads(job.resultado)
            except (json.JSONDecodeError, TypeError):
                pass

        return JobInfo(
            id=job.id,
            status=job.status,
            progresso=job.progresso or 0.0,
            tipo=job.tipo,
            mensagem=job.mensagem or "",
            resultado=resultado,
            erro=job.erro,
            criado_em=job.criado_em.isoformat() if job.criado_em else None,
            concluido_em=job.concluido_em.isoformat() if job.concluido_em else None,
        )


# Instância global
_async_job_service: AsyncJobService | None = None


def init_async_job_service(db_path: str = "sped_hub.db") -> AsyncJobService:
    global _async_job_service
    _async_job_service = AsyncJobService(db_path)
    return _async_job_service


def get_async_job_service(db_path: str | None = None) -> AsyncJobService:
    global _async_job_service
    if db_path is None:
        import os

        db_path = os.environ.get("SPED_HUB_DB", "sped_hub.db")
    if _async_job_service is None or _async_job_service.db_path != db_path:
        return init_async_job_service(db_path)
    return _async_job_service
