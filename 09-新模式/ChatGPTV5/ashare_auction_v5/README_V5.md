# V5：首板竞价模型——可审计离线重算

新增 `run_pipeline.py`：按沪市交易日历核验上一交易日、严格检查股票池日期与首板资格、全池覆盖、9:25快照日期、独立数据源一致性，全部通过后才调用95分评分引擎。评分输出标记 `OFFLINE_VERIFIED_INPUTS_NOT_LIVE_SIGNAL`，**不能声称为实盘信号**。

## 安装

```bash
python -m pip install -r requirements.txt
python -m pip install exchange-calendars
python -m unittest discover -s tests -v
```

## 离线重算

```bash
python run_pipeline.py --pool pool_20261027.csv --auction auction_20261028.csv --reference reference_20261028.csv --prev-close prev_close.csv --theme theme.csv --date 20261028 --outdir reports
```

输入要求沿用V4说明。**reference 必须是独立行情源，theme_heat必须有真实可追溯依据**。此包不提供虚构的10月28日竞价CSV。未提供上述真实输入时，不会输出正式前两名。

## 仍未解决的上线阻塞

1. 当前环境没有AKShare，且未完成免费接口在真实交易日9:26的联网联调。
2. 免费接口 `09:26` 行映射需要券商端交叉核验，不能默认跨版本永远有效。
3. 题材热度数据、5日成交额与封板质量需建立可追溯数据流水线。
4. 单机脚本未部署定时任务，ChatGPT提醒任务不等于数据服务。
5. 交易日9:28前完成率、独立校验误差、全池覆盖率仍无实测结果。
