"""已确认业务规则常量（V2.1）。改规则先改本文件。"""
from __future__ import annotations

# ── 选股硬过滤 ─────────────────────────────────────────────
MAX_PRICE = 30.0
MAX_PE_TTM = 28.0
MIN_LIST_YEARS = 5
MIN_DIVIDEND_PAYOUT = 0.20  # 连续两年分红率
LOOKBACK_LIMIT_UP_DAYS = 10  # 含昨日：昨日首次涨停
MAX_OBS_DAYS = 14  # 观察最多 14 个交易日（涨停后第1日…第14日）

# 主板（含原中小板 002/003）
MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")

# ── 仓位 / 持仓 ───────────────────────────────────────────
FIRST_BUY_PCT = 50.0  # 建仓半仓
ADD_BUY_PCT = 50.0  # 补仓再半仓
MAX_CONCURRENT_HOLDINGS = 2  # 最多同时持有 2 只
HOLD_MAX_DAYS = 7  # 从买入日起算，第 7 日到期卖

# 调整日（涨停后第 1 个交易日 = adj_day 1）
BUILD_ADJ_DAYS = (3, 4)  # 3–4 建仓
ADD_ADJ_DAYS = (4, 5)  # 4–5 可补仓（下跌/十字星等）

# ── 买卖触发 ──────────────────────────────────────────────
BUY_TIME = "14:30"
SELL_DAY7_TIME = "14:00"
BUY_DROP_PCT = -3.0  # 当日跌幅 ≤ -3% 可触发买入参考
# 止损（放宽）：跌破 30 日线 或 浮亏 ≤ -6%，两条件任一即卖
STOP_LOSS_MA_PCT = -6.0    # 百分比硬止损
STOP_LOSS_MIN_PCT = -6.0   # 兼容旧名：展示/主档止损收益%
STOP_LOSS_MAX_PCT = -6.0   # 兼容旧名
STOP_LOSS_PCT = STOP_LOSS_MA_PCT
# 止盈：涨停收盘当日不卖；次日看开盘溢价（有溢价高开卖 / 无溢价也卖）
RALLY_SELL_PCT = 7.0  # 已废弃同日冲高卖；保留常量兼容
LIMIT_UP_EXIT_NEXT_DAY = True

# ── 状态：涨停池 / 观察池 / 持有池 ─────────────────────────
# 涨停池
ZT_ACTIVE = "in_pool"          # 在涨停池
ZT_WATCHED = "watched"         # 已转入观察池
ZT_EXPIRED = "expired"

# 观察池
WATCH_WATCHING = "watching"    # 观察中（可买入）
WATCH_BOUGHT = "bought"
WATCH_EXPIRED = "expired"

# 兼容旧常量
OBS_OBSERVING = WATCH_WATCHING
OBS_BOUGHT = WATCH_BOUGHT
OBS_EXPIRED = WATCH_EXPIRED

POS_HOLDING = "holding"
POS_SOLD = "sold"
POS_STOPPED = "stopped"

# 进入观察池：调整第 3–5 天且股价在 10–20 日线之间
WATCH_ADJ_DAYS = (3, 4, 5)
# 每日观察池可同时 tracking 上限（符合条件应尽量多选，非一天一只）
WATCH_MAX_ACTIVE = 30
# 适宜度：优先 D3→D4→D5；低吸更深（更靠近 MA20）更好

# 近 N 个交易日涨停数据拉取
SCAN_RECENT_DAYS = 5

# ── 交易日流水线（铁律）──────────────────────────────────
# 9:40 顺序：涨停池 → 观察池 → 持有池（上午开盘后一次）
# 持有池额外：中午收盘前后、下午尾盘各一次（实时监控买卖）
MORNING_JOB_HOUR = 9
MORNING_JOB_MINUTE = 40
MORNING_SCAN_DAYS = 8
HOLD_MONITOR_MIDDAY = (11, 25)   # 中午收盘前
HOLD_MONITOR_TAIL = (14, 30)     # 尾盘 / 与买入参考时点对齐

# ── 集合竞价拉取（回头低吸只写库，不推送；推送仅 stock_updata）──
# 9:24:40 起跑 → 9:25 今开落地 → 目标 9:28 写完 → 最迟 9:29
AUCTION_SCAN_START = (9, 24, 40)
AUCTION_OPEN_TIME = (9, 25, 0)
AUCTION_SOFT_DEADLINE = (9, 28, 0)
AUCTION_HARD_DEADLINE = (9, 29, 0)
# 【铁律】腾讯实时行情禁止批量：每次 1 只 + 间隔
AUCTION_SINGLE_SLEEP = 0.45
AUCTION_POLL_SEC = 1.5
AUCTION_MIN_COVERAGE = 0.85

