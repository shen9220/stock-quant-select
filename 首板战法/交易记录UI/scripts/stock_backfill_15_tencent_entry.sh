#!/bin/bash
# 15:00 线路C — 腾讯/THS 正序
set -euo pipefail
source "$(dirname "$0")/_backfill_common.sh"
SLOT="15:00-C-tencent"
LOG="${BACKFILL_LOG_DIR}/slot_15_tencent.log"
DAYS="$(backfill_quota_days tencent_forward)"
cd "$BACKFILL_ROOT"
export DISABLE_BAOSTOCK=1
export TX_ALLOW_BAOSTOCK_FALLBACK=1

backfill_run_locked "$SLOT" bash -c "
  set -euo pipefail
  echo \"[\$(date '+%F %T')] ===== ${SLOT} days=${DAYS} =====\"
  caffeinate -dimsu -t 10800 \"\$BACKFILL_PYTHON\" jobs/stock_backfill_scheduler.py --run --phase forward --days ${DAYS}
  echo \"[\$(date '+%F %T')] ===== ${SLOT} 结束 =====\"
" >> "$LOG" 2>&1
