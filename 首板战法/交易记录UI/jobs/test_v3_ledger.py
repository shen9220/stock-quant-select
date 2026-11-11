"""Deterministic account-event tests. No services or real trade mutations."""
import sys
from pathlib import Path
import unittest
from copy import deepcopy
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from v3_ledger import Replay, split_shares, at_limit

CAL = ['2026-10-28','2026-10-29','2026-10-30','2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16']


class Market:
    def __init__(self):
        self.prices, self.quotes, self.halts, self.skips = {}, {}, set(), set()
    def previous(self, day):
        earlier = [d for d in CAL if d < day]
        return earlier[-1] if earlier else None
    def daily(self, code, day):
        return self.prices.get((code, day), dict(open=10, close=10, pre_close=10, volume=100))
    def quote(self, row, day, slot):
        return self.quotes.get((row['stock_code'], day, slot), 10)
    def suspended(self, code, day):
        return (code, day) in self.halts
    def valuation(self, code, day, clock):
        if clock == 'close':return self.daily(code, day).get('close')
        return self.daily(code, day).get('open') if clock == 'open' else self.quotes.get((code, day, 'am' if clock == '09:35' else 'pm'),10)
    def choose(self, source, mode, day):
        return (None,'模拟不可买') if (source['stock_code'],day) in self.skips else (source,'')


def signal(day='2026-11-02',code='000001',id=1):
    return dict(id=id,date=day,stock_code=code,stock_name=code,buy_price=999,d2_buy_price=888)


class LedgerTests(unittest.TestCase):
    def run_it(self,signals,market=None,mode='D1',as_of='2026-11-16T16:00'):
        return Replay(CAL,market or Market(),as_of).run(signals,mode)
    def test_flat_is_larger_am_and_conservation(self):
        for hands in range(1,12):
            for change in (Decimal('-1'),Decimal(0),Decimal('1')):
                am,pm=split_shares(hands*100,change)
                self.assertEqual(am+pm,hands*100)
                self.assertEqual(am%100,0)
                if hands%2:self.assertEqual(am>pm,change>=0)
        self.assertEqual(split_shares(300,Decimal(0)),(200,100))
    def test_whole_shares_profit_uses_actual_investment(self):
        m=Market();m.prices['000001','2026-11-02']=dict(open=45.2,close=45.2,pre_close=45.2)
        m.prices['000001','2026-11-03']=dict(open=45.2,close=45.2452,pre_close=45.2)
        for slot in ('am','pm'):m.quotes['000001','2026-11-03',slot]=45.2452
        r=self.run_it([signal()],m)['rows'][0]
        self.assertEqual(r['shares'],400);self.assertEqual(r['invested'],18080)
        self.assertAlmostEqual(r['reserve'],1920);self.assertAlmostEqual(r['profit'],18.08)
        self.assertAlmostEqual(r['saleAmount'],18098.08);self.assertAlmostEqual(r['ending'],20018.08)
        self.assertEqual(r['positionAmount'],0);self.assertEqual(r['reusable_on'],'2026-11-04')
    def test_d2_entry_is_d1_open_not_original_or_old_rebuy(self):
        m=Market();m.prices['000001','2026-11-03']=dict(open=20,close=20,pre_close=20)
        m.prices['000001','2026-11-04']=dict(open=20,close=20,pre_close=20)
        for slot in ('am','pm'):m.quotes['000001','2026-11-04',slot]=20
        r=self.run_it([signal()],m,mode='D2')['rows'][0]
        self.assertEqual((r['date'],r['planned_sell_date'],r['buy'],r['shares']),('2026-11-03','2026-11-04',20,1000))
        self.assertEqual(r['signal_date'],'2026-11-02')
    def test_sales_cannot_fund_same_day_entry(self):
        result=self.run_it([signal(),signal('2026-11-03','000002',2),signal('2026-11-04','000003',3)])
        by={r['stock_code']:r for r in result['rows']}
        self.assertEqual([by[c]['fund_id'] for c in ('000001','000002','000003')],['A','B','A'])
        self.assertEqual(by['000002']['buyAmount'],20000)
    def test_limit_exit_rolls_same_leg_no_double_sale(self):
        m=Market();m.quotes['000001','2026-11-03','am']=11
        result=self.run_it([signal()],m)
        r=result['rows'][0];self.assertEqual(r['am_sell_date'],'2026-11-04');self.assertEqual(r['pm_sell_date'],'2026-11-03')
        sells=[e for e in result['events'] if e['type']=='sell'];self.assertEqual(sum(e['shares'] for e in sells),2000)
        self.assertEqual(len(sells),2)
    def test_odd_hands_green_weighting(self):
        m=Market();m.prices['000001','2026-11-02']=dict(open=60,close=60,pre_close=60)
        m.prices['000001','2026-11-03']=dict(open=60,close=61.2,pre_close=60)
        m.quotes['000001','2026-11-03','am']=59.4;m.quotes['000001','2026-11-03','pm']=61.2
        r=self.run_it([signal()],m)['rows'][0]
        self.assertEqual((r['amShares'],r['pmShares']),(100,200))
        self.assertAlmostEqual(r['batchTotal'],1);self.assertAlmostEqual(r['profit'],180)
        self.assertAlmostEqual(r['aProfit']+r['pProfit'],r['profit'])
        self.assertNotAlmostEqual((r['ra']+r['rp'])/2,r['batchTotal'])
    def test_missing_pm_retains_position_not_zero_or_1315(self):
        m=Market()
        for d in CAL:
            if d>='2026-11-03':m.quotes['000001',d,'pm']=None
        r=self.run_it([signal()],m)['rows'][0]
        self.assertIsNone(r['pm']);self.assertIsNone(r['ending']);self.assertEqual(r['remainingShares'],1000)
        self.assertEqual(r['saleAmount'],10000);self.assertEqual(r['cashAmount'],10000)
    def test_even_hands_fold_once_and_profit_survives_full_exit(self):
        m=Market();m.prices['000001','2026-11-02']=dict(open=10,close=11,pre_close=10)
        m.prices['000001','2026-11-03']=dict(open=11,close=12,pre_close=11)
        m.quotes['000001','2026-11-03','am']=11;m.quotes['000001','2026-11-03','pm']=12
        r=self.run_it([signal()],m)['rows'][0]
        self.assertEqual(r['invested'],20000)
        self.assertAlmostEqual(r['amHalf'],5);self.assertAlmostEqual(r['pmHalf'],10)
        self.assertAlmostEqual(r['batchTotal'],15)
        self.assertEqual((r['aProfit'],r['pProfit'],r['profit']),(1000,2000,3000))
        self.assertEqual(r['positionAmount'],0);self.assertEqual(r['profit'],3000)
    def test_zero_return_stays_zero(self):
        r=self.run_it([signal()])['rows'][0]
        self.assertEqual(r['profit'],0);self.assertEqual(r['batchTotal'],0)
    def test_skipped_expensive_entry_and_flat_rebalance(self):
        m=Market();m.prices['000002','2026-11-03']=dict(open=230.3,close=230.3,pre_close=230.3)
        for slot in ('am','pm'):m.quotes['000001','2026-11-03',slot]=10.6275
        result=self.run_it([signal(),signal('2026-11-03','000002',2),signal('2026-11-05','000003',3)],m)
        by={r['stock_code']:r for r in result['rows']}
        self.assertFalse(by['000002']['executed']);self.assertEqual(by['000003']['buyBudget'],20627.5)
        rebalance=[e for e in result['events'] if e['type']=='rebalance'];self.assertEqual(rebalance[0]['cash'],41255)
    def test_available_cash_21837_cannot_exceed_half_assets_19500(self):
        m=Market()
        m.prices['000001','2026-11-02']=dict(open=181.63,close=181.63,pre_close=181.63)
        for day in ('2026-11-03','2026-11-04'):
            m.prices['000001',day]=dict(open=171.63,close=171.63,pre_close=156.03)
            for slot in ('am','pm'):m.quotes['000001',day,slot]=171.63
        m.prices['000002','2026-11-03']=dict(open=230.3,close=230.3,pre_close=230.3)
        m.prices['000003','2026-11-04']=dict(open=3.5,close=3.5,pre_close=3.5)
        result=self.run_it([signal(),signal('2026-11-03','000002',2),signal('2026-11-04','000003',3)],m,as_of='2026-11-04T16:00')
        r=result['rows'][0]
        self.assertEqual((r['capital'],r['buyAmount'],r['buyBudget']),(39000,21837,19500))
        self.assertEqual((r['shares'],r['invested']),(5500,19250))
        close=result['snapshots'][-1]
        self.assertEqual(close['cash'],2587)
        self.assertFalse(any(e['type']=='rebalance' for e in result['events']))
    def test_every_entry_cost_is_capped_by_half_marked_assets(self):
        m=Market()
        for day in CAL:
            for code in ('000001','000002','000003'):
                m.prices[code,day]=dict(open=10,close=10,pre_close=10)
                for slot in ('am','pm'):m.quotes[code,day,slot]=10.8
        result=self.run_it([signal(),signal('2026-11-03','000002',2),signal('2026-11-04','000003',3)],m)
        for row in result['rows']:
            if row['executed']:
                self.assertLessEqual(row['invested'],row['capital']/2)
                self.assertLessEqual(row['buyBudget'],row['buyAmount'])
    def test_both_funds_occupied_pause_new_buys(self):
        m=Market()
        for d in CAL:
            if d>='2026-11-03':
                for c in ('000001','000002'):
                    m.quotes[c,d,'am']=None;m.quotes[c,d,'pm']=None
        r=self.run_it([signal(),signal('2026-11-03','000002',2),signal('2026-11-04','000003',3)],m)['rows'][0]
        self.assertFalse(r['executed']);self.assertIn('占用',r['status'])
    def test_month_end_mark_and_actual_sell_date(self):
        m=Market();m.prices['000001','2026-10-30']=dict(open=10,close=9.8,pre_close=10)
        result=self.run_it([signal('2026-10-30')],m)
        close=[s for s in result['snapshots'] if s['date']=='2026-10-30' and s['time']=='close'][0]
        self.assertEqual(close['equity'],99000)
        self.assertTrue(all(e['date']=='2026-11-02' for e in result['events'] if e['type']=='sell'))
    def test_missing_valuation_never_uses_cost(self):
        m=Market();m.prices['000001','2026-11-02']=dict(open=10,pre_close=10)
        result=self.run_it([signal()],m)
        close=[s for s in result['snapshots'] if s['date']=='2026-11-02' and s['time']=='close'][0]
        self.assertIsNone(close['equity'])
    def test_no_future_session_execution(self):
        r=self.run_it([signal()],as_of='2026-11-03T10:00')['rows'][0]
        self.assertIsNotNone(r['am']);self.assertIsNone(r['pm']);self.assertEqual(r['remainingShares'],1000)
    def test_replay_idempotent_and_sources_immutable(self):
        source=[signal(),signal('2026-11-03','000002',2)];original=deepcopy(source)
        self.assertEqual(self.run_it(source),self.run_it(source));self.assertEqual(source,original)
    def test_suspension_shifts_then_uses_resumed_daily_change(self):
        m=Market();m.halts.add(('000001','2026-11-03'))
        r=self.run_it([signal()],m)['rows'][0]
        self.assertEqual(r['decision_date'],'2026-11-04');self.assertEqual(r['am_sell_date'],'2026-11-04')
    def test_limit_prices_use_tick_rounding_and_board(self):
        self.assertTrue(at_limit(Decimal('4.61'),{'pre_close':4.19},'601188'))
        self.assertFalse(at_limit(Decimal('4.60'),{'pre_close':4.19},'601188'))
        self.assertTrue(at_limit(Decimal('10.5'),{'pre_close':10,'is_st':True},'000001'))
    def test_d1_d2_independent_and_reset_accounts(self):
        signals=[signal('2026-10-30'),signal('2026-11-02','000002',2)]
        d1=self.run_it(signals);d2=self.run_it(signals,mode='D2')
        self.assertEqual(d1['rows'][0]['buyBudget'],20000)
        self.assertEqual(d2['rows'][0]['buyBudget'],20000)
        self.assertEqual(d1['mode'],'D1');self.assertEqual(d2['mode'],'D2')
    def test_duplicate_signal_does_not_double_spend(self):
        r=self.run_it([signal(),signal(id=2)])
        self.assertEqual(sum(x['executed'] for x in r['rows']),1)


if __name__=='__main__':unittest.main(verbosity=2)
