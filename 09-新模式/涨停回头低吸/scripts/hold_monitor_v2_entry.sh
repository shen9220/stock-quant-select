#!/bin/bash
# V2 持有池盘中监控（中午 / 尾盘）— 只跑 hold；写 data_v2/
# 用法: hold_monitor_v2_entry.sh midday|tail
set -uo pipefail

SLOT="${1:-midday}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/data_v2/hold_monitor_logs"
LOCK_DIR="${LOG_DIR}/running_${SLOT}.lockdir"
JOB="${ROOT}/jobs/run_v2.py"

mkdir -p "$LOG_DIR" || { echo "FATAL mkdir $LOG_DIR" >&2; exit 1; }
WRAPPER="${LOG_DIR}/${SLOT}_wrapper.log"
LOG="${LOG_DIR}/${SLOT}.log"

exec >>"$WRAPPER" 2>&1
echo "[$(date '+%F %T')] ===== hold_monitor_v2 slot=${SLOT} pid=$$ ====="

cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%F %T')] 已有 V2 ${SLOT} 实例在跑，退出"
  exit 0
fi

DOW="$(date '+%u')"
if [[ "$DOW" -ge 6 ]]; then
  echo "[$(date '+%F %T')] 周末跳过"
  exit 0
fi

GATE="/Users/admin/new_stock/scripts/a_share_trading_day_gate.sh"
if [[ -x "$GATE" ]] && ! "$GATE"; then
  echo "[$(date '+%F %T')] 闸门非交易日，跳过"
  exit 0
fi

PYTHON=/usr/local/bin/python3
[[ -x "$PYTHON" ]] || PYTHON=python3
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK="${DISABLE_BAOSTOCK:-1}"
export TZ="${TZ:-Asia/Shanghai}"

cd "$ROOT" || exit 1
echo "[$(date '+%F %T')] ===== V2 hold start slot=${SLOT} =====" | tee -a "$LOG"
set +e
caffeinate -dimsu -t 900 "$PYTHON" -u "$JOB" --phase hold --slot "$SLOT" 2>&1 | tee -a "$LOG"
EC=${PIPESTATUS[0]}
set -e
echo "[$(date '+%F %T')] ===== V2 hold end exit=${EC} =====" | tee -a "$LOG"
exit "${EC:-0}"
