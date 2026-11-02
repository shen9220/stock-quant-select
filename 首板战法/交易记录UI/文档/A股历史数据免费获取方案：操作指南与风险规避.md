# A股历史数据免费获取方案：操作指南与风险规避

> **适用项目**：首板战法 · 交易记录看板  
> **目标**：近 3 年（2023–2025）历史数据**缓慢、安全**补全，不影响每日 stock/slow/up 产出  
> **最后更新**：2026-08-21（已实测本机环境）

---

## 1. 结论先行（建议方案）

| 用途 | 建议数据源 | 实际代码位置 | 是否用于每日 cron |
|------|-----------|-------------|------------------|
| 历史首板涨停池 | **同花顺 THS** `limit_up_pool` | `historical_pool.py` | ✅ 是 |
| 日线 OHLC / 补价 | **腾讯财经** `ak.stock_zh_a_hist_tx` | `price_service._fetch_daily` | ✅ 是 |
| 当日实时价 | **新浪** `hq.sinajs.cn` | `price_service._fetch_today_ohlc_sina` | ✅ 是（仅当日） |
| 分钟 9:35/13:15 | 新浪 1 分钟（仅近 ~5 日）→ 日线近似 | `price_service.get_minute_price` | 历史回填兜底 |
| 交易日历 | **本地缓存** 周末+法定假日 | `fetch_calendar.py` | ✅ 是 |
| 东财 AKShare | 仅作备用 | `price_service` 第 3 顺位 | ❌ 默认不用 |
| **Baostock** | **限量正序回填（16:00）** | `baostock_backfill_scheduler.py` | ✅ **仅 16:00 专用入口**；禁止 CLI bulk |

**每日策略**：用 `daily_historical_fetch.py` 每天只处理 **1 个交易日 × 每程序**，THS 有数据才写明细，补价走腾讯。

---

## 2. 2026-08-21 实测调研结果

在本机（`DISABLE_BAOSTOCK=1`）探测：

| 接口 | 测试 | 结果 | 延迟 |
|------|------|------|------|
| 腾讯日线 000001 | 2025-12 整月 | ✅ 23 条 | ~1.2s |
| 同花顺涨停池 | 20251215 | ✅ 57 只 | ~0.2s |
| 同花顺涨停池 | 20230615 / 20240115 | ❌ 0 只 | — |
| 同花顺涨停池 | 20250901 / 20251201 | ✅ 有数据 | 不稳定，按日变化 |
| 新浪实时 | 000001 | ✅ | ~0.2s |
| 新浪 1 分钟历史 | 2025-12-15 | ❌ 无（仅近几日） | — |
| 东财日线 AKShare | 000001 | ❌ 连接断开 | — |
| 东财涨停池 AKShare | 20251215 | ❌ 空 | — |
| AKShare 交易日历 | sina | ❌ py_mini_racer 架构不兼容 | — |
| Baostock login | — | ❌ **已封禁** | — |

### 2.1 关键结论

1. **同花顺 THS 不是完整 3 年库**：历史日期常返回空，仅**部分近期日期**有涨停池；无 THS 数据的日期需 **腾讯全市场扫描**（极慢，单独周末跑）。
2. **腾讯日线稳定**，是个股补价首选；单股单次约 0.5–1.5s，**不可**一天扫全市场 4000+ 只。
3. **东财在本环境不稳定**，不要作为历史回填主通道。
4. **Baostock 因高并发 login 已封禁**（见 `00_项目必读/行情数据拉取规范.md`），日常必须 `DISABLE_BAOSTOCK=1`。

---

## 3. 限流 / 封号风险（必读）

| 风险 | 触发条件 | 后果 | 本项目防护 |
|------|---------|------|-----------|
| **IP 临时封禁** | 东财/同花顺 >60 次/分钟 | 503/空数据/断连 | 每日配额 + sleep |
| **Baostock 黑名单** | 多线程反复 `login()` / 无间隔 bulk | 账号/IP 拉黑 | 仅 16:00 调度器：单 login、串行、配额；命中写 `BAOSTOCK_PAUSED` |
| **THS 空数据误判** | 非交易日 / 接口无历史 | 漏补 | 标记跳过 + 周末 tx-scan |
| **全市场扫描过载** | 4000 股 × 1s ≈ 67 分钟/天 | IP 风控 | 仅 `--mode tx-scan` 周末手动 |
| **与日常 cron 冲突** | 15:05 补全 + 回填同时跑 | 接口争抢 | 回填排 22:30，避开交易时段 |

### 3.1 每日硬上限（脚本内置）

| 配额项 | 默认值 | 说明 |
|--------|--------|------|
| 处理交易日 | **1 天/次** | 每程序 |
| THS 请求 | **≤5 次** | 间隔 ≥1.2s |
| K 线请求 | **≤120 次** | 间隔 ≥0.45s |
| 补价笔数 | **≤12 笔** | 间隔 ≥0.35s |

### 3.2 禁止操作

```bash
# ❌ 禁止：baostock 全市场 + 多 worker
python3 backfill_stock_year.py --source baostock --workers 6

# ❌ 禁止：一次跑全年
python3 backfill_slow_up_historical.py --months 2023-01,...,2025-12 --commit --enrich

# ❌ 禁止：白天交易时段 bulk 拉数（9:30–15:00）

# ❌ 禁止：login 失败后循环重试
```

---

## 4. 实际拉取操作

### 4.1 环境

```bash
cd 01_量化模型/首板战法/每日交易记录
export DISABLE_BAOSTOCK=1   # 铁纪律
pip install akshare pandas requests  # 已装可跳过
```

### 4.2 每日限量脚本（推荐）

```bash
# 查看近 3 年回填进度
python3 daily_historical_fetch.py --status

# 每日自动模式：THS 有数据才补，无则跳过（安全）
python3 daily_historical_fetch.py --program all --days 1 --mode auto --ignore-pause

# 仅 slow 补 1 天
python3 daily_historical_fetch.py --program slow --days 1 --ignore-pause

# 预览不写入
python3 daily_historical_fetch.py --program stock --days 1 --dry-run
```

**状态文件**：`data/historical_fetch_state.json`  
**日志**：`data/historical_fetch_logs/fetch.log`

### 4.3 cron 定时

| 时间 | 脚本 | 数据源 | 说明 |
|------|------|--------|------|
| **03:00** | `scripts/stock_backfill_03_night_entry.sh` | **Baostock 倒序** | 优先 7→1 月；**只写明细不补价**；3 天/次 |
| **16:00** | `scripts/stock_backfill_16_baostock_entry.sh` | **Baostock 倒序** | 优先 7→1 月；含补价；3 天/次；倒序完再恢复正序 |
| **18:30** | `scripts/stock_backfill_17_entry.sh` | 正序补洞 | THS→Baostock 单日→腾讯；目标写出 1–3 天；`TX_ALLOW_BAOSTOCK_FALLBACK=1` |

日合计约 **6–9 个交易日**（3+3+1~3）。三条线共享「已有买入日则跳过」；Baostock 倒序与正序兜底错峰（16:00 / 18:30）。


```bash
# 状态
python3 baostock_backfill_scheduler.py --status
python3 stock_backfill_scheduler.py --status

# ❌ 禁止：CLI 开 baostock bulk / workers>1
# python3 ../stock/scripts/backfill_stock_year.py --source baostock --workers 6
```

**Baostock 日志**：`/tmp/stock_backfill_16_baostock_logs/cron.log`  
**18:30 日志**：`/tmp/stock_backfill_17_logs/cron.log`  
**03:00 日志**：`/tmp/stock_backfill_03_night_logs/cron.log`  
**状态**：`data/baostock_backfill_schedule.json` / `data/stock_backfill_schedule.json`  
**紧急暂停**：`data/BAOSTOCK_PAUSED`（出现后 cron 自动空跑，勿删后立刻重试轰炸）

### 4.3b 旧示例 22:30

```bash
chmod +x 每日交易记录/scripts/daily_historical_fetch_entry.sh
30 22 * * * /path/to/每日交易记录/scripts/daily_historical_fetch_entry.sh
```
### 4.4 手动单月批量（慎用，需删 BACKFILL_PAUSED）

仅在确认无其他拉数任务时：

```bash
# slow / up 整月（会连续跑，建议改用小脚本 daily 模式代替）
rm data/BACKFILL_PAUSED   # 确认后
DISABLE_BAOSTOCK=1 python3 backfill_slow_up_historical.py \
  --months 2025-11 --program slow --commit --enrich

# stock 整月
DISABLE_BAOSTOCK=1 python3 ../stock/scripts/backfill_stock_year.py \
  --year 2025 --months 2025-11 --source tx --records-only
DISABLE_BAOSTOCK=1 python3 ../stock/scripts/backfill_stock_year.py \
  --year 2025 --months 2025-11 --enrich-only
```

### 4.5 早期历史（THS 无数据）— 周末 tx-scan

THS 无法覆盖的 2023–2024 日期，用腾讯扫描首板（**极慢，每周最多 1–2 天**）：

```bash
# 周末夜间，单天，workers=1
DISABLE_BAOSTOCK=1 python3 daily_historical_fetch.py \
  --program stock --days 1 --mode tx-scan --ignore-pause
```

预计：全市场 ~4000 股 × 0.8s ≈ **50–90 分钟/天**，勿连续多跑。

---

## 5. 数据流（与现有代码一致）

```
D0 选股日
  ├─ THS limit_up_pool ──→ 首板列表
  ├─ 腾讯日线 ──→ 成交额/涨跌幅 ──→ Top2 排名
  └─ 写入 results/*_recommendations.md

D1 买入日
  ├─ 写入 trades/{program}/{D1}.json
  └─ enrich_trade_prices()
        ├─ 腾讯日线：买入开盘价
        ├─ 新浪分钟 / 日线近似：9:35、13:15
        └─ 盈亏 8 列重算
```

---

## 6. 近 3 年补全计划

| 阶段 | 程序 | 范围 | 方式 | 状态 |
|------|------|------|------|------|
| 1 | slow | 2025-12 | 手动批量 | ✅ 已完成 |
| 2 | slow/stock | 2025 余月 | daily 1 天/晚 | 进行中 |
| 3 | stock | 2024 | THS 能补则补，其余 tx-scan 周末 | 待开始 |
| 4 | 全部 | 2023 | tx-scan 周末 | 待开始 |
| 5 | up | 2023–2025 | **必须在 stock+slow 完成后** | 未开始 |

**预估**：daily 模式 1 天/程序/晚 ≈ 750 交易日 × 3 程序 ≈ **2+ 年**；配合周末 tx-scan 可加速 stock 早期。

---

## 7. 应急处理

| 现象 | 处理 |
|------|------|
| THS 返回空但应是交易日 | 改日重试；或 `--mode tx-scan` 周末补 |
| 腾讯 Connection reset | 停止脚本，等 30 分钟，减配额重跑 |
| Baostock login 失败 | **立即停止**，确认 `DISABLE_BAOSTOCK=1` |
| 补价全空 | 检查 `DISABLE_BAOSTOCK`；看腾讯是否可用 |
| 与 15:05 收盘补全冲突 | 停止回填，优先保 daily 产出 |

---

## 8. 相关文件

| 文件 | 作用 |
|------|------|
| `daily_historical_fetch.py` | **每日限量回填主脚本** |
| `fetch_calendar.py` | 交易日历（不依赖 baostock） |
| `price_service.py` | 腾讯/新浪价源 |
| `historical_pool.py` | THS 首板 + slow/up 回退 |
| `backfill_slow_up_historical.py` | slow/up 批量回填 |
| `../stock/scripts/backfill_stock_year.py` | stock 批量回填 |
| `data/BACKFILL_PAUSED` | 暂停开关 |
| `data/historical_fetch_state.json` | 每日进度 |
| `00_项目必读/行情数据拉取规范.md` | 铁纪律 |

---

## 9. 数据源对比（修订版）

| 数据源 | 本项目实测 | 3 年历史首板 | 日线补价 | 推荐 |
|--------|-----------|-------------|---------|------|
| **同花顺 THS** | ✅ 近期可用 | ⚠️ 部分日期有 | — | **首选涨停池** |
| **腾讯财经** | ✅ 稳定 | 需全扫（慢） | ✅ | **首选 K 线** |
| **新浪** | ✅ 实时/近分钟 | — | 当日 | 日常补全 |
| **AKShare 东财** | ❌ 本机不稳定 | 历史池常空 | 偶发断连 | 备用 |
| **Tushare** | 未接入 | 需 token+积分 | 可用 | 可选升级 |
| **Baostock** | ✅ 16:00 限量正序 | — | — | **仅调度器；禁 CLI bulk** |
| **zzshare/CNTrade** | 未接入 | — | — | 暂不推荐 |

---

**数据仅供研究，不构成投资建议。免费接口随时可能变更，请以实测为准。**
