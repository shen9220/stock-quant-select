#!/bin/bash
# 确保 Amphetamine 会话活跃（四批回填前兜底；弥补未开「防睡眠」系统设置）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${ROOT}/data/historical_fetch_logs/amphetamine.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# 是否 A 股交易日（法定工作日口径）
IS_TRADING=0
_PY="${BACKFILL_PYTHON:-python3}"
_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if PYTHONPATH="${_ROOT}:${_ROOT}/.." "$_PY" - <<PY 2>/dev/null
import sys
from datetime import datetime
sys.path.insert(0, "${_ROOT}")
import fetch_calendar as fc
today = datetime.now().strftime("%Y-%m-%d")
cal = set(fc.load_calendar(today, today))
raise SystemExit(0 if today in cal else 1)
PY
then
    IS_TRADING=1
    log "今日为 A 股交易日 — Amphetamine 必须成功"
else
    log "今日非交易日 — 仍启动 Amphetamine（历史回填继续）"
fi

if ! pgrep -xq Amphetamine; then
    open -a Amphetamine
    sleep 2
fi

ok=0
for attempt in 1 2 3 4; do
    if osascript <<'APPLESCRIPT' 2>/dev/null; then
tell application "Amphetamine"
    activate
    if not (session is active) then
        start new session
    end if
end tell
APPLESCRIPT
        ok=1
        log "Amphetamine 会话 OK (attempt=${attempt})"
        break
    fi
    log "AppleScript 第 ${attempt} 次失败，重试..."
    open -a Amphetamine
    sleep 2
done

if [[ "$ok" -ne 1 ]]; then
    log "ERROR: Amphetamine 无法启动会话"
    # 交易日失败则非零退出，便于 launchd 记录
    [[ "$IS_TRADING" -eq 1 ]] && exit 1
    exit 0
fi

# 交易日额外用 caffeinate 兜底到 20:00（与四批 19:00 对齐）
if [[ "$IS_TRADING" -eq 1 ]]; then
    # 后台保活，不阻塞；若已有则跳过
    if ! pgrep -f "caffeinate -dimsu -t 57600" >/dev/null 2>&1; then
        nohup caffeinate -dimsu -t 57600 >/dev/null 2>&1 &
        log "已启动 caffeinate 兜底（交易日）"
    fi
fi
exit 0
