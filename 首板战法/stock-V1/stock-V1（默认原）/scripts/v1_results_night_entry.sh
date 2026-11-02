#!/bin/bash
# V1 原 results 倒序夜班回补（限流；results_only 不覆盖 xlsx trades）
# 建议 cron：过完日盘后，如 15:40 或与 board 错开
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR" "$ROOT/cache"
LOG="${LOG_DIR}/v1_results_night.log"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"

# 若 V2 未暂停则不抢配额（双保险）
V2_PAUSE="/Users/admin/stock_quant_select/首板战法/stock-V2/Stock-V2（一进二）/cache/V2_HIST_PAUSED"
if [[ ! -f "$V2_PAUSE" ]]; then
  echo "[$(date '+%F %T')] 拒绝启动：V2 历史未暂停，先写 $V2_PAUSE" | tee -a "$LOG"
  exit 0
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export FORCE="${FORCE:-0}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

# LIMIT=0：不限天数，跑到 platform 软上限或补完
LIMIT="${LIMIT:-0}"
START="${START:-2026-01-01}"
END="${END:-$(date '+%Y-%m-%d')}"

{
  echo "[$(date '+%F %T')] v1 results start limit=$LIMIT $START..$END force=$FORCE (until quota)"
  cd "$ROOT"
  PYTHONPATH="$ROOT:$ROOT/scripts:/Users/admin/stock_quant_select/首板战法/交易记录UI/lib:/Users/admin/stock_quant_select/首板战法/交易记录UI:/Users/admin/stock_quant_select/首板战法/公共:/Users/admin/stock_quant_select/首板战法/规范文档/共享取数/lib" \
  caffeinate -dimsu -t 28800 "$PY" -u "$ROOT/scripts/backfill_v1_results_by_date.py" \
    --start "$START" --end "$END" --limit "$LIMIT" --sleep 2 --until-quota \
    $([[ "$FORCE" == "1" ]] && echo --force)
  echo "[$(date '+%F %T')] v1 results end ec=$?"
} >>"$LOG" 2>&1
