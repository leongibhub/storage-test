"""Isolated development environment only. Real dd/fio on disposable files; synthetic hardware probes."""
import json, os, pathlib, sys, tempfile
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import storage_test as s

class FixtureGuard:
    def __init__(self,c):
        self.c=c; self.path=pathlib.Path(c['test_directory']); self.fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY)
        self.initial={'nodes':[{'type':'disk','serial':'CONTAINER-FIXTURE','size':1,'model':'synthetic'}],'mount':{'fstype':'development-fixture'}}
    def check(self,needed=0,space=True):
        free=os.fstatvfs(self.fd).f_bavail*os.fstatvfs(self.fd).f_frsize
        if space and free-needed<self.c['min_free_bytes']: raise ValueError('fixture low space')
        return free
    def close(self): os.close(self.fd)

def health(self): return [{'serial':'CONTAINER-FIXTURE','passed':True,'temperature_c':None,'counters':{'media_errors':0,'critical_warning':0},'warnings':[],'evidence':'SYNTHETIC: no device telemetry in container'}]
def kernel(self): return {'errors':[],'text':'SYNTHETIC: no host kernel access','evidence':'fixture only'}

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='aistd-storage-validation-') as d:
        c=dict(expected_devices=[{'serial':'CONTAINER-FIXTURE','model':'synthetic','size':1}],test_directory=d,report_root='/evidence/linux-validation',max_space_bytes=64*1024**2,min_free_bytes=1024**2,max_total_write_bytes=512*1024**2,max_case_seconds=15000,authorization={'file_write':True},other_workload='isolated developer container',validation_only=True,validation_scale={'seconds':2,'size_bytes':4*1024**2},rate_limit_bytes_per_job=4*1024**2,approve_verify_cycles=True)
        ids=['AISTD-55','NEW-FILE','AISTD-57','AISTD-58','AISTD-65','AISTD-211']
        c['platform']={'product_type':'aipc','model':'container-fixture','classification_source':'isolated development fixture'}
        items=[next(x for x in s.CATALOG if x['id']==i) for i in ids]
        with patch.object(s,'Guard',FixtureGuard),patch.object(s.Runner,'health',health),patch.object(s.Runner,'kernel',kernel):
            r=s.Runner(c,items); r.run()
        expected={i:'Pass' for i in ids}
        for row in r.state['results']:
            assert row['status']==expected[row['id']],(row['id'],row['reason'])
            if row['id'] in ('AISTD-57','AISTD-58','AISTD-211'): assert row.get('measured'),row
        assert not list(pathlib.Path(d).iterdir()),'cleanup left files'
        print('DEVELOPMENT INTEGRATION OK: real dd/fio, synthetic topology/health/kernel, no hardware acceptance')
