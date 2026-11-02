#!/bin/bash
# Stock V2 历史竞价倒序回补入口
# cron: 0 11 * * 1-5  （11:00 起；脚本内 14:30 硬停；tdx ≤70% 软停）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/hist_daily.log"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export FORCE="${FORCE:-0}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

GATE="/Users/admin/new_stock/scripts/a_share_trading_day_gate.sh"
DOW="$(date '+%u')"
PAUSE_FLAG="${ROOT}/cache/V2_HIST_PAUSED"
{
  echo "[$(date '+%F %T')] hist_daily entry start"
  if [[ -f "$PAUSE_FLAG" ]]; then
    echo "V2 历史回补已暂停（存在 $PAUSE_FLAG）"; exit 0
  fi
  if [[ "$DOW" -ge 6 ]]; then
    echo "周末跳过"; exit 0
  fi
  if [[ -x "$GATE" ]] && ! "$GATE"; then
    echo "非交易日跳过"; exit 0
  fi
  # 14:30 后不启动
  hm="$(date '+%H%M')"
  if [[ $((10#$hm)) -gt 1430 ]]; then
    echo "已过 14:30，不启动"; exit 0
  fi
  # 人工全力续跑占用中则跳过，避免双开打爆 tdx
  if [[ -f /tmp/v2_hist_all.pid ]]; then
    opid="$(cat /tmp/v2_hist_all.pid 2>/dev/null || true)"
    if [[ -n "$opid" ]] && kill -0 "$opid" 2>/dev/null; then
      echo "hist_all 占用中 pid=$opid，跳过 cron 窗"; exit 0
    fi
  fi
  caffeinate -dimsu -t 14400 "$PY" -u "$ROOT/scripts/stock_v2_hist_daily.py"
  echo "[$(date '+%F %T')] hist_daily entry end ec=$?"
} >>"$LOG" 2>&1
