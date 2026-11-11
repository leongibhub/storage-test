"""Synthetic unit/contract validation. Never a hardware acceptance result."""
import argparse, copy, json, math, os, pathlib, sys, tempfile, unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import storage_test as s
from probe import check_expected, Guard
from report import save, generate

def cfg(root):
    return dict(platform={'product_type':'aipc','model':'synthetic','classification_source':'development fixture'},expected_devices=[{'serial':'fixture','model':'synthetic','size':100000}],test_directory=str(root/'data'),report_root=str(root/'validation'),max_space_bytes=20*1024**3,min_free_bytes=1,max_total_write_bytes=100*1024**3,max_case_seconds=15000,authorization={'file_write':True},other_workload='synthetic fixture',validation_only=True,rate_limit_bytes_per_job=1024**2)

def fio():
    return {'jobs':[{'error':0,'read':{'io_bytes':4096,'total_ios':1,'iops':1,'bw_bytes':4096,'runtime':1000,'clat_ns':{'mean':100,'percentile':{'99.000000':120}}}}]}

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.temp.name).resolve(); self.c=cfg(self.root)
    def tearDown(self): self.temp.cleanup()
    def test_valid_configuration(self): self.assertEqual(s.validate(self.c),self.c)
    def test_cli_reads_utf8_bom_config(self):
        path=self.root/'config.json';path.write_text(json.dumps(self.c),encoding='utf-8-sig')
        with patch.object(sys,'argv',['storage_test','preflight','--config',str(path),'--out',str(self.root/'preflight.json')]),patch.object(s,'preflight',return_value={'blocking_or_missing':[]}) as preflight:
            self.assertEqual(s.main(),0);self.assertEqual(preflight.call_args[0][0],self.c)
    def test_kernel_timestamp_is_unambiguous_epoch(self):
        r=s.Runner(self.c,[s.CATALOG[0]]);r.state['started']='2026-11-10T12:08:07.711764+00:00'
        with patch.object(r,'logcmd',side_effect=[({'rc':0,'stdout':'active\n','stderr':''},'service'),({'rc':0,'stdout':'','stderr':''},'fixture')]) as log:
            r.kernel();self.assertIn('@1794312487.711764',log.call_args[0][0])
    def test_inactive_journal_is_not_complete_kernel_evidence(self):
        r=s.Runner(self.c,[s.CATALOG[0]])
        with patch.object(r,'logcmd',return_value=({'rc':3,'stdout':'inactive\n','stderr':''},'service')) as log:
            with self.assertRaises(s.Blocked):r.kernel()
            self.assertEqual(log.call_count,2)
    def test_record_only_and_explicit_baseline_mode(self):
        case=next(x for x in s.CATALOG if x['id']=='AISTD-57')
        results=[{'conditions':{'fixture':True},'profiles':[{'metrics':{'write':{'bw_bytes':10}}}]}]
        r=s.Runner(self.c,[case]); r.judge_performance(case,results)
        self.c['performance_mode']='compare_baseline'
        with self.assertRaises(s.Blocked): r.judge_performance(case,results)
        self.c['baselines']={'AISTD-57:0':{'source':'fixture','conditions':{'fixture':True},'min_bw_bytes':20}}
        with self.assertRaises(s.Failed): r.judge_performance(case,results)
        self.c['performance_mode']='record_only'; r.judge_performance(case,results)
        with self.assertRaises(s.Blocked): r.judge_performance({'id':'AISTD-62'},results)
    def test_invalid_performance_mode(self):
        self.c['performance_mode']='ignore_errors'
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_aio_enosys_is_actionable_blocker(self):
        r=s.Runner(self.c,[s.CATALOG[0]])
        with self.assertRaisesRegex(s.Blocked,'ENOSYS'):
            r.check_rc({'rc':1,'stdout':'fio: io_queue_init, error=38','stderr':'Function not implemented','evidence':'fixture'})
        with self.assertRaises(s.Failed):
            r.check_rc({'rc':1,'stdout':'Input/output error','stderr':'','evidence':'fixture'})
    def test_no_target(self):
        self.c['expected_devices']=[]
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_no_raw_write(self):
        self.c['mode']='raw_write'
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_string_false_is_not_authorization(self):
        self.c['authorization']['file_write']='false'
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_string_false_is_not_validation(self):
        self.c['validation_only']='false'
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_temperature_stops_before_load(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.c['stop_temperature_c']=60
        b={'serial':'fixture','temperature_c':61,'passed':True,'counters':{'media_errors':0},'warnings':[]}
        with self.assertRaises(s.Failed): r.check_health([b],[b])
    def test_temperature_disappears_is_blocked(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.c['stop_temperature_c']=60
        b={'serial':'fixture','temperature_c':20,'passed':True,'counters':{'media_errors':0},'warnings':[]}; a=dict(b,temperature_c=None)
        self.assertTrue(r.check_health([b],[a])[1])
    def test_expired_case_starts_no_command(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.case_deadline=0
        with patch('storage_test.subprocess.Popen') as p:
            with self.assertRaises(s.Blocked): r.execute(['never-run'],10)
            p.assert_not_called()
    def test_negative_budget(self):
        self.c['max_total_write_bytes']=-1
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_validation_only_scaling(self):
        self.c.update(validation_only=False,validation_scale={'seconds':1})
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_invalid_baseline(self):
        self.c['baselines']={'AISTD-57:0':{'source':'fixture','conditions':{'x':1},'max_bw_drop_fraction':0.1}}
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_nan_baseline(self):
        self.c['baselines']={'AISTD-57:0':{'source':'fixture','conditions':{'x':1},'min_iops':math.nan}}
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_direction_baseline_cannot_disable_threshold(self):
        self.c['baselines']={'AISTD-57:0':{'source':'fixture','conditions':{'x':1},'min_iops':1,'write':{}}}
        with self.assertRaises(ValueError): s.validate(self.c)
    def test_identity_mismatch(self):
        with self.assertRaises(ValueError): check_expected([{'type':'disk','serial':'wrong','major_minor':'8:0'}],self.c['expected_devices'])
    def test_duplicate_identity(self):
        nodes=[{'type':'disk','serial':'a','major_minor':'8:0'},{'type':'disk','serial':'a','major_minor':'8:16'}]
        with self.assertRaises(ValueError): check_expected(nodes,[{'serial':'a'},{'serial':'a'}])
    def test_parser_valid(self): self.assertEqual(s.fio_parse(fio())['read']['p99_latency_ns'],120)
    def test_parser_nan(self):
        d=fio(); d['jobs'][0]['read']['bw_bytes']=math.nan
        with self.assertRaises(s.Blocked): s.fio_parse(d)
    def test_parser_failure(self):
        d=fio(); d['jobs'][0]['error']=5
        with self.assertRaises(s.Failed): s.fio_parse(d)
    def test_parser_empty(self):
        with self.assertRaises(s.Blocked): s.fio_parse({'jobs':[{'error':0}]})
    def test_health_missing_is_unknown(self): self.assertIsNone(s.health_parse({},0)['passed'])
    def test_health_delta_missing(self):
        r=s.Runner(self.c,[s.CATALOG[0]])
        b={'serial':'x','passed':True,'counters':{'media_errors':0,'critical_warning':0},'warnings':[]}; a=copy.deepcopy(b); del a['counters']['media_errors']
        self.assertTrue(r.check_health([b],[a])[1])
    def test_health_increment_fails(self):
        r=s.Runner(self.c,[s.CATALOG[0]])
        b={'serial':'x','passed':True,'counters':{'media_errors':0},'warnings':[]}; a=copy.deepcopy(b); a['counters']['media_errors']=1
        with self.assertRaises(s.Failed): r.check_health([b],[a])
    def test_budget_prevents_prepare(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.c['max_total_write_bytes']=1; r.prepare=Mock(); r.guard=Mock()
        case=next(x for x in s.CATALOG if x['id']=='AISTD-57')
        with self.assertRaises(s.Blocked): r.fio_profile(case,case['profiles'][0],0)
        r.prepare.assert_not_called()
    def test_verify_approval_prevents_prepare(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.prepare=Mock(); r.guard=Mock()
        case=next(x for x in s.CATALOG if x['id']=='AISTD-65')
        with self.assertRaises(s.Blocked): r.fio_profile(case,case['profiles'][0],0)
        r.prepare.assert_not_called()
    def test_rerun_requires_repair(self):
        a=argparse.Namespace(only=None,group=None,rerun_from=str(self.root),repair_note=None)
        with self.assertRaises(ValueError): s.select(self.c,a)
    def test_reports_escape_and_blocked(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.state['results'][0]['reason']='<script>alert(1)</script>'; r.persist(); summary=generate(r.root)
        self.assertEqual(summary['counts']['Blocked'],1); h=(r.root/'report.html').read_text(encoding='utf8'); self.assertNotIn('<script>',h); self.assertIn('&lt;script&gt;',h)
    def test_recovery_of_unfinished_report(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); self.assertNotIn('全部通过',generate(r.root)['conclusion'].split('；')[0].replace('未全部通过',''))
    def test_known_identity_mutation(self):
        g=object.__new__(Guard); g.path=self.root; g.initial={'x':1}; g.inode=self.root.stat().st_ino; g.c=self.c
        with patch('probe.snapshot',return_value={'x':2}):
            with self.assertRaises(ValueError): g.check()
    @unittest.skipUnless(sys.platform=='linux','directory-fd cleanup is Linux only')
    def test_cleanup_ignores_space_but_retains_foreign_inode(self):
        self.root.joinpath('data').mkdir(); r=s.Runner(self.c,[s.CATALOG[0]])
        guard=Mock(); guard.fd=os.open(self.root/'data',os.O_RDONLY|os.O_DIRECTORY); r.guard=guard
        fd=os.open('owned',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600,dir_fd=guard.fd); st=os.fstat(fd)
        r.owned=[dict(name='owned',fd=fd,inode=st.st_ino,dev=st.st_dev,size=0)]
        r.clean(); guard.check.assert_called_with(space=False); self.assertFalse((self.root/'data/owned').exists())
        os.close(guard.fd)
    @unittest.skipUnless(sys.platform=='linux','directory-fd cleanup is Linux only')
    def test_cleanup_preserves_foreign_file(self):
        self.root.joinpath('data').mkdir(); r=s.Runner(self.c,[s.CATALOG[0]]); guard=Mock(); guard.fd=os.open(self.root/'data',os.O_RDONLY|os.O_DIRECTORY); r.guard=guard
        fd=os.open('foreign',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600,dir_fd=guard.fd); st=os.fstat(fd)
        r.owned=[dict(name='foreign',fd=fd,inode=st.st_ino+1,dev=st.st_dev,size=0)]
        r.clean(); self.assertTrue((self.root/'data/foreign').exists()); self.assertFalse(r.state['cleanup'][0]['removed']); os.close(guard.fd)
    @unittest.skipUnless(sys.platform=='linux','process-group monitor is Linux only')
    def test_monitor_failure_stops_only_child(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.guard=Mock(); r.guard.check.side_effect=ValueError('simulated mount change')
        with self.assertRaises(ValueError): r.execute([sys.executable,'-c','import time; time.sleep(60)'],10)
        self.assertIsNone(r.active)
        meta=json.loads(next(r.root.glob('command-*.json')).read_text())
        self.assertIsNotNone(meta['rc']); self.assertEqual(meta['stop_reason'],'simulated mount change')
    @unittest.skipUnless(sys.platform=='linux','process-group monitor is Linux only')
    def test_user_stop_stops_child(self):
        r=s.Runner(self.c,[s.CATALOG[0]]); r.stop=True
        with self.assertRaises(s.Blocked): r.execute([sys.executable,'-c','import time; time.sleep(60)'],10,monitor=False)
        self.assertIsNone(r.active)

if __name__=='__main__': unittest.main(verbosity=2)
