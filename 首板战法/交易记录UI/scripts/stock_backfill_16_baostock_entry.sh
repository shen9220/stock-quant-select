#!/bin/bash
# 每日 16:00 stock × Baostock 正序回填（与 17:00 腾讯线分头行动）
# 铁纪律：单进程、单次 login、串行扫市；禁止与 bulk CLI 并行
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="/tmp/stock_backfill_16_baostock_logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/cron.log"

if [[ -f "${ROOT}/../../../.venv/bin/python3" ]]; then
    PYTHON="${ROOT}/../../../.venv/bin/python3"
elif command -v /usr/local/bin/python3 >/dev/null; then
    PYTHON="/usr/local/bin/python3"
else
    PYTHON="python3"
fi

# 本任务必须启用 baostock；显式清除禁用标记
unset DISABLE_BAOSTOCK || true
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

cd "$ROOT"

echo "[$(date '+%F %T')] ===== 16:00 baostock 回填开始 =====" >> "$LOG"

# 防睡眠 4 小时（4 天 × 全市场串行 + 补价）
caffeinate -dimsu -t 14400 "$PYTHON" jobs/baostock_backfill_scheduler.py --run --days 3 >> "$LOG" 2>&1 \
    || echo "[$(date '+%F %T')] exit=$?" >> "$LOG"

echo "[$(date '+%F %T')] ===== 16:00 baostock 回填结束 =====" >> "$LOG"

find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
