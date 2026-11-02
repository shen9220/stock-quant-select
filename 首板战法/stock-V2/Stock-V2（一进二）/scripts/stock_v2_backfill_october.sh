#!/bin/bash
# 包装：倒序回补 2026-10（优先 V2；board_night 15:30 已暂停）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/backfill_october.log"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export FORCE="${FORCE:-1}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
cd "$ROOT" || exit 1
echo "[$(date '+%F %T')] wrapper start FORCE=$FORCE" >>"$LOG"
caffeinate -dimsu -t 28800 "$PY" -u "$ROOT/scripts/stock_v2_backfill_october.py" >>"$LOG" 2>&1
echo "[$(date '+%F %T')] wrapper end ec=$?" >>"$LOG"
