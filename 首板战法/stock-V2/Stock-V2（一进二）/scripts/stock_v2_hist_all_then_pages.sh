#!/bin/bash
# V2 历史倒序全力续跑（守 tdx≤70%）；结束后补全 trades→HTML/Pages。
# 环境：IGNORE_WINDOW=1 WAIT_HOUR_RESET=1（小时满等下一小时；日满停）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI="/Users/admin/stock_quant_select/首板战法/交易记录UI"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/hist_all_then_pages.log"
PIDF="/tmp/v2_hist_all.pid"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"

export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export IGNORE_WINDOW="${IGNORE_WINDOW:-1}"
export WAIT_HOUR_RESET="${WAIT_HOUR_RESET:-1}"
export FORCE="${FORCE:-0}"
export V2_HIST_UNTIL="${V2_HIST_UNTIL:-2026-01-01}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
unset MAX_MINUTES  # 不限分钟，日软上限/跑完才停

if [[ -f "$PIDF" ]]; then
  old="$(cat "$PIDF" 2>/dev/null || true)"
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    echo "[$(date '+%F %T')] already running pid=$old" | tee -a "$LOG"
    exit 0
  fi
fi

echo $$ >"$PIDF"
{
  echo "[$(date '+%F %T')] ===== hist_all start pid=$$ until=$V2_HIST_UNTIL ====="
  # 足够长 caffeinate：按日软 1400×~1s + 等整点，给足余量
  caffeinate -dimsu -t 86400 \
    "$PY" -u "$ROOT/scripts/stock_v2_hist_daily.py"
  hist_ec=$?
  echo "[$(date '+%F %T')] hist_daily end ec=$hist_ec"

  echo "[$(date '+%F %T')] ===== sync stock_v2 results → trades ====="
  "$PY" -u - <<'PY'
import json, os, sys
ROOT = "/Users/admin/stock_quant_select/首板战法/stock-V2/Stock-V2（一进二）"
sys.path.insert(0, ROOT)
import stock_v2 as v2
res_dir = v2.RESULTS_DIR
n_ok = 0
for name in sorted(os.listdir(res_dir)):
    if not name.endswith("_recommendations.json"):
        continue
    path = os.path.join(res_dir, name)
    try:
        result = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"skip {name}: {e}")
        continue
    if result.get("mode") not in ("backfill_mootdx_0925", "backfill_open_same_day", "live", None):
        # 仍尝试同步带 recommendations 的
        pass
    try:
        v2._sync_trades_ui(result)
        n_ok += 1
        print(f"synced {result.get('d1')} n={len(result.get('recommendations') or [])}")
    except Exception as e:
        print(f"FAIL sync {name}: {e}")
print(f"trades sync done files={n_ok}")
PY

  echo "[$(date '+%F %T')] ===== pages / HTML export+push ====="
  cd "$UI"
  # Pages 加密依赖 Crypto：优先系统 /usr/bin/python3（与 pages_sync._crypto_python 一致）
  PAGE_PY="/usr/bin/python3"
  [[ -x "$PAGE_PY" ]] || PAGE_PY="$PY"
  PYTHONPATH="$UI/lib:$UI/jobs:$UI:${PYTHONPATH:-}" \
    "$PAGE_PY" -u "$UI/jobs/sync_trade_ui_pages.py" --push
  pages_ec=$?
  echo "[$(date '+%F %T')] pages sync ec=$pages_ec"

  echo "[$(date '+%F %T')] ===== hist_all_then_pages done hist_ec=$hist_ec pages_ec=$pages_ec ====="
} >>"$LOG" 2>&1
ec=$?
rm -f "$PIDF"
# 一次性 LaunchAgent 自卸
launchctl bootout "gui/$(id -u)/com.stockquant.v2hist.all" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.stockquant.v2hist.all.plist"
exit "$ec"
