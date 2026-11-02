# A股首板竞价 V4：免费数据采集 + 独立核验（实验版）

本版本新增 `audit.py`：用**独立行情终端导出的CSV**逐只比对 9:25 竞价价、量（手）、额（元），缺失、日期错误、时间错误、误差超阈值均不允许宣布正式前二。仍然**未完成真实交易日联网端到端测试**，不能作为已验证交易信号。

## 安装和测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## 交易日操作顺序

前一交易日收盘后：

```bash
python pool_builder.py --date 20261027 --out pool_20261027.csv
```

当日北京时间9:26之后：

```bash
python free_auction.py --pool pool_20261027.csv --date 20261028 --outdir reports
```

参考数据源须为**独立于东方财富的行情终端**，人工导出 `reference_20261028.csv`，列名为 `code,trade_date,auction_time,price,volume_hands,amount_yuan`，trade_date格式 `YYYYMMDD`，auction_time固定 `09:25:00`。然后：

```bash
python audit.py --pool pool_20261027.csv --primary reports/auction_20261028.csv --reference reference_20261028.csv --out reports/audit_20261028.csv
```

默认价差容忍0.1%，量额容忍2%，**这些只是工程测试阈值，不是已验证的市场标准**。手动导出无法确保9:28前完成；现阶段仅用于盘后核验，正式自动化需要合法、稳定的独立接口。

`scoring.py`仍需真实 `prev_close.csv`（含5日平均成交额）和 `theme.csv`（题材热度），不能凭空生成。它目前仅为初步启发式模型，未完成回测、真实题材数据、交易日历校验、成交可行性验证。

## 实盘上线门槛

- 连续5个交易日，记录9:25后每只股票的抓取耗时，9:28前全池完成率100%。
- 独立行情终端逐日核验至少10只，含主板、创业板，记录差异；若全池双源核验不可行，不应宣称全池已核验。
- 主数据源失败、接口限流、单位不符、字段缺失、交易日不一致时，停止输出正式排名。
- 9:25只能计算95分；开盘承接5分只能在9:30后计算。
- 不能保证一字涨停买得到，且次日可能跌停卖不出。此程序不执行自动下单。
