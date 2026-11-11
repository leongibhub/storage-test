"""Read-only inventory and fail-closed Linux identity/mount checks."""
import hashlib, json, os, pathlib, platform, shutil, subprocess
from platform_support import host_identity, elf, binary_issue

def command(argv, timeout=30):
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,encoding='utf8',errors='replace')
        return {'argv':argv,'rc':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    except (OSError,subprocess.TimeoutExpired) as e:
        return {'argv':argv,'rc':None,'stdout':'','stderr':str(e)}

def decoded(result):
    if result['rc'] != 0: raise ValueError('查询失败: '+str(result))
    return json.loads(result['stdout'])

def flatten(nodes):
    out=[]
    for n in nodes:
        out.append({k:v for k,v in n.items() if k!='children'})
        out.extend(flatten(n.get('children',[])))
    return out

def inventory():
    env={'os':platform.platform(),'architecture':platform.machine(),'hostname':platform.node(),'python':platform.python_version(),'queries':{},'tools':{}}
    env['platform_identity']=host_identity()
    if platform.system()!='Linux':
        if platform.system()=='Windows':
            env['queries']['disks']=command(['powershell','-NoProfile','-Command','Get-Disk | Select-Object Number,FriendlyName,SerialNumber,UniqueId,Size,BusType,FirmwareVersion,IsBoot,IsSystem,OperationalStatus | ConvertTo-Json -Depth 5'])
            env['queries']['volumes']=command(['powershell','-NoProfile','-Command','Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,Size,AccessPaths,IsBoot,IsSystem | ConvertTo-Json -Depth 5'])
        env['execution_supported']=False
        return env
    queries={'lsblk':['lsblk','--json','--bytes','--paths','--output','NAME,KNAME,PKNAME,MAJ:MIN,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,REV,ROTA,LOG-SEC,PHY-SEC,FSTYPE,UUID,PARTUUID,MOUNTPOINTS'],
             'mounts':['findmnt','--json','--output','SOURCE,TARGET,FSTYPE,OPTIONS,MAJ:MIN'],
             'swap':['swapon','--show','--json'], 'controllers':['lspci','-nnk'],
             'nvme':['nvme','list','-o','json'], 'lvm':['lvs','--reportformat','json','-o','lv_path,lv_uuid,devices'],
             'md':['cat','/proc/mdstat']}
    for name,argv in queries.items(): env['queries'][name]=command(argv)
    if env['queries']['lsblk']['rc']!=0:
        fallback=command([v.replace('MOUNTPOINTS','MOUNTPOINT') for v in queries['lsblk']])
        if fallback['rc']==0:
            env['queries']['lsblk_original']=env['queries']['lsblk']
            env['queries']['lsblk']=fallback
            env['inventory_compatibility']='lsblk legacy MOUNTPOINT; full mount inventory remains in findmnt'
    for name in ['fio','smartctl','nvme','lsblk','findmnt','dd']:
        path=shutil.which(name); env['tools'][name]={'path':path}
        if path:
            env['tools'][name]['elf']=elf(path)
            mismatch=binary_issue(path,env['platform_identity']['arch'])
            env['tools'][name]['architecture_issue']=mismatch
            env['tools'][name]['version']=command([path,'--version']) if not mismatch else {'rc':None,'stdout':'','stderr':mismatch}
            try: env['tools'][name]['sha256']=hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
            except OSError: pass
    try: env['devices']=flatten(decoded(env['queries']['lsblk'])['blockdevices'])
    except (ValueError,KeyError) as e: env['inventory_error']=str(e)
    env['boot_id']=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    env['queries']['cpu']=command(['lscpu','--json'])
    env['queries']['os_release']=command(['cat','/etc/os-release'])
    env['execution_supported']=env['platform_identity']['supported']
    return env

def mount_for(path):
    return decoded(command(['findmnt','--json','--target',str(path),'--output','SOURCE,TARGET,FSTYPE,OPTIONS,MAJ:MIN']))['filesystems'][0]

def topology_for(major_minor):
    """Resolve sysfs slaves and partition parent, preserving DM/MD/NVMe namespace IDs."""
    start=pathlib.Path('/sys/dev/block')/major_minor
    if not start.exists(): raise ValueError('目标不是可追溯本地块设备')
    result={}
    def visit(p):
        p=p.resolve(); dev=(p/'dev').read_text().strip()
        if dev in result: return
        q=decoded(command(['lsblk','-d','-b','-J','-p','-o','NAME,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,UUID,PARTUUID','/dev/block/'+dev]))['blockdevices'][0]
        q['sysfs']=str(p); q['major_minor']=dev
        for name in ['wwid','nsid','dm/uuid']:
            f=p/name
            if f.exists(): q[name]=f.read_text().strip()
        result[dev]=q
        slaves=list((p/'slaves').iterdir()) if (p/'slaves').is_dir() else []
        if (p/'partition').exists(): visit(p.parent)
        for s in slaves: visit(s)
    visit(start)
    return sorted(result.values(),key=lambda n:n['major_minor'])

def snapshot(path):
    m=mount_for(path); nodes=topology_for(m['maj:min'])
    return {'mount':m,'nodes':nodes,'device':os.stat(path).st_dev}

def check_expected(nodes, expected):
    if not expected: raise ValueError('缺少目标稳定标识清单；先inspect并集中确认')
    actual=[n for n in nodes if n.get('type')=='disk']
    if len(actual)!=len(expected): raise ValueError('底层物理盘数量不符或拓扑不透明，需要控制器映射')
    seen=set()
    for e in expected:
        if not (e.get('serial') or e.get('wwn')): raise ValueError('目标必须有serial或wwn')
        hits=[n for n in actual if all(str(n.get(k,'')).strip()==str(v).strip() for k,v in e.items())]
        if len(hits)!=1 or hits[0]['major_minor'] in seen: raise ValueError('目标SN/WWN/容量/型号不匹配或不唯一')
        seen.add(hits[0]['major_minor'])

def check_global_unique(expected):
    nodes=flatten(decoded(command(['lsblk','-b','-J','-p','-o','NAME,TYPE,SIZE,MODEL,SERIAL,WWN']))['blockdevices'])
    disks={n['name']:n for n in nodes if n['type']=='disk'}.values()
    for e in expected:
        hits=[n for n in disks if all(str(n.get(k,'')).strip()==str(v).strip() for k,v in e.items())]
        if len(hits)!=1: raise ValueError('目标在整机枚举中不唯一；同SN多namespace须指定唯一WWN，不按路径猜测')

class Guard:
    def __init__(self,c):
        self.c=c; self.path=pathlib.Path(c['test_directory'])
        if not self.path.is_absolute() or self.path.resolve()!=self.path: raise ValueError('测试目录必须为现有绝对路径，不能含符号链接')
        if str(self.path) in ('/','/boot','/boot/efi','/home','/dev','/proc','/sys'): raise ValueError('需要专用测试子目录，不能直接使用系统目录')
        self.initial=snapshot(self.path); check_expected(self.initial['nodes'],c['expected_devices'])
        check_global_unique(c['expected_devices'])
        roots=set()
        for protected in ['/','/boot','/boot/efi','/home']:
            if pathlib.Path(protected).exists(): roots.update(n['major_minor'] for n in snapshot(protected)['nodes'])
        if any(n['major_minor'] in roots for n in self.initial['nodes']) and not c.get('allow_system_disk_file_test',False):
            raise ValueError('测试目录位于系统底层盘；需明确允许系统盘专用文件测试')
        report=pathlib.Path(c['report_root']).resolve()
        if report==self.path or self.path in report.parents: raise ValueError('报告目录不能位于测试目录内')
        self.fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        self.inode=os.fstat(self.fd).st_ino
        try: self.check()
        except BaseException:
            os.close(self.fd); raise

    def check(self,needed=0,space=True):
        now=snapshot(self.path)
        if now!=self.initial or os.stat(self.path).st_ino!=self.inode: raise ValueError('设备身份、拓扑或挂载关系改变；停止本次负载')
        if os.fstat(self.fd).st_dev!=now['device']: raise ValueError('目录描述符不再匹配')
        st=os.fstatvfs(self.fd); free=st.f_bavail*st.f_frsize
        if space and free-needed < self.c['min_free_bytes']: raise ValueError('可用空间低于批准余量或无法容纳测试文件')
        return free

    def close(self): os.close(self.fd)
