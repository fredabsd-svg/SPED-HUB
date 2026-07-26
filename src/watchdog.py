"""Watchdog — monitora diretório e importa ECDs automaticamente (Fase 7).

Polling-based: verifica periodicamente um diretório configurável por novos
arquivos .txt/.ecd e os importa automaticamente.

Uso:
    python -m src.watchdog --dir /app/uploads --db sped_hub.db --interval 30
"""

import argparse
import datetime
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.parsers.ecd import ECDParser

logger = logging.getLogger("sped-hub.watchdog")

# Estado: arquivos já processados (hash → timestamp)
_processed: dict[str, float] = {}


def _hash_file(path: Path) -> str:
    """SHA-256 do arquivo."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _parse_data(valor: str) -> datetime.date:
    """Parse de data DDMMAAAA."""
    if len(valor) == 8 and valor.isdigit():
        return datetime.date(int(valor[4:8]), int(valor[2:4]), int(valor[0:2]))
    return datetime.date.today()


def processar_arquivo(caminho: Path, db_path: str) -> bool:
    """Importa um arquivo ECD. Retorna True se sucesso."""
    engine = criar_engine(db_path)
    init_db(engine)
    session = get_session(engine)
    repo = Repository(session)

    try:
        file_hash = _hash_file(caminho)

        # Verifica se já foi processado
        if file_hash in _processed:
            logger.debug("Arquivo já processado: %s", caminho.name)
            return False

        logger.info("Importando %s...", caminho.name)

        parser = ECDParser()
        registros = parser.parse_todos(caminho)

        from collections import defaultdict
        grupos = defaultdict(list)
        for r in registros:
            grupos[r["_reg"]].append(r)

        if not grupos.get("0000"):
            logger.warning("Arquivo sem registro 0000: %s", caminho.name)
            return False

        r0000 = grupos["0000"][0]
        empresa = repo.upsert_empresa({
            "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
            "nome": r0000.get("NOME", ""),
            "uf": r0000.get("UF", ""),
            "ie": r0000.get("IE", ""),
            "cod_mun": str(int(r0000.get("COD_MUN", 0))).zfill(7) if r0000.get("COD_MUN") else None,
            "im": r0000.get("IM", ""),
            "ind_sit_esp": int(r0000.get("IND_SIT_ESP", 0)) if r0000.get("IND_SIT_ESP") else None,
            "ind_nire": int(r0000.get("IND_NIRE", 0)) if r0000.get("IND_NIRE") else None,
            "ind_fin_esc": int(r0000.get("IND_FIN_ESC", 0)) if r0000.get("IND_FIN_ESC") else None,
            "ind_grande_por": int(r0000.get("IND_GRANDE_POR", 0)) if r0000.get("IND_GRANDE_POR") else None,
            "tip_ecd": r0000.get("TIP_ECD", ""),
            "ident_mf": r0000.get("IDENT_MF", ""),
            "ind_esc_cons": r0000.get("IND_ESC_CONS", ""),
        })

        rI010 = grupos["I010"][0] if grupos["I010"] else {}
        leiaute = rI010.get("COD_VER_LC", "009")
        dt_ini = _parse_data(str(int(r0000.get("DT_INI", 0))).zfill(8))
        dt_fin = _parse_data(str(int(r0000.get("DT_FIN", 0))).zfill(8))

        ecd = repo.criar_ecd(empresa.id, {
            "leiaute": leiaute,
            "dt_ini": dt_ini,
            "dt_fin": dt_fin,
            "ind_esc": rI010.get("IND_ESC", ""),
            "cod_ver_lc": leiaute,
            "hash_arquivo": file_hash,
            "nome_arquivo": caminho.name,
        })

        # Plano de Contas
        contas = []
        for r in grupos["I050"]:
            contas.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_cta_sup": r.get("COD_CTA_SUP", ""),
                "nome_cta": r.get("NOME_CTA", ""),
                "cod_nat": r.get("COD_NAT", "01"),
                "ind_cta": r.get("IND_CTA", "A"),
                "nivel": int(r.get("NIVEL", 0)),
                "dt_alt": _parse_data(str(int(r.get("DT_ALT", 0))).zfill(8)) if r.get("DT_ALT") else None,
            })
        repo.inserir_plano_contas(ecd.id, contas)

        # Saldos Periódicos
        saldos = []
        for r in grupos["I155"]:
            saldos.append({
                "cod_cta": r.get("COD_CTA", ""), "cod_ccus": r.get("COD_CCUS", ""),
                "dt_ini": dt_ini, "dt_fin": dt_fin,
                "vl_sld_ini": r.get("VL_SLD_INI", 0.0) or 0.0, "ind_dc_ini": r.get("IND_DC_INI", "D"),
                "vl_deb": r.get("VL_DEB", 0.0) or 0.0, "vl_cred": r.get("VL_CRED", 0.0) or 0.0,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0, "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            })
        repo.inserir_saldos_periodicos(ecd.id, saldos)

        # Saldos Resultado
        saldos_res = []
        for r in grupos["I355"]:
            saldos_res.append({
                "cod_cta": r.get("COD_CTA", ""), "cod_ccus": r.get("COD_CCUS", ""),
                "dt_res": dt_fin,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0, "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            })
        repo.inserir_saldos_resultado(ecd.id, saldos_res)

        # Lançamentos e Partidas
        lancs = []
        for r in grupos["I200"]:
            lancs.append({
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)) if r.get("DT_LCTO") else dt_ini,
                "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
                "ind_lcto": r.get("IND_LCTO", "N"),
                "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
            })
        repo.inserir_lancamentos(ecd.id, lancs)

        partidas = []
        for r in grupos["I250"]:
            partidas.append({
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)).isoformat() if r.get("DT_LCTO") else dt_ini.isoformat(),
                "cod_cta": r.get("COD_CTA", ""), "cod_ccus": r.get("COD_CCUS", ""),
                "vl_dc": r.get("VL_DC", 0.0) or 0.0, "ind_dc": r.get("IND_DC", "D"),
                "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
                "cod_hist_pad": r.get("COD_HIST_PAD", ""), "hist": r.get("HIST", ""),
                "cod_part": r.get("COD_PART", ""),
            })
        repo.inserir_partidas(ecd.id, partidas)

        repo.commit()
        _processed[file_hash] = time.time()
        logger.info("ECD #%d importada: %s (%d contas, %d lançamentos)",
                     ecd.id, empresa.nome, len(contas), len(lancs))
        return True

    except Exception:
        repo.rollback()
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