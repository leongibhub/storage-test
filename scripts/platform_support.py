"""Product scope is independent of CPU architecture. Unknown capability is not N/A."""
import os, pathlib, platform, re, shutil

PRODUCTS=('aipc','ai_workstation','ai_server')
SERVER={'AISTD-'+str(n) for n in (213,292,323,326)}
CAPABILITIES={'ssd_hotplug','raid_hba'}

def rotational_targets(c,env):
    expected=c.get('expected_devices',[])
    found=[]
    for item in expected:
        hits=[d for d in env.get('devices',[]) if d.get('type')=='disk' and all(str(d.get(k,'')).strip()==str(v).strip() for k,v in item.items())]
        if len(hits)!=1:return False
        found.append(hits[0])
    return bool(found) and all(d.get('rota') in (True,1,'1') for d in found)

def normalize_arch(value):
    return {'arm64':'aarch64','amd64':'x86_64'}.get(value.lower(),value.lower())

def elf(path):
    try:
        with open(path,'rb') as f: h=f.read(20)
    except OSError as e: return {'format':'unavailable','reason':str(e),'arch':None}
    if len(h)<20 or h[:4]!=b'\x7fELF' or h[4] not in (1,2) or h[5] not in (1,2): return {'format':'unknown','arch':None}
    machine=int.from_bytes(h[18:20],'little' if h[5]==1 else 'big')
    return {'format':'ELF','arch':{183:'aarch64',62:'x86_64',40:'arm',3:'i386'}.get(machine,str(machine)),'bits':32 if h[4]==1 else 64,'endian':'little' if h[5]==1 else 'big'}

def binary_issue(path,arch):
    info=elf(path)
    if info.get('arch')!=normalize_arch(arch) or info.get('bits')!=64:
        return '需要与目标架构匹配的原生64位ELF工具: '+str(path)+' '+str(info)
    return None

def host_identity():
    arch=normalize_arch(platform.machine())
    data={'arch':arch,'supported':platform.system()=='Linux' and arch in ('x86_64','aarch64'),'page_size_bytes':os.sysconf('SC_PAGE_SIZE') if hasattr(os,'sysconf') else None,'firmware':{}}
    if platform.system()=='Linux':
        data['python_elf']=elf('/proc/self/exe')
        if data['python_elf'].get('arch')!=arch or data['python_elf'].get('bits')!=64: data['supported']=False
        for name,path in {'dmi_product':'/sys/class/dmi/id/product_name','dmi_board':'/sys/class/dmi/id/board_name','dt_model':'/sys/firmware/devicetree/base/model','dt_compatible':'/sys/firmware/devicetree/base/compatible'}.items():
            try: value=pathlib.Path(path).read_bytes().rstrip(b'\0').replace(b'\0',b', ').decode(errors='replace')
            except OSError: value=None
            data['firmware'][name]={'source':path,'value':value}
    # DMI/DT are evidence for a model, never an automatic marketing-category guess.
    return data

def validate_platform(c):
    p=c.get('platform')
    if not isinstance(p,dict) or p.get('product_type') not in PRODUCTS:
        raise ValueError('platform.product_type必填: aipc / ai_workstation / ai_server')
    for name in ('model','classification_source'):
        if not isinstance(p.get(name),str) or not p[name].strip(): raise ValueError('platform.'+name+'必填；沿用BOM、知识库或用户已确认信息')
    if p.get('expected_arch') is not None and (not isinstance(p['expected_arch'],str) or normalize_arch(p['expected_arch']) not in ('aarch64','x86_64')): raise ValueError('当前支持Linux x86_64和ARM64；ARM32尚未适配')
    if not isinstance(p.get('capabilities',{}),dict): raise ValueError('platform.capabilities必须为对象')
    for name,v in p.get('capabilities',{}).items():
        if name not in CAPABILITIES or not isinstance(v,dict) or (v.get('supported') is not None and type(v['supported']) is not bool): raise ValueError('无效平台能力项: '+name)
        if v.get('supported') is not None and not v.get('source'): raise ValueError('已确认能力必须有source: '+name)
    return p

def applicability(case,c):
    p=validate_platform(c); key=case['id']; product=p['product_type']
    allowed=PRODUCTS
    if key in SERVER: allowed=('ai_server',)
    result={'product_type':product,'allowed_products':list(allowed),'source':case.get('url',key),'classification_source':p['classification_source']}
    if product not in allowed: return dict(result,status='Skipped',reason='原用例机型范围不适用于 '+product)
    required='ssd_hotplug' if key in ('AISTD-213','AISTD-292') else 'raid_hba' if key in ('AISTD-323','AISTD-326') else None
    if required:
        v=p.get('capabilities',{}).get(required,{})
        if v.get('supported') is None: return dict(result,status='Blocked',reason='待确认平台能力: '+required)
        if v['supported'] is False: return dict(result,status='Skipped',reason='已确认不支持 '+required,capability_source=v['source'])
    return dict(result,status='Applicable',reason='机型范围适用；仍需满足用例前置及执行权限')

def execution_issues(case,c,env,command):
    issues=[]; host=env.get('platform_identity',{}); arch=host.get('arch')
    if not host.get('supported'): return ['负载需要原生Linux x86_64/ARM64及64位Python；当前平台不支持']
    expected=c['platform'].get('expected_arch')
    if expected and normalize_arch(expected)!=arch: issues.append('目标架构与已确认平台不符')
    if case['kind']=='external': return issues
    if case['kind']=='trim' and rotational_targets(c,env):return issues
    names={'lsblk','findmnt'}
    if case['kind'] not in ('identity','logs','raw_read'): names.add('smartctl')
    if case['kind'] in ('basic','file','fio','raw_read'): names.add('dd')
    if case['kind']=='fio': names.add('fio')
    if case['kind']=='disk_inventory':
        names.add('lspci')
        if any(x.get('tran')=='nvme' for x in env.get('devices',[])):names.add('nvme')
    if case['kind']=='trim':names.update({'fstrim','systemctl'})
    if case['kind']=='disk_full':names.update({'mkfs.ext4','losetup','mount','umount'})
    for name in sorted(names):
        path=shutil.which(name)
        if not path: issues.append('缺少原生工具: '+name); continue
        problem=binary_issue(path,arch)
        if problem: issues.append(problem)
    if case['kind']=='fio' and not issues:
        engines=command(['fio','--enghelp'])
        wanted={p.get('ioengine','libaio') for p in case['profiles']}
        listed=set(re.findall(r'\b[a-z][a-z0-9_]*\b',engines['stdout']))
        if engines['rc']!=0 or not wanted<=listed: issues.append('fio缺少原用例I/O引擎: '+','.join(sorted(wanted-listed))+'；不得静默换引擎')
    return issues
