#!/bin/bash
# stock 主板/创业板 2026 夜班多源回填（v11）
# cron: 30 15 * * *  （收盘补全之后；硬停次日 04:50）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data/board_night_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron_wrapper.log"
PY="/usr/local/bin/python3"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

{
  echo "[$(date '+%F %T')] board_night_entry start"
  # 默认禁用 baostock 轨（login 不稳会卡死）；稳定源：东财/同花顺×腾讯/新浪/东财日线
  export DISABLE_BAOSTOCK=1
export PYTHONPATH="$ROOT/lib:$ROOT/jobs:$ROOT:${PYTHONPATH:-}"
  # 多轮直到硬停或补完；每轮每轨 3 天
  caffeinate -dimsu -t 46800 "$PY" "$ROOT/jobs/board_night_orchestrator.py" \
    --per-lane 3 --sleep 1.0 --rounds 40
  echo "[$(date '+%F %T')] board_night_entry end"
} >> "$LOG" 2>&1
