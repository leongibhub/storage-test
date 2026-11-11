import json,pathlib,sys,tempfile,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import reboot_case as b
from test_storage import cfg

class RebootTests(unittest.TestCase):
    def test_unchanged_boot_cannot_be_reported_as_reboot_pass(self):
        with tempfile.TemporaryDirectory() as d:
            c=cfg(pathlib.Path(d));c['authorization']['reboot']=True
            guard=Mock();guard.initial={'nodes':[{'type':'disk','name':'/dev/fixture','serial':'fixture'}]}
            health={'serial_number':'fixture','smart_status':{'passed':True},'nvme_smart_health_information_log':{'critical_warning':0,'media_errors':0}}
            receipt={'status':'Prepared','path':str(pathlib.Path(c['test_directory'])/'reboot-fixture.bin'),'boot_id':'same'}
            with patch.object(b,'Guard',return_value=guard),patch.object(b,'inventory',return_value={'boot_id':'same'}),patch.object(b,'command',return_value={'rc':0,'stdout':json.dumps(health),'stderr':''}),patch.object(pathlib.Path,'unlink') as remove:
                result=b.execute(c,'verify',receipt)
            self.assertEqual(result['status'],'Blocked');remove.assert_not_called()
