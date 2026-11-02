#!/bin/bash
# 每日 21:30 — 结束 Amphetamine 回填会话
set -euo pipefail

LOG="/Users/admin/stock_quant_select/首板战法/交易记录UI/data/historical_fetch_logs/amphetamine.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

if ! pgrep -xq Amphetamine; then
    log "Amphetamine 未运行，跳过"
    exit 0
fi

log "结束 Amphetamine 回填会话（21:30）..."
osascript <<'APPLESCRIPT' 2>/dev/null || true
tell application "Amphetamine"
    try
        end session
    end try
end tell
APPLESCRIPT
log "已发送结束会话"
