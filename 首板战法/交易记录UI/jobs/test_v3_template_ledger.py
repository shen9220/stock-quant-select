"""Fixed business examples and boundary checks for the confirmed template model."""
import sys
from pathlib import Path
from decimal import Decimal as D
from copy import deepcopy
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from v3_template_ledger import Replay, VERSION
from v3_period_stats import monthly_stats

CAL=['2026-10-30','2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12']


class Market:
    def __init__(self):
        self.bars={};self.quotes={};self.halts=set();self.given={};self.cashouts={}
    def previous(self,day):
        return next((d for d in reversed(CAL) if d<day),None)
    def daily(self,code,day):
        return self.bars.get((code,day),dict(open=10,close=10,pre_close=10,volume=100))
    def quote(self,row,day,slot):return self.quotes.get((row['stock_code'],day,slot),10)
    def valuation(self,code,day,clock):return self.daily(code,day).get('close')
    def suspended(self,code,day):return (code,day) in self.halts
    def choose(self,row,mode,day):return row,''
    def budget_for(self,row,day):return self.given.get(day)
    def cashout_for(self,row,day):return self.cashouts.get((row['stock_code'],day))


def signals():
    return [dict(id=i+1,date=day,program='stock',stock_code=code,stock_name=name,rank=1)
            for i,(day,code,name) in enumerate([('2026-11-02','301520','万邦医药'),
                ('2026-11-03','000678','襄阳轴承'),('2026-11-04','002696','百洋股份')])]


def fixture_market():
    m=Market()
    m.bars['301520','2026-11-02']=dict(open=61.72,close=62.69,pre_close=61.72)
    m.bars['301520','2026-11-03']=dict(open=61,close=58,pre_close=62.69)
    m.bars['000678','2026-11-03']=dict(open=19.91,close=20.26,pre_close=19.91)
    m.bars['000678','2026-11-04']=dict(open=19.6,close=19.48,pre_close=20.26)
    m.bars['002696','2026-11-04']=dict(open=11.5,close=11.12,pre_close=11.5)
    m.quotes['301520','2026-11-03','am']=59.43
    m.quotes['301520','2026-11-03','pm']=57.14
    m.given['2026-11-03']='20089.5'
    m.cashouts['000678','2026-11-04']='20.26'
    return m


class TemplateTests(unittest.TestCase):
    def replay(self,m=None,rows=None,as_of='2026-11-04T16:00',source='template'):
        return Replay(CAL,m or fixture_market(),as_of,source).run(rows or signals(),'D1')
    def equal(self,actual,expected):self.assertEqual(D(str(actual)),D(str(expected)))
    def test_exact_three_day_closes_and_cash(self):
        r=self.replay();s=[s for s in r['snapshots'] if s['time']=='close']
        for a,c,e in zip(s,['21484','19556.0563','19985.7413'],['40179.6052','39535.7413','39461.4513']):
            self.equal(a['cash'],c);self.equal(a['equity'],e)
            self.equal(D(str(a['cash']))+D(str(a['position_value'])),e)
        self.assertEqual(r['version'],VERSION)
    def test_batch_fields_have_distinct_fixed_business_values(self):
        wb=self.replay()['rows'][-1]
        for k,v in dict(invested='18516',positionAmount='18516',cashAmount='1484',ra='-424.0164',
            rp='-848.0328',amHalf='-142.7098',pmHalf='-570.8391',aProfit='-82.8414',pProfit='-451.1023',
            batchTotal='-713.5489',profit='-533.9437',saleAmount='17982.0563',ending='40179.6052').items():self.equal(wb[k],v)
        self.assertEqual((wb['amShares'],wb['pmShares']),(100,200))
    def test_initial_cost_and_budget_remainder_never_turn_into_proceeds(self):
        r=self.replay()['rows'];self.equal(r[0]['budget_remainder'],'217.87065')
        self.equal(r[1]['budget_remainder'],'179.5');self.equal(r[2]['initial_buy_cost'],'18516')
        self.equal(r[2]['remaining_original_cost'],0)
    def test_fixture_cashout_does_not_invent_am_or_pm_fill(self):
        xy=self.replay()['rows'][1]
        self.assertIsNone(xy['am']);self.assertIsNone(xy['pm']);self.equal(xy['saleAmount'],'19979.685')
        self.equal(xy['profit'],'69.685');self.assertIsNone(xy['events'][0]['time'])
    def test_market_mode_cannot_use_template_budget_or_cashout(self):
        r=self.replay(source='market');xy=r['rows'][1]
        self.equal(xy['buyBudget'],'20089.8026');self.assertNotEqual(xy['saleAmount'],19979.685)
        self.assertTrue(all(e.get('settlement_mode')!='close_snapshot_cashout' for e in r['events']))
    def test_no_sale_profit_is_float_not_realized(self):
        wb=self.replay(as_of='2026-11-02T16:00')['rows'][0]
        self.equal(wb['display_profit'],'179.6052');self.equal(wb['realized_profit_total'],0)
        self.equal(wb['batchTotal'],0);self.assertEqual(wb['profit_kind'],'浮动')
    def test_period_realized_plus_float_change_is_asset_change(self):
        r=self.replay();p=monthly_stats(r)['2026-11']
        for k,v in dict(opening=40000,ending='39461.4513',profit='-464.2587',floatingChange='-74.29',
                         assetPnl='-538.5487',otherProfit='69.685').items():self.equal(p[k],v)
        self.equal(D(str(p['opening']))+D(str(p['profit']))+D(str(p['floatingChange'])),p['ending'])
    def test_real_time_cash_deducts_open_purchase_before_morning_sale(self):
        r=self.replay();s=next(s for s in r['snapshots'] if s['date']=='2026-11-03' and s['time']=='09:35')
        self.equal(s['cash'],'7663.1586')
    def test_input_is_immutable_and_replay_is_idempotent(self):
        rows=signals();original=deepcopy(rows);a=self.replay(rows=rows);b=self.replay(rows=rows)
        self.assertEqual(rows,original);self.assertEqual(a,b)
    def test_missing_quote_keeps_quantity_and_does_not_book_receipts(self):
        m=fixture_market();m.quotes['301520','2026-11-03','pm']=None
        r=self.replay(m,as_of='2026-11-03T16:00')['rows'][-1]
        self.assertEqual(r['remainingShares'],200);self.equal(r['saleAmount'],'6089.1586')
        self.assertIsNone(r['pm']);self.assertEqual(r['pm_status'],'行情缺失')
    def test_future_slots_are_not_executed(self):
        r=self.replay(as_of='2026-11-03T10:00')['rows'][-1]
        self.assertEqual(r['remainingShares'],200);self.assertIsNone(r['pm'])
    def test_proceeds_lock_until_full_exit_and_next_session(self):
        r=self.replay(as_of='2026-11-03T16:00');s=r['snapshots'][-1]
        self.equal(s['cash_locked'],'17982.0563');self.equal(s['cash_available'],'1574')
        later=self.replay()['rows'][0];self.equal(later['cash_available_before_buy'],'19556.0563')
    def test_missing_valuation_propagates_unknown_assets(self):
        m=fixture_market();m.bars['301520','2026-11-02']['close']=None
        r=self.replay(m,as_of='2026-11-02T16:00');self.assertIsNone(r['snapshots'][-1]['equity'])
        self.assertIsNone(r['rows'][0]['display_profit'])
    def test_history_freezes_prior_day_float_before_later_realization(self):
        wb=self.replay()['rows'][-1];self.equal(wb['history'][0]['display_profit'],'179.6052')
        self.equal(wb['history'][-1]['display_profit'],'-533.9437')
    def test_d2_uses_shifted_actual_entry_and_its_own_quotes(self):
        m=Market();m.bars['301520','2026-11-03']=dict(open=20,close=20.5,pre_close=20)
        r=Replay(CAL,m,'2026-11-03T16:00').run(signals()[:1],'D2')['rows'][0]
        self.assertEqual(r['date'],'2026-11-03');self.equal(r['buy'],20);self.equal(r['display_profit'],100)


if __name__=='__main__':unittest.main(verbosity=2)
