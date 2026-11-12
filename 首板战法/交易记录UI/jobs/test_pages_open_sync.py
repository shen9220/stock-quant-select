"""Offline quotes and failed/clean-worktree publication regression checks."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess
import sys
import unittest
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from v3_ledger_cache import offline_projection,save_ledger,read_ledger
from v3_ledger_service import LocalMarket
from v3_template_ledger import VERSION
import pages_sync


class OfflineTests(unittest.TestCase):
    def cached(self):
        return dict(version=VERSION,as_of='2026-11-11T19:00',rows=[dict(id=1,date='2026-11-11',
            signal_date='2026-11-11',executed=True,stock_code='000001',initial_buy_cost=19000,
            profit=12.5,saleAmount=19012.5)],snapshots=[dict(date='2026-11-11',period='2026-second',equity=40012.5)],
            events=[],monthly_stats={'2026-11':{'ending':40012.5}})
    def test_offline_preserves_money_and_adds_today_recommendation(self):
        old=self.cached();new=dict(id=2,date='2026-11-12',stock_code='002384',stock_name='东山精密',buy_price=None)
        m=type('M',(),{'error':'ConnectionRefusedError'})()
        r=offline_projection(old,[new],['2026-11-11','2026-11-12','2026-11-13'],m,'2026-11-12T09:30','original',1,'D1')
        self.assertEqual(r['monthly_stats'],old['monthly_stats']);self.assertEqual(r['snapshots'],old['snapshots'])
        today=r['rows'][0];self.assertEqual(today['date'],'2026-11-12');self.assertFalse(today['executed'])
        self.assertIsNone(today['profit']);self.assertIsNone(today['buy']);self.assertEqual(r['market_status'],'offline')
        self.assertEqual(old['as_of'],'2026-11-11T19:00')
    def test_d2_does_not_buy_current_signal_tomorrow_early(self):
        m=type('M',(),{'error':'offline'})();s=[dict(id=2,date='2026-11-12',stock_code='000002')]
        r=offline_projection(self.cached(),s,['2026-11-11','2026-11-12','2026-11-13'],m,'2026-11-12T09:30','original',1,'D2')
        self.assertEqual(len(r['rows']),1)
    def test_cache_is_atomic_versioned_and_does_not_save_offline_projection_as_valid(self):
        with TemporaryDirectory() as tmp:
            save_ledger(tmp,'original',1,'D1',self.cached());self.assertEqual(read_ledger(tmp,'original',1,'D1')['version'],VERSION)
            invalid={**self.cached(),'market_status':'offline'};save_ledger(tmp,'original',1,'D1',invalid)
            self.assertIsNone(read_ledger(tmp,'original',1,'D1'))
    def test_absent_quote_server_does_not_import_native_sdk(self):
        with TemporaryDirectory() as tmp,patch('v3_ledger_service.socket.create_connection',side_effect=ConnectionRefusedError):
            market=LocalMarket(tmp,{'program':'stock'},[],[])
            self.assertIsNone(market.rd);self.assertEqual(market.error,'ConnectionRefusedError')


class PushTests(unittest.TestCase):
    def test_sync_supervisor_terminates_timed_out_worker(self):
        script=Path(__file__).with_name('sync_trade_ui_pages.py')
        spec=importlib.util.spec_from_file_location('sync_supervisor_test',script)
        job=importlib.util.module_from_spec(spec);spec.loader.exec_module(job)
        with patch.object(sys,'argv',[str(script),'--timeout','1']),patch.object(job.subprocess,'Popen') as spawn,patch.object(job.os,'killpg') as kill,patch.object(pages_sync,'_write_sync_state') as state:
            spawn.return_value.pid=12345;spawn.return_value.wait.side_effect=[subprocess.TimeoutExpired('worker',1),0]
            self.assertEqual(job.main(),124);kill.assert_called_once();self.assertEqual(state.call_args.kwargs['status'],'failed')
    def test_concurrent_day_writes_do_not_share_temp_filename(self):
        from stock_UI import _atomic_write
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'2026-11-12.json'
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(lambda n:_atomic_write(str(path),[{'id':n}]),range(40)))
            self.assertEqual(len(json.loads(path.read_text())),1)
            self.assertFalse(list(Path(tmp).glob('*.tmp')))
    def cp(self,output='',code=0):return subprocess.CompletedProcess([],code,output,'failure' if code else '')
    def runner(self,remote_sha='old',fail_push=False):
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:2]==['remote','get-url']:return self.cp('git@github.com:example/project.git\n')
            if args[:2]==['rev-parse','HEAD']:return self.cp('new\n')
            if 'ls-remote' in args:return self.cp(remote_sha+'\trefs/heads/main\n')
            if 'push' in args:return self.cp(code=1 if fail_push else 0)
            return self.cp()
        return run,calls
    def test_clean_worktree_retries_previously_unpushed_commit_over_https(self):
        run,calls=self.runner()
        with patch.object(pages_sync,'pages_git_push_enabled',return_value=True),patch.object(pages_sync,'_run_git',side_effect=run):
            result=pages_sync._git_push_pages()
        self.assertTrue(result['pushed']);self.assertTrue(any('push' in c and 'https://github.com/example/project.git' in c for c in calls))
    def test_remote_already_current_does_not_create_duplicate_commit(self):
        run,calls=self.runner(remote_sha='new')
        with patch.object(pages_sync,'pages_git_push_enabled',return_value=True),patch.object(pages_sync,'_run_git',side_effect=run):result=pages_sync._git_push_pages()
        self.assertEqual(result['reason'],'already_current');self.assertFalse(any('commit' in c or 'push' in c for c in calls))
    def test_failed_push_is_reported_as_failure(self):
        run,calls=self.runner(fail_push=True)
        with patch.object(pages_sync,'pages_git_push_enabled',return_value=True),patch.object(pages_sync,'_run_git',side_effect=run):result=pages_sync._git_push_pages()
        self.assertFalse(result['pushed']);self.assertIn('error',result)


if __name__=='__main__':unittest.main(verbosity=2)
