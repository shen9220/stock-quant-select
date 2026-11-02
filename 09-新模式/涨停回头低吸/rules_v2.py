"""涨停回头低吸 V2 规则常量（权威：文档/涨停回头低吸 gpt 的 V2 版本.md V2.3）。

与 rules.py（V1）完全隔离；改 V2 只改本文件。
"""
from __future__ import annotations

STRATEGY_VERSION = "V2.3"
EXPERIMENT_ID = "V2_MAIN"  # 首月主组：D4 主窗 + 缩量 + 结构 + 二次启动确认
PAPER_MODE = True          # 首月默认模拟，不发真实委托

# ── 基础过滤（与 V1 对齐，保证可比）────────────────────────
MAX_PRICE = 30.0
MAX_PE_TTM = 28.0
MIN_LIST_YEARS = 5
MIN_DIVIDEND_PAYOUT = 0.20
LOOKBACK_LIMIT_UP_DAYS = 10
MAX_OBS_DAYS = 14
MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")

# ── 观察 / 建仓窗（GPT V2.3：D3 不自动买；D4 主窗；D5 补充）──
WATCH_ADJ_DAYS = (3, 4, 5)
BUILD_ADJ_DAYS = (4, 5)          # 自动建仓仅 D4/D5
BUILD_PRIMARY_DAY = 4            # 主验证窗口
BUILD_SUPPLEMENT_DAY = 5         # 补充窗口（非最高优先级）
D3_AUTO_BUY = False              # 主策略禁止 D3 自动买
ENABLE_D3_STRONG_SHADOW = False  # 影子组默认关；单独实验再开

# ── 仓位（首月关补仓；与 V1 对照用固定 50% 模拟组）────────
FIRST_BUY_PCT = 50.0
ADD_BUY_ENABLED = False
MAX_CONCURRENT_HOLDINGS = 2
HOLD_MAX_DAYS = 7
MAX_SINGLE_RISK_PCT = 6.0        # 入场至止损最大风险上限（实验 CAP6 基线）

# ── 缩量（主组用 50%；40/60 仅实验开关）──────────────────
VOL_PULLBACK_RATIO = 0.50        # C2：信号/回调日量 ≤ 首板日 × 此值
VOL_PULLBACK_EXPERIMENT = "VOL_PULLBACK_50"
VOL_DUMP_RATIO = 0.80            # 放量阴线：量≥首板 80% 且收阴 → 拒绝
VOL_CONFIRM_VS_PULLBACK = 1.0    # 启动确认：相对回调均量回升（不要求=首板 50%）
# 说明：C2 与「启动放量」分属两阶段，主组不要求同一根 K 同时≤50%又≥首板50%

# ── 确认次日执行 ──────────────────────────────────────────
CONFIRM_NEXT_DAY_EXEC = True
SKIP_GAP_UP_PCT = 3.0            # 次日相对信号日收盘高开超过此值则放弃（实验阈值）
ALLOWED_SLOTS = ("morning", "midday", "tail")  # unknown 不自动买

# ── 止损 / 止盈（主组：结构 + CAP6；移动止盈用 TRAIL_TIGHT 实验）──
STOP_MODEL = "STOP_STRUCT_CAP6"
RISK_CAP_PCT = -6.0              # 最大风险上限（相对成本）
USE_MA30_AS_PRIMARY_STOP = False
TRAIL_MODEL = "TRAIL_TIGHT"
TRAIL_ARM_BREAKEVEN_PCT = 2.0    # 浮盈达此值后武装保本
TRAIL_LOOSE_PEAK = 3.0           # ≥3% 且 <5%：峰回撤 1.5%
TRAIL_LOOSE_DD = 1.5
TRAIL_TIGHT_PEAK = 5.0           # ≥5%：峰回撤 1.0%
TRAIL_TIGHT_DD = 1.0
# 涨停次日：主策略不强制 70/30；影子组开关
LIMITUP_NEXTDAY_70_30 = False

# ── 状态 ──────────────────────────────────────────────────
ZT_ACTIVE = "in_pool"
ZT_WATCHED = "watched"
ZT_EXPIRED = "expired"
WATCH_WATCHING = "watching"
WATCH_BOUGHT = "bought"
WATCH_EXPIRED = "expired"
POS_HOLDING = "holding"
POS_SOLD = "sold"
POS_STOPPED = "stopped"
POS_EXIT_PENDING_T1 = "exit_pending_t1"

WATCH_MAX_ACTIVE = 30
SCAN_RECENT_DAYS = 5
MORNING_SCAN_DAYS = 8
MORNING_JOB_HOUR = 9
MORNING_JOB_MINUTE = 40
HOLD_MONITOR_MIDDAY = (11, 25)
HOLD_MONITOR_TAIL = (14, 30)
AUCTION_SINGLE_SLEEP = 0.45

# 龙头潜力评分门槛（评分只定优先级，不可单独触发买入）
LEADER_SCORE_MIN = 55.0
LEADER_SCORE_ENABLED = True  # 数据不全时降权，不默认满分
