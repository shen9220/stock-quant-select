# Results 全量落盘规范（铁纪律）

> 生效：2026-10-30  
> 状态：**权威**  
> 族入口：`规范文档/规范要求/首板战法总规范.md`  
> 背景：历史多次「脚本已跑但未落 md/json」，导致主板/创业板无法按评分追溯。

---

## 1. 结论（一句话）

**任何程序选股跑完（含 dry-run 以外的正式/回补/补跑），必须立刻写出可追溯的全量 results；只写 Top2 / 只 print 不落盘 = 不合格。**

---

## 2. 适用范围

| program | 目录 | results 形态 |
|---------|------|----------------|
| `stock`（V1 原） | `stock-V1/stock-V1（默认原）/results/` | `{D0}_recommendations.md`（+ 可选 json） |
| `stock_updata` | `Stock-V1（水下）/results/` | 同族命名，全量得分/淘汰原因 |
| `slow` | `Stock（低价）/results/` | `*_slow_recommendations.md` 等，全量 |
| `up` | `up/results/` | 全量 |
| `stock_v2` | `stock-V2/Stock-V2（一进二）/results/` | `{D1}_recommendations.json` + `.md` |

主板 / 创业板 **不是**独立程序：其 Top2 从 **V1 原 results 全量评分** 派生（见开盘六 Tab / `backfill_board_records`）。

---

## 3. 最低内容（对标 V1 原）

每个交易日（按该模型自己的 D0/D1 命名）至少包含：

1. **入选区**：正式推荐 / 各路径 Top（代码、名称、得分、关键因子、状态）  
2. **未入选区**：同日候选池内 **其余全部** 按得分从高到低列出  
   - 至少：`代码`、`名称`、`综合得分`（或路径分）、**未入选原因**（得分低于前二 / 未过门槛 / 硬滤剔除…）  
3. **头信息**：数据日 D0、买入日 D1、生成时间、模式（live / backfill）  
4. **空仓也要落盘**：`status=NO_TRADE` + `reason`，禁止「没票就不写文件」

V1 原现有结构即为样板：

```text
=== … 强度推荐排名（前2只…）===
排名 1: …
排名 2: …
=== 其他未入选强度前二原因 ===
  代码 - 名称: 综合得分 x.xx - 得分低于前二
```

### 3.1 Stock-V2 补充

当前常见问题：json 里 `recommendations` **只有入选前几名**，全池/未入选难追溯。

必须额外持久化（json 权威，md 人读）：

| 字段（建议） | 含义 |
|--------------|------|
| `recommendations` | 正式入选（各 path Top，保持现有） |
| `scored_all` 或按 path 的 `a_scored` / `b_scored` **全文** | 所有已打分候选（含未入选），按分降序 |
| `rejects` | 硬滤未进打分池的剔除（可截断说明，但须落盘） |
| `scan_log` | 可保留 head；**不得**只留 head 而丢掉 scored 全文 |

禁止：只写 `a_pool_n` / `scan_log_n` 计数却不写明细。

---

## 4. 落盘铁律

1. **跑完即写**：选股主流程 `return` 前写盘；失败也要写失败/空仓结果（能区分「没跑」与「跑了没票」）。  
2. **路径**：只落本包对应模型 `results/`（`公共/paths.py`）；禁止写到废弃目录。  
3. **幂等**：同一 D0/D1 重跑可覆盖；覆盖前建议保留或可追溯 `generated_at`。  
4. **与 trades 关系**：trades 可只存展示用 Top；**追溯以 results 为准**。无 results 不得声称主板/创业板评分可追溯。  
5. **回补同等**：`--backfill` / 历史补跑与 live **同一落盘标准**。  
6. **必须支持 `--date`（或等价买入日/D0 参数）**：历史回补按该日锚定取数与打分，**禁止用「今天」窗口导致同日重跑结果漂移**。  
   - V1 原：`python3 stock.py --date 2026-09-17 --results-only`（D0；基因/趋势 as_of=D0）  
   - V1 原：`python3 stock.py --buy-date 2026-09-18 --results-only`  
   - V2 / up / 水下：各自 `--date` / `--backfill` 已有或须补齐  
7. **限流**：涨停池/日线/买卖价一律走 `platform_gate` + `platform_rate_limits.json` 软上限；触顶停跑次日续。

---

## 5. 验收

- [ ] 当日 results 文件存在且非空（或明确 NO_TRADE）  
- [ ] 含入选 + 未入选（或 rejects+scored 全文）  
- [ ] 随机抽一只「未入选」能在文件里找到代码与得分/原因  
- [ ] V1：主板/创业板 `board_rank` 能从该日 results 复算一致  

---

## 6. 禁止

- 只 print / 只发通知、不写 results  
- 只写 Top1/Top2，丢掉同池其余排名  
- 用另源涨停池「猜」主板/创业板顶替缺失的 V1 results  
- 空仓省略文件  

---

## 7. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-10-30 | 初版：全程序强制全量 results；V2 须落未入选/全量得分 |
