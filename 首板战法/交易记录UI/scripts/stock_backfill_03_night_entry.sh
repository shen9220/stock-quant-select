#!/bin/bash
# 每日 03:00 — Baostock 倒序续跑（优先 7 月），只写明细不补价
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="/tmp/stock_backfill_03_night_logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/cron.log"

if [[ -f "${ROOT}/../../../.venv/bin/python3" ]]; then
    PYTHON="${ROOT}/../../../.venv/bin/python3"
elif command -v /usr/local/bin/python3 >/dev/null; then
    PYTHON="/usr/local/bin/python3"
else
    PYTHON="python3"
fi

unset DISABLE_BAOSTOCK || true
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

cd "$ROOT"

echo "[$(date '+%F %T')] ===== 03:00 baostock 倒序开始 =====" >> "$LOG"

caffeinate -dimsu -t 14400 "$PYTHON" jobs/baostock_backfill_scheduler.py \
    --run --no-enrich --days 4 \
    >> "$LOG" 2>&1 \
    || echo "[$(date '+%F %T')] exit=$?" >> "$LOG"

echo "[$(date '+%F %T')] ===== 03:00 baostock 倒序结束 =====" >> "$LOG"

find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
