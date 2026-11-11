"""External controller: complete disk plan, durable receipts, real reboot resume.
Requires Paramiko on the controller only. Credentials are never saved.
"""
import argparse,getpass,hashlib,json,pathlib,shlex,sys,time,uuid,tarfile,shutil
from report import save,generate
from storage_test import CATALOG,validate
from platform_support import rotational_targets

def main():
    import paramiko
    p=argparse.ArgumentParser();p.add_argument('--host',required=True);p.add_argument('--user',required=True);p.add_argument('--config',required=True);p.add_argument('--out',required=True);p.add_argument('--only',nargs='+');p.add_argument('--password-stdin',action='store_true');p.add_argument('--resume',action='store_true');a=p.parse_args()
    c=validate(json.loads(pathlib.Path(a.config).read_text(encoding='utf-8-sig')))
    expected=c.get('ssh_host_key_sha256')
    if not expected:raise ValueError('缺已核对的ssh_host_key_sha256；先只读发现主机身份')
    password=sys.stdin.readline().rstrip('\r\n') if a.password_stdin else getpass.getpass('SSH/sudo password: ')
    out=pathlib.Path(a.out).resolve();out.mkdir(parents=True,exist_ok=True);control=out/'controller.json'
    def connect():
        client=paramiko.SSHClient();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(a.host,username=a.user,password=password,timeout=15,allow_agent=False,look_for_keys=False)
        if hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()!=expected:client.close();raise ValueError('SSH host key changed')
        return client
    def cmd(client,argv,sudo=False,timeout=60):
        line=shlex.join(argv)
        if sudo:line='sudo -S -p "" -- '+line
        i,o,e=client.exec_command(line,timeout=timeout)
        if sudo:i.write(password+'\n');i.flush();i.channel.shutdown_write()
        stdout=o.read().decode(errors='replace');stderr=e.read().decode(errors='replace')
        return {'rc':o.channel.recv_exit_status(),'stdout':stdout,'stderr':stderr}
    def ok(result):
        if result['rc']!=0:raise RuntimeError(result['stderr'] or result['stdout'])
        return result
    def fetch(client,remote,local):
        s=client.open_sftp();s.get(remote,str(local));s.close()
    if a.resume:
        meta=json.loads(control.read_text(encoding='utf-8'))
        if meta['host']!=a.host or meta['user']!=a.user or meta['host_key']!=expected:raise ValueError('恢复目标与控制记录不一致')
        if meta.get('finished'):print('已有完成记录，不重复执行');return 0
        base=meta['base'];selected=meta['selected'];c=json.loads((out/'resolved-config.json').read_text(encoding='utf-8'))
    else:
        if control.exists():raise ValueError('输出目录已有控制记录；使用--resume，不重复启动')
        selected=a.only or [x['id'] for x in CATALOG if x['scope']=='core']
        if not set(selected)<={x['id'] for x in CATALOG}:raise ValueError('计划含非硬盘或未知用例')
        # Ask before any load, not after hours of work.
        if 'AISTD-56' in selected and c['authorization'].get('reboot') is not True:raise ValueError('完整计划包含56，请先确认具体重启窗口')
        client=connect();home=ok(cmd(client,['pwd']))['stdout'].strip();base=home+'/aistd-disk-'+uuid.uuid4().hex[:12]
        ok(cmd(client,['mkdir','-m','700',base]));s=client.open_sftp();skill=pathlib.Path(__file__).resolve().parents[1]
        for directory in ('scripts','references'):s.mkdir(base+'/'+directory)
        for directory,pattern in [('scripts','*.py'),('references','*.json')]:
            for f in (skill/directory).glob(pattern):s.put(str(f),base+'/'+directory+'/'+f.name)
        s.close()
        ok(cmd(client,['env','LC_ALL=C','python3',base+'/scripts/storage_test.py','inspect','--out',base+'/inspection.json']))
        fetch(client,base+'/inspection.json',out/'inspection.json');env=json.loads((out/'inspection.json').read_text(encoding='utf-8'))
        if set(selected)&{'AISTD-70','AISTD-212'} and not rotational_targets(c,env) and (c['authorization'].get('trim') is not True or not c.get('trim',{}).get('mounts')):raise ValueError('已发现非机械盘目标；完整计划需先确认TRIM挂载点与授权')
        ok(cmd(client,['mkdir','-p',c['test_directory']],True))
        c=dict(c,report_root=base+'/reports',cases=[x for x in selected if x!='AISTD-56'])
        save(out/'resolved-config.json',c)
        regular=dict(c)
        if 'AISTD-56' in selected:
            regular['max_total_write_bytes']-=1024**3
            if regular['max_total_write_bytes']<=0:raise ValueError('总预算不足以预留重启基线文件')
        save(out/'regular-config.json',regular)
        s=client.open_sftp();s.put(str(out/'resolved-config.json'),base+'/config.json');s.put(str(out/'regular-config.json'),base+'/regular-config.json');s.close();client.close()
        meta={'host':a.host,'user':a.user,'host_key':expected,'base':base,'selected':selected,'stage':'ready'};save(control,meta)
    def persist(**values):meta.update(values);save(control,meta)
    if meta['stage']=='ready' and c['cases']:
        client=connect();pre=cmd(client,['env','LC_ALL=C','python3',base+'/scripts/storage_test.py','preflight','--config',base+'/regular-config.json','--out',base+'/preflight.json'],True)
        fetch(client,base+'/preflight.json',out/'preflight.json');ok(pre)
        unit='aistd-disk-'+base.rsplit('-',1)[-1]
        runner=shlex.join(['env','LC_ALL=C','python3',base+'/scripts/storage_test.py','run','--config',base+'/regular-config.json'])+' >'+shlex.quote(base+'/runner.stdout')+' 2>'+shlex.quote(base+'/runner.stderr')
        # Persist intent before mutation. On ambiguous launch failure never start a second run.
        persist(stage='running',unit=unit)
        ok(cmd(client,['systemd-run','--unit='+unit,'--property=RuntimeMaxSec='+str(c['max_case_seconds']*len(c['cases'])+300),'/bin/sh','-c',runner],True));client.close()
    if meta['stage']=='running':
        while True:
            client=connect();unit=cmd(client,['systemctl','show',meta['unit']+'.service','-p','ActiveState','-p','MainPID','-p','ExecMainStatus']);save(out/'unit-status.json',unit)
            active='ActiveState=active' in unit['stdout'] or 'ActiveState=activating' in unit['stdout']
            client.close()
            if not active:break
            time.sleep(15)
        persist(stage='regular_done')
    if meta['stage']=='ready':persist(stage='regular_done')
    if meta['stage']=='regular_done' and 'AISTD-56' in selected:
        client=connect();s=client.open_sftp()
        if c['cases']:
            for folder in s.listdir(base+'/reports'):
                with s.open(base+'/reports/'+folder+'/state.json') as f:state=json.load(f)
                if not state.get('completed') or any(x['status']=='Fail' for x in state['results']):raise RuntimeError('常规测试存在失败/中断，保留现场，不继续重启')
        s.close();prepare=cmd(client,['env','LC_ALL=C','python3',base+'/scripts/reboot_case.py','--config',base+'/config.json','--phase','prepare','--out',base+'/reboot-before.json'],True,180)
        fetch(client,base+'/reboot-before.json',out/'reboot-before.json');ok(prepare)
        before=json.loads((out/'reboot-before.json').read_text(encoding='utf-8'))
        if before['status']!='Prepared':raise RuntimeError('Reboot baseline not prepared')
        persist(stage='reboot_requested',before_boot_id=before['boot_id'])
        # Intent persisted off-host; a resumed controller never repeats this reboot request.
        response=cmd(client,['systemctl','reboot'],True);save(out/'reboot-request.json',response);client.close()
    if meta['stage']=='reboot_requested':
        connected=False
        for _ in range(60):
            time.sleep(10)
            try:
                client=connect();boot=ok(cmd(client,['cat','/proc/sys/kernel/random/boot_id']))['stdout'].strip()
                if boot==meta['before_boot_id']:client.close();continue
                result=cmd(client,['env','LC_ALL=C','python3',base+'/scripts/reboot_case.py','--config',base+'/config.json','--phase','verify','--receipt',base+'/reboot-before.json','--out',base+'/reboot-after.json'],True,180)
                fetch(client,base+'/reboot-after.json',out/'reboot-after.json');client.close();connected=True;break
            except (OSError,paramiko.SSHException):continue
        if not connected:raise RuntimeError('10分钟内未核实重启恢复；保留文件和基线，使用--resume继续，不重复重启')
        persist(stage='reboot_done')
    client=connect();states=[]
    if c['cases']:
        ok(cmd(client,['tar','-czf',base+'/reports.tar.gz','-C',base,'reports','scripts','references','config.json','regular-config.json'],True,600))
        digest=ok(cmd(client,['sha256sum',base+'/reports.tar.gz']))['stdout'].split()[0]
        fetch(client,base+'/reports.tar.gz',out/'reports.tar.gz')
        if hashlib.sha256((out/'reports.tar.gz').read_bytes()).hexdigest()!=digest:raise ValueError('证据包哈希不一致')
        save(out/'archive-receipt.json',{'sha256':digest,'verified':True})
        destination_root=(out/'evidence').resolve();destination_root.mkdir(exist_ok=True)
        extracted_hashes={}
        with tarfile.open(out/'reports.tar.gz') as archive:
            for member in archive:
                destination=(destination_root/member.name).resolve()
                if destination_root not in destination.parents or not (member.isfile() or member.isdir()):raise ValueError('不安全的证据归档成员')
                if member.isdir():destination.mkdir(parents=True,exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    hasher=hashlib.sha256()
                    with archive.extractfile(member) as source,destination.open('wb') as target:
                        for block in iter(lambda:source.read(1024**2),b''):target.write(block);hasher.update(block)
                    extracted_hashes[str(destination)]=hasher.hexdigest()
        for destination in (destination_root/'reports').iterdir():
            state=json.loads((destination/'state.json').read_text(encoding='utf-8'));states.append(state)
            for row in state['results']:row['source_run']=state['run_id'];row['evidence_directory']=str(destination)
            manifest=json.loads((destination/'sha256.json').read_text(encoding='utf-8'))
            for name,digest in manifest.items():
                if extracted_hashes.get(str(destination/name))!=digest:raise ValueError('下载证据哈希不一致: '+name)
    client.close()
    results=[r for state in states for r in state['results']]
    if (out/'reboot-after.json').exists():results.append(json.loads((out/'reboot-after.json').read_text(encoding='utf-8')))
    merged={'run_id':base.rsplit('/',1)[-1],'config':c,'results':results,'completed':all(x.get('completed') for x in states) and len(results)==len(selected),'cleanup':[x for state in states for x in state.get('cleanup',[])],'environment':states[0].get('environment') if states else {},'controller':meta}
    save(out/'state.json',merged);summary=generate(out);persist(stage='finished',finished=True,counts=summary['counts'])
    print(json.dumps({'report':str(out/'report.html'),'counts':summary['counts']},ensure_ascii=False))
    return 0 if not summary['counts']['Fail'] and not summary['counts']['Blocked'] else 2

if __name__=='__main__':raise SystemExit(main())
