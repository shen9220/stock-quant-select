"""Dated valuation marks cannot masquerade as historical execution prices."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'公共/lib'))
from current_daily_marks import parse_quote


def quote(day='2026-11-11',clock='15:34:59'):
    fields=['龙江交通','4.31','4.61','4.29','4.40','4.20','0','0','123400','0']+['0']*20+[day,clock,'00','']
    return 'var hq_str_sh601188="'+','.join(fields)+'";'


class MarksTests(unittest.TestCase):
    def test_final_daily_prices_have_embedded_date_and_source(self):
        r=parse_quote(quote(),'601188','2026-11-11')
        self.assertEqual((r['open'],r['pre_close'],r['close']),(4.31,4.61,4.29))
        self.assertEqual(r['quote_time'],'2026-11-11T15:34:59')
        self.assertEqual(r['source'],'sina_dated_daily')
    def test_stale_quote_is_not_relabelled_today(self):
        self.assertIsNone(parse_quote(quote(day='2026-11-10'),'601188','2026-11-11'))
    def test_intraday_last_is_not_final_close_or_slot_fill(self):
        r=parse_quote(quote(clock='14:00:00'),'601188','2026-11-11')
        self.assertNotIn('close',r)
        for key in ('am','pm','sell_price_935','sell_price_1335'):
            self.assertNotIn(key,r)
        self.assertEqual(r['last'],4.29)
    def test_malformed_quote_stays_missing(self):
        self.assertIsNone(parse_quote('','601188','2026-11-11'))
        self.assertIsNone(parse_quote('var a="abc";','601188','2026-11-11'))

if __name__=='__main__':unittest.main(verbosity=2)
