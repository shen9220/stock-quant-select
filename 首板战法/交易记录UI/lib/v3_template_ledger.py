"""The user-confirmed price-difference percentage model, isolated from legacy trades.

0.97 price difference means 0.97 percent. Cash receipts settle allocated previous
close value; they are not shares * quote. All posted money has four decimals.
"""
from decimal import Decimal, ROUND_HALF_UP

from v3_ledger import Replay as ShareReplay, ZERO, dec, positive, as_float, at_limit, split_shares

VERSION = 'doc-delta-v1'
SCALE = Decimal('.0001')


def money(value):
    return value.quantize(SCALE, rounding=ROUND_HALF_UP)


def mark_value(cost, buy, quote):
    return money(cost * (1 + (quote - buy) / 100))


class Replay(ShareReplay):
    def __init__(self, calendar, market, as_of, source_mode='market'):
        super().__init__(calendar, market, as_of)
        self.source_mode = source_mode

    def run(self, signals, mode='D1'):
        result = super().run(signals, mode)
        result.update(version=VERSION, calc_version=VERSION, source_mode=self.source_mode,
                      money_scale=4, date_basis='settlement_date',
                      rules=['价差数值作为收益率百分数，金额计算再除100',
                             '分批回款=分摊前日持仓金额+分批盈亏',
                             '账户现金、额度余款和回款独立；期初承接前日收盘'])
        return result

    def empty(self, source, mode, status):
        row = super().empty(source, mode, status)
        row.update(ledger_version=VERSION, calc_version=VERSION, source_mode=self.source_mode,
                   buy_date=source['date'], initial_buy_cost=None, budget_remainder=None,
                   buy_budget_target=None, cash_available_before_buy=None, day_open_equity=None,
                   realized_profit_total=None, unrealized_profit=None, display_profit=None,
                   remaining_position_value=None, remaining_original_cost=None,
                   batch_change_total=None, buy_day_close_equity=None, profit_kind='未买入',
                   am_status='未安排', pm_status='未安排')
        return row

    def run_period(self, signals, mode, key, base):
        cash, previous_equity = base, base
        funds = [dict(id=x, position=None, reusable_on='', used='') for x in ('A', 'B')]
        rows, events, snapshots, locks = [], [], [], []
        by_day = {}
        for source in signals:
            by_day.setdefault(source['date'], []).append(source)

        def available(day):
            return cash - sum((x['amount'] for x in locks if x['release'] > day), ZERO)

        def update(row):
            if not row['executed']:
                return
            realized = sum((dec(e['profit']) for e in row['events']), ZERO)
            changed = sum((dec(e['batch_change']) for e in row['events']), ZERO)
            remaining_cost = row['_cost'] * row['remainingShares'] / row['shares']
            value = row['_value'] if row['remainingShares'] else ZERO
            floating = None if value is None else value - remaining_cost
            total = None if floating is None else realized + floating
            row.update(realized_profit_total=as_float(realized), unrealized_profit=as_float(floating),
                       remaining_original_cost=as_float(remaining_cost), remaining_position_value=as_float(value),
                       display_profit=as_float(total), profit=as_float(total),
                       batch_change_total=as_float(changed), batchTotal=as_float(changed),
                       profit_kind='已实现' if not row['remainingShares'] else '含浮动' if row['events'] else '浮动')

        def snapshot(day, clock, opening=False):
            marks, holding = [], ZERO
            for fund in funds:
                row = fund['position']
                if not row:
                    continue
                if clock == 'close':
                    quote = positive(self.market.valuation(row['stock_code'], day, clock))
                    cost = row['_cost'] * row['remainingShares'] / row['shares']
                    row['_value'] = mark_value(cost, row['_buy'], quote) if quote is not None else None
                    row['_mark'] = dict((getattr(self.market, 'valuation_info', {}).get(
                        (str(row['stock_code']), day, clock)) or dict(stock_code=row['stock_code'],
                        date=day if quote is not None else None, price=as_float(quote), source='dated_close', stale=False)))
                info = dict(row.get('_mark') or {})
                info['stale'] = bool(info.get('stale') or info.get('date') and info['date'] < day)
                info.update(shares=row['remainingShares'], position_value=as_float(row['_value']),
                            valuation_method=VERSION)
                marks.append(info)
                holding = None if holding is None or row['_value'] is None else holding + row['_value']
                update(row)
            unrealized = [r['unrealized_profit'] for r in rows if r['executed'] and r['remainingShares']]
            floating = None if any(v is None for v in unrealized) else sum((dec(v) for v in unrealized), ZERO)
            equity = None if holding is None else cash + holding
            item = dict(snapshot_id=f'{key}:{day}:{clock}', date=day, time=clock, period=key,
                        equity=as_float(equity), cash=as_float(cash), cash_total=as_float(cash),
                        cash_available=as_float(available(day)), cash_locked=as_float(cash-available(day)),
                        position_value=as_float(holding), unrealized_profit=as_float(floating),
                        position_shares=sum(r['remainingShares'] for r in rows), valuation_marks=marks,
                        calc_version=VERSION, source_mode=self.source_mode)
            snapshots.append(item)
            return item

        def settle(row, fund, day, slot, quote, quantity, cashout=False):
            nonlocal cash
            if quantity == row['remainingShares']:
                allocated = row['_value']
            else:
                allocated = money(row['_day_base'] * quantity / row['_day_quantity'])
            cost = row['_cost'] * quantity / row['shares']
            rate = quote - row['_buy']
            change = ZERO if cashout else money(allocated * rate / 100)
            proceeds = allocated + change
            if proceeds < 0:
                row['status'] = '该报价按模板结算为负值，待核实'; return
            profit = proceeds - cost
            whole = money(row['_cost'] * rate / 100)
            event = dict(type='sell', date=day, time=None if cashout else '09:35' if slot=='am' else '13:35',
                         period=key, slot=slot, fund_id=fund['id'], trade_id=row.get('id'),
                         trade_key=row['trade_key'], stock_code=row['stock_code'], shares=quantity,
                         price=as_float(quote), proceeds=as_float(proceeds), profit=as_float(profit),
                         allocated_carry_value=as_float(allocated), allocated_initial_cost=as_float(cost),
                         raw=as_float(rate), weighted=as_float(rate*quantity/row['shares']),
                         whole_pnl=as_float(whole), batch_change=as_float(profit if cashout else change),
                         settlement_mode='close_snapshot_cashout' if cashout else 'carry_value_delta',
                         source_mode=self.source_mode)
            events.append(event); row['events'].append(event)
            cash += proceeds
            row['_lock']['amount'] += proceeds
            row['_value'] -= allocated
            row['remainingShares'] -= quantity
            row['saleAmount'] = as_float(dec(row['saleAmount']) + proceeds)
            if slot in ('am','pm'):
                row.update({slot:as_float(quote), slot+'_sell_date':day, slot+'_status':'已结算',
                            'ra' if slot=='am' else 'rp':as_float(whole),
                            'amHalf' if slot=='am' else 'pmHalf':as_float(change),
                            'aProfit' if slot=='am' else 'pProfit':as_float(profit),
                            slot+'ReturnPct':as_float(rate)})
                row['_legs'][slot]['done'] = True
            if not row['remainingShares']:
                row['status'] = '已全部卖出'
                row['reusable_on'] = self.shift(day)
                fund['reusable_on'] = row['reusable_on'] or '9999-12-31'
                row['_lock']['release'] = fund['reusable_on']
                fund['position'] = None
            else:
                row['status'] = '部分卖出，剩余顺延'
            update(row)

        for day in (d for d in self.calendar if min(by_day) <= d <= self.today):
            for fund in funds:
                row = fund['position']
                if row:
                    row['_day_base'], row['_day_quantity'] = row['_value'], row['remainingShares']
            opening = snapshot(day, 'opening')
            # Opening equity is the preceding close, never a later same-day quote.
            day_equity = previous_equity
            for index, source in enumerate(sorted(by_day.get(day, []), key=lambda r:int(r.get('id') or 0))):
                row = self.empty(source, mode, '等待买入')
                row.update(period=key, planned_sell_date=self.shift(day),
                           limit_date=self.market.previous(source['signal_date']))
                row['check_date'] = self.market.previous(row['limit_date']) if row['limit_date'] else None
                rows.append(row)
                if index:
                    row['status']='重复信号未重复买入'; continue
                if source.get('timeline_missing') or not row['planned_sell_date']:
                    row['status']='交易日历缺失'; continue
                if not self.passed(day,'09:30'):
                    continue
                free = available(day)
                row.update(capital=as_float(day_equity), day_open_equity=as_float(day_equity),
                           capital_marks=opening['valuation_marks'], buyAmount=as_float(free),
                           cash_available_before_buy=as_float(free), buy_snapshot_id=opening['snapshot_id'])
                ready=sorted([f for f in funds if not f['position'] and f['reusable_on']<=day], key=lambda f:(f['used'],f['id']))
                if not ready:
                    row['status']='两份持仓均占用，暂停买入'; continue
                if day_equity is None:
                    row['status']='前日收盘估值缺失，买入额度待确认'; continue
                target = day_equity / 2
                if self.source_mode=='template' and hasattr(self.market,'budget_for'):
                    given = self.market.budget_for(source,day)
                    if given is not None:
                        target = dec(given)
                row.update(buyBudget=as_float(target), buy_budget_target=as_float(target),
                           budget_source='template_given' if target!=day_equity/2 else 'opening_equity_half')
                candidate, reason=self.market.choose(source,mode,day)
                if candidate is None:
                    row['status']=reason; continue
                row.update({k:candidate.get(k,row.get(k)) for k in ('stock_code','stock_name','board','score','score_label')})
                row['substituted_from']=source['stock_code'] if candidate['stock_code']!=source['stock_code'] else None
                buy=positive(self.market.daily(row['stock_code'],day).get('open'))
                row['buy']=as_float(buy)
                if buy is None:
                    row['status']='买入日开盘价缺失'; continue
                if str(row['stock_code']).startswith(('688','689','4','8','92')):
                    row['status']='该板块买卖单位尚未配置'; continue
                quantity=max(0,int(min(free,target)/(buy*100)))*100
                if not quantity:
                    row['status']='可用金额不足一手，未买入'; continue
                cost=money(quantity*buy); fund=ready[0]
                row.update(executed=True, shares=quantity, remainingShares=quantity, fund_id=fund['id'],
                           invested=as_float(cost), initial_buy_cost=as_float(cost), positionAmount=as_float(cost),
                           reserve=as_float(target-cost), budget_remainder=as_float(target-cost),
                           cashAmount=as_float(target-cost), saleAmount=0.0, status='持仓待卖',
                           _cost=cost, _buy=buy, _value=cost, _legs=None, _day_base=cost, _day_quantity=quantity,
                           _mark=dict(stock_code=row['stock_code'],date=day,price=as_float(buy),source='entry',stale=False))
                row['history']=[]
                row['trade_key']=':'.join(str(v) for v in (source.get('program',''),source.get('id'),source['signal_date'],day,row['stock_code'],mode))
                row['_lock']=dict(amount=ZERO,release='9999-12-31'); locks.append(row['_lock'])
                fund.update(position=row,used=day)
                cash-=cost
                events.append(dict(type='buy',date=day,time='09:30',period=key,fund_id=fund['id'],
                                   trade_id=row.get('id'),trade_key=row['trade_key'],stock_code=row['stock_code'],
                                   shares=quantity,price=as_float(buy),cost=as_float(cost)))
                update(row)
            if self.passed(day,'09:30'):
                snapshot(day,'09:30')
            for slot,clock in (('am','09:35'),('pm','13:35')):
                if not self.passed(day,clock):
                    continue
                for fund in funds:
                    row=fund['position']
                    if not row or day<row['planned_sell_date']:
                        continue
                    if self.source_mode=='template' and getattr(self.market,'cashout_for',lambda r,d:None)(row,day) is not None:
                        continue
                    if self.market.suspended(row['stock_code'],day):
                        row['status']='停牌顺延'; row[slot+'_status']='停牌顺延'; continue
                    if row['_legs'] is None:
                        decision=row['planned_sell_date']
                        while decision and decision<=day and self.market.suspended(row['stock_code'],decision):
                            decision=self.shift(decision)
                        quote=positive(self.market.quote(row,decision,'am')) if decision and decision<=day else None
                        prev=positive(self.market.daily(row['stock_code'],decision).get('pre_close')) if decision else None
                        if quote is None or prev is None:
                            row['status']='9:35判仓行情缺失'; row[slot+'_status']='判仓行情缺失'; continue
                        qa,qp=split_shares(row['shares'],quote/prev-1)
                        row.update(amShares=qa,pmShares=qp,decision_date=decision,
                                   decision_change=as_float((quote/prev-1)*100),decision_prev_close=as_float(prev),
                                   _legs={s:dict(shares=q,done=q==0) for s,q in (('am',qa),('pm',qp))})
                    leg=row['_legs'][slot]
                    if leg['done']:
                        if leg['shares']==0: row[slot+'_status']='不安排卖出'
                        continue
                    quote=positive(self.market.quote(row,day,slot))
                    blocked=at_limit(quote,self.market.daily(row['stock_code'],day),row['stock_code']) if quote else None
                    if quote is None or blocked is None:
                        row['status']=clock+'行情缺失，未确认卖出'; row[slot+'_status']='行情缺失'; continue
                    if blocked:
                        row['status']=clock+'涨跌停顺延'; row[slot+'_status']='涨跌停顺延'; continue
                    if row['_day_base'] is None or row['_value'] is None:
                        row['status']='前日持仓估值缺失，结算待确认'; row[slot+'_status']='结算基准缺失'; continue
                    settle(row,fund,day,slot,quote,leg['shares'])
                snapshot(day,clock)
            if self.passed(day,'15:00'):
                # Only the explicit template fixture can request the documented
                # previous-close cashout. Market data never calls this branch.
                if self.source_mode=='template':
                    for fund in funds:
                        row=fund['position']
                        quote=getattr(self.market,'cashout_for',lambda r,d:None)(row,day) if row else None
                        if quote is not None and row['_value'] is not None:
                            settle(row,fund,day,'other',dec(quote),row['remainingShares'],cashout=True)
                end=snapshot(day,'close')
                previous_equity=dec(end['equity'])
                for row in rows:
                    if row['date']==day:
                        row.update(accountEnding=end['equity'],ending=end['equity'],
                                   buy_day_close_equity=end['equity'],buy_day_close_snapshot_id=end['snapshot_id'])
                    if row['executed'] and (row['remainingShares'] or row['date']==day or any(e['date']==day for e in row['events'])):
                        row['history'].append(dict(date=day, remainingShares=row['remainingShares'],
                            remaining_position_value=row['remaining_position_value'],
                            remaining_original_cost=row['remaining_original_cost'],unrealized_profit=row['unrealized_profit'],
                            display_profit=row['display_profit'],realized_profit_total=row['realized_profit_total'],
                            batch_change_total=row['batch_change_total'],saleAmount=row['saleAmount'],
                            status=row['status'],profit_kind=row['profit_kind']))
        for row in rows:
            for name in list(row):
                if name.startswith('_'): row.pop(name)
        return rows,events,snapshots
