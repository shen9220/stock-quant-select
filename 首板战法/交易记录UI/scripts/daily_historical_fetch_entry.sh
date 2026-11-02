#!/bin/bash
# cron 入口：每日限量历史回填（非交易时段）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/lib:$ROOT/jobs:$ROOT:${PYTHONPATH:-}"
LOG_DIR="${ROOT}/data/historical_fetch_logs"
mkdir -p "$LOG_DIR"

if [[ -f "${ROOT}/../../../.venv/bin/python3" ]]; then
    PYTHON="${ROOT}/../../../.venv/bin/python3"
elif command -v /usr/local/bin/python3 >/dev/null; then
    PYTHON="/usr/local/bin/python3"
else
    PYTHON="python3"
fi

cd "$ROOT"
export DISABLE_BAOSTOCK=1

echo "[$(date '+%F %T')] daily_historical_fetch 开始" >> "${LOG_DIR}/cron.log"
"$PYTHON" jobs/daily_historical_fetch.py --program all --days 1 --mode auto --ignore-pause \
    >> "${LOG_DIR}/cron.log" 2>&1 || true
echo "[$(date '+%F %T')] daily_historical_fetch 结束 exit=$?" >> "${LOG_DIR}/cron.log"
