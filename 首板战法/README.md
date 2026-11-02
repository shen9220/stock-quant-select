# 首板战法

权威包位置（工作区根）：

`/Users/admin/stock_quant_select/首板战法/`

根目录**只允许**：`README.md` + 下列文件夹（禁止再堆散落文档）。

## 根目录结构

| 路径 | 说明 |
|------|------|
| `规范文档/` | 总规范、IRON_RULES、通知、共享取数等**全部文档/取数规范** |
| `交易记录UI/` | 原「每日交易记录」；Flask 交易看板与补价作业 |
| `公共/` | `paths.py` / `stock_meta.py` / `pushplus_notify.py` 等共用运行时 |
| `stock-V1/` | Stock-V1 族（默认原 / 主板 / 创业板 子目录） |
| `Stock-V1（水下）/` | 原 stock_updata；UI Tab「Stock-V1（水下）」 |
| `Stock（低价）/` | 原 slow；UI Tab「Stock（低价）」 |
| `up/` | Up 模型（保持原名） |
| `stock-V2/` | Stock-V2（一进二）/ Stock-V2（稳定版） |

## 规范入口

- [规范文档/首板战法总规范.md](./规范文档/首板战法总规范.md)
- [规范文档/目录与文档规范.md](./规范文档/目录与文档规范.md)

## 调度铁律

- 作业总窗 10:00 → 次日 02:00；接口软上限当日停  
- **撞车优先 V2**；V2 全部完成后再跑 V1 回补  
- 入录成功 → HTML + Pages 双端同步  

## 启动

```bash
cd /Users/admin/stock_quant_select/首板战法/交易记录UI
python3 stock_UI.py
```
