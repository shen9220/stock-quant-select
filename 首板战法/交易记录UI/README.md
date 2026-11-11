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

**铁律**（权威：`../规范/首板战法总规范.md` §1）：**数据入录成功 → 本机 HTML 与 Pages 必须双端同步推送**，禁止只更一端。

- 地址：https://shen9220.github.io/stock-quant-select/trade-ui/
- 未登录：脱敏；登录后看全量
- Pages **隐藏**「收盘补全 / 补全空值 / 强制重拉」（仅本机 Flask 可用）
- 落盘后调度：`lib/pages_sync.schedule_pages_sync()` → `jobs/sync_trade_ui_pages.py`（`TRADE_UI_PAGES_SYNC=0` 仅调试可关）

## V3 本机验收（2026-11-10）

- [字段映射、确认口径与验收说明](文档/V3界面验收与字段映射.md)
- 修改 `templates/v3-dashboard.js/css` 后运行 `python3 jobs/build_v3_dashboard.py`；计算检查：`node jobs/test_v3_dashboard.cjs`。
- `data/PAGES_REVIEW_HOLD` 存在时暂停自动导出/发布；必须等待用户界面验收。

## D1 / D2 实股资金账本（2026-11-11，本机验收）

- [统一交易日、首板与50%买入上限规范](文档/D1双份资金滚动与金额字段规范.md)。D1：D0买/D1卖；D2：D1买/D2卖。平盘早盘多卖一手，实际卖出日归月。
- `lib/v3_ledger.py`为可重放事件账本，`lib/v3_ledger_service.py`只读本机free和13:35旁挂数据；不覆写原交易记录。三页共用`/api/v3/ledger`。
- 检查：`python3 jobs/test_v3_ledger.py`、`node jobs/test_v3_dashboard.cjs`。首板公共门禁为`../公共/lib/first_board_gate.py`。
- 可用现金与买入预算不同，每次预算均受总资产50%硬上限约束。两笔持仓分别跟踪，完全空仓后重新平分。
- 等待本机验收，不执行Pages发布。


## 第十二版概览与估值（2026-11-11）

7张交易概览卡，资金三列分组与紧凑身份列；分批只计一次贡献、奇数手实际加权、利润以最初投入成本计算。估值缺口采用最近真实收盘并显示日期；`公共/lib/current_daily_marks.py`校验当日报价日期，后台单票补估值。比较使用同账本的轻量月度汇总，18行独立加载与重试。

补充校验：`python3 jobs/test_v3_current_marks.py`；当日行情sidecar为`data/v3_daily_marks.json`。继续本机验收，不发布Pages。


## 第十三版顶部导航（2026-11-11）

固定顶部四页面入口＋四档九模型，四档蓝/绿/橙/紫可点选；主体全宽。明细维护四按钮位于刷新右侧，移除列分组图例。手机顶部展开模型菜单，支持键盘与外部关闭；纯黑适配，算法沿用第十二版。预览5051等待检查，未推Pages。

## 第十四版分值补齐（2026-11-11）

移除“交易数据记录 · 首板战法 / 本机真实数据 · 待验收”整行。`lib/v3_scores.py`只读对应result和同笔交易已记录的分值，自动补齐明细与CSV；D2按原始信号日取分，不按顺延后的买入日取分。V1含未入选候选区，V2严格区分A/B路径，保留真实0分和门槛标签；没有对应评分的历史记录和空占位仍为空。result文件变更使缓存自动失效，不改推荐或财务账本。

检查：`python3 jobs/test_v3_scores.py`、`node jobs/verify_v3_scores.cjs`。本机数据恢复842条原有评分，36账户与四组财务快照核对通过；评分缺口详见外层workspace/reviews/交易看板V3/第十四版-分值来源审计.json。继续5051本机验收，未发布Pages。

## 模板口径取值设计（2026-11-11，尚未实施）

[UI取值逻辑设计](文档/UI取值逻辑设计.md)以同目录《完整数据流程以及计算公式》为依据。用户明确确认价差0.97按收益率0.97%计算；设计按模板区分整批盈亏、分批盈亏、分批利润、回款、额度余款及账户现金，覆盖列表、概览、月表、对比和CSV。三天样例59项数值及守恒校验通过：期末现金19985.7413、持仓19475.7100、总资产39461.4513。

该文档是下一版计算与字段契约，现行实股账本尚未替换。襄阳11-04以此前收盘持仓金额结算仅为原文校验输入，不声明已有实际分批卖点。本次仅交付设计与离线核对；未修改运行页面或原始交易数据。

## 通则康威日期核验（2026-11-11）

首板日期按真实交易日：11-09涨停→11-06检查→11-05前收。补齐该股free未收录的四根有凭据不复权日线后，D1/D2买入与资金链恢复。校验状态注明缺少哪天/哪个字段，状态气泡含两校验日期；API返回first_board_check证据。详情见V3验收映射；56 Python、15 JS和36账户审计通过，继续5051验收，未发布。

## 模板收益模型已接入（2026-11-11）

用户已授权核验后同步UI并发布Pages。`lib/v3_template_ledger.py`为当前计算入口，版本`doc-delta-v1`；价差0.97作为收益率0.97%。期初承接前日收盘，分批回款按分摊持仓结算，初始成本/预算余款/账户现金分开。月表和比较共用`lib/v3_period_stats.py`；CSV和手机字段一致，历史行按截止时点投影。原始trades/result/行情不覆盖，模板校验输入不写入实际行情。

验证：71项Python、17项前端契约、36账户守恒、桌面/手机/CSV、静态登录解密与脱敏均通过。Pages直接使用加密后的36账户服务端账本，避免静态端重新计算。复核脚本为`jobs/test_v3_template_ledger.py`、`jobs/verify_v3_template.cjs`、`jobs/verify_v3_pages.cjs`。
