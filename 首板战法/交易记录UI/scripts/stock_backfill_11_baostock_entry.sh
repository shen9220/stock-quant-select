#!/bin/bash
# 11:00 线路B — Baostock 倒序写明细（全天唯一 login）
set -euo pipefail
source "$(dirname "$0")/_backfill_common.sh"
SLOT="11:00-B-baostock"
LOG="${BACKFILL_LOG_DIR}/slot_11_baostock.log"
DAYS="$(backfill_quota_days baostock_reverse)"
cd "$BACKFILL_ROOT"
unset DISABLE_BAOSTOCK || true

backfill_run_locked "$SLOT" bash -c "
  set -euo pipefail
  echo \"[\$(date '+%F %T')] ===== ${SLOT} days=${DAYS} =====\"
  caffeinate -dimsu -t 10800 \"\$BACKFILL_PYTHON\" jobs/baostock_backfill_scheduler.py --run --no-enrich --days ${DAYS}
  echo \"[\$(date '+%F %T')] ===== ${SLOT} 结束 =====\"
" >> "$LOG" 2>&1
