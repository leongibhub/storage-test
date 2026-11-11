"""Additional disk-only cases. No formatting of existing block devices."""
import errno, hashlib, json, os, pathlib, re, time
from probe import check_expected, mount_for, snapshot

def checked(r,argv,timeout=60):
    result,name=r.logcmd(argv,timeout);result['evidence']=name;r.check_rc(result)
    return result

def host_write_counters(r,health):
    output=[]
    for record in health:
        command=json.loads((r.root/record['evidence']).read_text(encoding='utf-8'))
        data=json.loads(command['stdout'])
        nv=data.get('nvme_smart_health_information_log',{})
        output.append({'serial':record['serial'],'evidence':record['evidence'],'nvme_data_units_written':nv.get('data_units_written'),'nand_written_bytes':None})
    return output

def inventory_all(r,row):
    env=r.state['environment'];disks=[dict(x,major_minor=x.get('major_minor',x.get('maj:min'))) for x in env['devices'] if x['type']=='disk']
    bom=r.c.get('storage_bom',r.c['expected_devices'])
    check_expected(disks,bom)
    controllers=checked(r,['lspci','-nnk'])
    blocks=[x for x in re.split(r'\n(?=\S)',controllers['stdout']) if re.search(r'SATA|RAID|Non-Volatile memory|SCSI storage',x,re.I)]
    if not blocks or any('Kernel driver in use:' not in x for x in blocks):raise r.Failed('存储控制器未识别或未绑定驱动')
    wanted=r.c.get('expected_controller_drivers')
    drivers=[re.search(r'Kernel driver in use:\s*(\S+)',x)[1] for x in blocks]
    if wanted and sorted(drivers)!=sorted(wanted):raise r.Failed('存储控制器驱动与批准清单不一致')
    scan=checked(r,['smartctl','--scan-open','-j']);scan_data=json.loads(scan['stdout'])
    records=[]
    for disk in disks:
        result,name=r.logcmd(['smartctl','-a','-j',disk['name']]);data=json.loads(result['stdout'])
        if data.get('serial_number','').strip()!=disk.get('serial','').strip():raise r.Blocked('SMART序列号与BOM不匹配或透传不完整')
        health=r.parse_health(data,result['rc'])
        if health['passed'] is False or health['counters'].get('critical_warning',0):raise r.Failed('SMART健康失败')
        if health['passed'] is None or health['warnings'] or not health['counters']:raise r.Blocked('健康字段不足，需要对应控制器/设备管理工具')
        if any(v>0 for k,v in health['counters'].items() if re.search('media|uncorrect|pending',k,re.I)):raise r.Failed('发现未批准的介质/不可纠正错误')
        entry={'device':disk,'health':health,'smart_evidence':name}
        if disk.get('tran')=='nvme':
            ident=checked(r,['nvme','id-ctrl',disk['name'],'-o','json']);identity=json.loads(ident['stdout'])
            if identity.get('sn','').strip()!=disk['serial'].strip():raise r.Blocked('NVMe控制器SN不匹配')
            entry['nvme_smart']=checked(r,['nvme','smart-log',disk['name'],'-o','json'])
        records.append(entry)
    if any(x.get('tran')=='nvme' for x in disks):checked(r,['nvme','list','-o','json'])
    row['measured']={'bom':bom,'records':records,'scan':scan_data,'controllers':blocks,'drivers':drivers,'approved_drivers':wanted}
    if not wanted:row['coverage_limit']='驱动已识别并绑定；未提供独立批准驱动清单，不宣称完成该规格比对'

def trim_mount(r,target):
    if r.c['authorization'].get('trim') is not True:raise r.Blocked('需要明确TRIM授权；prepare会列出挂载点')
    allowed=r.c.get('trim',{}).get('mounts',[])
    if target not in allowed:raise r.Blocked('TRIM挂载点未获授权: '+target)
    before=snapshot(target)
    if before['mount']['target']!=target or before['mount']['fstype'] not in ('ext4','xfs','btrfs'):raise r.Blocked('需要明确的ext4/xfs/btrfs挂载点')
    check_expected(before['nodes'],r.c['expected_devices'])
    for disk in [x for x in before['nodes'] if x['type']=='disk']:
        rotational=checked(r,['lsblk','-dn','-o','ROTA',disk['name']])['stdout'].strip()
        if rotational=='1':raise r.NotApplicable('当前是机械硬盘，SSD TRIM用例不适用')
        maximum=checked(r,['lsblk','-bdn','-o','DISC-MAX',disk['name']])['stdout'].strip()
        if not maximum.isdigit() or int(maximum)==0:raise r.Blocked('存储链路未公布discard能力；核对控制器/驱动')
    timeout=min(r.c.get('trim',{}).get('timeout_seconds',600),max(1,r.case_deadline-time.monotonic()))
    result=checked(r,['fstrim','-v',target],timeout)
    if snapshot(target)!=before:raise r.Failed('TRIM过程中挂载或目标身份变化')
    match=re.search(r'\((\d+) bytes\)',result['stdout']) or re.search(r':\s*(\d+) bytes',result['stdout'])
    if not match:raise r.Blocked('无法解析实际TRIM返回字节数，保留原输出')
    return {'mount':target,'bytes_reported':int(match[1]),'evidence':result['evidence'],'note':'返回的是文件系统提交的范围，不代表NAND回收量或写放大'}

def trim(r,case,row):
    if case['id']=='AISTD-70':
        row['measured']=trim_mount(r,r.guard.initial['mount']['target']);return
    # Validate target approval before creating data; do not start/enable timers implicitly.
    if r.c['authorization'].get('trim') is not True or '/' not in r.c.get('trim',{}).get('mounts',[]):raise r.Blocked('212需要根挂载点TRIM授权')
    timer=checked(r,['systemctl','show','fstrim.timer','-p','LoadState','-p','ActiveState','-p','UnitFileState','-p','NextElapseUSecRealtime'])
    fields=dict(x.split('=',1) for x in timer['stdout'].splitlines() if '=' in x)
    row['timer']=fields
    if fields.get('LoadState')!='loaded' or fields.get('ActiveState')!='active' or not fields.get('NextElapseUSecRealtime'):raise r.Failed('fstrim.timer未处于loaded/active或无下次执行时间')
    root=mount_for('/')
    if 'discard' not in root['options'].split(',') and fields.get('UnitFileState')!='enabled':raise r.Failed('未检测到已启用的TRIM机制')
    row['measured']={'root_trim':trim_mount(r,'/')}
    row['measured']['before_create']=r.health()
    row['measured']['host_write_counters_before']=host_write_counters(r,row['measured']['before_create'])
    r.require_file();size=1024**3;fd=r.own_file('trim-data',size);r.prepare(fd,size)
    before=r.health();r.clean()
    row['measured']['after_create']=before
    row['measured']['host_write_counters_after_create']=host_write_counters(r,before)
    row['measured']['after_delete_trim']=trim_mount(r,r.guard.initial['mount']['target'])
    row['measured']['after_trim']=r.health()
    row['measured']['host_write_counters_after_trim']=host_write_counters(r,row['measured']['after_trim'])
    # Standard NVMe SMART exposes host writes, not NAND writes. Never manufacture WAF.
    row['measured']['write_amplification']={'assessed':False,'reason':'标准SMART无NAND写入量，不能从host写入计数推出写放大下降'}
    row['performance_assessment']={'mode':'record_only','note':'TRIM功能与定时器实测；无写放大达标结论'}
    row['coverage_limit']='原212步骤4的写放大下降不作性能验收；仅记录可观察的实际数据'

def disk_full(r,row):
    r.require_file()
    if r.c.get('disk_full',{}).get('mode')!='isolated_image':raise r.Blocked('盘满需提前选定模式；当前入口为目标盘上的隔离文件系统，不填满系统/业务分区')
    size=r.c.get('disk_full',{}).get('image_bytes',1024**3)
    if type(size) is not int or size<256*1024**2 or size>r.c['max_space_bytes']:raise r.Blocked('隔离文件系统大小不符合预算')
    r.reserve(size*2);fd=r.own_file('enospc.img',size);r.prepare(fd,size)
    path=pathlib.Path(r.c['test_directory'])/r.owned[-1]['name']
    mount=pathlib.Path(r.c['test_directory'])/('enospc-mount-'+r.id);mount.mkdir(mode=0o700)
    loop=None;mounted=False
    def retain(reason):
        r.state.setdefault('retained_resources',[]).append({'mount':str(mount),'loop':loop,'image':str(path),'reason':reason})
        for item in r.owned[:]:
            if item['fd']==fd:os.close(fd);r.owned.remove(item)
        r.persist()
    try:
        # Only format the regular file created O_EXCL above. Never accept user block paths.
        checked(r,['mkfs.ext4','-F','-m','0',str(path)],120)
        loop=checked(r,['losetup','--find','--show',str(path)])['stdout'].strip()
        if not re.fullmatch(r'/dev/loop\d+',loop):raise r.Blocked('无法确认本任务loop设备')
        backing=json.loads(checked(r,['losetup','-J','-O','NAME,BACK-FILE',loop])['stdout'])['loopdevices'][0]['back-file']
        if pathlib.Path(backing).resolve()!=path.resolve():raise r.Blocked('loop backing file不匹配')
        checked(r,['mount','-o','nodev,nosuid,noexec',loop,str(mount)]);mounted=True
        if mount_for(mount)['source']!=loop:raise r.Blocked('隔离挂载来源不匹配')
        file=mount/'fill.bin';written=0;chunk=b'\xa5'*(1024**2);enospc=False
        with file.open('xb',buffering=0) as f:
            while written<=size:
                if r.stop or time.monotonic()>r.case_deadline:raise r.Blocked('盘满测试超时或用户停止')
                try:written+=f.write(chunk)
                except OSError as ex:
                    if ex.errno!=errno.ENOSPC:raise
                    enospc=True;break
        if not enospc:raise r.Failed('未观察到ENOSPC')
        small=mount/'small.bin';small_errno=None
        try:
            with small.open('xb',buffering=0) as f:f.write(chunk)
        except OSError as ex:small_errno=ex.errno
        if small_errno!=errno.ENOSPC:raise r.Failed('盘满后小文件未明确报告ENOSPC')
        file.unlink()
        if small.exists():small.unlink()
        recovered=mount/'recovered.bin';payload=os.urandom(1024**2)
        with recovered.open('xb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        if recovered.read_bytes()!=payload:raise r.Failed('释放空间后读写不一致')
        recovered.unlink();r.guard.check()
        row['measured']={'mode':'isolated_image','image_bytes':size,'written_before_enospc':written,'small_write_errno':small_errno,'recovered_sha256':hashlib.sha256(payload).hexdigest(),'host_guard_unchanged':True}
        row['coverage_limit']='目标物理盘上隔离ext4文件系统的ENOSPC及恢复；未填满真实系统/业务分区，未宣称覆盖全部业务反应'
    finally:
        if mounted:
            result,name=r.logcmd(['umount',str(mount)],60)
            if result['rc']!=0:
                # Preserve the owned image if still mounted; never unlink active backing storage.
                retain('unmount failed');raise r.Blocked('卸载失败，保留loop与镜像，不强制卸载')
        if loop:
            result,name=r.logcmd(['losetup','-d',loop],60)
            if result['rc']!=0:
                retain('loop detach failed');raise r.Blocked('loop释放失败，保留镜像，需核对资源')
        if mount.exists():mount.rmdir()
