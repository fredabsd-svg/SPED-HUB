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

from src.worker_queue import WorkerQueue, init_worker_queue, get_worker_queue
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
    """Handler para importação de ECD."""
    from src.parsers.ecd import ECDParser
    from src.db.models import criar_engine, get_session, init_db
    from src.db.repository import Repository
    from collections import defaultdict
    import datetime
    import hashlib

    arquivo = payload.get("arquivo", "")
    filepath = Path("/workspace/uploads") / arquivo

    if not filepath.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {arquivo}")

    update_progress(5.0, "Iniciando parser...")

    parser = ECDParser()
    update_progress(10.0, "Parser carregado, lendo arquivo...")

    # Conta registros
    contagem = parser.contar_registros(filepath)
    total_regs = sum(contagem.values())
    update_progress(15.0, f"Detectados {total_regs} registros")

    # Processa em lotes
    registros = []
    lotes_processados = 0
    for lote in parser.parse_em_lotes(filepath, tamanho_lote=500):
        registros.extend(lote)
        lotes_processados += 1
        progresso = min(90.0, 15.0 + (lotes_processados / max(1, total_regs / 500)) * 75.0)
        update_progress(progresso, f"Processando lote {lotes_processados}...")

    update_progress(90.0, "Salvando no banco...")

    # Agrupa e salva
    grupos = defaultdict(list)
    for r in registros:
        grupos[r["_reg"]].append(r)

    if not grupos.get("0000"):
        raise ValueError("Arquivo não contém registro 0000")

    engine = criar_engine(DB_PATH)
    init_db(engine)
    session = get_session(engine)
    repo = Repository(session)

    try:
        r0000 = grupos["0000"][0]
        empresa = repo.upsert_empresa({
            "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
            "nome": r0000.get("NOME", ""),
            "uf": r0000.get("UF", ""),
            "ie": r0000.get("IE", ""),
        })

        def _parse_data(valor):
            if len(valor) == 8 and valor.isdigit():
                return datetime.date(int(valor[4:8]), int(valor[2:4]), int(valor[0:2]))
            return datetime.date.today()

        dt_ini = _parse_data(str(int(r0000.get("DT_INI", 0))).zfill(8))
        dt_fin = _parse_data(str(int(r0000.get("DT_FIN", 0))).zfill(8))

        hash_arquivo = hashlib.sha256(filepath.read_bytes()).hexdigest()

        ecd = repo.criar_ecd(empresa.id, {
            "leiaute": "009",
            "dt_ini": dt_ini,
            "dt_fin": dt_fin,
            "hash_arquivo": hash_arquivo,
            "nome_arquivo": arquivo,
        })

        # Plano de Contas
        contas = []
        for r in grupos.get("I050", []):
            contas.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_cta_sup": r.get("COD_CTA_SUP", ""),
                "nome_cta": r.get("NOME_CTA", ""),
                "cod_nat": r.get("COD_NAT", "01"),
                "ind_cta": r.get("IND_CTA", "A"),
                "nivel": int(r.get("NIVEL", 0)),
            })
        repo.inserir_plano_contas(ecd.id, contas)

        # Saldos
        saldos = []
        for r in grupos.get("I155", []):
            saldos.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "dt_ini": dt_ini, "dt_fin": dt_fin,
                "vl_sld_ini": r.get("VL_SLD_INI", 0.0) or 0.0,
                "ind_dc_ini": r.get("IND_DC_INI", "D"),
                "vl_deb": r.get("VL_DEB", 0.0) or 0.0,
                "vl_cred": r.get("VL_CRED", 0.0) or 0.0,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
                "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            })
        repo.inserir_saldos_periodicos(ecd.id, saldos)

        # Lançamentos
        lancs = []
        for r in grupos.get("I200", []):
            lancs.append({
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)),
                "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
                "ind_lcto": r.get("IND_LCTO", "N"),
            })
        repo.inserir_lancamentos(ecd.id, lancs)

        partidas = []
        for r in grupos.get("I250", []):
            partidas.append({
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)).isoformat(),
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "vl_dc": r.get("VL_DC", 0.0) or 0.0,
                "ind_dc": r.get("IND_DC", "D"),
                "cod_hist_pad": r.get("COD_HIST_PAD", ""),
                "hist": r.get("HIST", ""),
            })
        repo.inserir_partidas(ecd.id, partidas)

        repo.commit()

        return {
            "ecd_id": ecd.id,
            "empresa": empresa.nome,
            "contas": len(contas),
            "lancamentos": len(lancs),
            "partidas": len(partidas),
        }
    except Exception:
        repo.rollback()
        raise
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