"""Versioned, read-only share/cash replay. No writes to recommendations or legacy trades.

D0 is the original recommendation's buy date; D2 shifts entry to D1 (not a
second purchase on D2). Money uses Decimal; rates use actual sold quantities.
"""
from decimal import Decimal, ROUND_HALF_UP
from bisect import bisect_right
from collections import Counter

VERSION = 'shares-v1-20261111'
ZERO = Decimal('0')


def dec(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except Exception:
        return None


def positive(value):
    value = dec(value)
    return value if value is not None and value > 0 else None


def as_float(value):
    return None if value is None else float(value)


def limit_price(prev, ratio):
    return (prev * (1 + ratio)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def at_limit(price, bar, code):
    if bar.get('limit_rule_unknown'):
        return None
    prev = positive(bar.get('pre_close'))
    if prev is None:
        return None
    ratio = Decimal('.3') if str(code).startswith(('4', '8', '92')) else Decimal('.2') if str(code).startswith(('300', '301', '688', '689')) else Decimal('.05') if bar.get('is_st') else Decimal('.1')
    return price >= limit_price(prev, ratio) or price <= limit_price(prev, -ratio)


def split_shares(shares, change):
    """A hand is 100 shares. Flat is explicitly in the larger-AM branch."""
    hands = shares // 100
    am = ((hands + 1) // 2 if change >= 0 else hands // 2) * 100
    return am, shares - am


def capital_period(day):
    if day < '2026-01-01':
        return 'unknown', None
    if day < '2026-11-01':
        return '2026-first', Decimal(100000)
    return '2026-second', Decimal(40000)


class Replay:
    """market supplies daily(code,day), quote(trade,day,slot), candidate_check.

    All methods must be dated and local/read-only. Missing quotes never execute.
    API timestamps cap execution, preventing replay of later sessions today.
    """
    def __init__(self, calendar, market, as_of):
        self.calendar = sorted(set(calendar))
        self.market = market
        self.as_of = as_of  # ISO local Asia/Shanghai, YYYY-MM-DDTHH:MM
        self.today = as_of[:10]

    def shift(self, day, count=1):
        i = bisect_right(self.calendar, day) + count - 1
        return self.calendar[i] if 0 <= i < len(self.calendar) else None

    def passed(self, day, clock):
        return day + 'T' + clock <= self.as_of

    def equity(self, funds, day, clock):
        total = sum((f['cash'] for f in funds), ZERO)
        self.last_asset_marks = []
        for fund in funds:
            row = fund['position']
            if not row:
                continue
            price = positive(self.market.valuation(row['stock_code'], day, clock))
            info = (getattr(self.market, 'valuation_info', {}).get((str(row['stock_code']), day, clock)) or
                    dict(stock_code=str(row['stock_code']), date=day if price is not None else None,
                         price=as_float(price), stale=False, source='dated_quote', requested_time=clock))
            self.last_asset_marks.append(dict(info, shares=row['remainingShares']))
            if price is None:
                return None
            total += row['remainingShares'] * price
        return total

    def run(self, signals, mode='D1'):
        if mode not in ('D1', 'D2'):
            raise ValueError('Unknown mode')
        output, events, snapshots = [], [], []
        grouped = {}
        for source in signals:
            row = dict(source)
            row['signal_date'] = str(source['date'])[:10]
            row['date'] = self.shift(row['signal_date']) if mode == 'D2' else row['signal_date']
            if not row['date']:
                row['date'] = row['signal_date']
                row['timeline_missing'] = True
            key, _ = capital_period(row['date'])
            grouped.setdefault(key, []).append(row)
        for key, group in sorted(grouped.items()):
            base = capital_period(group[0]['date'])[1]
            if base is None:
                for r in group:
                    output.append(self.empty(r, mode, '2026年前资金口径未定义'))
                continue
            # Each capital regime has an archived independent replay; no phantom
            # liquidation at reset, and old holdings remain in their own regime.
            result = self.run_period(group, mode, key, base)
            output.extend(result[0]); events.extend(result[1]); snapshots.extend(result[2])
        output.sort(key=lambda r: (r['date'], int(r.get('id') or 0)), reverse=True)
        return {'version': VERSION, 'mode': mode, 'as_of': self.as_of,
                'rows': output, 'events': events, 'snapshots': snapshots,
                'statuses': dict(Counter(r['status'] for r in output)),
                'rules': ['每次买入预算=min(可用现金,当时总资产×50%)', '所有页面及结果只计入首次涨停', '平盘早盘多卖一手']}

    def empty(self, source, mode, status):
        r = dict(source)
        for key in ('capital', 'buyAmount', 'buy', 'am', 'pm', 'ra', 'rp',
                    'morningBatchPct', 'afternoonBatchPct', 'amHalf', 'pmHalf',
                    'aProfit', 'pProfit', 'batchTotal', 'profit', 'ending',
                    'accountEnding', 'positionAmount', 'cashAmount', 'saleAmount', 'invested', 'buyBudget'):
            r[key] = None
        r.update(executed=False, status=status, mode=mode, events=[], remainingShares=0,
                 shares=0, amShares=None, pmShares=None, ledger_version=VERSION)
        return r

    def run_period(self, signals, mode, key, base):
        funds = [dict(id=x, cash=base / 2, position=None, reusable_on='', used='', locked=ZERO) for x in ('A', 'B')]
        by_day = {}
        for source in signals:
            by_day.setdefault(source['date'], []).append(source)
        start = min(by_day)
        days = [d for d in self.calendar if start <= d <= self.today]
        rows, events, snapshots = [], [], []
        rebalance = False
        for day in days:
            pre_positions = any(f['position'] for f in funds)
            ready = [f for f in funds if not f['position'] and f['reusable_on'] <= day]
            if rebalance and len(ready) == 2:
                total = sum((f['cash'] for f in funds), ZERO)
                funds[0]['cash'] = total / 2; funds[1]['cash'] = total / 2
                events.append(dict(type='rebalance', date=day, period=key, cash=as_float(total), budget=as_float(total / 2)))
                rebalance = False
            # One entry per model/rank/signal day. Duplicate legacy identities
            # are made visible and never spend the same fund twice.
            for index, source in enumerate(sorted(by_day.get(day, []), key=lambda x: int(x.get('id') or 0))):
                row = self.empty(source, mode, '等待买入')
                row['period'] = key
                row['planned_sell_date'] = self.shift(day)
                row['limit_date'] = self.market.previous(source['signal_date'])
                row['check_date'] = self.market.previous(row['limit_date']) if row['limit_date'] else None
                rows.append(row)
                if index:
                    row['status'] = '重复信号未重复买入'; continue
                if source.get('timeline_missing') or not row['planned_sell_date']:
                    row['status'] = '交易日历缺失'; continue
                if not self.passed(day, '09:30'):
                    continue
                ready = sorted([f for f in funds if not f['position'] and f['reusable_on'] <= day], key=lambda f: (f['used'], f['id']))
                equity = self.equity(funds, day, 'open')
                row['capital'] = as_float(equity)
                row['capital_marks'] = list(self.last_asset_marks)
                def free_cash(f):
                    locked = dec(f['position']['saleAmount']) if f['position'] else f['locked'] if f['reusable_on'] > day else ZERO
                    return max(ZERO, f['cash'] - locked)
                available = sum((free_cash(f) for f in funds), ZERO)
                row['buyAmount'] = as_float(available)
                if not ready:
                    row['status'] = '两份资金均占用，暂停买入'; continue
                fund = ready[0]
                row['fund_id'] = fund['id']
                if equity is None:
                    row['status'] = '总资产估值缺失，不能确定50%买入上限'; continue
                budget = min(available, equity / 2)
                row['buyBudget'] = as_float(budget)
                candidate, reason = self.market.choose(source, mode, day)
                if candidate is None:
                    row['status'] = reason; continue
                row.update({k: candidate.get(k, row.get(k)) for k in ('stock_code', 'stock_name', 'board', 'score', 'score_label')})
                row['trade_key'] = ':'.join(str(x) for x in (source.get('program',''),source.get('id'),source['signal_date'],day,row['stock_code'],mode))
                row['substituted_from'] = source['stock_code'] if candidate['stock_code'] != source['stock_code'] else None
                buy = positive(self.market.daily(row['stock_code'], day).get('open'))
                if buy is None:
                    row['status'] = '买入日开盘价缺失'; continue
                row['buy'] = as_float(buy)
                # Applicable strategy instruments use 100-share lots. Unsupported
                # listing boards remain in the pool, not silently simulated.
                if str(row['stock_code']).startswith(('688', '689', '4', '8', '92')):
                    row['status'] = '该板块买卖单位尚未配置'; continue
                shares = int(budget / (buy * 100)) * 100
                if shares == 0:
                    row['status'] = '可用金额不足一手，未买入'; continue
                row.update(executed=True, shares=shares, remainingShares=shares, invested=as_float(shares * buy),
                           reserve=as_float(budget - shares * buy), saleAmount=0.0,
                           positionAmount=as_float(shares * buy), status='持仓待卖', _legs=None,
                           _fund=fund, _buy=buy)
                # Cash is fungible, holdings are two separately tracked lots.
                # Transfer only free cash; same-day proceeds remain locked.
                cost = shares * buy
                shortfall = max(ZERO, cost - free_cash(fund))
                for donor in funds:
                    if donor is fund or shortfall == 0:
                        continue
                    amount = min(shortfall, free_cash(donor))
                    donor['cash'] -= amount; fund['cash'] += amount; shortfall -= amount
                    events.append(dict(type='cash_transfer', date=day, period=key,
                                       source=donor['id'], target=fund['id'], amount=as_float(amount)))
                if shortfall != 0:
                    raise AssertionError('Available cash conservation failure')
                fund['cash'] -= cost
                fund['locked'] = ZERO
                fund['position'] = row; fund['used'] = day
                events.append(dict(type='buy', date=day, time='09:30', period=key, fund_id=fund['id'],
                                   trade_id=row.get('id'), trade_key=row['trade_key'], stock_code=row['stock_code'], shares=shares, price=as_float(buy), cost=row['invested']))
            for slot, clock, valuation in [('am', '09:35', '09:35'), ('pm', '13:35', '13:35')]:
                if not self.passed(day, clock):
                    continue
                for fund in funds:
                    row = fund['position']
                    if not row or day < row['planned_sell_date']:
                        continue
                    bar = self.market.daily(row['stock_code'], day)
                    if self.market.suspended(row['stock_code'], day):
                        row['status'] = '停牌顺延'; continue
                    # The quantity decision always uses planned-day 09:35 vs
                    # that day's previous close. Never infer it from a later fill.
                    if row['_legs'] is None:
                        decision_day = row['planned_sell_date']
                        while decision_day and decision_day <= day and self.market.suspended(row['stock_code'], decision_day):
                            decision_day = self.shift(decision_day)
                        if not decision_day or decision_day > day:
                            continue
                        decision_bar = self.market.daily(row['stock_code'], decision_day)
                        decision_quote = positive(self.market.quote(row, decision_day, 'am'))
                        prev = positive(decision_bar.get('pre_close'))
                        if decision_quote is None or prev is None:
                            row['status'] = '9:35判仓行情缺失'; continue
                        change = decision_quote / prev - 1
                        qa, qp = split_shares(row['shares'], change)
                        row.update(amShares=qa, pmShares=qp, decision_date=decision_day,
                                   decision_change=as_float(change * 100), decision_prev_close=as_float(prev),
                                   _legs={'am': dict(shares=qa, done=qa == 0), 'pm': dict(shares=qp, done=qp == 0)})
                    leg = row['_legs'][slot]
                    if leg['done']:
                        continue
                    quote = positive(self.market.quote(row, day, slot))
                    blocked = at_limit(quote, bar, row['stock_code']) if quote else None
                    if quote is None or blocked is None:
                        row['status'] = f'{clock}行情缺失，未确认卖出'; continue
                    if blocked:
                        row['status'] = f'{clock}涨跌停顺延'; continue
                    shares = leg['shares']; buy = row['_buy']
                    proceeds, profit = shares * quote, shares * (quote - buy)
                    raw = (quote / buy - 1) * 100
                    weighted = raw * shares / row['shares']
                    row[slot] = as_float(quote)
                    row['ra' if slot == 'am' else 'rp'] = as_float(raw)
                    row['morningBatchPct' if slot == 'am' else 'afternoonBatchPct'] = as_float(raw)
                    row['amHalf' if slot == 'am' else 'pmHalf'] = as_float(weighted)
                    row['aProfit' if slot == 'am' else 'pProfit'] = as_float(profit)
                    row[slot + '_sell_date'] = day
                    row['remainingShares'] -= shares
                    row['saleAmount'] = as_float(dec(row['saleAmount']) + proceeds)
                    fund['cash'] += proceeds
                    leg['done'] = True
                    event = dict(type='sell', date=day, time=clock, slot=slot, period=key,
                                 fund_id=fund['id'], trade_id=row.get('id'), trade_key=row['trade_key'], stock_code=row['stock_code'],
                                 shares=shares, price=as_float(quote), proceeds=as_float(proceeds),
                                 profit=as_float(profit), raw=as_float(raw), weighted=as_float(weighted))
                    events.append(event); row['events'].append(event)
                    row['profit'] = sum(e['profit'] for e in row['events'])
                    row['batchTotal'] = sum(e['weighted'] for e in row['events'])
                    row['positionAmount'] = as_float(row['remainingShares'] * buy)
                    row['cashAmount'] = row['reserve'] + row['saleAmount']
                    if row['remainingShares'] == 0:
                        row['ending'] = row['cashAmount']; row['status'] = '已全部卖出'
                        row['reusable_on'] = self.shift(day)
                        # Unknown future calendar must lock, not release immediately.
                        fund['reusable_on'] = row['reusable_on'] or '9999-12-31'
                        fund['locked'] = dec(row['saleAmount'])
                        fund['position'] = None
                    else:
                        row['status'] = '部分卖出，剩余顺延'
                # Trading quotes and update job clocks are recorded separately.
                snapshots.append(dict(date=day, time=clock, period=key, equity=as_float(self.equity(funds, day, valuation)),
                                      cash=as_float(sum((f['cash'] for f in funds), ZERO)), valuation_marks=list(self.last_asset_marks)))
            snapshot_clock = 'close' if self.passed(day, '15:00') else '13:35' if self.passed(day, '13:35') else '09:35' if self.passed(day, '09:35') else 'open'
            total = self.equity(funds, day, snapshot_clock)
            snapshots.append(dict(date=day, time=snapshot_clock, period=key, equity=as_float(total),
                                  cash=as_float(sum((f['cash'] for f in funds), ZERO)), valuation_marks=list(self.last_asset_marks),
                                  position_shares=sum(f['position']['remainingShares'] for f in funds if f['position'])))
            for row in rows:
                if row['date'] == day:
                    row['accountEnding'] = as_float(total)
            for fund in funds:
                if fund['position']:
                    row = fund['position']; row['cashAmount'] = row['reserve'] + row['saleAmount']
            if (pre_positions or any(r['date'] == day and r['executed'] for r in rows)) and not any(f['position'] for f in funds):
                rebalance = True
        for row in rows:
            # Fund references are internal mutable state, never API payload.
            for name in ('_fund', '_buy', '_legs'):
                row.pop(name, None)
        return rows, events, snapshots
