#!/bin/bash
# 19:00 线路D — 补价 + THS 查缺
set -euo pipefail
source "$(dirname "$0")/_backfill_common.sh"
SLOT="19:00-D-enrich-ths"
LOG="${BACKFILL_LOG_DIR}/slot_19_enrich.log"
DAYS="$(backfill_quota_days ths_gap)"
cd "$BACKFILL_ROOT"
export DISABLE_BAOSTOCK=1
export TX_ALLOW_BAOSTOCK_FALLBACK=1

backfill_run_locked "$SLOT" bash -c "
  set -euo pipefail
  echo \"[\$(date '+%F %T')] ===== ${SLOT} gap_days=${DAYS} =====\"
  for ym in 2025-07 2025-06 2025-05 2025-04; do
    \"\$BACKFILL_PYTHON\" jobs/enrich_stock_range.py --program stock --month \"\$ym\" --limit 20 || true
  done
  caffeinate -dimsu -t 7200 \"\$BACKFILL_PYTHON\" jobs/stock_backfill_scheduler.py --run --phase gap --days ${DAYS}
  echo \"[\$(date '+%F %T')] ===== ${SLOT} 结束 =====\"
" >> "$LOG" 2>&1
