#!/bin/bash
# =============================================================================
# Stock V2 早盘竞价终选入口（与 V1 / 水下完全隔离）
# - cron 建议 9:24 触发；Python 内等到 09:25 竞价落地
# - 目标 09:27–09:28 单次 PushPlus（本人+好友）；最迟 09:29
# - 日志只写本目录 logs/，禁止依赖 /tmp 外层重定向
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/stock_v2.py"
LOG_DIR="${ROOT}/logs"
LOCK_DIR="${LOG_DIR}/running.lockdir"
DEADLINE_HMS=92900

if ! mkdir -p "$LOG_DIR"; then
  echo "[$(date '+%F %T')] FATAL: 无法创建日志目录 $LOG_DIR" >&2
  exit 1
fi

WRAPPER="${LOG_DIR}/cron_wrapper.log"
LOG="${LOG_DIR}/cron.log"

exec >>"$WRAPPER" 2>&1
echo "[$(date '+%F %T')] ===== stock_v2 entry start pid=$$ ====="

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%F %T')] 已有实例在跑（$LOCK_DIR），本进程退出"
  exit 0
fi

GATE="/Users/admin/new_stock/scripts/a_share_trading_day_gate.sh"
DOW="$(date '+%u')"
if [[ "$DOW" -ge 6 ]]; then
  echo "[$(date '+%F %T')] 周末，跳过"
  exit 0
fi
if [[ -x "$GATE" ]]; then
  if ! "$GATE"; then
    echo "[$(date '+%F %T')] 闸门判定非交易日，跳过"
    exit 0
  fi
  echo "[$(date '+%F %T')] 闸门通过：交易日"
fi

# 过 9:29 不放弃：交由 Python late_rescue（读盘补发 / 今开补算 LATE），禁止「过点不发」
now_hms="$(date '+%H%M%S')"
now_hms=$((10#$now_hms))
LATE_ARGS=()
if [[ "$now_hms" -gt $DEADLINE_HMS ]]; then
  echo "[$(date '+%F %T')] 已过 9:29 → LATE 补发模式（--force --notify）" | tee -a "$LOG"
  LATE_ARGS=(--force)
fi

if [[ -x /usr/local/bin/python3 ]]; then
  PYTHON="/usr/local/bin/python3"
elif [[ -f "/Users/admin/stock_quant_select/.venv/bin/python3" ]]; then
  PYTHON="/Users/admin/stock_quant_select/.venv/bin/python3"
else
  PYTHON="python3"
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
# 早盘假空仓自愈需要 baostock（Stage B）；历史回补脚本另设 DISABLE=1
export DISABLE_BAOSTOCK="${DISABLE_BAOSTOCK:-0}"
# 本机常挂 7890 坏代理，东财/akshare 会连失败；脚本内再 trust_env=False 双保险
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export PUSHPLUS_TOKEN="${PUSHPLUS_TOKEN:-}"
if [[ -z "${PUSHPLUS_TOKEN}" && -f "/Users/admin/new_stock/github/pushplus/token.md" ]]; then
  PUSHPLUS_TOKEN="$(grep -oE '[a-f0-9]{32}' /Users/admin/new_stock/github/pushplus/token.md | head -1 || true)"
  export PUSHPLUS_TOKEN
fi

cd "$ROOT" || exit 1
# cron 常 09:24 触发；必须等到 09:25。注意：date +%H%M%S 有前导 0，[[ ]] 会当八进制炸，一律 10# 十进制。
hms_dec() { local x; x="$(date '+%H%M%S')"; echo $((10#$x)); }
now_hms="$(hms_dec)"
if [[ "$now_hms" -lt 92500 ]]; then
  echo "[$(date '+%F %T')] 未到 09:25（now=$now_hms），等待竞价窗…" | tee -a "$LOG"
  while [[ "$(hms_dec)" -lt 92500 ]]; do sleep 1; done
  echo "[$(date '+%F %T')] 已到 09:25" | tee -a "$LOG"
fi
# macOS bash + set -u：空数组 "${LATE_ARGS[@]}" 会报 unbound，必须分支
if ((${#LATE_ARGS[@]})); then
  echo "[$(date '+%F %T')] ▶ 启动 stock_v2.py --stage auction-final --notify ${LATE_ARGS[*]}" | tee -a "$LOG"
  set +e
  caffeinate -dimsu -t 420 "$PYTHON" -u "$SCRIPT" --stage auction-final --notify "${LATE_ARGS[@]}" 2>&1 | tee -a "$LOG"
  EC=${PIPESTATUS[0]}
  set -e
else
  echo "[$(date '+%F %T')] ▶ 启动 stock_v2.py --stage auction-final --notify" | tee -a "$LOG"
  set +e
  caffeinate -dimsu -t 420 "$PYTHON" -u "$SCRIPT" --stage auction-final --notify 2>&1 | tee -a "$LOG"
  EC=${PIPESTATUS[0]}
  set -e
fi
echo "[$(date '+%F %T')] ===== stock_v2 结束 exit=${EC} =====" | tee -a "$LOG"

find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
exit "${EC:-0}"
