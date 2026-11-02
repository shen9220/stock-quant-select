#!/bin/bash
# 一次性：V1 results → 主板/创业板 board_rank（零外部接口）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data/board_night_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/from_results_once.log"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export TRADE_UI_PAGES_SYNC=0
export PYTHONPATH="$ROOT/lib:$ROOT/jobs:$ROOT:${ROOT}/../公共:${PYTHONPATH:-}"

{
  echo "[$(date '+%F %T')] board from-results force start"
  "$PY" -u "$ROOT/jobs/backfill_board_records.py" \
    --program stock --from-results --force --limit 9999 --sleep 0.02 --start 2026-01-01
  ec=$?
  echo "[$(date '+%F %T')] board from-results force end ec=$ec"
  # 收尾一次 Pages
  TRADE_UI_PAGES_SYNC=1 "$PY" -u -c "from pages_sync import schedule_pages_sync; schedule_pages_sync(delay=2)" || true
  exit "$ec"
} >>"$LOG" 2>&1
