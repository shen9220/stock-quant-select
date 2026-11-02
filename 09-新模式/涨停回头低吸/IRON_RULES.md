# 铁律（必须遵守）

## 腾讯实时行情禁止批量

- `qt.gtimg.cn`：**每次只请求 1 只**，禁止批量拼接；间隔 ≥0.4s。

## 通知

- **本项目不发 PushPlus**；早盘通知只属于首板 `stock_updata`。

## 每日流水线（顺序不可乱）

| 时刻 | 阶段 | 动作 |
|------|------|------|
| **9:40** | ① 涨停池 | 近 N 日涨停写入 |
| **9:40** | ② 观察池 | 涨停池跑完后：D3–D5+低吸，**适宜度排序多选**（可多只，非一天一只）；无人符合可为空 |
| **9:40** | ③ 持有池 | 先卖后买（现价）；最多 2 只 |
| **11:25** | 持有监控 | 中午收盘前：现价止损/强势/补仓 |
| **14:30** | 持有监控 | 尾盘：现价买卖节奏（对齐建仓参考时点） |

入口：
- V1：`scripts/morning_0940_entry.sh` → `--phase all`；`hold_monitor_entry.sh midday|tail`
- V2：`scripts/morning_v2_entry.sh`；`hold_monitor_v2_entry.sh midday|tail`（数据 `data_v2/`，PAPER）

crontab **禁止** `>> /tmp/...` 外层重定向。

每次运行必须落盘 `data/runs/` 或 `data_v2/runs/`（`services/run_recorder.py`），供月度归因。

详见 `.cursor/rules/zt-pullback-morning-0940.mdc` · `文档/V1V2并行月度运行说明.md`。
