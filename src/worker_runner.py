"""Worker Runner — Fase 15.

Processo dedicado para workers de processamento assíncrono.
Inicia a WorkerQueue com handlers registrados e fica em loop
processando resultados.

Uso:
    python -m src.worker_runner
    WORKER_COUNT=4 python -m src.worker_runner
"""

import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.worker_queue import init_worker_queue
from src.cache.redis_cache import RedisCacheService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("sped-hub.worker_runner")

# Config
DB_PATH = os.environ.get("SPED_HUB_DB", "sped_hub.db")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
WORKER_COUNT = int(os.environ.get("WORKER_COUNT", "4"))


def handler_ecd_import(payload: dict, update_progress):
    """Handler incremental para importação de ECD."""
    from src.db.models import criar_engine, get_session, init_db
    from src.ecd_importer import ECDImportService

    raw_path = payload.get("path")
    if raw_path:
        filepath = Path(raw_path).resolve()
    else:
        upload_dir = Path(os.environ.get("SPED_HUB_UPLOAD_DIR", "/workspace/uploads")).resolve()
        filename = Path(str(payload.get("arquivo", ""))).name
        filepath = (upload_dir / filename).resolve()
        if upload_dir not in filepath.parents:
            raise ValueError("Caminho de arquivo inválido")

    if not filepath.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {filepath.name}")

    engine = criar_engine(DB_PATH)
    init_db(engine)
    session = get_session(engine)
    try:
        result = ECDImportService(session).importar(
            filepath,
            hash_arquivo=payload.get("hash_arquivo"),
            nome_arquivo=payload.get("nome_arquivo") or filepath.name,
            escritorio_id=payload.get("escritorio_id"),
            progress=update_progress,
        )
        return result.to_dict()
    finally:
        session.close()


def main():
    """Inicia o worker runner."""
    logger.info("SPED-HUB Worker Runner iniciando...")
    logger.info("DB: %s, Workers: %d", DB_PATH, WORKER_COUNT)

    # Inicializa cache
    cache = RedisCacheService(redis_url=REDIS_URL, prefix="worker:")
    logger.info("Cache: backend=%s", cache.stats()["backend"])

    # Inicializa worker queue
    queue = init_worker_queue(num_workers=WORKER_COUNT, db_path=DB_PATH)
    queue.register_handler("ecd_import", handler_ecd_import)

    # Callbacks
    def on_complete(task):
        logger.info("Task %s concluída: %s", task.id, task.resultado)

    def on_failure(task):
        logger.error("Task %s falhou: %s", task.id, task.erro)

    queue.on_complete(on_complete)
    queue.on_failure(on_failure)

    queue.start()
    logger.info("Worker queue iniciada com %d workers", WORKER_COUNT)

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("Sinal %d recebido, desligando...", signum)
        queue.shutdown(wait=True, timeout=10)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Loop principal
    try:
        while True:
            queue.process_results()
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()