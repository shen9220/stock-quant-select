"""Strict first-board scope regression with isolated data; no remote calls."""
import importlib.util
from pathlib import Path
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import sys
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'公共/lib'))
import first_board_gate
spec=importlib.util.spec_from_file_location('v3_pool',Path(__file__).parents[1]/'lib/v3_pool.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module._RUNNING=True
codes=[('600001','*ST测试'),('688001','科创测试'),('300001','创业测试'),('000001','主板测试'),('830001','北交测试'),('600002','连板'),('600003','资料不足')]
response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status_code':0,'data':{'info':[{'code':c,'name':n,'high_days_value':1} for c,n in codes]}})
calendar=pd.DataFrame({'trade_date':pd.to_datetime(['2026-11-06','2026-11-09','2026-11-10'])})
with TemporaryDirectory() as temporary:
 base=Path(temporary)/'work/首板战法/交易记录UI';(base/'data/zt_pool_cache').mkdir(parents=True)
 ns={'BASE_DIR':str(base),'datetime':datetime,'ps':SimpleNamespace(get_trade_calendar=lambda:calendar),'stock_meta':SimpleNamespace(board_of_code=lambda code:'测试板块')}
 def classify(code,limit,check):
  assert (limit,check)==('2026-11-09','2026-11-06')
  return 'lianban' if code=='600002' else 'unknown' if code=='600003' else 'first'
 with patch.object(module.requests,'get',return_value=response),patch.object(first_board_gate,'classify',side_effect=classify):
  result=module.pool_view(ns,'2026-11-09')
 assert {r['stock_code'] for r in result['items']}=={c for c,_ in codes[:5]}
 assert result['rejected']==1 and result['unknown']==1
 assert all(r['d1']=='2026-11-10' for r in result['items'])
 assert all(r['auction_volume'] is None and r['close_volume_ratio'] is None for r in result['items'])
 with patch.object(module.requests,'get',side_effect=AssertionError('holiday request')):
  assert module.pool_view(ns,'2026-11-08')['items']==[]
# Decimal tick limits apply equally to gate/candidate/result.
def bars(code,day):return {'close':10.5 if day=='prior' else 11.55,'pre_close':10 if day=='prior' else 10.5,'is_st':True}
assert first_board_gate.classify('600001','limit','prior',bars)=='lianban'
assert first_board_gate.classify('600001','limit','prior',lambda c,d:{})=='unknown'
print('Pool scope passed: all boards may contain first boards; consecutive/unknown excluded; exchange calendar and missing metrics preserved.')
