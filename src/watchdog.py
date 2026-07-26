"""Watchdog — monitora diretório e importa ECDs automaticamente (Fase 7).

Polling-based: verifica periodicamente um diretório configurável por novos
arquivos .txt/.ecd e os importa automaticamente.

Uso:
    python -m src.watchdog --dir /app/uploads --db sped_hub.db --interval 30
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.db.models import criar_engine, get_session, init_db
from src.ecd_importer import DuplicateECDImportError, ECDImportService, hash_file

logger = logging.getLogger("sped-hub.watchdog")

# Estado: arquivos já processados (hash → timestamp)
_processed: dict[str, float] = {}


def _hash_file(path: Path) -> str:
    """SHA-256 incremental do arquivo."""
    return hash_file(path)


def processar_arquivo(caminho: Path, db_path: str) -> bool:
    """Importa uma ECD nova; arquivos repetidos são ignorados."""
    file_hash = _hash_file(caminho)
    if file_hash in _processed:
        logger.debug("Arquivo já processado: %s", caminho.name)
        return False

    engine = criar_engine(db_path)
    init_db(engine)
    session = get_session(engine)
    try:
        result = ECDImportService(session).importar(
            caminho,
            hash_arquivo=file_hash,
            progress=lambda pct, msg: logger.debug("%.0f%% — %s", pct, msg),
        )
        _processed[file_hash] = time.time()
        logger.info(
            "ECD #%d importada: %s (%d contas, %d lançamentos)",
            result.ecd_id,
            result.empresa,
            result.contas,
            result.lancamentos,
        )
        return True
    except DuplicateECDImportError:
        _processed[file_hash] = time.time()
        logger.info("ECD já importada: %s", caminho.name)
        return False
    except Exception:
        logger.exception("Erro ao importar %s", caminho.name)
        return False
    finally:
        session.close()


def escanear_e_importar(watch_dir: Path, db_path: str) -> int:
    """Escaneia diretório por novos arquivos e importa. Retorna qtd importada."""
    if not watch_dir.exists():
        logger.warning("Diretório não existe: %s", watch_dir)
        return 0

    importados = 0
    for ext in ("*.txt", "*.ecd"):
        for path in sorted(watch_dir.glob(ext)):
            if path.is_file():
                if processar_arquivo(path, db_path):
                    importados += 1
    return importados


def main():
    parser = argparse.ArgumentParser(
        prog="sped-hub-watchdog",
        description="Monitora diretório e importa ECDs automaticamente",
    )
    parser.add_argument("--dir", default="/app/uploads", help="Diretório a monitorar")
    parser.add_argument("--db", default="sped_hub.db", help="Banco SQLite")
    parser.add_argument("--interval", type=int, default=30, help="Intervalo de polling (segundos)")
    parser.add_argument("--once", action="store_true", help="Executa uma vez e sai")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    watch_dir = Path(args.dir)
    logger.info("Watchdog iniciado — diretório: %s, intervalo: %ds", watch_dir, args.interval)

    if args.once:
        n = escanear_e_importar(watch_dir, args.db)
        logger.info("Escaneamento concluído: %d arquivos importados", n)
        return

    while True:
        try:
            n = escanear_e_importar(watch_dir, args.db)
            if n > 0:
                logger.info("Ciclo concluído: %d novos arquivos importados", n)
        except Exception:
            logger.exception("Erro no ciclo de watchdog")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()