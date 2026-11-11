"""Disk persistence phases; reboot is issued only by an external controller."""
import argparse,hashlib,json,os,pathlib,uuid
from probe import Guard,command,inventory
from storage_test import validate,health_parse,now

def digest(path,algorithm):
    h=hashlib.new(algorithm)
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()

def execute(c,phase,receipt=None):
    validate(c)
    if c['authorization'].get('reboot') is not True or c['authorization'].get('file_write') is not True:raise ValueError('需要具体整机重启和文件写入授权')
    if c['max_space_bytes']<1024**3 or c['max_total_write_bytes']<1024**3:raise ValueError('重启数据至少需要1GiB空间及写入预算')
    g=Guard(c);g.check(1024**3);env=inventory()
    row={'id':'AISTD-56','title':'验证重启后一致性','started':now(),'phase':phase,'environment':env,'target':g.initial,'status':'Blocked','commands':[]}
    def health():
        output=[]
        for disk in [x for x in g.initial['nodes'] if x['type']=='disk']:
            v=command(['smartctl','-a','-j',disk['name']]);row['commands'].append(v);data=json.loads(v['stdout']);parsed=health_parse(data,v['rc'])
            if data.get('serial_number','').strip()!=disk.get('serial','').strip():raise ValueError('SMART序列号无法对应目标')
            if parsed['passed'] is False or parsed['counters'].get('critical_warning',0):raise AssertionError('SMART健康失败')
            if parsed['passed'] is None or parsed['warnings'] or not parsed['counters']:raise ValueError('健康信息不完整')
            output.append({'serial':disk['serial'],**parsed})
        return output
    try:
        row['health']=health()
        if phase=='prepare':
            path=pathlib.Path(c['test_directory'])/('reboot-'+uuid.uuid4().hex+'.bin')
            fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                for _ in range(1024):
                    data=os.urandom(1024**2);offset=0
                    while offset<len(data):offset+=os.write(fd,data[offset:])
                os.fsync(fd)
            finally:os.close(fd)
            v=command(['sync']);row['commands'].append(v)
            if v['rc']:raise ValueError('sync未完成')
            g.check(space=False);st=path.stat()
            row.update(path=str(path),inode=st.st_ino,size=st.st_size,md5=digest(path,'md5'),sha256=digest(path,'sha256'),boot_id=env['boot_id'],status='Prepared',reason='基线已持久化；尚未重启验证')
        else:
            if not receipt or receipt.get('status')!='Prepared':raise ValueError('缺重启前外部保存的有效基线')
            path=pathlib.Path(receipt['path'])
            if path.parent!=pathlib.Path(c['test_directory']) or not path.name.startswith('reboot-') or path.is_symlink():raise ValueError('基线路径不在批准目录')
            if env['boot_id']==receipt['boot_id']:raise ValueError('未观察到真实重启')
            st=path.stat()
            if st.st_ino!=receipt['inode'] or st.st_size!=receipt['size']:raise AssertionError('重启后文件身份/大小改变')
            hashes={a:digest(path,a) for a in ('md5','sha256')}
            if any(hashes[a]!=receipt[a] for a in hashes):raise AssertionError('重启后数据校验不一致；保留文件')
            for before,after in zip(receipt['health'],row['health']):
                if before['serial']!=after['serial']:raise AssertionError('重启前后磁盘序列号不一致')
                for k,v in before['counters'].items():
                    if k!='percentage_used' and after['counters'].get(k)!=v:raise AssertionError('重启前后健康计数变化: '+k)
            g.check(space=False);path.unlink()
            row.update(before_boot_id=receipt['boot_id'],after_boot_id=env['boot_id'],hashes=hashes,cleanup='owned file removed',status='Pass',reason='实际重启后文件可访问，MD5/SHA256一致')
    except AssertionError as ex:row.update(status='Fail',reason=str(ex))
    except Exception as ex:row.update(status='Blocked',reason=str(ex))
    finally:g.close();row['ended']=now()
    return row

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--phase',choices=['prepare','verify'],required=True);p.add_argument('--receipt');p.add_argument('--out',required=True);a=p.parse_args()
    row=execute(json.loads(pathlib.Path(a.config).read_text()),a.phase,json.loads(pathlib.Path(a.receipt).read_text()) if a.receipt else None)
    from report import save
    save(a.out,row);print(json.dumps({'status':row['status'],'reason':row.get('reason')}));raise SystemExit(0 if row['status'] in ('Prepared','Pass') else 2)
