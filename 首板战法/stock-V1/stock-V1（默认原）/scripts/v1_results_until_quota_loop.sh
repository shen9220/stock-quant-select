#!/bin/bash
# V1 原 results：立刻开跑，触 platform 软上限即停；崩溃由 launchd KeepAlive 拉起。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR" "$ROOT/cache"
LOGW="${LOG_DIR}/v1_results_wrapper.log"
LOGB="${LOG_DIR}/v1_results_backfill.log"
V2_PAUSE="/Users/admin/stock_quant_select/首板战法/stock-V2/Stock-V2（一进二）/cache/V2_HIST_PAUSED"

PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export PYTHONPATH="${ROOT}:${ROOT}/scripts:/Users/admin/stock_quant_select/首板战法/交易记录UI/lib:/Users/admin/stock_quant_select/首板战法/交易记录UI:/Users/admin/stock_quant_select/首板战法/公共:/Users/admin/stock_quant_select/首板战法/规范文档/共享取数/lib"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

if [[ ! -f "$V2_PAUSE" ]]; then
  echo "[$(date '+%F %T')] refuse: V2 hist not paused" | tee -a "$LOGW"
  exit 0
fi

START="${START:-2026-01-01}"
END="${END:-2026-10-30}"

echo "[$(date '+%F %T')] until-quota loop start $START..$END" >>"$LOGW"

while true; do
  echo "[$(date '+%F %T')] batch start" >>"$LOGW"
  # 不用 caffeinate -t：避免父退出带走子进程；launchd 保活即可
  "$PY" -u "${ROOT}/scripts/backfill_v1_results_by_date.py" \
    --start "$START" --end "$END" --limit 0 --sleep 2 --until-quota \
    >>"$LOGB" 2>&1
  ec=$?
  echo "[$(date '+%F %T')] batch end ec=$ec" >>"$LOGW"

  if [[ $ec -eq 2 ]]; then
    echo "[$(date '+%F %T')] QUOTA_STOP — exit clean (到阈值停)" >>"$LOGW"
    exit 0
  fi

  left="$("$PY" -u "${ROOT}/scripts/backfill_v1_results_by_date.py" \
    --start "$START" --end "$END" --dry-run 2>&1 | grep -oE '待补 D0=[0-9]+' | head -1 || true)"
  echo "[$(date '+%F %T')] $left" >>"$LOGW"
  if [[ "$left" == "待补 D0=0" ]]; then
    echo "[$(date '+%F %T')] ALL_DONE" >>"$LOGW"
    exit 0
  fi

  # 非配额异常结束：短歇后继续（KeepAlive 也会兜底）
  sleep 8
done
