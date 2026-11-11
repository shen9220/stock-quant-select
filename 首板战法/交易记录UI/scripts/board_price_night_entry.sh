#!/bin/bash
# 兼容旧名：转调每小时入口逻辑的「本段会话硬停」长跑（仍受 platform_gate）
# 推荐 cron 用 board_price_hourly_entry.sh（每小时到点停）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data/board_price_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron_wrapper.log"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export BACKFILL_RESPECT_RESERVE=1
export PYTHONPATH="$ROOT/lib:$ROOT/jobs:$ROOT:${PYTHONPATH:-}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

GATE="$("$PY" - <<'PY'
from backfill_window import quiet_reason, default_until, describe
q = quiet_reason()
if q:
    print("SKIP|" + q)
else:
    print("RUN|" + default_until() + "|" + describe())
PY
)"

{
  echo "[$(date '+%F %T')] board_price_night start gate=$GATE"
  case "$GATE" in
    SKIP\|*)
      echo "[$(date '+%F %T')] skip: ${GATE#SKIP|}"
      exit 0
      ;;
  esac
  UNTIL="$(printf '%s' "$GATE" | cut -d'|' -f2)"
  echo "[$(date '+%F %T')] until=$UNTIL (unified hard-stop 00:00)"
  "$PY" -u -c "from platform_gate import quota_status; import json; print(json.dumps(quota_status(),ensure_ascii=False))" || true

  # 串行买→卖；到配额/硬停即停；BACKFILL_RESPECT_RESERVE 禁生产源 00:00–10:00
  caffeinate -dimsu -t 46800 "$PY" -u "$ROOT/jobs/board_buy_orchestrator.py" \
    --lanes sohu,qq --per-lane 2 --rounds 80 --start 2026-01-01 --until "$UNTIL" || true
  if [[ -f "$ROOT/data/SELL_BACKFILL_PAUSED" ]]; then
    echo "[$(date '+%F %T')] sell backfill paused: $(tr '\n' ' ' < "$ROOT/data/SELL_BACKFILL_PAUSED")"
  else
    caffeinate -dimsu -t 46800 "$PY" -u "$ROOT/jobs/board_sell_orchestrator.py" \
      --per-lane 2 --rounds 60 --lookback-days 400 --until "$UNTIL" || true
  fi

  echo "[$(date '+%F %T')] board_price_night end"
  "$PY" -u -c "from platform_gate import quota_status; import json; print(json.dumps(quota_status(),ensure_ascii=False))" || true
} >>"$LOG" 2>&1
