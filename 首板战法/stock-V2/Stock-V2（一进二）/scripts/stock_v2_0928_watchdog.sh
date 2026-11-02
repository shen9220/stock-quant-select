#!/bin/bash
# =============================================================================
# Stock V2 早盘通知看门狗（09:28）
# - 若当日 results 无 OK/NO_TRADE，或未见 notify 成功痕迹 → 立即 --notify 补发（可 LATE）
# - DATA_GAP / 假空仓（预筛大量 turnover_missing）→ 先重跑 d0-preselect 再终选
# - 本人+好友；绝不因「已过点」放弃
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/stock_v2.py"
LOG_DIR="${ROOT}/logs"
LOG="${LOG_DIR}/notify_watchdog.log"
mkdir -p "$LOG_DIR"

exec >>"$LOG" 2>&1
echo "[$(date '+%F %T')] ===== V2 notify watchdog start ====="

GATE="/Users/admin/new_stock/scripts/a_share_trading_day_gate.sh"
DOW="$(date '+%u')"
if [[ "$DOW" -ge 6 ]]; then
  echo "周末跳过"; exit 0
fi
if [[ -x "$GATE" ]] && ! "$GATE"; then
  echo "非交易日跳过"; exit 0
fi

PYTHON="/usr/local/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="python3"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
# 假空仓自愈需要 baostock；禁止再默认 DISABLE=1
export DISABLE_BAOSTOCK="${DISABLE_BAOSTOCK:-0}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
if [[ -z "${PUSHPLUS_TOKEN:-}" && -f "/Users/admin/new_stock/github/pushplus/token.md" ]]; then
  PUSHPLUS_TOKEN="$(grep -oE '[a-f0-9]{32}' /Users/admin/new_stock/github/pushplus/token.md | head -1 || true)"
  export PUSHPLUS_TOKEN
fi

TODAY="$(date '+%Y-%m-%d')"
RESULT="${ROOT}/results/${TODAY}_recommendations.json"
PRE="${ROOT}/cache/${TODAY}_d0_preselect.json"
CRONLOG="${LOG_DIR}/cron.log"

need=0
rerun_pre=0
if [[ ! -f "$RESULT" ]]; then
  echo "无当日结果文件 → 需补跑"
  need=1
  rerun_pre=1
else
  st="$("$PYTHON" - <<PY
import json, os
d=json.load(open("$RESULT",encoding="utf-8"))
print(d.get("status") or "")
print(d.get("mode") or "")
print(len(d.get("recommendations") or []))
print(d.get("reason") or "")
pre_path="$PRE"
miss=0
pre_status=""
if os.path.isfile(pre_path):
    p=json.load(open(pre_path,encoding="utf-8"))
    pre_status=p.get("status") or ""
    miss=int(p.get("turnover_missing_count") or 0)
    if miss<=0:
        miss=sum(1 for r in (p.get("rejects") or []) if (r.get("detail") or "")=="turnover_missing_after_enrich")
print(pre_status)
print(miss)
PY
)"
  status="$(echo "$st" | sed -n '1p')"
  mode="$(echo "$st" | sed -n '2p')"
  nrec="$(echo "$st" | sed -n '3p')"
  reason="$(echo "$st" | sed -n '4p')"
  pre_status="$(echo "$st" | sed -n '5p')"
  miss="$(echo "$st" | sed -n '6p')"
  echo "result status=$status mode=$mode nrec=$nrec reason=$reason pre=$pre_status miss=$miss"
  if [[ "$status" == "DATA_GAP" || "$pre_status" == "DATA_GAP" ]]; then
    echo "检测到 DATA_GAP（假空仓数据缺口）→ 重跑预筛+终选"
    need=1
    rerun_pre=1
  elif [[ "$status" == "NO_TRADE" && "${miss:-0}" -ge 3 ]]; then
    echo "NO_TRADE 但预筛 turnover_missing>=3 → 疑似假空仓，重跑"
    need=1
    rerun_pre=1
  elif [[ "$status" != "OK" && "$status" != "NO_TRADE" ]]; then
    need=1
  fi
  # 窗内真竞价应为 live；若仍是 backfill_open_same_day 且无 Push 成功记录 → 补发
  if ! grep -q "PushPlus 发送成功" "$CRONLOG" 2>/dev/null; then
    # 仅看今日段落
    if ! awk -v d="$TODAY" '$0 ~ d {p=1} p && /PushPlus 发送成功/ {ok=1} END{exit ok?0:1}' "$CRONLOG" 2>/dev/null; then
      echo "今日 cron 未见 PushPlus 成功 → 需补发"
      need=1
    fi
  fi
fi

if [[ "$need" -eq 0 ]]; then
  echo "已有结果且疑似已通知，跳过"
  exit 0
fi

cd "$ROOT" || exit 1
set +e
if [[ "$rerun_pre" -eq 1 ]]; then
  echo "▶ 先重跑 d0-preselect（假空仓自愈）"
  caffeinate -dimsu -t 900 "$PYTHON" -u "$SCRIPT" --stage d0-preselect
fi
echo "▶ 补跑 auction-final --notify --force（允许 LATE）"
caffeinate -dimsu -t 420 "$PYTHON" -u "$SCRIPT" --stage auction-final --notify --force
EC=$?
set -e
echo "[$(date '+%F %T')] ===== watchdog end exit=$EC ====="
exit "${EC:-0}"
