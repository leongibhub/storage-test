#!/usr/bin/env python3
"""Bounded storage runner. Python 3.8+, Linux load execution; no raw writes."""
import argparse, datetime, errno, hashlib, json, math, os, pathlib, platform, re, shutil, signal, subprocess, sys, time, uuid
from probe import Guard, command, decoded, inventory, snapshot, topology_for, check_expected, check_global_unique
from report import save, generate
from platform_support import validate_platform, applicability, execution_issues, rotational_targets
from thermal import bandwidth_windows
ROOT=pathlib.Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'references/catalog.json').read_text(encoding='utf8'))
ERRORS=re.compile(r'I/O error|Buffer I/O|blk_update_request|nvme.*(?:reset|timeout)|EXT4-fs error|XFS.*(?:corrupt|error)|BTRFS.*error|ata\d.*(?:failed|error)|scsi.*(?:error|timeout)',re.I)

class Blocked(Exception): pass
class Failed(Exception): pass
class NotApplicable(Exception): pass

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def amount(v):
    m=re.fullmatch(r'(\d+)([kKmMgGtT]?)',str(v))
    if not m: raise ValueError('无效字节值 '+str(v))
    return int(m[1])*1024**(' kmgt'.index(m[2].lower()) if m[2] else 0)
def seconds(v):
    m=re.fullmatch(r'(\d+)([smh]?)',str(v))
    if not m: raise ValueError('无效时长 '+str(v))
    return int(m[1])*{'':1,'s':1,'m':60,'h':3600}[m[2]]

def validate(c):
    validate_platform(c)
    if c.get('performance_mode','record_only') not in ('record_only','compare_baseline'): raise ValueError('performance_mode需record_only或compare_baseline')
    required=['expected_devices','test_directory','report_root','max_space_bytes','min_free_bytes','max_total_write_bytes','max_case_seconds','authorization','other_workload']
    missing=[k for k in required if k not in c or c[k] is None]
    if missing: raise ValueError('一次性补充字段: '+', '.join(missing))
    if not isinstance(c['authorization'],dict): raise ValueError('authorization应为对象')
    for k,v in c['authorization'].items():
        if type(v) is not bool: raise ValueError('授权字段必须为JSON布尔值: '+k)
    for k in ['allow_system_disk_file_test','approve_verify_cycles','approve_174_libaio','validation_only']:
        if k in c and type(c[k]) is not bool: raise ValueError(k+'必须为JSON布尔值')
    for k,v in c.get('validation_scale',{}).items():
        if k not in ('seconds','size_bytes') or type(v) is not int or v<=0: raise ValueError('开发缩放值必须为正整数seconds/size_bytes')
    for k in ['max_space_bytes','min_free_bytes','max_total_write_bytes','max_case_seconds']:
        if type(c[k]) is not int or c[k]<=0: raise ValueError(k+'必须为正整数')
    if c.get('mode','file') not in ('file','raw_read'): raise ValueError('不支持裸写、擦除或格式化模式')
    for p in ['test_directory','report_root']:
        if not (pathlib.Path(c[p]).is_absolute() or pathlib.PurePosixPath(c[p]).is_absolute()): raise ValueError(p+'必须为绝对路径')
    if not c['expected_devices']: raise ValueError('不能自动猜测目标设备')
    for e in c['expected_devices']:
        if not set(e)<= {'serial','wwn','model','size'} or not (e.get('serial') or e.get('wwn')) or not e.get('size') or not e.get('model'):
            raise ValueError('每个目标需serial或wwn，以及model和精确size字节')
    if 'validation_scale' in c and not c.get('validation_only'): raise ValueError('缩短负载只能用于开发验证')
    if c.get('validation_only') and 'validation' not in c['report_root'].lower(): raise ValueError('开发结果目录需含validation以隔离')
    for key,value in c.get('exclusions',{}).items():
        if key not in {x['id'] for x in CATALOG} or not isinstance(value,dict) or not value.get('reason') or not value.get('source'): raise ValueError('排除项需真实用例ID、明确reason及授权/不适用source')
    if c.get('stop_temperature_c') is not None:
        if type(c['stop_temperature_c']) not in (int,float) or not math.isfinite(c['stop_temperature_c']) or not c.get('temperature_limit_source'): raise ValueError('温度停止值需有限数值和temperature_limit_source')
    for key,base in c.get('baselines',{}).items():
        if not base.get('source') or not base.get('conditions'): raise ValueError(key+'基准缺来源或可比条件')
        if any(k in base for k in ('read','write','trim')): raise ValueError('每profile使用顶层阈值，不接受方向子字典；读写不同阈值须分profile')
        allowed={'min_bw_bytes','min_iops','max_mean_latency_ns','max_p99_latency_ns'}
        if not any(k in base for k in allowed): raise ValueError(key+'基准无阈值')
        for k in allowed&base.keys():
            if type(base[k]) not in (int,float) or not math.isfinite(base[k]) or base[k]<0: raise ValueError('基准需有限非负数')
    return c

def select(c,args):
    ids=getattr(args,'only',None) or c.get('cases')
    chosen=[x for x in CATALOG if x['scope']=='core'] if not ids else [next((x for x in CATALOG if x['id']==i),None) for i in ids]
    if any(x is None for x in chosen): raise ValueError('未知用例ID')
    if len({x['id'] for x in chosen})!=len(chosen): raise ValueError('用例不能重复')
    if getattr(args,'group',None): chosen=[x for x in chosen if x['group']==args.group]
    if getattr(args,'rerun_from',None):
        if not args.repair_note: raise ValueError('重跑必须记录实际修复或新的诊断依据')
        old=json.loads((pathlib.Path(args.rerun_from)/'state.json').read_text(encoding='utf8'))
        ids={x['id'] for x in old['results'] if x['status'] in ('Fail','Blocked')}
        chosen=[x for x in chosen if x['id'] in ids]
    if not chosen: raise ValueError('没有选中测试项')
    return chosen

def health_parse(data,rc):
    """SMART bitmask is not a normal exit code. Missing metrics remain unknown."""
    result={'passed':data.get('smart_status',{}).get('passed'),'temperature_c':data.get('temperature',{}).get('current'),'counters':{},'warnings':[]}
    nv=data.get('nvme_smart_health_information_log',{})
    for k in ['media_errors','num_err_log_entries','unsafe_shutdowns','critical_warning','percentage_used']:
        if k in nv: result['counters'][k]=nv[k]
    for a in data.get('ata_smart_attributes',{}).get('table',[]):
        if a.get('id') in (5,187,188,197,198,199): result['counters'][a['name']]=a.get('raw',{}).get('value')
    for direction,items in data.get('scsi_error_counter_log',{}).items():
        if 'total_uncorrected_errors' in items: result['counters'][direction+'_uncorrected']=items['total_uncorrected_errors']
    if rc is None or rc&7: result['warnings'].append('健康查询不完整或设备无法访问')
    if rc is not None and rc&248: result['warnings'].append('SMART返回历史/当前健康告警，需依据原始输出区分')
    return result

def fio_parse(data):
    jobs=data.get('jobs')
    if not isinstance(jobs,list) or not jobs: raise Blocked('fio JSON无jobs')
    if any(j.get('error',0) for j in jobs): raise Failed('fio报告I/O或校验错误')
    output={}
    for direction in ['read','write','trim']:
        records=[j.get(direction,{}) for j in jobs]
        active=[x for x in records if x.get('io_bytes',0)>0 or x.get('total_ios',0)>0]
        if not active: continue
        total_ios=sum(x.get('total_ios',0) for x in active)
        if not total_ios: raise Blocked('fio缺少实际I/O计数')
        lat=[]; p99=[]
        for x in active:
            l=x.get('clat_ns') or x.get('clat_us') or x.get('clat')
            scale=1 if 'clat_ns' in x else 1000
            if not l or 'mean' not in l: raise Blocked('fio缺少完成延迟指标')
            lat.append(l['mean']*scale*x.get('total_ios',0))
            percent=l.get('percentile',{})
            val=next((v for k,v in percent.items() if float(k)==99),None)
            if val is not None: p99.append(val*scale)
        output[direction]={'io_bytes':sum(x.get('io_bytes',0) for x in active),'iops':sum(x.get('iops',0) for x in active),'bw_bytes':sum(x.get('bw_bytes',x.get('bw',0)*1024) for x in active),'mean_latency_ns':sum(lat)/total_ios,'p99_latency_ns':max(p99) if p99 else None,'p99_method':'maximum of reported job/group p99; not pooled quantile','total_ios':total_ios,'runtime_ms':min(x.get('runtime',0) for x in active)}
        for key,value in output[direction].items():
            if key=='p99_method' or value is None: continue
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0: raise Blocked('fio指标无效: '+key)
    if not output: raise Blocked('fio没有实际I/O')
    return output

class Runner:
    Failed=Failed
    Blocked=Blocked
    NotApplicable=NotApplicable
    parse_health=staticmethod(health_parse)
    def __init__(self,c,items,previous=None,repair=None):
        self.c=c; self.items=items; self.guard=None; self.owned=[]; self.spent=0; self.seq=0; self.stop=False; self.lock=None; self.active=None; self.case_deadline=None
        self.boot_start=time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time,'CLOCK_BOOTTIME') else None
        self.id=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        self.root=pathlib.Path(c['report_root'])/self.id; self.root.mkdir(parents=True,exist_ok=False)
        self.state={'run_id':self.id,'started':now(),'completed':False,'config':c,'previous':previous,'repair_note':repair,'results':[dict(id=i['id'],title=i['title'],status='Blocked',reason='尚未执行',criteria=i['criteria'],source=i['source'],attempts=[]) for i in items],'cleanup':[]}
        self.state['implementation_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scripts').glob('*.py')}
        self.state['owned_files']=[{k:v for k,v in x.items() if k!='fd'} for x in self.owned]
        self.persist()
    def persist(self): save(self.root/'state.json',self.state)
    def logcmd(self,argv,timeout=30):
        r=command(argv,timeout); self.seq+=1; name=f'command-{self.seq:04d}.json'; save(self.root/name,r); return r,name
    def kernel(self):
        service,service_name=self.logcmd(['systemctl','is-active','systemd-journald.service'])
        if service['rc']!=0 or service['stdout'].strip()!='active':
            raw,raw_name=self.logcmd(['dmesg'])
            boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip() if platform.system()=='Linux' else None
            if raw['rc']!=0 or not re.match(r'^\[\s*0\.0+\]',raw['stdout']) or self.boot_start is None or boot!=self.state.get('environment',{}).get('boot_id'):
                raise Blocked('日志服务未运行，且无法证明内核环形日志覆盖完整时间窗: '+service_name+' '+raw_name)
            lines=[line for line in raw['stdout'].splitlines() if (re.match(r'^\[\s*([\d.]+)\]',line) and float(re.match(r'^\[\s*([\d.]+)\]',line)[1])>=self.boot_start)]
            text='\n'.join(lines)
            return {'evidence':raw_name,'errors':ERRORS.findall(text),'text':text,'fallback':'same boot, ring buffer retained from boot; monotonic test window','service_evidence':service_name}
        since='@'+format(datetime.datetime.fromisoformat(self.state['started']).timestamp(),'.6f')
        r,name=self.logcmd(['journalctl','-k','-b','--since',since,'--no-pager','-o','short-monotonic'])
        if r['rc']!=0 or 'permission' in r['stderr'].lower() or 'not seeing messages' in r['stderr'].lower(): raise Blocked('无法读取完整内核时间窗日志: '+name)
        return {'evidence':name,'errors':ERRORS.findall(r['stdout']),'text':r['stdout']}
    def health(self):
        all_data=[]
        for node in self.guard.initial['nodes']:
            if node.get('type')!='disk': continue
            r,name=self.logcmd(['smartctl','-a','-j',node['name']])
            try: data=json.loads(r['stdout']); parsed=health_parse(data,r['rc'])
            except (ValueError,TypeError): parsed={'passed':None,'temperature_c':None,'counters':{},'warnings':['SMART输出无法解析']}
            all_data.append({'serial':node.get('serial'),'path':node['name'],'evidence':name,**parsed})
        return all_data
    def check_health(self,before,after):
        reasons=[]; deltas=[]
        if len(before)!=len(after): raise Failed('测试期间健康设备数量改变')
        for b,a in zip(before,after):
            if b['serial']!=a['serial']: raise Failed('健康记录设备身份改变')
            delta={k:a['counters'][k]-v for k,v in b['counters'].items() if isinstance(v,(int,float)) and isinstance(a['counters'].get(k),(int,float))}
            if set(b['counters'])!=set(a['counters']): reasons.append('测试前后健康指标缺失或集合变化')
            deltas.append({'serial':a['serial'],'delta':delta})
            if a['passed'] is False or a['counters'].get('critical_warning',0): raise Failed('当前SMART健康失败/critical_warning')
            if self.c.get('stop_temperature_c') is not None and a.get('temperature_c') is None: reasons.append('必需温度测点不可读')
            if self.c.get('stop_temperature_c') is not None and a.get('temperature_c') is not None and a['temperature_c']>=self.c['stop_temperature_c']: raise Failed('达到批准温度停止条件')
            if any(v>0 for k,v in delta.items() if k!='percentage_used'): raise Failed('测试期间新增健康错误/异常计数: '+str(delta))
            if any(v<0 for v in delta.values()): reasons.append('计数回退，基线不可比')
            if a['passed'] is None or a['warnings'] or b['warnings'] or not a['counters']: reasons.append('健康信息缺失或存在需解释的历史告警')
        return deltas,reasons
    def own_file(self,tag,size):
        self.guard.check(size)
        if size>self.c['max_space_bytes']: raise Blocked('测试文件超过已批准空间上限')
        name='aistd-storage-'+self.id+'-'+tag
        fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.guard.fd)
        st=os.fstat(fd); self.owned.append({'name':name,'fd':fd,'inode':st.st_ino,'dev':st.st_dev,'size':size})
        self.state['owned_files']=[{k:v for k,v in x.items() if k!='fd'} for x in self.owned]; self.persist()
        return fd
    def reserve(self,write_bytes):
        if self.spent+write_bytes>self.c['max_total_write_bytes']: raise Blocked('写入量预算不足；包括准备和复制，不能降低原规模')
        self.spent+=write_bytes; self.state['reserved_write_bytes_upper_bound']=self.spent; self.persist()
    def clean(self):
        if not self.guard: return
        for f in self.owned[:]:
            try:
                self.guard.check(space=False)
                st=os.stat(f['name'],dir_fd=self.guard.fd,follow_symlinks=False)
                if (st.st_dev,st.st_ino)!=(f['dev'],f['inode']): raise ValueError('文件身份变化，保留现场')
                os.unlink(f['name'],dir_fd=self.guard.fd); self.state['cleanup'].append({'file':f['name'],'removed':True})
            except (OSError,ValueError) as e: self.state['cleanup'].append({'file':f['name'],'removed':False,'reason':str(e)})
            finally: os.close(f['fd']); self.owned.remove(f)
        self.state['owned_files']=[{k:v for k,v in x.items() if k!='fd'} for x in self.owned]
        self.persist()
    def execute(self,argv,timeout,pass_fds=(),monitor=True):
        if self.case_deadline is not None:
            timeout=min(timeout,self.case_deadline-time.monotonic())
            if timeout<=0: raise Blocked('达到整项时长上限，包含准备及所有负载阶段')
        self.seq+=1; prefix=f'command-{self.seq:04d}'
        out=self.root/(prefix+'.stdout'); err=self.root/(prefix+'.stderr'); metadata={'argv':argv,'started':now(),'timeout_seconds':timeout,'pass_fds':list(pass_fds)}
        save(self.root/(prefix+'.json'),metadata)
        p=None; watchdog=None; start=time.monotonic(); reason=None
        try:
            with out.open('wb') as stdout,err.open('wb') as stderr:
                p=subprocess.Popen(argv,stdout=stdout,stderr=stderr,pass_fds=pass_fds,start_new_session=True,cwd=self.root)
                self.active=p
                watchdog=subprocess.Popen([sys.executable,str(ROOT/'scripts/watchdog.py'),str(p.pid)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                metadata['pid']=p.pid; save(self.root/(prefix+'.json'),metadata)
                next_health=0
                while p.poll() is None:
                    if self.stop: raise Blocked('用户中断')
                    if time.monotonic()-start>timeout: raise Blocked('命令超时；负载已停止，需诊断')
                    if monitor:
                        free=self.guard.check()
                        with (self.root/'monitor.jsonl').open('a',encoding='utf8') as f: f.write(json.dumps({'time':now(),'command':prefix,'free_bytes':free,'elapsed':time.monotonic()-start})+'\n')
                        logs=self.kernel()
                        if logs['errors']: raise Failed('测试时间窗新增存储错误: '+logs['evidence'])
                        if time.monotonic()>=next_health:
                            h=self.health(); next_health=time.monotonic()+15
                            with (self.root/'health-monitor.jsonl').open('a',encoding='utf8') as f: f.write(json.dumps({'time':now(),'command':prefix,'elapsed':time.monotonic()-start,'health':h})+'\n')
                            for v in h:
                                if v['passed'] is False or v['counters'].get('critical_warning',0): raise Failed('负载期间健康故障')
                                limit=self.c.get('stop_temperature_c')
                                if limit is not None and v['temperature_c'] is None: raise Blocked('运行中必需温度测点不可读，已停止负载')
                                if limit is not None and v['temperature_c'] is not None and v['temperature_c']>=limit: raise Failed('达到批准温度停止条件')
                    time.sleep(1)
                metadata['rc']=p.returncode
        except BaseException as e:
            reason=str(e); raise
        finally:
            if p and p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait(timeout=5)
            if watchdog:
                try: watchdog.stdin.write(b'done\n'); watchdog.stdin.close(); watchdog.wait(timeout=5)
                except (OSError,subprocess.TimeoutExpired): pass
            self.active=None; metadata.update(ended=now(),elapsed_seconds=time.monotonic()-start,rc=p.returncode if p else None,stop_reason=reason)
            save(self.root/(prefix+'.json'),metadata)
        return {'rc':p.returncode,'stdout':out.read_text(encoding='utf8',errors='replace'),'stderr':err.read_text(encoding='utf8',errors='replace'),'evidence':prefix,'elapsed_seconds':metadata['elapsed_seconds']}
    def require_file(self):
        if self.c.get('mode','file')!='file' or self.c['authorization'].get('file_write') is not True: raise Blocked('缺少目标目录专用文件写入授权')
    def prepare(self,fd,size):
        self.reserve(size); self.guard.check(size)
        result=self.execute(['dd','if=/dev/urandom',f'of=/proc/self/fd/{fd}','bs=1M',f'count={size}','iflag=count_bytes','conv=fsync,notrunc','status=none'],self.c['max_case_seconds'],(fd,))
        self.check_rc(result)
        if os.fstat(fd).st_size!=size: raise Failed('准备文件长度不正确')
    def check_rc(self,r):
        if r['rc']!=0:
            if re.search(r'io_queue_init.*(?:Function not implemented|error=38)|io_setup.*Function not implemented',r['stdout']+r['stderr'],re.I):
                raise Blocked('libaio内核接口不可用（ENOSYS）；核对原生内核或完整系统虚拟机，用户态跨架构仿真可能不支持；保持原引擎/QD/direct，不静默降级: '+r['evidence'])
            if re.search(r'Input/output error|verify.*(?:failed|mismatch)|bad magic|checksum.*error',r['stdout']+r['stderr'],re.I): raise Failed('I/O或数据校验失败: '+r['evidence'])
            raise Blocked('工具未有效完成；检查参数、权限及依赖: '+r['evidence'])
    def file_test(self,basic=False):
        self.require_file(); size=1024**3 if basic else self.c.get('file_bytes',16*1024**2)
        if self.c.get('validation_only'): size=min(size,self.c.get('validation_scale',{}).get('size_bytes',size))
        if type(size) is not int or size<=0: raise Blocked('无效文件大小')
        if (size if basic else size*2)>self.c['max_space_bytes']: raise Blocked('文件与复制峰值超过空间上限')
        fd=self.own_file('file',size)
        if basic:
            self.reserve(size)
            for argv in [['dd','if=/dev/zero',f'of=/proc/self/fd/{fd}',f'bs={size}','count=1','oflag=direct','conv=fsync,notrunc'],['dd',f'if=/proc/self/fd/{fd}','of=/dev/null',f'bs={size}','count=1','iflag=direct']]: self.check_rc(self.execute(argv,self.c['max_case_seconds'],(fd,)))
            if os.fstat(fd).st_size!=size: raise Failed('基础读写长度不符')
            return {'bytes':size,'direct':True,'coverage':'1GiB写入和读取；不把零数据读取视作完整性校验'}
        self.prepare(fd,size)
        def digest(f):
            os.lseek(f,0,0); h=hashlib.sha256()
            while True:
                if self.stop or (self.case_deadline is not None and time.monotonic()>=self.case_deadline): raise Blocked('校验被中断或达到整项时长上限')
                b=os.read(f,1024**2)
                if not b: break
                h.update(b)
            return h.hexdigest()
        before=digest(fd); copy=self.own_file('copy',size); self.reserve(size)
        self.check_rc(self.execute(['dd',f'if=/proc/self/fd/{fd}',f'of=/proc/self/fd/{copy}','bs=1M','conv=fsync,notrunc','status=none'],self.c['max_case_seconds'],(fd,copy)))
        after=digest(copy)
        if before!=after or os.fstat(copy).st_size!=size: raise Failed('实际回读SHA256不一致')
        f=self.owned[-1]; new=f['name']+'-renamed'; self.guard.check(); os.rename(f['name'],new,src_dir_fd=self.guard.fd,dst_dir_fd=self.guard.fd); f['name']=new
        self.state['owned_files']=[{k:v for k,v in x.items() if k!='fd'} for x in self.owned]; self.persist()
        return {'bytes':size,'sha256_original':before,'sha256_copy':after,'cache':'buffered + fsync','coverage':'在线回读，不代表掉电持久性'}
    def fio_profile(self,case,p,index):
        self.require_file(); p=dict(p); runtime=seconds(p['runtime']); size=amount(p['size']); jobs=int(p.get('numjobs',1))
        if case['id']=='AISTD-174' and self.c.get('approve_174_libaio',True) is not True: raise Blocked('174需提前确认使用libaio兑现原QD64，其他参数保留')
        if case['id']=='AISTD-62':
            heat_health=self.health()
            if not heat_health or any(h.get('temperature_c') is None for h in heat_health): raise Blocked('62需可读取的目标盘温度；先解决测点再启动长写')
        if runtime>self.c['max_case_seconds']: raise Blocked('原用例时长超过授权上限')
        if self.c.get('validation_only'):
            runtime=min(runtime,self.c.get('validation_scale',{}).get('seconds',runtime)); size=min(size,self.c.get('validation_scale',{}).get('size_bytes',size))
        multi_files=case['id']=='AISTD-211'; count=jobs if multi_files else 1
        if size*count>self.c['max_space_bytes']: raise Blocked('原用例测试数据区域超过空间上限')
        is_verify=case['id']=='AISTD-65'
        writing=p['rw'] not in ('read','randread')
        if is_verify and not self.c.get('approve_verify_cycles'): raise Blocked('65需批准完整写/回读循环方法，避免time_based只写不验')
        if writing and not is_verify:
            approved=self.c.get('rate_limit_bytes_per_job')
            if type(approved) is not int or approved<=0: raise Blocked('需要明确正整数rate_limit_bytes_per_job；基准须匹配该条件')
            reserve=size*count+approved*(runtime+1)*jobs+amount(p['bs'])*int(p.get('iodepth',1))*jobs
        else: reserve=size*count+(size*jobs if is_verify else 0)
        if self.spent+reserve>self.c['max_total_write_bytes']: raise Blocked('准备前检查：写入量预算不足')
        self.guard.check(size*count)
        fds=[self.own_file(f'fio-{index}-{n}',size) for n in range(count)]
        for fd in fds: self.prepare(fd,size)
        p.update(runtime=str(runtime),size=str(size)); p.setdefault('numjobs','1'); p.setdefault('ioengine','libaio')
        # Known jobs only, never accept arbitrary shell commands or fio options from configuration.
        argv=['fio','--output-format=json','--group_reporting=1','--eta=never','--exitall_on_error=1','--allow_file_create=0','--unlink=0','--log_avg_msec=1000']
        conditions={'profile':p,'file_layout':'per-job files' if multi_files else 'shared file','cache':'direct','preparation':'full random-data write + fsync','preconditioning':'none beyond preparation','warmup_seconds':0,'other_workload':self.c['other_workload'],'filesystem':self.guard.initial['mount']['fstype'],'tool_version':self.state['environment']['tools']['fio'].get('version'),'topology':self.guard.initial['nodes']}
        conditions['platform']={'product_type':self.c['platform']['product_type'],'model':self.c['platform']['model'],'host':self.state['environment'].get('platform_identity'),'os':self.state['environment'].get('os')}
        if is_verify and not self.c.get('approve_verify_cycles'): raise Blocked('65需批准完整写/回读循环方法，避免time_based只写不验')
        if is_verify:
            p.pop('runtime'); p.update(do_verify='1',verify='crc32c',verify_fatal='1',verify_dump='0',end_fsync='1')
        else: argv.append('--time_based=1')
        logprefix=str(self.root/f'fio-{case["id"]}-{index}')
        argv += [f'--write_bw_log={logprefix}',f'--write_iops_log={logprefix}',f'--write_lat_log={logprefix}']
        if multi_files:
            for j,fd in enumerate(fds):
                args=dict(p,name=p['name']+str(j),numjobs='1'); argv += ['--name='+args.pop('name'),f'--filename=/proc/self/fd/{fd}']+[f'--{k}={v}' for k,v in args.items()]
        else: argv += ['--name='+p['name'],f'--filename=/proc/self/fd/{fds[0]}']+[f'--{k}={v}' for k,v in p.items() if k!='name']
        metrics=[]; start=time.monotonic(); loops=0
        while True:
            if writing:
                # An explicit time bound authorizes the write load; record actual bytes afterwards.
                # Rate ceiling also bounds aggregate host writes, including queue slack.
                remaining=self.c['max_total_write_bytes']-self.spent
                if is_verify: estimate=size*jobs; limited=argv
                else:
                    block=amount(p['bs']); slack=block*int(p.get('iodepth',1))*jobs
                    rate=(remaining-slack)//max(1,(runtime+1)*jobs)
                    if rate<block: raise Blocked('写入预算不足以完成批准时长')
                    approved=self.c.get('rate_limit_bytes_per_job')
                    if approved is None: raise Blocked('需要明确rate_limit_bytes_per_job以严格限制写入预算；性能基准须匹配该条件')
                    if type(approved) is not int or approved<=0: raise Blocked('无效速率上限')
                    if approved>rate: raise Blocked('按批准速率和时长计算的写入预算不足')
                    estimate=approved*(runtime+1)*jobs+slack
                    limited=[argv[0],f'--rate={approved}']+argv[1:]; conditions['rate_limit_bytes_per_job']=approved
                self.reserve(estimate)
            else: limited=argv+['--readonly']
            remaining_seconds=self.c['max_case_seconds']-(time.monotonic()-start)
            if remaining_seconds<=0: raise Blocked('达到单项总时长上限，未完成校验循环')
            r=self.execute(limited,min(runtime+max(60,runtime//10),remaining_seconds) if not is_verify else remaining_seconds,tuple(fds))
            self.check_rc(r)
            try: parsed=fio_parse(json.loads(r['stdout']))
            except json.JSONDecodeError: raise Blocked('fio输出不是有效JSON: '+r['evidence'])
            expected_directions={'read'} if p['rw'] in ('read','randread') else {'read','write'} if p['rw'] in ('randrw','readwrite') or is_verify else {'write'}
            if not expected_directions<=parsed.keys(): raise Failed('缺少实际预期读/写/校验阶段')
            if is_verify and (parsed['read']['io_bytes']<size or parsed['write']['io_bytes']<size): raise Failed('校验未覆盖整个测试文件')
            if not is_verify and min(x['runtime_ms'] for x in parsed.values())<runtime*1000*0.95: raise Blocked('负载未完成原定时长')
            metrics.append({'conditions':conditions,'metrics':parsed,'evidence':r['evidence']}); loops+=1
            if not is_verify or time.monotonic()-start>=runtime: break
        result={'profiles':metrics,'loops':loops,'conditions':conditions}
        if case['id']=='AISTD-62':
            try: result['thermal_analysis']=bandwidth_windows(sorted(self.root.glob(f'fio-{case["id"]}-{index}_bw*.log')),runtime)
            except ValueError as e: raise Blocked(str(e))
            monitor=self.root/'health-monitor.jsonl'
            result['temperature_series']=[x for x in (json.loads(line) for line in monitor.read_text(encoding='utf8').splitlines()) if x.get('command')==r['evidence']]
            if not result['temperature_series'] or any(h.get('temperature_c') is None for x in result['temperature_series'] for h in x['health']): raise Blocked('62负载期间温度序列缺失')
        return result

    def judge_performance(self,case,results):
        if self.c.get('performance_mode','record_only')=='record_only':
            if case['id']=='AISTD-62' and any(not r.get('thermal_analysis') or not r.get('temperature_series') for r in results): raise Blocked('62缺少完整带宽分析或温度序列')
            return
        failures=[]; missing=[]
        for n,result in enumerate(results):
            base=self.c.get('baselines',{}).get(case['id']+':'+str(n))
            if not base: missing.append('缺少匹配第'+str(n)+'组参数的性能基准'); continue
            if not any(k in base for k in ('min_bw_bytes','min_iops','max_mean_latency_ns','max_p99_latency_ns')): missing.append('无有效性能阈值'); continue
            if base['conditions']!=result['conditions']: missing.append('基准可比条件与实际条件不一致'); continue
            for profile in result['profiles']:
                for direction,m in profile['metrics'].items():
                    b=base
                    for key,metric,minimum in [('min_bw_bytes','bw_bytes',True),('min_iops','iops',True),('max_mean_latency_ns','mean_latency_ns',False),('max_p99_latency_ns','p99_latency_ns',False)]:
                        if key not in b: continue
                        if m[metric] is None: missing.append('缺指标 '+metric)
                        elif (m[metric]<b[key] if minimum else m[metric]>b[key]): failures.append(direction+' '+key+'不达标')
        if failures: raise Failed('; '.join(failures))
        if case['id']=='AISTD-62': missing.append('需后段带宽衰减阈值/温度测点与时间序列分析，平均性能不能替代热衰减')
        if missing: raise Blocked('; '.join(missing))

    def run_item(self,case,row):
        kind=case['kind']; row['started']=now(); row['coverage_limit']=case.get('limitation','')
        self.persist()
        self.case_deadline=time.monotonic()+self.c['max_case_seconds']
        if self.stop: raise Blocked('本轮已停止，未执行')
        problems=execution_issues(case,self.c,self.state['environment'],command)
        if problems: raise Blocked('; '.join(problems))
        if kind=='external': raise Blocked(case['limitation'])
        if kind=='trim' and rotational_targets(self.c,self.state['environment']):raise NotApplicable('按目标盘ROTA枚举为机械盘，SSD TRIM不适用')
        if kind=='reboot': raise Blocked('使用remote_suite.py跨重启控制器；不能在本机进程内模拟重启完成')
        if platform.system()!='Linux': raise Blocked('当前为控制端；负载执行需要明确Linux目标')
        if kind=='raw_read': return self.raw_read(row)
        if not self.guard: self.guard=Guard(self.c)
        row['target']=self.guard.initial; self.guard.check()
        if kind=='identity':
            check_expected(self.guard.initial['nodes'],self.c['expected_devices'])
            spec=self.c.get('device_spec')
            if not spec: raise Blocked('缺规格书/BOM来源及容量、接口验收值；身份符合不等于规格通过')
            if not spec.get('source'): raise Blocked('规格缺来源')
            for node in [n for n in self.guard.initial['nodes'] if n['type']=='disk']:
                if any(str(node.get(k)).strip()!=str(v).strip() for k,v in spec.get('expected',{}).items()): raise Failed('盘信息不符合批准规格')
            if not all(k in spec.get('expected',{}) for k in ['model','size','tran']): raise Blocked('规格需model,size字节,tran；标称容量另记录')
            row['measured']={'nominal_capacity_bytes':spec.get('nominal_capacity_bytes'),'os_visible':self.guard.initial,'filesystem':dict(zip(['total','used','free'],shutil.disk_usage(self.c['test_directory'])))}; return
        if kind=='logs':
            logs=self.kernel(); row['measured']=logs
            if logs['errors']: raise Failed('本次时间窗存在存储错误')
            return
        before=self.health(); row['health_before']=before
        if kind=='health':
            delta,missing=self.check_health(before,before); row['measured']=before
            if any(any(v>0 for k,v in h['counters'].items() if re.search('media|uncorrect|pending|realloc',k,re.I)) for h in before): raise Failed('存在介质错误计数，52要求无介质错误')
            if missing: raise Blocked('; '.join(missing))
            return
        _,initial_missing=self.check_health(before,before)
        if initial_missing: raise Blocked('负载前健康信息不完整: '+'; '.join(initial_missing))
        self.kernel() # no load if time-window monitoring unavailable
        if kind=='basic': row['measured']=self.file_test(True)
        elif kind=='file': row['measured']=self.file_test()
        elif kind in ('disk_inventory','trim','disk_full'):
            from disk_cases import inventory_all,trim,disk_full
            if kind=='disk_inventory':inventory_all(self,row)
            elif kind=='trim':trim(self,case,row)
            else:disk_full(self,row)
        elif kind=='fio':
            if not re.match(r'^fio-\d',self.state['environment']['tools']['fio'].get('version',{}).get('stdout','').strip()): raise Blocked('缺少真正的Flexible I/O fio工具，或路径指向同名程序')
            row['measured']=[]
            for index,p in enumerate(case['profiles']):
                row['measured'].append(self.fio_profile(case,p,index)); self.clean(); self.persist()
        after=self.health(); row['health_after']=after; delta,missing=self.check_health(before,after); row['health_delta']=delta
        logs=self.kernel(); row['kernel']=logs
        if logs['errors']: raise Failed('测试时间窗新增I/O/控制器/文件系统错误')
        if kind=='fio' and case['group']=='performance':
            row['performance_assessment']={'mode':self.c.get('performance_mode','record_only'),'note':'仅记录实测数据，未做性能达标判定' if self.c.get('performance_mode','record_only')=='record_only' else '按配置基准判定'}
            self.judge_performance(case,row['measured'])
        if missing: raise Blocked('; '.join(missing))

    def raw_read(self,row):
        if self.c.get('mode')!='raw_read' or self.c['authorization'].get('raw_read') is not True: raise Blocked('缺限定范围裸设备只读授权')
        raw=self.c.get('raw',{}); path=raw.get('path'); offset=raw.get('offset_bytes'); size=raw.get('length_bytes')
        if not path or type(offset) is not int or offset<0 or type(size) is not int or size<=0: raise Blocked('需要实际裸设备路径、非负偏移和正长度')
        import stat
        st=os.stat(path)
        if not stat.S_ISBLK(st.st_mode): raise Blocked('裸读目标必须是块设备')
        mm=f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}'; nodes=topology_for(mm); check_expected(nodes,self.c['expected_devices']); check_global_unique(self.c['expected_devices'])
        target=next(n for n in nodes if n['major_minor']==mm)
        if offset+size>int(target['size']): raise Blocked('裸读超出设备边界')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_rdev!=st.st_rdev or topology_for(mm)!=nodes: raise Blocked('裸读目标变化')
            r=self.execute(['dd',f'if=/proc/self/fd/{fd}','of=/dev/null','bs=1M','iflag=skip_bytes,count_bytes',f'skip={offset}',f'count={size}'],self.c['max_case_seconds'],(fd,),monitor=False)
            self.check_rc(r)
            if not re.search(r'\b'+str(size)+r' bytes',r['stderr']): raise Blocked('无法确认dd实际读取完整范围；需检查本地化输出')
            row['measured']={'offset_bytes':offset,'length_bytes':size,'evidence':r['evidence'],'identity':nodes,'coverage':'buffered read only; content correctness unknown'}
        finally: os.close(fd)

    def run(self):
        start=time.monotonic()
        try:
            self.state['environment']=inventory(); self.persist()
            if platform.system()=='Linux':
                import fcntl
                # Same user serializes storage jobs. Never infer stale PID ownership.
                lockdir=pathlib.Path('/var/tmp')/('aistd-storage-lock-'+str(os.getuid()))
                lockdir.mkdir(mode=0o700,exist_ok=True)
                if lockdir.is_symlink() or lockdir.stat().st_uid!=os.getuid() or lockdir.stat().st_mode&0o077: raise Blocked('锁目录权限/归属不可信')
                self.lock=os.open(lockdir/'runner.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
                try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: raise Blocked('本用户已有存储任务；检查其真实状态后再执行')
            for case,row in zip(self.items,self.state['results']):
                try:
                    scope=applicability(case,self.c); row['applicability']=scope
                    exclusion=self.c.get('exclusions',{}).get(case['id'])
                    if exclusion: row.update(status='Skipped',reason=exclusion['reason'],exclusion_source=exclusion['source'])
                    elif scope['status']!='Applicable': row.update(status=scope['status'],reason=scope['reason'])
                    else:
                        self.run_item(case,row)
                        row.update(status='Pass',reason='采集完成，实测数据已记录；未做性能达标判定' if row.get('performance_assessment',{}).get('mode')=='record_only' else '实际步骤完成并满足已核对标准')
                except NotApplicable as e:row.update(status='Skipped',reason=str(e))
                except Failed as e: row.update(status='Fail',reason=str(e)); self.stop=True
                except (Blocked,ValueError,OSError,KeyError,TypeError) as e: row.update(status='Blocked',reason=str(e))
                except KeyboardInterrupt: row.update(status='Blocked',reason='用户停止'); self.stop=True
                finally:
                    row['ended']=now(); row['attempts'].append({'started':row.get('started'),'ended':row['ended'],'status':row['status'],'reason':row['reason'],'repair':self.state.get('repair_note')})
                    self.clean(); self.persist(); generate(self.root)
            self.state['completed']=not self.stop
        except BaseException as e: self.state['interruption']=type(e).__name__+': '+str(e)
        finally:
            self.clean()
            if self.guard: self.guard.close()
            if self.lock is not None: os.close(self.lock)
            self.state.update(ended=now(),elapsed_seconds=time.monotonic()-start); self.persist(); summary=generate(self.root)
        print(json.dumps({'run_directory':str(self.root),'report':str(self.root/'report.html'),'counts':summary['counts']},ensure_ascii=False))
        return 0 if summary['counts'].get('Pass')==len(self.items) and self.state['completed'] else 2

def preflight(c,items):
    issues=[]; env=inventory(); scopes=[]
    if not env['execution_supported']: issues.append('需要Linux测试目标；当前仅控制端')
    else:
        try:
            g=Guard(c); g.close()
        except (ValueError,OSError,KeyError) as e: issues.append(str(e))
    for i in items:
        scope=applicability(i,c)
        if i['kind']=='trim' and rotational_targets(c,env):scope.update(status='Skipped',reason='实际目标ROTA为机械盘，SSD TRIM不适用')
        scopes.append(dict(id=i['id'],**scope))
        if scope['status']=='Skipped': continue
        if scope['status']=='Blocked': issues.append(i['id']+': '+scope['reason']); continue
        issues.extend(i['id']+': '+s for s in execution_issues(i,c,env,command))
        if i['kind']=='external': issues.append(i['id']+': '+i['limitation'])
        if i['kind']=='trim':
            if c['authorization'].get('trim') is not True:issues.append(i['id']+': 需确认TRIM挂载点及授权')
            if not c.get('trim',{}).get('mounts'):issues.append(i['id']+': 缺trim.mounts')
        if i['kind']=='disk_full' and c.get('disk_full',{}).get('mode')!='isolated_image':issues.append(i['id']+': 需确认隔离文件系统盘满模式')
        if i['kind']=='reboot' and c['authorization'].get('reboot') is not True:issues.append(i['id']+': 需具体整机重启授权')
        if i['kind']=='fio':
            if i['id']=='AISTD-174' and c.get('approve_174_libaio',True) is not True: issues.append('AISTD-174需提前确认libaio方法，配置approve_174_libaio=true')
            if not env['tools'].get('fio',{}).get('path'): issues.append('缺fio依赖')
            for n,p in enumerate(i['profiles']):
                if amount(p['size'])*(int(p.get('numjobs',1)) if i['id']=='AISTD-211' else 1)>c['max_space_bytes']: issues.append(i['id']+'空间预算不足')
                if seconds(p['runtime'])>c['max_case_seconds']: issues.append(i['id']+'时长超授权')
                if i['group']=='performance' and c.get('performance_mode','record_only')=='compare_baseline' and i['id']+':'+str(n) not in c.get('baselines',{}): issues.append(i['id']+'缺匹配性能基准；可测量但不能宣称达标')
    return {'environment':env,'platform':c['platform'],'applicability':scopes,'blocking_or_missing':issues,'selected':[i['id'] for i in items]}

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('action',choices=['prepare','inspect','list','preflight','run','report']); ap.add_argument('--config'); ap.add_argument('--out'); ap.add_argument('--run-dir'); ap.add_argument('--only',nargs='+'); ap.add_argument('--group'); ap.add_argument('--rerun-from'); ap.add_argument('--repair-note'); args=ap.parse_args()
    if args.action=='prepare':
        from preparation import packet,write_packet
        c=json.loads(pathlib.Path(args.config).read_text(encoding='utf8')) if args.config else {}
        originals=json.loads((ROOT/'references/jira-original.json').read_text(encoding='utf8'))['cases']
        if not args.out: ap.error('prepare需--out目录')
        write_packet(args.out,packet(c,select(c,args),originals)); print(str(pathlib.Path(args.out)/'preparation.html')); return 0
    if args.action=='report': generate(args.run_dir); print(str(pathlib.Path(args.run_dir)/'report.html')); return 0
    if args.action in ('list','inspect'):
        data=CATALOG if args.action=='list' else inventory()
        if args.out: save(args.out,data)
        else: print(json.dumps(data,ensure_ascii=False,indent=2))
        return 0
    c=validate(json.loads(pathlib.Path(args.config).read_text(encoding='utf-8-sig'))); items=select(c,args)
    if args.action=='preflight':
        data=preflight(c,items)
        if args.out: save(args.out,data)
        else: print(json.dumps(data,ensure_ascii=False,indent=2))
        return 2 if data['blocking_or_missing'] else 0
    r=Runner(c,items,args.rerun_from,args.repair_note)
    def stop(*_): r.stop=True
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    return r.run()
if __name__=='__main__':
    try: sys.exit(main())
    except (ValueError,OSError,KeyError) as e: print('Blocked: '+str(e),file=sys.stderr); sys.exit(2)
