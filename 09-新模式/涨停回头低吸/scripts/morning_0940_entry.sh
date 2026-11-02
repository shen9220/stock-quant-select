#!/bin/bash
# 交易日 9:40 — 涨停回头低吸日更（涨停池写入 + 观察池晋升 + 持有买卖扫描）
# 【铁律】禁止 crontab 外层重定向到 /tmp；本脚本 mkdir 持久目录后再 exec 重定向
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT}/data/morning_0940_logs"
LOCK_DIR="${LOG_DIR}/running.lockdir"
JOB="${ROOT}/jobs/run_morning_0940.py"

mkdir -p "$LOG_DIR" || { echo "FATAL mkdir $LOG_DIR" >&2; exit 1; }
WRAPPER="${LOG_DIR}/cron_wrapper.log"
LOG="${LOG_DIR}/cron.log"

exec >>"$WRAPPER" 2>&1
echo "[$(date '+%F %T')] ===== morning_0940 entry pid=$$ ====="

cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%F %T')] 已有实例在跑，退出"
  exit 0
fi

DOW="$(date '+%u')"
if [[ "$DOW" -ge 6 ]]; then
  echo "[$(date '+%F %T')] 周末跳过"
  exit 0
fi

GATE="/Users/admin/new_stock/scripts/a_share_trading_day_gate.sh"
if [[ -x "$GATE" ]]; then
  if ! "$GATE"; then
    echo "[$(date '+%F %T')] 闸门非交易日，跳过"
    exit 0
  fi
fi

if [[ -x /usr/local/bin/python3 ]]; then
  PYTHON=/usr/local/bin/python3
else
  PYTHON=python3
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK="${DISABLE_BAOSTOCK:-1}"
export TZ="${TZ:-Asia/Shanghai}"

cd "$ROOT" || exit 1
echo "[$(date '+%F %T')] ===== job start =====" | tee -a "$LOG"
set +e
caffeinate -dimsu -t 3600 "$PYTHON" -u "$JOB" --phase all --slot morning 2>&1 | tee -a "$LOG"
EC=${PIPESTATUS[0]}
set -e
echo "[$(date '+%F %T')] ===== job end exit=${EC} =====" | tee -a "$LOG"
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
exit "${EC:-0}"
