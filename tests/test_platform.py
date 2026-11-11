"""Product/architecture tests use synthetic identities, not hardware results."""
import pathlib, sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import platform_support as p
import storage_test as s
from test_storage import cfg

def config(product='aipc',caps=None):
    return {'platform':{'product_type':product,'model':'fixture','classification_source':'test fixture','capabilities':caps or {}}}

class PlatformTests(unittest.TestCase):
    def case(self,n):
        import json
        outside=json.loads((s.ROOT/'references/manual-cases.json').read_text(encoding='utf8'))
        return next(x for x in s.CATALOG+outside if x['id']=='AISTD-'+str(n))
    def test_missing_class_blocks(self):
        with self.assertRaises(ValueError): p.validate_platform({})
    def test_unknown_class_blocks(self):
        with self.assertRaises(ValueError): p.validate_platform(config('desktop'))
    def test_common_storage_all_three(self):
        for product in p.PRODUCTS: self.assertEqual(p.applicability(self.case(55),config(product))['status'],'Applicable')
    def test_server_cases_excluded_from_aipc_and_workstation(self):
        for product in ('aipc','ai_workstation'):
            for n in (213,292,323,326): self.assertEqual(p.applicability(self.case(n),config(product))['status'],'Skipped')
    def test_unknown_hotplug_blocks(self): self.assertEqual(p.applicability(self.case(213),config('ai_server'))['status'],'Blocked')
    def test_known_no_hotplug_skips(self): self.assertEqual(p.applicability(self.case(213),config('ai_server',{'ssd_hotplug':{'supported':False,'source':'BOM'}}))['status'],'Skipped')
    def test_known_hotplug_still_needs_adapter(self):
        self.assertEqual(p.applicability(self.case(213),config('ai_server',{'ssd_hotplug':{'supported':True,'source':'BOM'}}))['status'],'Applicable')
        self.assertEqual(self.case(213)['kind'],'external')
    def test_no_raid_server_skips(self): self.assertEqual(p.applicability(self.case(323),config('ai_server',{'raid_hba':{'supported':False,'source':'BOM'}}))['status'],'Skipped')
    def test_capability_false_needs_source(self):
        with self.assertRaises(ValueError): p.validate_platform(config('ai_server',{'raid_hba':{'supported':False}}))
    def test_capability_string_false_rejected(self):
        with self.assertRaises(ValueError): p.validate_platform(config('ai_server',{'raid_hba':{'supported':'false','source':'x'}}))
    def test_arm_independent_of_product(self):
        for product in p.PRODUCTS:
            c=config(product);c['platform']['expected_arch']='arm64';p.validate_platform(c)
            self.assertEqual(p.applicability(self.case(55),c)['status'],'Applicable')
    def test_arm32_is_explicitly_unsupported(self):
        c=config();c['platform']['expected_arch']='armv7l'
        with self.assertRaises(ValueError): p.validate_platform(c)
    def test_arm_elf_and_x86_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=pathlib.Path(tmp)/'fio';h=bytearray(20);h[:6]=b'\x7fELF\x02\x01';h[18:20]=(183).to_bytes(2,'little');f.write_bytes(h)
            self.assertIsNone(p.binary_issue(f,'arm64'));self.assertIsNotNone(p.binary_issue(f,'x86_64'))
    def test_native_arm_missing_dmi_allowed(self):
        with patch.object(p.platform,'system',return_value='Linux'),patch.object(p.platform,'machine',return_value='aarch64'),patch.object(p,'elf',return_value={'arch':'aarch64','bits':64}),patch.object(pathlib.Path,'read_bytes',side_effect=FileNotFoundError):
            data=p.host_identity();self.assertTrue(data['supported']);self.assertIsNone(data['firmware']['dmi_product']['value'])
    def test_arch_mismatch_blocks_before_load(self):
        c=config();c['platform']['expected_arch']='aarch64'
        env={'platform_identity':{'arch':'x86_64','supported':True}}
        self.assertTrue(p.execution_issues(self.case(213),c,env,lambda *_:None))
    def test_missing_io_uring_does_not_fallback(self):
        with patch.object(p.shutil,'which',return_value='/native/fio'),patch.object(p,'binary_issue',return_value=None):
            issues=p.execution_issues(self.case(59),config(),{'platform_identity':{'arch':'aarch64','supported':True}},lambda *_:{'rc':0,'stdout':'libaio sync'})
            self.assertTrue(any('io_uring' in x for x in issues))
    def test_inapplicable_case_never_calls_execution(self):
        with tempfile.TemporaryDirectory() as d:
            c=cfg(pathlib.Path(d));r=s.Runner(c,[self.case(213)])
            with patch.object(s,'inventory',return_value={}),patch.object(s.platform,'system',return_value='Windows'),patch.object(r,'run_item') as execute:
                r.run();execute.assert_not_called()
            self.assertEqual(r.state['results'][0]['status'],'Skipped')
    def test_unknown_server_capability_never_calls_execution(self):
        with tempfile.TemporaryDirectory() as d:
            c=cfg(pathlib.Path(d));c['platform']['product_type']='ai_server';r=s.Runner(c,[self.case(213)])
            with patch.object(s,'inventory',return_value={}),patch.object(s.platform,'system',return_value='Windows'),patch.object(r,'run_item') as execute:
                r.run();execute.assert_not_called()
            self.assertEqual(r.state['results'][0]['status'],'Blocked')

if __name__=='__main__': unittest.main(verbosity=2)
