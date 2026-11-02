import unittest
import pandas as pd
from audit import reconcile

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.pool=pd.DataFrame([{'code':'000001','streak':1},{'code':'300001','streak':1}])
        self.data=pd.DataFrame([{'code':c,'trade_date':'20261028','auction_time':'09:25:00','price':10.,'volume_hands':1000.,'amount_yuan':1000000.} for c in self.pool.code])
    def test_pass(self):self.assertTrue(reconcile(self.pool,self.data,self.data.copy()).passed.all())
    def test_mismatch(self):
        ref=self.data.copy();ref.loc[0,'amount_yuan']=800000
        self.assertFalse(reconcile(self.pool,self.data,ref).passed.all())
    def test_missing_stock(self):
        with self.assertRaises(ValueError):reconcile(self.pool,self.data,self.data.iloc[:1])
    def test_wrong_time(self):
        ref=self.data.copy();ref.loc[0,'auction_time']='09:30:00'
        with self.assertRaises(ValueError):reconcile(self.pool,self.data,ref)
    def test_date_mismatch(self):
        ref=self.data.copy();ref['trade_date']='20261027'
        with self.assertRaises(ValueError):reconcile(self.pool,self.data,ref)
if __name__=='__main__':unittest.main()
