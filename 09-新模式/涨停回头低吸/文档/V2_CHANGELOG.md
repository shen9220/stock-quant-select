# V2_CHANGELOG

## 2026-10-29 — V2.3 首版落地（权威：涨停回头低吸 gpt 的 V2 版本.md）

### 权威与冲突处理

- **只按** `文档/涨停回头低吸 gpt 的 V2 版本.md`（V2.3 整合版）开发。
- `文档/涨停回头低吸 · V2 开发设定.md` **不作权威**（其中 D5 最高优先级、三级缩量同日闭环等与整合版冲突，已忽略）。

### 已实现

| 项 | 说明 |
|----|------|
| 隔离存储 | `data_v2/`（V1 仍用 `data/`） |
| 规则 | `rules_v2.py`：D3 不自动买；D4 主窗；D5 补充；关补仓；PAPER |
| 缩量/结构/确认 | `v2/quality.py` |
| 扫描/晋升 | `v2/scanner.py` → 只写 data_v2 |
| 买卖 | `v2/signals.py`：确认次日执行、跳空放弃、结构止损+CAP6、TRAIL_TIGHT、T+1 |
| 日更 | `jobs/run_v2.py` |
| UI | 顶栏大 Tab **V1 \| V2**，API `?profile=v1\|v2` |

### 未实现 / 首月不做（规范允许分期）

- 市场环境闸门完整实现与敏感性测试
- 龙头评分全因子（封板时间/题材扩散等）——现为可解释简化分
- 影子实验组并行回放（VOL_40/60、STOP_CAP3/4、LIMITUP_70_30 等）
- 独立 UI 端口（按你要求改为同页 Tab）
- 风险预算动态仓位（对照期固定 50%）

### V1 回归

- V1 入口 `jobs/run_morning_0940.py`、`data/`、`rules.py`、`services/*` 未改业务逻辑
- `store.py` 仅增加 `JsonStore` / `for_profile`；模块级 API 仍默认 V1
