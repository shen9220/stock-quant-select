#!/bin/bash
# V2 历史 · 优先涨停池回补（Stage A）；买卖价后补；不占 tdx
# 推荐：launchctl 加载 com.stockquant.v2hist.pool-first
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/hist_pool_first.log"
PIDF="/tmp/v2_hist_pool_first.pid"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"
SCRIPT_PY="$ROOT/scripts/stock_v2_hist_pool_first.py"

export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export V2_POOL_FIRST=1
export V2_HIST_UNTIL="${V2_HIST_UNTIL:-2026-01-01}"
export FORCE="${FORCE:-0}"
export POOL_SLEEP="${POOL_SLEEP:-0.35}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

PAUSE_FLAG="${ROOT}/cache/V2_HIST_PAUSED"
if [[ -f "$PAUSE_FLAG" ]]; then
  echo "[$(date '+%F %T')] V2 历史回补已暂停（存在 $PAUSE_FLAG）" | tee -a "$LOG"
  exit 0
fi

_alive_py() {
  pgrep -f "$SCRIPT_PY" >/dev/null 2>&1
}

# 若已有 python 在跑，不重复抢（launchd KeepAlive 可能叠启动）
if _alive_py; then
  echo "[$(date '+%F %T')] already running python — exit 0 keep" | tee -a "$LOG"
  # 阻塞等到对方结束，避免 launchd 立刻再拉起抢跑
  while _alive_py; do sleep 20; done
  exit 0
fi

echo $$ >"$PIDF"
trap 'rm -f "$PIDF"' EXIT

echo "[$(date '+%F %T')] ===== pool_first LIGHT entry pid=$$ until=$V2_HIST_UNTIL FORCE=$FORCE =====" >>"$LOG"

# 前台跑：SIG 会进本进程；崩溃由 launchd KeepAlive 拉起
echo "[$(date '+%F %T')] launch caffeinate+python (fg)" >>"$LOG"
caffeinate -dimsu -t 86400 "$PY" -u "$SCRIPT_PY" >>"$LOG" 2>&1
ec=$?
echo "[$(date '+%F %T')] python exit ec=$ec" >>"$LOG"

# 0=完成；2=配额停 → 都当成功退出，避免 KeepAlive 狂重启
if [[ "$ec" -eq 0 || "$ec" -eq 2 ]]; then
  exit 0
fi
exit "$ec"
