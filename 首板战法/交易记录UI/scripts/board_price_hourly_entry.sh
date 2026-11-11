#!/bin/bash
# 主板/创业板 买卖价 — 每小时入口（平台软上限硬停）
# 顺序：月份倒序，月内先创业板全部，再主板全部，回到 2026-01。
#
# 时间窗（backfill_window 统一）：
#   每日 10:00 → 次日 00:00；过零点硬停，第二天 10:00 续
#   00:00–10:00 全体安静（护 07:00–10:00 正式通知）
# 铁律：不批量硬撞；小时软上限到了就停。旧卖价另走证券宝 5 分钟，每天最多 8 个买入日。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data/board_price_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/hourly_wrapper.log"
PY="/usr/local/bin/python3"
[[ -x "$PY" ]] || PY="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK=1
export BACKFILL_RESPECT_RESERVE=1
export PYTHONPATH="$ROOT/lib:$ROOT/jobs:$ROOT:${PYTHONPATH:-}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

# V2 历史若未暂停，价格回补仍可跑（与涨停池不同源）；不强制 V2_PAUSE

GATE="$("$PY" - <<'PY'
from backfill_window import quiet_reason, hour_slot_until, describe
q = quiet_reason()
if q:
    print("SKIP|" + q)
else:
    print("RUN|" + hour_slot_until() + "|" + describe())
PY
)"

{
  echo "[$(date '+%F %T')] board_price_hourly gate=$GATE"
  case "$GATE" in
    SKIP\|*)
      echo "[$(date '+%F %T')] skip: ${GATE#SKIP|}"
      exit 0
      ;;
  esac
  # GATE=RUN|<until>|<describe>
  UNTIL="$(printf '%s' "$GATE" | cut -d'|' -f2)"
  echo "[$(date '+%F %T')] start until=$UNTIL (hour slot; session hard-stop 00:00)"
  "$PY" -u -c "from platform_gate import quota_status; import json; print(json.dumps(quota_status(),ensure_ascii=False))" || true

  # 买→卖串行：避免同机抢锁；各轨内部仍单源单线程；00:00 后仅 sohu 等非生产源可继续
  caffeinate -dimsu -t 3500 "$PY" -u "$ROOT/jobs/board_buy_orchestrator.py" \
    --lanes sohu,qq --per-lane 2 --rounds 40 --start 2026-01-01 --until "$UNTIL" \
    || true
  # 2026-11-04：涨停池连板误入首板，先停卖价回补，复核完成前不打卖价。
  if [[ -f "$ROOT/data/SELL_BACKFILL_PAUSED" ]]; then
    echo "[$(date '+%F %T')] sell backfill paused: $(tr '\n' ' ' < "$ROOT/data/SELL_BACKFILL_PAUSED")"
  else
    caffeinate -dimsu -t 3500 "$PY" -u "$ROOT/jobs/board_sell_orchestrator.py" \
      --per-lane 2 --rounds 40 --lookback-days 400 --until "$UNTIL" \
      || true
  fi

  echo "[$(date '+%F %T')] board_price_hourly end"
  "$PY" -u -c "from platform_gate import quota_status; import json; print(json.dumps(quota_status(),ensure_ascii=False))" || true
} >>"$LOG" 2>&1
