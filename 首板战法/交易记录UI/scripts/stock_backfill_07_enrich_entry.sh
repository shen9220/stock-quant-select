#!/bin/bash
# 07:00 线路A — 纯补价（腾讯/新浪单条；不扫市）
set -euo pipefail
source "$(dirname "$0")/_backfill_common.sh"
SLOT="07:00-A-enrich"
LOG="${BACKFILL_LOG_DIR}/slot_07_enrich.log"
cd "$BACKFILL_ROOT"
export DISABLE_BAOSTOCK=1
export TX_ALLOW_BAOSTOCK_FALLBACK=1

backfill_run_locked "$SLOT" bash -c "
  set -euo pipefail
  echo \"[\$(date '+%F %T')] ===== ${SLOT} 开始 =====\"
  for ym in 2025-07 2025-06 2025-05; do
    \"\$BACKFILL_PYTHON\" jobs/enrich_stock_range.py --program stock --month \"\$ym\" --limit 20 || true
  done
  echo \"[\$(date '+%F %T')] ===== ${SLOT} 结束 =====\"
" >> "$LOG" 2>&1
