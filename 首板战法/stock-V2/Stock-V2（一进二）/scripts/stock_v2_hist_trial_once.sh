#!/bin/bash
# 人工补跑：窗外试跑（默认 60 分钟）。不改 stock 定时任务。
# FOREGROUND=1：前台跑（给 launchd / cron）；默认也前台，便于保活。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/hist_daily_trial_60m.log"
PIDF="/tmp/v2_hist_trial.pid"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export IGNORE_WINDOW="${IGNORE_WINDOW:-1}"
export MAX_MINUTES="${MAX_MINUTES:-60}"
export FORCE="${FORCE:-0}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

if [[ -f "$PIDF" ]]; then
  old="$(cat "$PIDF" 2>/dev/null || true)"
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    echo "[$(date '+%F %T')] already running pid=$old" | tee -a "$LOG"
    exit 0
  fi
fi

echo $$ >"$PIDF"
echo "[$(date '+%F %T')] trial start pid=$$ MAX_MINUTES=$MAX_MINUTES IGNORE_WINDOW=$IGNORE_WINDOW" | tee -a "$LOG"
# 评估：V2=tdx；stock/board=腾讯搜狐新浪 — 平台不重叠，默认跑满 MAX_MINUTES
caffeinate -dimsu -t $(( MAX_MINUTES * 60 + 120 )) \
  "$PY" -u "$ROOT/scripts/stock_v2_hist_daily.py" >>"$LOG" 2>&1
ec=$?
echo "[$(date '+%F %T')] trial end ec=$ec" | tee -a "$LOG"
rm -f "$PIDF"
# 一次性 LaunchAgent：跑完卸掉，避免下次登录重跑
launchctl bootout "gui/$(id -u)/com.stockquant.v2hist.trial" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.stockquant.v2hist.trial.plist"
exit "$ec"
