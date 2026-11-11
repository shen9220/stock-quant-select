# 首板战法公共模块

- `lib/current_daily_marks.py`：日期校验的当日估值行情，逐票异步缓存；不伪造历史卖点，收盘前现价不冒充收盘。
- `lib/historical_daily_cache.py`：free日线缺口复用持久化历史日线缓存；昨收必须取真实上一交易日，不伪造高低价/成交量。
- `lib/first_board_gate.py`：统一首次涨停门禁。D0-1涨停、D0-2未涨停才放行；本机free日线，未知不放行。所有模型新候选/result与V3涨停池复用。
- [交易日与资金规范](../交易记录UI/文档/D1双份资金滚动与金额字段规范.md)：D1/D2分开，逐次买入总资产50%硬上限。
- `paths.py`、`stock_meta.py`等沿用既有路径和股票元信息入口。
