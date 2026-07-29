"""Worker Queue — Fase 15.

Fila de processamento com workers dedicados usando multiprocessing.
Substitui threads simples por workers gerenciados com:
- Pool de workers configurável
- Retry com backoff exponencial
- Graceful shutdown
- Health check

Uso:
    from src.worker_queue import WorkerQueue, enqueue

    queue = WorkerQueue(num_workers=4)
    queue.start()
    job_id = enqueue("ecd_import", {"arquivo": "test.txt"})
    status = queue.status(job_id)
    queue.shutdown()
"""

import datetime
import logging
import os
import signal
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from multiprocessing import Process, Queue, Value
from queue import Empty

logger = logging.getLogger("sped-hub.worker_queue")


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


def _make_progress_callback(task, result_queue):
    """Cria o callback de progresso com ``task`` fixado no escopo da factory.

    Definir a closure dentro do laço capturaria a variável de iteração, e não
    o valor — o callback passaria a enxergar a tarefa errada se o handler o
    guardasse para chamar depois.
    """

    def update_progress(pct: float, msg: str = ""):
        task.progresso = min(100.0, max(0.0, pct))
        task.mensagem = msg
        result_queue.put(("update", replace(task)))

    return update_progress


@dataclass
class Task:
    """Tarefa na fila de processamento."""

    id: str
    tipo: str
    payload: dict
    status: str = TaskStatus.QUEUED.value
    progresso: float = 0.0
    resultado: dict | None = None
    erro: str | None = None
    mensagem: str = ""
    tentativas: int = 0
    max_tentativas: int = 3
    criado_em: float = field(default_factory=time.time)
    iniciado_em: float | None = None
    concluido_em: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "status": self.status,
            "progresso": self.progresso,
            "resultado": self.resultado,
            "erro": self.erro,
            "mensagem": self.mensagem,
            "tentativas": self.tentativas,
            "max_tentativas": self.max_tentativas,
            "criado_em": datetime.datetime.fromtimestamp(
                self.criado_em, tz=datetime.UTC
            ).isoformat(),
            "iniciado_em": (
                datetime.datetime.fromtimestamp(self.iniciado_em, tz=datetime.UTC).isoformat()
                if self.iniciado_em
                else None
            ),
            "concluido_em": (
                datetime.datetime.fromtimestamp(self.concluido_em, tz=datetime.UTC).isoformat()
                if self.concluido_em
                else None
            ),
        }


class WorkerQueue:
    """Fila de processamento com workers dedicados.

    Características:
    - Pool de workers via multiprocessing
    - Retry com backoff exponencial (1s, 2s, 4s...)
    - Graceful shutdown com SIGTERM/SIGINT
    - Status e progresso por task
    - Callbacks de conclusão e falha
    """

    def __init__(
        self,
        num_workers: int = 4,
        max_queue_size: int = 100,
        db_path: str | None = None,
    ):
        self._num_workers = num_workers
        self._max_queue_size = max_queue_size
        self._db_path = db_path or os.environ.get("SPED_HUB_DB", "sped_hub.db")

        # Comunicação entre processos
        self._task_queue: Queue | None = None
        self._result_queue: Queue | None = None
        self._workers: list[Process] = []
        self._running = Value("b", False)

        # Registro de tasks (no processo principal)
        self._tasks: dict[str, Task] = {}
        self._handlers: dict[str, Callable] = {}

        # Callbacks
        self._on_complete: Callable | None = None
        self._on_failure: Callable | None = None

    def register_handler(self, tipo: str, handler: Callable):
        """Registra um handler para um tipo de task.

        O handler recebe (payload: dict, update_progress: Callable) e
        deve retornar o resultado como dict.
        """
        self._handlers[tipo] = handler

    def on_complete(self, callback: Callable):
        """Registra callback para task concluída."""
        self._on_complete = callback

    def on_failure(self, callback: Callable):
        """Registra callback para task falha."""
        self._on_failure = callback

    def start(self):
        """Inicia o pool de workers."""
        if self._running.value:
            logger.warning("Worker queue já está rodando")
            return

        self._task_queue = Queue(maxsize=self._max_queue_size)
        self._result_queue = Queue()
        self._running.value = True

        for i in range(self._num_workers):
            p = Process(
                target=self._worker_loop,
                args=(i, self._task_queue, self._result_queue, self._running),
                name=f"sped-worker-{i}",
                daemon=True,
            )
            p.start()
            self._workers.append(p)

        logger.info("Worker queue iniciada com %d workers", self._num_workers)

    def shutdown(self, wait: bool = True, timeout: float = 10.0):
        """Desliga gracefulmente todos os workers."""
        if not self._running.value:
            return

        logger.info("Desligando worker queue...")
        self._running.value = False

        # Envia poison pills
        if self._task_queue:
            for _ in self._workers:
                try:
                    self._task_queue.put(None, timeout=1)
                except Exception:
                    pass

        if wait:
            for p in self._workers:
                p.join(timeout=timeout)
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=2)

        self._workers.clear()
        logger.info("Worker queue desligada")

    def enqueue(self, tipo: str, payload: dict | None = None) -> str:
        """Enfileira uma task e retorna o ID."""
        if not self._running.value:
            raise RuntimeError("Worker queue não está rodando")

        task_id = uuid.uuid4().hex[:16]
        task = Task(id=task_id, tipo=tipo, payload=payload or {})
        self._tasks[task_id] = task

        if self._task_queue:
            self._task_queue.put(task)

        logger.info("Task %s enfileirada: %s", task_id, tipo)
        return task_id

    def status(self, task_id: str) -> dict | None:
        """Retorna status de uma task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return task.to_dict()

    def list_tasks(self, status: str | None = None) -> list[dict]:
        """Lista todas as tasks."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [t.to_dict() for t in tasks]

    def pending_count(self) -> int:
        """Número de tasks pendentes."""
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.QUEUED.value)

    def active_count(self) -> int:
        """Número de tasks em execução."""
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING.value)

    def _worker_loop(
        self,
        worker_id: int,
        task_queue: Queue,
        result_queue: Queue,
        running: Value,
    ):
        """Loop principal de um worker."""
        # Ignora SIGINT no worker (o principal gerencia)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        logger.info("Worker %d iniciado", worker_id)

        while True:
            try:
                task = task_queue.get(timeout=1)
            except Exception:
                continue

            if task is None:  # Poison pill
                break

            task.status = TaskStatus.RUNNING.value
            task.iniciado_em = time.time()
            task.tentativas += 1
            task.mensagem = "Processamento iniciado"
            result_queue.put(("update", replace(task)))

            handler = self._handlers.get(task.tipo)
            if handler is None:
                task.status = TaskStatus.FAILED.value
                task.erro = f"Handler não registrado para tipo: {task.tipo}"
                task.concluido_em = time.time()
                result_queue.put(("final", replace(task)))
                continue

            try:
                resultado = handler(task.payload, _make_progress_callback(task, result_queue))
                task.status = TaskStatus.COMPLETED.value
                task.progresso = 100.0
                task.resultado = resultado
            except Exception as e:
                if task.tentativas < task.max_tentativas:
                    task.status = TaskStatus.RETRYING.value
                    task.erro = str(e)
                    # Backoff exponencial
                    delay = 2 ** (task.tentativas - 1)
                    task.mensagem = f"Nova tentativa em {delay}s"
                    result_queue.put(("update", replace(task)))
                    logger.warning(
                        "Task %s falhou (tentativa %d/%d), retry em %ds: %s",
                        task.id,
                        task.tentativas,
                        task.max_tentativas,
                        delay,
                        e,
                    )
                    time.sleep(delay)
                    task_queue.put(task)
                    continue
                else:
                    task.status = TaskStatus.FAILED.value
                    task.erro = str(e)
                    logger.error(
                        "Task %s falhou definitivamente após %d tentativas: %s",
                        task.id,
                        task.max_tentativas,
                        e,
                    )

            task.concluido_em = time.time()
            result_queue.put(("final", replace(task)))

        logger.info("Worker %d encerrado", worker_id)

    def process_results(self):
        """Aplica eventos de status e resultados produzidos pelos workers."""
        if self._result_queue is None:
            return

        while True:
            try:
                event, task = self._result_queue.get_nowait()
            except Empty:
                break
            except (EOFError, OSError):
                logger.exception("Falha ao ler fila de resultados")
                break

            self._tasks[task.id] = task
            if event != "final":
                continue

            try:
                if task.status == TaskStatus.COMPLETED.value and self._on_complete:
                    self._on_complete(task)
                elif task.status == TaskStatus.FAILED.value and self._on_failure:
                    self._on_failure(task)
            except Exception:
                logger.exception("Callback da task %s falhou", task.id)


# Instância global
_worker_queue: WorkerQueue | None = None


def init_worker_queue(num_workers: int = 4, db_path: str | None = None) -> WorkerQueue:
    global _worker_queue
    _worker_queue = WorkerQueue(num_workers=num_workers, db_path=db_path)
    return _worker_queue


def get_worker_queue() -> WorkerQueue | None:
    global _worker_queue
    return _worker_queue


def enqueue(tipo: str, payload: dict | None = None) -> str:
    """Enfileira uma task na fila global."""
    q = get_worker_queue()
    if q is None:
        raise RuntimeError("Worker queue não inicializada")
    return q.enqueue(tipo, payload)
