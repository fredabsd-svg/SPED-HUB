#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# SPED-HUB — Script de Importação Agendada via Cron
# Fase 10
#
# Uso:
#   Adicione ao crontab:
#   */30 * * * * /opt/sped-hub/scripts/cron-import.sh >> /var/log/sped-hub-cron.log 2>&1
#
# Ou execute manualmente:
#   ./cron-import.sh --dir /app/uploads --db /data/sped_hub.db
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Configuração ────────────────────────────────────────────────────────────

WATCH_DIR="${SPED_HUB_WATCH_DIR:-/app/uploads}"
DB_PATH="${SPED_HUB_DB:-$PROJECT_DIR/sped_hub.db}"
LOG_FILE="${SPED_HUB_LOG:-/var/log/sped-hub-cron.log}"

# ── Argumentos ──────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) WATCH_DIR="$2"; shift 2 ;;
        --db) DB_PATH="$2"; shift 2 ;;
        --log) LOG_FILE="$2"; shift 2 ;;
        *) echo "Argumento desconhecido: $1"; exit 1 ;;
    esac
done

# ── Log ──────────────────────────────────────────────────────────────────────

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── Verificações ────────────────────────────────────────────────────────────

if [ ! -d "$WATCH_DIR" ]; then
    log "ERRO: Diretório de watch não existe: $WATCH_DIR"
    exit 1
fi

if [ ! -f "$DB_PATH" ]; then
    log "AVISO: Banco não encontrado em $DB_PATH — será criado na primeira importação"
fi

# ── Importação ──────────────────────────────────────────────────────────────

log "Iniciando ciclo de importação — dir=$WATCH_DIR db=$DB_PATH"

cd "$PROJECT_DIR"

# Usa o watchdog em modo --once para processar uma vez e sair
python -m src.watchdog --dir "$WATCH_DIR" --db "$DB_PATH" --once 2>&1 | while IFS= read -r line; do
    log "$line"
done

EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -eq 0 ]; then
    log "Ciclo concluído com sucesso"
else
    log "ERRO: Ciclo falhou com código $EXIT_CODE"
fi

exit $EXIT_CODE