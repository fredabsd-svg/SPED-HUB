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
  4. Jobs concluídos expiram por `SPED_HUB_JOB_RETENTION_HOURS` (24h por
     omissão).  Quem executa é o laço de manutenção do `dashboard.app`, não
     este módulo: `limpar_antigos` existia aqui e a "limpeza automática" que
     esta linha prometia não tinha ninguém a chamando.
"""

import datetime
import json
import logging
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.db.models import AsyncJob, get_session, init_db_once, obter_engine
from src.settings import database_reference

logger = logging.getLogger("sped-hub.async_jobs")


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: O processo que executava o job morreu — reinício, atualização, queda.
    #: Sem este estado, a linha ficava em `pending` com a mensagem
    #: "Aguardando processamento..." para sempre: quem enviou a escrituração
    #: esperava por uma importação que ninguém mais ia executar.
    INTERRUPTED = "interrupted"


#: Estados em que ninguém mais vai mexer no job.  A lista existe porque estava
#: escrita à mão como `(COMPLETED, FAILED)` em vários pontos — `cancelled` já
#: ficava de fora, e cada novo estado exigiria caçar todas as ocorrências.
STATUS_TERMINAIS = (
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.INTERRUPTED.value,
)

#: Estados de job que ainda espera ou está executando.  Depois que o processo
#: que o executava morre, nenhum deles volta a andar sozinho: o executor é uma
#: thread dentro do processo, não uma fila que alguém varre.
STATUS_EM_ABERTO = (JobStatus.PENDING.value, JobStatus.PROCESSING.value)


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
        # Tokens dos jobs em execução neste processo, para que outra
        # requisição consiga pedir o cancelamento.
        self._cancel_tokens: dict[int, object] = {}
        self._live_lock = threading.Lock()

    def _get_session(self) -> Session:
        engine = obter_engine(self.db_path)
        init_db_once(engine)
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

    def marcar_em_execucao(self, job_id: int, arquivo_temporario: str | None = None) -> None:
        """Grava no banco que o job começou, e onde está o arquivo dele.

        A importação assíncrona reporta progresso com `persistir=False` — de
        propósito, para não escrever no banco a cada bloco lido.  O efeito
        colateral era a linha continuar dizendo `pending` / 0% /
        "Aguardando processamento..." durante a importação inteira: o estado
        real só existia na memória do processo.  Depois de um reinício, o que
        sobrava no banco era um job que parecia nem ter começado.

        `arquivo_temporario` é o caminho do upload em disco.  Ele é gravado
        aqui porque o `finally` que o apaga vive numa thread `daemon`, e thread
        `daemon` é morta no encerramento do interpretador **sem** rodar
        `finally`: sem o caminho registrado, o arquivo ficava órfão e nada
        sabia onde procurá-lo.
        """
        session = self._get_session()
        try:
            job = session.get(AsyncJob, job_id)
            if not job:
                return
            job.status = JobStatus.PROCESSING.value
            job.mensagem = "Processando..."
            if arquivo_temporario:
                parametros = json.loads(job.parametros) if job.parametros else {}
                parametros["arquivo_temporario"] = arquivo_temporario
                job.parametros = json.dumps(parametros)
            session.commit()
        finally:
            session.close()

    def recuperar_interrompidos(self) -> int:
        """Encerra os jobs que o processo anterior deixou em aberto.

        Chamada na subida da aplicação.  O executor de um job é uma thread
        dentro deste processo, não uma fila que alguém varre: job em aberto no
        banco quando o processo está subindo é, necessariamente, job que o
        processo anterior abandonou — nenhum threshold de tempo é preciso, e
        não há falso positivo.

        Vale para instância única, que é o deploy documentado (o limite por IP
        e o progresso em memória já pressupõem isso).  Com mais de uma réplica
        web, a subida de uma marcaria como interrompido o job em andamento da
        outra; nesse cenário o executor precisaria sair para um worker com
        posse explícita, e isso está registrado em `docs/status.md`.

        Devolve quantos foram encerrados.  O arquivo de upload de cada um é
        removido: ele não serve para mais nada e ocupa o volume.
        """
        session = self._get_session()
        try:
            abertos = list(
                session.execute(
                    select(AsyncJob).where(AsyncJob.status.in_(STATUS_EM_ABERTO))
                ).scalars()
            )
            if not abertos:
                return 0
            agora = datetime.datetime.now(datetime.UTC)
            orfaos: list[str] = []
            # Os ids saem daqui, dentro da sessão: depois do `close()` o objeto
            # está desanexado e ler qualquer atributo dispara um refresh que
            # levanta `DetachedInstanceError`.
            ids = [job.id for job in abertos]
            for job in abertos:
                job.status = JobStatus.INTERRUPTED.value
                job.erro = "Processo encerrado antes de a importação terminar"
                job.mensagem = (
                    "Interrompida por reinício do sistema — nada foi gravado. "
                    "Envie o arquivo novamente."
                )
                job.concluido_em = agora
                if job.parametros:
                    try:
                        caminho = json.loads(job.parametros).get("arquivo_temporario")
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        caminho = None
                    if caminho:
                        orfaos.append(caminho)
            session.commit()
        finally:
            session.close()

        with self._live_lock:
            for job_id in ids:
                self._live_progress.pop(job_id, None)
                self._cancel_tokens.pop(job_id, None)

        for caminho in orfaos:
            # Falha aqui não pode impedir a aplicação de subir: o job já está
            # encerrado no banco, e um arquivo a mais no volume é o menor dos
            # problemas.
            try:
                Path(caminho).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Upload órfão %s não pôde ser removido: %s", caminho, exc)

        logger.warning(
            "%d job(s) encerrado(s) como interrompido(s): o processo anterior morreu "
            "durante a importação e quem enviou o arquivo precisa reenviá-lo",
            len(ids),
        )
        return len(ids)

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

    # ── Cancelamento (Fase 17, Etapa 4) ───────────────────────────────────
    #
    # O token vive no processo que está importando; o pedido de cancelamento
    # chega por outra requisição HTTP.  O registro abaixo é o que liga os dois.

    def registrar_token(self, job_id: int, token) -> None:
        """Associa o token de cancelamento de um job em execução."""
        with self._live_lock:
            self._cancel_tokens[job_id] = token

    def esquecer_token(self, job_id: int) -> None:
        with self._live_lock:
            self._cancel_tokens.pop(job_id, None)

    def cancelar(self, job_id: int, motivo: str | None = None) -> bool:
        """Sinaliza o cancelamento.  ``False`` se o job não estiver rodando.

        Não marca o job como cancelado aqui: quem importa é que sabe quando
        de fato parou, e é lá que o estado final é gravado.
        """
        with self._live_lock:
            token = self._cancel_tokens.get(job_id)
        if token is None:
            return False
        token.cancelar(motivo)
        logger.info("Cancelamento solicitado para o job #%d", job_id)
        return True

    def marcar_cancelado(self, job_id: int, mensagem: str) -> None:
        """Estado final de um job interrompido — nada foi persistido."""
        session = self._get_session()
        try:
            job = session.get(AsyncJob, job_id)
            if job:
                job.status = JobStatus.CANCELLED.value
                job.mensagem = mensagem[:200]
                job.concluido_em = datetime.datetime.now(datetime.UTC)
                session.commit()
                with self._live_lock:
                    self._live_progress.pop(job_id, None)
                    self._cancel_tokens.pop(job_id, None)
                logger.info("Job #%d cancelado", job_id)
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
        if live and info.status not in STATUS_TERMINAIS:
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
            if live and info.status not in STATUS_TERMINAIS:
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
                        AsyncJob.status.in_(STATUS_TERMINAIS),
                        AsyncJob.concluido_em < corte,
                    )
                ).scalar()
                or 0
            )

            from sqlalchemy import delete as _delete

            session.execute(
                _delete(AsyncJob).where(
                    AsyncJob.status.in_(STATUS_TERMINAIS),
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
        db_path = database_reference()
    if _async_job_service is None or _async_job_service.db_path != db_path:
        return init_async_job_service(db_path)
    return _async_job_service
