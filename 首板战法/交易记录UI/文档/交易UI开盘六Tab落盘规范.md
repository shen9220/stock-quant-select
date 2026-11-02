# 交易 UI 开盘 Tab 落盘规范

> 生效：2026-10-28；修订：2026-10-30  
> 范围：`交易记录UI`（`stock_UI.py`）+ GitHub Pages  
> 性质：**铁纪律**（交易日开盘后必须满足）  
> 族入口：`规范文档/规范要求/首板战法总规范.md`（落盘成功后须 **HTML+Pages 双端同步**）

## 1. 结论（当日最新一批）

每个 **A 股交易日 09:30**（开盘后），UI **与** Pages 上**每一个 Tab** 必须已写入**当日买入日**的**最新一批**选票基础记录（代码 / 名称 / 名次；**不强制补买卖价**，买卖价仍走 15:00 收盘链路）。

| Tab | 数据程序 | 当日必须具备 |
|-----|----------|--------------|
| Stock | `stock` | `rank` 1、2 |
| Stock-V2 | `stock_v2` | 当日 results→trades（有则 rank/槽位齐） |
| 水下 | `stock_updata` | `rank` 1、2 |
| **主板** | `stock` + `board=主板` | **`board_rank` 1、2（当日必写）** |
| **创业板** | `stock` + `board=创业板` | **`board_rank` 1、2（当日必写）** |
| Slow | `slow` | `rank` 1、2 |
| Up | `up` | `rank` 1、2 |

铁律补充（2026-10-30）：

1. **所有模型 / 所有 Tab：交易日当天必须以「当日最新一批」为准**；禁止只留上一交易日冒充今日。  
2. **主板 / 创业板与 Stock 等同优先级写「当日」**：09:30 复检时若 `board_rank` 不齐，必须当场补写；失败则记状态并在午间窗口重试，**不得默默跳过**。  
3. **历史回补可以慢**（夜班 / 限流 / 软上限），但**不得挤占或耽误「当日」写入**。  
4. 落盘成功后须调度 **本机 UI 可读 + Pages 同步**（`schedule_pages_sync`）。  
5. **名次空占位（全模型铁律，2026-10-30）**：任一 Tab 的「第一名 / 第二名」**两行都必须落盘可展示**。若当日该维度只有 1 只候选（或 0 只），**第二名（及缺的第一名）写空占位**（`stock_name=（空占位）`，`is_board_placeholder`/`is_rank_placeholder=true`，不补买卖价），**禁止因缺第二名导致整日从 UI 消失**。

说明：主板 / 创业板是展示 lane，不另建 program；落盘在  
`stock-V1/stock-V1（默认原）/trades/{买入日}.json`，靠 `board` + `board_rank` 区分。

## 2. 时间与职责

| 时刻 | 职责 | 入口 |
|------|------|------|
| 07:50 | up 提前产出 | cron `morning_up_early_entry.sh` |
| 08:50 | stock + slow 产出；等 up；slow/up sync | cron `morning_all_models_entry.sh` |
| ≈09:23–09:29 | 水下 / V2 产出并 sync | `stock_updata_0918_entry.sh` / `stock_v2_0924_entry.sh` |
| **09:30** | **全 Tab 复检 + 缺啥补啥（含主板/创业板当日）** | cron `morning_slow_up_verify_entry.sh` → `daily_close_job.py --ensure-morning`；UI 进程内同名窗口 |
| 10:30 / 12:30 / 14:30 | 若仍缺则再 ensure | UI 调度 / midday verify |
| 15:00 | 补买卖价与盈亏 | 收盘补全（**不替代** 09:30 选票落盘） |

09:30 补写规则：

1. `stock` / `stock_updata`：只读已有推荐 md → `sync_morning_records`（**不**在 09:30 重跑带通知的选股主脚本）  
2. `stock_v2`：读当日 `results/*_recommendations.json` 兜底 sync  
3. `slow` / `up`：源缺失则 produce + sync  
4. **主板 / 创业板：若当日 `board_rank` 不齐，调用 `backfill_board_records.process_one_day("stock", 今日)` 写入板块 Top2（优先完成「今日」，历史另排）**  
5. 结束后 **必须** `schedule_pages_sync`（短生命周期 cron 禁止只靠进程内 Timer）

### 2.1 主板 / 创业板权威口径（2026-10-30）

最底层数据源 = `stock-V1/stock-V1（默认原）/results/{D0}_recommendations.md`
（含「排名前二」+「其他未入选」全量综合得分）。**禁止另拉涨停池重算板块 Top2。**

| 情形 | 规则 |
|------|------|
| V1 原全局 Top1/Top2 本身是主板（或创业板） | 该板 `board_rank` **必须与全局完全一致**（例：买入日 10-08 → 昭衍新药 / 南华生物） |
| 全局 Top 全是另一板 | 在同一份 results 里按综合得分从高到低，筛该板最高分两只（例：买入日 10-09 全局贝特利/领湃均为创业板 → 主板取同文件评分最高两只主板） |
| results 缺失 | **不臆造**；当日 ensure 记失败并重试；历史待有元数据再补 |
| 该板在 results 里只有 1 只（或 0 只） | **仍写 board_rank 1、2**：有票写实票，缺的写空占位；筛「第二名」时该日必须仍可见 |

买入日 D1 ↔ results 文件名 D0：`D0 = 前一交易日`（md 头 `买入日(D1)` 可校验）。

## 3. 验收口径

对买入日 `D`（默认当天）：

- Stock / 水下 / Slow / Up / V2：`trades` 中具备当日有效选票（按各模型规则）  
- **主板 & 创业板：`stock` 当日记录中，`board_rank` 与 V1 原 results 按板评分 Top2 一致**（全局 Top 若属该板则同码同名）  
- UI 与 Pages 打开对应 Tab，能看到 **D** 的票，而不是只剩上一交易日  

未齐时：写 `data/close_job_state.json` 的 `last_ui_tabs_ensure`，允许午间窗口重试；**禁止**用非交易日文件冒充今日；**禁止**因 `import stock` 路径错误导致主板/创业板整日空白；**禁止**用另源涨停池覆盖已有 V1 results 口径。

## 4. 禁止

- 用同花顺涨停池顶替各模型权威推荐  
- **另拉涨停池 / 多源重算覆盖 V1 原 results 的主板/创业板 Top2**  
- 09:30 为「赶 UI」批量并发拉行情 / 打爆限流  
- 把主板 / 创业板写成独立 program 目录  
- 09:30 强制重拉买卖价（与 15:00 收盘铁纪律冲突）  
- **只同步 HTML 不同步 Pages，或只同步主 Tab 不同步主板/创业板**  
- 硬编码已废弃路径 `每日交易记录` / `01_量化模型/首板战法/stock`

## 5. 实现锚点

- 函数：`daily_close_job.ensure_all_ui_tabs_ready`  
- CLI：`python3 jobs/daily_close_job.py --ensure-morning`  
- 板块写入：`backfill_board_records.process_one_day` / `build_board_top2`（**只读 results md**）  
- 强制回写：`python3 jobs/backfill_board_records.py --program stock --force --from-results`  
- 路径：`公共/paths.py` + `交易记录UI/lib/runtime_paths.py`（须能 `import stock`）  
- Pages：`lib/pages_sync.schedule_pages_sync`

## 6. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-10-28 | 初版六 Tab 09:30 落盘 |
| 2026-10-30 | 强调主板/创业板**当日必写**；全 Tab 当日最新一批；历史可慢；修 `import stock` 路径；Pages 同步铁律 |
| 2026-10-30 | 主板/创业板改为 **只读 V1 原 results 全量评分**；禁止另源重算；`--force` 回写历史 |
