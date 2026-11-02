#!/bin/bash
# 每日 18:30 — 腾讯正序补洞（THS → 腾讯全量 → Baostock 单日兜底）
# 目标：当天至少写出 1–3 天有效明细；避开 16:00 Baostock 倒序高峰
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="/tmp/stock_backfill_17_logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/cron.log"
WRAPPER="${LOG_DIR}/cron_wrapper.log"

{
  echo "[$(date '+%F %T')] wrapper: 18:30 entry invoked"
} >> "$WRAPPER" 2>&1 || true

if [[ -f "${ROOT}/../../../.venv/bin/python3" ]]; then
    PYTHON="${ROOT}/../../../.venv/bin/python3"
elif command -v /usr/local/bin/python3 >/dev/null; then
    PYTHON="/usr/local/bin/python3"
else
    PYTHON="python3"
fi

export DISABLE_BAOSTOCK=1
export TX_ALLOW_BAOSTOCK_FALLBACK=1
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

cd "$ROOT"

echo "[$(date '+%F %T')] ===== 18:30 stock 正序补洞开始 =====" >> "$LOG"

caffeinate -dimsu -t 14400 "$PYTHON" jobs/stock_backfill_scheduler.py \
    --run --phase forward --days 3 \
    >> "$LOG" 2>&1 \
    || echo "[$(date '+%F %T')] exit=$?" >> "$LOG"

echo "[$(date '+%F %T')] ===== 18:30 stock 正序补洞结束 =====" >> "$LOG"

find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
