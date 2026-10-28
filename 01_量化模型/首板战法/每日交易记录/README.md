# 每日交易记录

首板战法交易 UI 与收盘/回填作业。

## 目录约定（铁律）

| 位置 | 放什么 |
|------|--------|
| **根目录** | 仅主运行程序 `stock_UI.py` + 本 README |
| `lib/` | 被 import 的支撑模块（价格、门禁、取数源等） |
| `jobs/` | 可独立运行的作业 / CLI（收盘补全、夜班编排、回填、修复） |
| `scripts/` | shell / launchd 入口 |
| `templates/` | Flask 模板 |
| `data/` | 状态、日志、缓存、备份 |
| `文档/` | 操作手册与专项规范 |
| `迭代记录/` | 版本迭代说明 |
| `trades/` | 遗留目录（只读兼容） |

## 启动

```bash
cd …/01_量化模型/首板战法/每日交易记录
python3 stock_UI.py
```

## 常用作业

```bash
python3 jobs/daily_close_job.py --all-programs
python3 jobs/board_buy_orchestrator.py --help
```

路径解析统一走 `lib/runtime_paths.py`。

## GitHub Pages（只读镜像）

- 地址：https://shen9220.github.io/stock-quant-select/trade-ui/
- 未登录：脱敏；登录 `stock` / `stock123` 后看全量
- Pages **隐藏**「收盘补全 / 补全空值 / 强制重拉」（仅本机 Flask 可用）
- 本地交易落盘后防抖同步：`jobs/sync_trade_ui_pages.py`（`TRADE_UI_PAGES_SYNC=0` 可关）
