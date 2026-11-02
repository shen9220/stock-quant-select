# 涨停回头低吸（独立项目）

三层筛选：**涨停池 → 观察池 → 持有池**（顺序固定）

打法与开发设定（供优化）：[`文档/打法逻辑与开发设定-供优化.md`](文档/打法逻辑与开发设定-供优化.md)  
亏损归因（2026-09-01～10-29）：[`文档/亏损归因诊断-2026-09-01_2026-10-29.md`](文档/亏损归因诊断-2026-09-01_2026-10-29.md)  
复跑诊断：`python3 jobs/loss_attribution_diag.py --start 2026-09-01 --end 2026-10-29`

## V1 / V2 并行（月度对照）

- **权威规范**：[`文档/涨停回头低吸 gpt 的 V2 版本.md`](文档/涨停回头低吸%20gpt%20的%20V2%20版本.md)（V2.3）
- **月度运行说明**：[`文档/V1V2并行月度运行说明.md`](文档/V1V2并行月度运行说明.md)
- **变更日志**：[`文档/V2_CHANGELOG.md`](文档/V2_CHANGELOG.md)
- UI：**V1 | V2** 大 Tab → 下接涨停/观察/持有
- 定时：V1 `09:40 / 11:25 / 14:30`；V2 `09:42 / 11:27 / 14:32`（已装 crontab + launchd）
- 运行台账：`data/runs/` · `data_v2/runs/`（每次任务可追溯）
- 手工：`bash scripts/morning_0940_entry.sh` · `bash scripts/morning_v2_entry.sh`

## 启动 UI

```bash
cd "09-新模式/涨停回头低吸"
python zt_pullback_UI.py   # http://127.0.0.1:5055/
```

## 交易日节奏（铁律）

| 时刻 | 动作 |
|------|------|
| **9:40** | ①涨停池 → ②观察池（适宜度多选）→ ③持有监控 |
| **11:25** | 持有池中午现价监控 |
| **14:30** | 持有池尾盘现价监控 |

```bash
bash scripts/morning_0940_entry.sh
bash scripts/hold_monitor_entry.sh midday
bash scripts/hold_monitor_entry.sh tail

# 手工
python3 jobs/run_morning_0940.py --phase all --force
python3 jobs/run_morning_0940.py --phase watch --force
python3 jobs/run_morning_0940.py --phase hold --slot midday --force
```

观察池：D3–D5 + 低吸区间，**每天更新、可多只**；持有最多 2 只。  
腾讯行情禁止批量；本项目不发 PushPlus。

## 数据

- `data/limitup.json` / `watch.json` / `position.json`  
- `data/morning_0940_logs/` · `data/hold_monitor_logs/`  
