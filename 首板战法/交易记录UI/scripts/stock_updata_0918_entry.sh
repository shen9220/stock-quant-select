#!/bin/bash
# =============================================================================
# stock_updata 早盘入口（铁律）
# - 9:23 cron/launchd 触发预热；【9:24:40 准时】进入扫描
# - 目标 9:28 推送，最迟 9:29 PushPlus 必达
# - 日志只写项目内持久目录，禁止依赖 /tmp 外层重定向（2026-09-09 事故）
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QUANT="$(cd "$ROOT/.." && pwd)"
# 水下脚本：Stock-V1（水下）/stock_updata.py（program id 仍为 stock_updata）
SCRIPT="${QUANT}/Stock-V1（水下）/stock_updata.py"
LOG_DIR="${ROOT}/data/stock_updata_logs"
LOCK_DIR="${LOG_DIR}/running.lockdir"
DEADLINE_HMS=92900

# ① 第一件事：持久日志目录（必须成功，否则本机告警退出）
if ! mkdir -p "$LOG_DIR"; then
  echo "[$(date '+%F %T')] FATAL: 无法创建日志目录 $LOG_DIR" >&2
  exit 1
fi

WRAPPER="${LOG_DIR}/cron_wrapper.log"
LOG="${LOG_DIR}/cron.log"

# ② 自身重定向到持久路径（目录已存在 → 永不因重定向失败）
exec >>"$WRAPPER" 2>&1
echo "[$(date '+%F %T')] ===== entry start pid=$$ ====="

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

# ③ 互斥：cron + launchd 双触发时只跑一份
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%F %T')] 已有实例在跑（$LOCK_DIR），本进程退出"
  exit 0
fi

# ④ 交易日闸门（工作日闸门异常则 fail-open，避免漏跑）
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

# ⑤ 9:23 预热后立刻启 Python（由 py 内精确等到 9:24:40 开扫，避免 shell 等到点再 import 迟到）
now_hms="$(date '+%H%M%S')"
now_hms=$((10#$now_hms))
if [[ "$now_hms" -gt $DEADLINE_HMS ]]; then
  echo "[$(date '+%F %T')] 已过 9:29，放弃（请人工 --force）"
  exit 0
fi
echo "[$(date '+%F %T')] ▶ 启动 stock_updata.py（目标开扫 09:24:40）"

if [[ -f "${QUANT}/../../.venv/bin/python3" ]]; then
  PYTHON="${QUANT}/../../.venv/bin/python3"
elif [[ -x /usr/local/bin/python3 ]]; then
  PYTHON="/usr/local/bin/python3"
else
  PYTHON="python3"
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export DISABLE_BAOSTOCK="${DISABLE_BAOSTOCK:-1}"
export PUSHPLUS_TOKEN="${PUSHPLUS_TOKEN:-}"
if [[ -z "${PUSHPLUS_TOKEN}" && -f "/Users/admin/new_stock/github/pushplus/token.md" ]]; then
  PUSHPLUS_TOKEN="$(grep -oE '[a-f0-9]{32}' /Users/admin/new_stock/github/pushplus/token.md | head -1 || true)"
  export PUSHPLUS_TOKEN
fi

cd "$(dirname "$SCRIPT")" || exit 1
echo "[$(date '+%F %T')] ===== stock_updata 开始 =====" | tee -a "$LOG"
set +e
# 输出同时进 cron.log（Python 侧 print）与 wrapper
caffeinate -dimsu -t 420 "$PYTHON" -u "$SCRIPT" 2>&1 | tee -a "$LOG"
EC=${PIPESTATUS[0]}
set -e
echo "[$(date '+%F %T')] ===== stock_updata 结束 exit=${EC} =====" | tee -a "$LOG"

find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
exit "${EC:-0}"
