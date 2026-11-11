import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from preparation import packet

class PreparationTests(unittest.TestCase):
    def test_default_plan_has_only_automatic_cases(self):
        import argparse,storage_test
        items=storage_test.select({},argparse.Namespace())
        self.assertEqual(len(items),21)
        self.assertTrue(all(x['kind']!='external' for x in items))
        result=packet({},items,[])
        self.assertFalse({'raid','hotplug','power','gui'} & {q['key'] for q in result['questions']})
    def item(self,key):return {'id':key,'title':key,'kind':'external','criteria':['expected']}
    def test_selected_cases_only_and_reuse_answers(self):
        p=packet({'preparation_answers':{'machine':'confirmed machine'}},[self.item('AISTD-65')],[])
        q={x['key']:x for x in p['questions']}
        self.assertIn('duration',q);self.assertNotIn('raid',q)
        self.assertFalse(q['machine']['needs_answer']);self.assertTrue(q['duration']['needs_answer'])
        self.assertEqual(p['execution'],'NOT_EXECUTED')
    def test_preserve_original_steps_and_expected(self):
        steps=[{'step':'actual operation','expectedResult':'observable result'}]
        p=packet({},[self.item('AISTD-62')],[{'key':'AISTD-62','steps':steps}])
        self.assertEqual(p['cases'][0]['steps'],steps)
        self.assertTrue(any(q['key']=='temperature' for q in p['questions']))
