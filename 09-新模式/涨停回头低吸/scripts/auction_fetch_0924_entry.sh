#!/bin/bash
# 交易日 9:24 — 涨停回头低吸：仅拉取集合竞价写入观察/持有池（不推送）
# 时间窗：9:24:40 起 → 9:25 今开 → 目标 9:28 写完 → 最迟 9:29
# 通知请用首板 stock/stock_updata.py，勿在此脚本发 PushPlus
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/jobs/auction_fetch.py"
LOG_DIR="/tmp/zt_pullback_auction_logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/cron.log"
WRAPPER="${LOG_DIR}/cron_wrapper.log"

{
  echo "[$(date '+%F %T')] wrapper: zt_pullback auction_fetch 9:24 entry"
} >> "$WRAPPER" 2>&1 || true

if [[ -f "${ROOT}/../../.venv/bin/python3" ]]; then
    PYTHON="${ROOT}/../../.venv/bin/python3"
elif command -v /usr/local/bin/python3 >/dev/null; then
    PYTHON="/usr/local/bin/python3"
else
    PYTHON="python3"
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

cd "$ROOT"
echo "[$(date '+%F %T')] ===== auction_fetch 开始 =====" >> "$LOG"
caffeinate -dimsu -t 420 "$PYTHON" "$SCRIPT" >> "$LOG" 2>&1 \
    || echo "[$(date '+%F %T')] exit=$?" >> "$LOG"
echo "[$(date '+%F %T')] ===== auction_fetch 结束 =====" >> "$LOG"

find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
