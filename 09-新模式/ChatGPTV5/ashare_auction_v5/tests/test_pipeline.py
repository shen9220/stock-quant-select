import unittest
from run_pipeline import previous_trading_day,verify_pool
import pandas as pd
class PipelineTest(unittest.TestCase):
 def test_previous_day(self):self.assertEqual(previous_trading_day('2026-10-28'),'20261027')
 def test_weekend_rejected(self):
  with self.assertRaises(ValueError):previous_trading_day('2026-10-31')
 def test_stale_pool(self):
  df=pd.DataFrame([dict(code='300001',name='测试',streak=1,pool_date='20261026')])
  with self.assertRaises(ValueError):verify_pool(df,'20261028')
 def test_valid_pool(self):
  df=pd.DataFrame([dict(code='300001',name='测试',streak=1,pool_date='20261027')])
  self.assertEqual(verify_pool(df,'20261028'),'20261027')
if __name__=='__main__':unittest.main()
