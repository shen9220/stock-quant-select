#!/bin/bash
# =============================================================================
# Stock V2 D0 预筛（早盘加速）：建议 08:40 跑，冻涨停池/硬过滤，写出 cache
# 终选仍由 stock_v2_0924_entry.sh 负责竞价+推送
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/stock_v2.py"
LOG_DIR="${ROOT}/logs"
LOCK_DIR="${LOG_DIR}/d0.lockdir"

mkdir -p "$LOG_DIR" || exit 1
WRAPPER="${LOG_DIR}/d0_wrapper.log"
LOG="${LOG_DIR}/d0_cron.log"
exec >>"$WRAPPER" 2>&1
echo "[$(date '+%F %T')] ===== stock_v2 d0-preselect start pid=$$ ====="

cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%F %T')] 已有 d0 实例，退出"
  exit 0
fi

GATE="/Users/admin/new_stock/scripts/a_share_trading_day_gate.sh"
DOW="$(date '+%u')"
if [[ "$DOW" -ge 6 ]]; then
  echo "[$(date '+%F %T')] 周末，跳过"
  exit 0
fi
if [[ -x "$GATE" ]] && ! "$GATE"; then
  echo "[$(date '+%F %T')] 非交易日，跳过"
  exit 0
fi

PYTHON="/usr/local/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
# 早盘 Stage B 假空仓兜底需要 baostock；历史回补脚本另设 DISABLE=1
export DISABLE_BAOSTOCK="${DISABLE_BAOSTOCK:-0}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

cd "$ROOT" || exit 1
set +e
caffeinate -dimsu -t 900 "$PYTHON" -u "$SCRIPT" --stage d0-preselect 2>&1 | tee -a "$LOG"
EC=${PIPESTATUS[0]}
set -e
echo "[$(date '+%F %T')] ===== d0-preselect 结束 exit=${EC} =====" | tee -a "$LOG"
exit "${EC:-0}"
