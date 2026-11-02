#!/bin/bash
# 四批回填共享配置
set -euo pipefail

_BACKFILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BACKFILL_ROOT="$_BACKFILL_ROOT"
export BACKFILL_LOG_DIR="${BACKFILL_ROOT}/data/historical_fetch_logs"
export BACKFILL_SCRIPTS="${BACKFILL_ROOT}/scripts"
mkdir -p "$BACKFILL_LOG_DIR"

if [[ -f "${BACKFILL_ROOT}/../../../.venv/bin/python3" ]]; then
    export BACKFILL_PYTHON="${BACKFILL_ROOT}/../../../.venv/bin/python3"
elif command -v /usr/local/bin/python3 >/dev/null; then
    export BACKFILL_PYTHON="/usr/local/bin/python3"
else
    export BACKFILL_PYTHON="python3"
fi

export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:${PATH:-}"
export PYTHONPATH="${BACKFILL_ROOT}/lib:${BACKFILL_ROOT}/jobs:${BACKFILL_ROOT}:${PYTHONPATH:-}"

backfill_quota_days() {
    "$BACKFILL_PYTHON" "${BACKFILL_SCRIPTS}/backfill_quota.py" "$1"
}

# 每批开始前：若 Amphetamine 未会话则唤起（弥补漏开系统防睡眠）
backfill_ensure_awake() {
    bash "${BACKFILL_SCRIPTS}/amphetamine_backfill_on.sh" >> "${BACKFILL_LOG_DIR}/amphetamine.log" 2>&1 || true
}

backfill_run_locked() {
    local slot="$1"
    shift
    backfill_ensure_awake
    "$BACKFILL_PYTHON" "${BACKFILL_SCRIPTS}/backfill_master_lock.py" "$slot" "$@"
}
