import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from jira_coverage import mapping

class CoverageTests(unittest.TestCase):
    def test_skill_has_only_disk_definitions_and_manual_cases_are_separate(self):
        import json,storage_test
        original=json.loads((storage_test.ROOT/'references/jira-original.json').read_text(encoding='utf-8'))['cases']
        result=mapping({'run_id':'fixture','config':{},'results':[]},original,storage_test.CATALOG)
        rows={x['jira_key']:x for x in result['cases']}
        for key in ('AISTD-24','AISTD-36','AISTD-72','AISTD-74','AISTD-76','AISTD-77','AISTD-280'):self.assertNotIn(key,rows)
        self.assertIn('人工',rows['AISTD-67']['coverage'])
        for key in ('AISTD-56','AISTD-68','AISTD-70','AISTD-209','AISTD-212'):self.assertTrue(rows[key]['automatic_support'])
    def test_development_never_recommends_product_pass(self):
        original=[{'key':'AISTD-55','title':'basic','steps':[{'id':163}]}]
        state={'run_id':'fixture','config':{'validation_only':True},'results':[{'id':'AISTD-55','status':'Pass'}]}
        r=mapping(state,original,[])['cases'][0]
        self.assertEqual(r['recommendation'],'不写回');self.assertEqual(r['original_steps'][0]['id'],163)
    def test_measurement_is_partial_and_unselected_not_tested(self):
        original=[{'key':k,'title':k,'steps':[]} for k in ['AISTD-57','AISTD-56','AISTD-53']]
        state={'run_id':'fixture','config':{},'results':[{'id':'AISTD-57','status':'Pass','performance_assessment':{'mode':'record_only'}}]}
        r=mapping(state,original,[])['cases'];self.assertEqual(len(r),2)
        self.assertIn('部分覆盖',r[0]['coverage']);self.assertEqual(r[1]['coverage'],'未执行')
