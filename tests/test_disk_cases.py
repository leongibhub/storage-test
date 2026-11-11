import pathlib,sys,unittest,tempfile
from unittest.mock import Mock,patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import storage_test as s
import disk_cases as d
from test_storage import cfg

class DiskTests(unittest.TestCase):
    def runner(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        return s.Runner(cfg(pathlib.Path(self.tmp.name)),[s.CATALOG[0]])
    def test_trim_without_authorization_never_invokes_tool(self):
        r=self.runner()
        with patch.object(r,'logcmd') as command:
            with self.assertRaises(s.Blocked):d.trim_mount(r,'/')
            command.assert_not_called()
    def test_unapproved_mount_never_trims(self):
        r=self.runner();r.c['authorization']['trim']=True;r.c['trim']={'mounts':['/data']}
        with patch.object(r,'logcmd') as command:
            with self.assertRaises(s.Blocked):d.trim_mount(r,'/')
            command.assert_not_called()
    def test_trim_rejects_other_disk_identity_before_ioctl(self):
        r=self.runner();r.c['authorization']['trim']=True;r.c['trim']={'mounts':['/data']}
        with patch.object(d,'snapshot',return_value={'mount':{'target':'/data','fstype':'ext4'},'nodes':[]}),patch.object(r,'logcmd') as command:
            with self.assertRaises(ValueError):d.trim_mount(r,'/data')
            command.assert_not_called()
    def test_disk_full_cannot_default_to_filling_existing_partition(self):
        r=self.runner();r.require_file=Mock()
        with patch.object(r,'own_file') as create:
            with self.assertRaises(s.Blocked):d.disk_full(r,{})
            create.assert_not_called()
    def test_dead_journal_complete_same_boot_ring_recovers_window(self):
        r=self.runner();r.boot_start=100;r.state['environment']={'boot_id':'fixture'}
        replies=[({'rc':3,'stdout':'inactive','stderr':''},'service'),({'rc':0,'stdout':'[    0.000000] boot\n[ 90.0] I/O error\n[ 101.0] healthy','stderr':''},'ring')]
        with patch.object(r,'logcmd',side_effect=replies),patch.object(s.platform,'system',return_value='Linux'),patch.object(pathlib.Path,'read_text',return_value='fixture'):
            result=r.kernel();self.assertEqual(result['errors'],[]);self.assertIn('healthy',result['text'])
    def test_truncated_ring_is_not_success(self):
        r=self.runner();r.boot_start=100;r.state['environment']={'boot_id':'fixture'}
        replies=[({'rc':3,'stdout':'inactive','stderr':''},'service'),({'rc':0,'stdout':'[ 120.0] healthy','stderr':''},'ring')]
        with patch.object(r,'logcmd',side_effect=replies),patch.object(s.platform,'system',return_value='Linux'),patch.object(pathlib.Path,'read_text',return_value='fixture'):
            with self.assertRaises(s.Blocked):r.kernel()
    def test_hdd_trim_is_skipped_before_tool_dependencies(self):
        from platform_support import rotational_targets,execution_issues
        r=self.runner();disk={'type':'disk','serial':'fixture','model':'synthetic','size':100000,'rota':True}
        env={'devices':[disk],'platform_identity':{'arch':'x86_64','supported':True}}
        self.assertTrue(rotational_targets(r.c,env))
        self.assertEqual(execution_issues({'kind':'trim'},r.c,env,Mock()),[])
        disk['serial']='different';self.assertFalse(rotational_targets(r.c,env))
    def test_timeout_return_is_not_success(self):
        r=self.runner()
        with self.assertRaises(s.Blocked):r.check_rc({'rc':None,'stdout':'','stderr':'timeout','evidence':'fixture'})
