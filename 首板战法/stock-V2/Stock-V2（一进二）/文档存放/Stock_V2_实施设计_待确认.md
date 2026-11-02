# Stock V2 实施设计（单文件版）· 已确认

> 日期：2026-10-28  
> 权威需求：`Stock_V2_双路径首板选股模型_开发规范.md`（V2.2）  
> **用户确认**：`1A 2A 3全部` → A1 目录 / B1 单文件主路径 / **UI Tab+trades + 早盘 cron，9:27 必达通知**

---

## 0. 拍板结论

| 冲突 | 选择 | 落地 |
|------|------|------|
| A 目录 | **A1** | `01_量化模型/最新一进二/stock_v2.py` |
| B 范围 | **B1** | D0 预筛 → D1 9:25 竞价评分 → Top0–2 PushPlus；缺字段 `NO_TRADE` |
| C 交付 | **全部** | UI `StockV2` Tab + `program=stock_v2` trades；cron 08:40 预筛 + 09:24 终选通知 |

---

## 1. 隔离铁律

| | Stock V1 | Stock V2 |
|--|----------|----------|
| 代码 | `首板战法/stock/stock.py` | `最新一进二/stock_v2.py` |
| 通知 | ≈09:05 | **09:25–09:26:30**（用户 09:27 收到） |
| 结果 | `stock/results/` | `最新一进二/results/{D1}_recommendations.{json,md}` |
| 交易 | `stock/trades/` + Stock Tab | `最新一进二/trades/` + **StockV2 Tab** |
| cron | 不动 | `scripts/stock_v2_*_entry.sh` 另挂 |

禁止改 V1 因子；禁止把 V2 写入 `program=stock`。

---

## 2. 早盘时间轴

| 时刻 | 动作 |
|------|------|
| 08:40 | `stock_v2_d0_preselect_entry.sh` → `--stage d0-preselect` |
| 09:24 | `stock_v2_0924_entry.sh` 启动；py 等到 09:25 |
| 09:25–09:26:20 | 真实竞价 → A 优先 B 补位 → ≤2 |
| **≤09:26:30** | PushPlus（含空仓 `NO_TRADE`） |
| 09:28 | 硬截止 |
| 09:30 | `ensure_all_ui_tabs_ready` 核验含 `stock_v2` |

---

## 3. 文件清单

- `stock_v2.py` — 单文件主路径
- `scripts/stock_v2_d0_preselect_entry.sh`
- `scripts/stock_v2_0924_entry.sh`
- `paths.PROGRAMS` 含 `stock_v2`；`STOCK_V2_ROOT`
- UI：`templates/index.html` StockV2 lane；`daily_close_job.ensure_all_ui_tabs_ready` 含 V2

---

## 4. 运维命令

```bash
# 探针
python3 stock_v2.py --stage probe-auction --codes 600000,000001,300750

# 盘后干跑（不发推）
python3 stock_v2.py --force

# 盘后干跑 + 推送自检
python3 stock_v2.py --force --notify
```

crontab（已挂）：

```
40 8 * * 1-5 …/最新一进二/scripts/stock_v2_d0_preselect_entry.sh
24 9 * * 1-5 …/最新一进二/scripts/stock_v2_0924_entry.sh
```
