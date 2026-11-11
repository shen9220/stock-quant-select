"""Local market boundary tests: detached SDK values, exact dates and substitution."""
from pathlib import Path
import sys
import unittest
import json
from tempfile import TemporaryDirectory
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'公共/lib'))
from v3_ledger_service import LocalMarket
from v3_ledger import Replay
from historical_daily_cache import merge_history
from first_board_gate import classify

class Query:
    def __init__(self,data):self.data=data
    def do(self):return deepcopy(self.data)
class Rd:
    def get(self,table,code,key):
        if table=='日k':return Query(dict(open=10,close=11,pre_close=10,volume=100))
        return Query(dict(close=10.25))
class ServiceTests(unittest.TestCase):
    def market(self):
        m=object.__new__(LocalMarket);m.rd=Rd();m.daily_cache={};m.minute_cache={};m.calendar=['2026-11-05','2026-11-06','2026-11-09','2026-11-10'];m.all_rows=[];m.model={'program':'stock'};m.exact1335={};m.quotes1335={}
        return m
    def test_sdk_values_are_detached_not_lazy_wrappers(self):
        m=self.market();bar=m.daily('000001','2026-11-09');self.assertIsInstance(bar,dict);self.assertEqual(bar['close'],11)
        self.assertEqual(m.minute('000001','2026-11-09','am'),10.25)
    def test_explicit_1335_null_overrides_other_quotes(self):
        m=self.market();row=dict(id=1,stock_code='000001',signal_date='2026-11-09')
        m.exact1335['stock','1','2026-11-09','000001']={'sell_date':'2026-11-10','sell_price_1335':None}
        self.assertIsNone(m.quote(row,'2026-11-10','pm'))
        self.assertEqual(m.quote(row,'2026-11-09','pm'),10.25)
    def test_suspended_buy_uses_next_candidate_same_day(self):
        m=self.market();a={'stock_code':'000001','signal_date':'2026-11-09'};b={'stock_code':'000002'}
        m.fallback=lambda source:[a,b];m.suspended=lambda code,day:code=='000001';m.first_board=lambda code,day:True
        selected,reason=m.choose(a,'D2','2026-11-10');self.assertEqual(selected['stock_code'],'000002')
    def test_missing_bar_does_not_claim_suspension(self):
        m=self.market();m.daily=lambda code,day:{}
        self.assertFalse(m.suspended('000001','2026-11-09'))
    def test_proven_suspension_mark_uses_last_market_price_not_cost(self):
        m=self.market();m.suspended=lambda code,day:True
        m.daily=lambda code,day:dict(open=0,close=0,pre_close=12,volume=0)
        self.assertEqual(m.valuation('000001','2026-11-09','open'),12)
    def test_missing_first_board_evidence_is_not_first(self):
        m=self.market();m.daily=lambda code,day:{}
        self.assertIsNone(m.first_board('000001','2026-11-09'))

    def test_monday_limit_checks_friday_not_weekend(self):
        m=self.market();asked=[]
        def daily(code,day):
            asked.append(day)
            return {'close':47.90,'pre_close':48.27} if day=='2026-11-06' else {'close':57.48,'pre_close':47.90}
        m.daily=daily
        self.assertTrue(m.first_board('301718','2026-11-10'))
        self.assertEqual(set(asked),{'2026-11-06','2026-11-09'})
        info=m.first_board_checks['301718','2026-11-10']
        self.assertEqual((info['limit_date'],info['check_date'],info['check_previous_date']),('2026-11-09','2026-11-06','2026-11-05'))

    def test_holiday_skips_calendar_days_and_makeup_workdays(self):
        m=self.market();m.calendar=['2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12']
        self.assertEqual(m.previous('2026-10-08'),'2026-09-30')
        self.assertEqual(m.previous('2026-10-12'),'2026-10-09')
        self.assertEqual(m.previous(m.previous('2026-10-09')),'2026-09-30')

    def test_unknown_reason_names_actual_missing_day_and_field(self):
        m=self.market();m.fallback=lambda source:[source];m.suspended=lambda code,day:False
        m.daily=lambda code,day: {'close':47.9} if day=='2026-11-06' else {'close':57.48,'pre_close':47.9}
        selected,reason=m.choose(dict(stock_code='301718',signal_date='2026-11-10'),'D2','2026-11-11')
        self.assertIsNone(selected);self.assertIn('D0-2（2026-11-06）缺少前收盘价',reason)
        m.daily=lambda code,day: {'close':47.9,'pre_close':48.27} if day=='2026-11-06' else {}
        selected,reason=m.choose(dict(stock_code='301718',signal_date='2026-11-10'),'D1','2026-11-10')
        self.assertIsNone(selected);self.assertIn('D0-1（2026-11-09）',reason);self.assertNotIn('D0-2',reason)

    def test_tongzekangwei_dated_history_restores_entry_and_real_share_split(self):
        with TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'data').mkdir()
            calendar=['2026-11-05','2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12']
            series={'301718':{'2026-11-05':{'c':48.27},'2026-11-06':{'c':47.9},'2026-11-09':{'c':57.48},'2026-11-10':{'o':58.05,'c':55.4,'h':59.25,'l':54.05,'source':'tencent_unadjusted_daily'}}}
            (base/'data/v3_daily_history.json').write_text(json.dumps({'series':series}))
            (base/'data/trade_calendar.json').write_text(json.dumps({'trade_dates':calendar}))
            row=dict(id=2431,date='2026-11-10',program='stock',stock_code='301718',buy_price=58.05,sell_date_935='2026-11-11',sell_price_935=55.2)
            m=self.market();m.base=base;m.calendar=calendar;m.all_rows=[row]
            m.rd=type('EmptyFree',(),{'get':lambda self,*args:Query([])})();m.fallback=lambda source:[source]
            self.assertTrue(m.first_board('301718','2026-11-10'))
            bar=m.daily('301718','2026-11-10');self.assertEqual((bar['high'],bar['low']),(59.25,54.05))
            self.assertEqual(bar['quote_sources']['high'],'tencent_unadjusted_daily')
            r=Replay(calendar,m,'2026-11-11T10:00').run([row],'D1')['rows'][0]
            self.assertTrue(r['executed']);self.assertEqual((r['shares'],r['invested']),(300,17415))
            self.assertEqual((r['amShares'],r['remainingShares'],r['aProfit']),(100,200,-285))
            self.assertIsNone(r['pm']);self.assertEqual((r['limit_date'],r['check_date']),('2026-11-09','2026-11-06'))

    def test_oct26_301660_history_recovers_real_buy_and_sales(self):
        with TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'data').mkdir()
            calendar=['2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-26','2026-10-27','2026-10-28']
            series={'301660':{d:{'c':c} for d,c in [('2026-10-20',30.56),('2026-10-21',30.45),('2026-10-22',28.03),('2026-10-23',33.64),('2026-10-26',31.04),('2026-10-27',30.05)]}}
            (base/'data/v3_daily_history.json').write_text(json.dumps({'series':series}))
            (base/'data/trade_calendar.json').write_text(json.dumps({'trade_dates':calendar}))
            row=dict(id=1,program='stock',date='2026-10-26',signal_date='2026-10-26',stock_code='301660',stock_name='粤芯半导-U',buy_price=32.88,sell_date_935='2026-10-27',sell_price_935=31.53)
            m=self.market();m.base=base;m.calendar=calendar;m.all_rows=[row];m.rd=type('EmptyFree',(),{'get':lambda self,*args:Query([])})()
            m.fallback=lambda source:[source]
            m.exact1335[('stock','1','2026-10-26','301660')]={'sell_date':'2026-10-27','sell_price_1335':31.48}
            self.assertTrue(m.first_board('301660','2026-10-26'))
            self.assertEqual(m.daily('301660','2026-10-22')['pre_close'],30.45)
            self.assertEqual(m.daily('301660','2026-10-26')['open'],32.88)
            ledger=Replay(calendar,m,'2026-10-27T16:00').run([row],'D1');r=ledger['rows'][0]
            self.assertTrue(r['executed']);self.assertEqual(r['shares'],1500)
            self.assertEqual((r['amShares'],r['pmShares']),(800,700))
            self.assertEqual(r['status'],'已全部卖出');self.assertEqual(r['profit'],-2060)
            self.assertEqual(r['ending'],47940)
            self.assertNotIn('high',m.daily('301660','2026-10-26'))
    def test_history_requires_exact_previous_exchange_session(self):
        with TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'data').mkdir()
            (base/'data/v3_daily_history.json').write_text(json.dumps({'series':{'301660':{'2026-10-20':{'c':10},'2026-10-22':{'c':12}}}}))
            (base/'data/trade_calendar.json').write_text(json.dumps({'trade_dates':['2026-10-20','2026-10-21','2026-10-22']}))
            bar=merge_history('301660','2026-10-22',base=base)
            self.assertNotIn('pre_close',bar)
            self.assertEqual(classify('301660','2026-10-22','2026-10-21',lambda c,d:merge_history(c,d,base=base)),'unknown')
    def test_primary_quote_wins_and_partial_history_never_invents_ohlc(self):
        with TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'data').mkdir()
            (base/'data/v3_daily_history.json').write_text(json.dumps({'series':{'301660':{'2026-10-22':{'c':28.03,'o':29}}}}))
            (base/'data/trade_calendar.json').write_text(json.dumps({'trade_dates':['2026-10-22']}))
            bar=merge_history('301660','2026-10-22',{'close':28.04,'volume':100},base)
            self.assertEqual(bar['close'],28.04);self.assertEqual(bar['open'],29)
            self.assertEqual(bar['volume'],100);self.assertNotIn('high',bar);self.assertNotIn('low',bar)
    def test_missing_today_uses_last_real_close_with_its_date(self):
        m=self.market();m.suspended=lambda code,day:False
        m.daily=lambda code,day: {'close':12.3} if day=='2026-11-06' else {}
        self.assertEqual(float(m.valuation('000001','2026-11-10','open')),12.3)
        info=m.valuation_info['000001','2026-11-10','open']
        self.assertEqual(info['date'],'2026-11-06');self.assertTrue(info['stale'])
    def test_open_valuation_does_not_use_later_same_day_close(self):
        m=self.market();m.suspended=lambda code,day:False
        m.daily=lambda code,day: {'close':99} if day=='2026-11-10' else {'close':12.3}
        self.assertEqual(float(m.valuation('000001','2026-11-10','open')),12.3)
        self.assertEqual(m.valuation_info['000001','2026-11-10','open']['date'],'2026-11-09')
    def test_recorded_entry_without_same_day_bar_does_not_create_buy(self):
        bar=merge_history('301660','2026-10-26',base='/missing-test-only',recorded_open=32.88)
        self.assertNotIn('open',bar)
    def test_archived_main_board_without_st_state_remains_unknown(self):
        bar=merge_history('600001','2026-10-22',{'close':10.5,'pre_close':10},base='/missing-test-only')
        from first_board_gate import is_limit_up
        self.assertIsNone(is_limit_up('600001',bar))

if __name__=='__main__':unittest.main(verbosity=2)
