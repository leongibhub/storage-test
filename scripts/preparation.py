"""Generate a reviewable preparation packet, without connecting or executing."""
import html, json, pathlib

GROUPS=[
 ('machine',None,'待测机器型号/连接名、AIPC或AI Workstation或AI Server；OS及架构由只读发现核对'),
 ('target',None,'目标盘SN/WWN、测试目录、业务占用、空间和写入预算；不得把控制端当待测机'),
 ('temperature',{62},'只读发现盘温测点；无法读取时提前说明工具/权限缺口，不要求环境箱'),
 ('duration',{62,63,64,65},'确认原30分钟/4小时负载窗口、空间和写入预算；不默认缩短正式测试'),
 ('verify',{65},'提前确认完整写入/回读循环方法；若会话已批准则沿用'),
 ('reboot',{56},'确认正常整机重启窗口；外部控制端保存基线并自动重连，不需要现场人员'),
 ('disk_full',{68},'确认目标盘上的隔离文件系统ENOSPC模式和镜像空间；不填满系统/业务分区，报告区别于真实业务分区盘满'),
 ('trim',{70,212},'只读核对SSD/discard、文件系统和fstrim.timer，再确认明确挂载点的TRIM授权；不要自动启用或修改系统定时器'),
 ('bom',{209},'核对完整存储BOM、各盘SN/型号/容量/接口、控制器及批准驱动清单；现有信息沿用，缺独立规格不编造'),
]
def packet(c,items,originals):
    ids={int(x['id'].split('-')[1]) for x in items if x['id'].startswith('AISTD-')}
    questions=[]; answers=dict(c.get('preparation_answers',{}))
    platform=c.get('platform',{});auth=c.get('authorization',{})
    if all(platform.get(k) for k in ('product_type','model','classification_source')):answers.setdefault('machine',platform)
    if all(c.get(k) for k in ('expected_devices','test_directory','other_workload','max_space_bytes','max_total_write_bytes','min_free_bytes')):answers.setdefault('target',{k:c[k] for k in ('expected_devices','test_directory','other_workload','max_space_bytes','max_total_write_bytes','min_free_bytes')})
    if c.get('max_case_seconds'):answers.setdefault('duration',{'max_case_seconds':c['max_case_seconds'],'note':'沿用已确认预算；执行器仍核对完整时长'})
    if c.get('approve_verify_cycles') is True:answers.setdefault('verify','已批准完整写入/回读循环')
    if auth.get('reboot') is True:answers.setdefault('reboot','会话/配置已明确授权正常整机重启')
    if auth.get('trim') is True and c.get('trim',{}).get('mounts'):answers.setdefault('trim',c['trim'])
    if c.get('disk_full',{}).get('mode')=='isolated_image':answers.setdefault('disk_full',c['disk_full'])
    if c.get('storage_bom') and c.get('expected_controller_drivers'):answers.setdefault('bom',{'storage_bom':c['storage_bom'],'expected_controller_drivers':c['expected_controller_drivers']})
    for key,scope,prompt in GROUPS:
        if scope is None or ids&scope:
            questions.append({'key':key,'cases':[x['id'] for x in items if scope is None or x['id'] in {'AISTD-'+str(n) for n in scope}], 'question':prompt,'answer':answers.get(key), 'needs_answer':not bool(answers.get(key))})
    source={x['key']:x for x in originals}
    cases=[]
    for item in items:
        raw=source.get(item['id'],{})
        cases.append({'id':item['id'],'title':item['title'],'execution':'不在当前自动执行范围' if item['kind']=='external' else '已有执行器，核对前置后执行',
                      'limitation':item.get('limitation',''),'steps':raw.get('steps',[]),'criteria':item['criteria']})
    return {'execution':'NOT_EXECUTED','questions':questions,'cases':cases,
            'note':'准备回答不等于已验证能力或授权所有操作。本Skill不安排人工操作；范围外用例保留未执行映射。机型不适用项在确认后剔除，未知不得当成不适用。'}

def write_packet(root,data):
    root=pathlib.Path(root); root.mkdir(parents=True,exist_ok=True)
    (root/'preparation.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    e=lambda x:html.escape(str(x))
    out=['<!doctype html><meta charset="utf-8"><title>存储测试准备</title><h1>测试前集中确认与执行步骤</h1><p>'+e(data['note'])+'</p>']
    for q in data['questions']:out.append('<p><b>'+e(q['key'])+'</b> '+e(q['question'])+'<br>涉及：'+e(', '.join(q['cases']))+'<br>已有回答：'+e(q['answer'] or '待确认')+'</p>')
    for case in data['cases']:
        out.append('<h2>'+e(case['id']+' '+case['title'])+'</h2><p>'+e(case['execution'])+'</p><table border="1"><tr><th>原步骤（执行前核对现场参数）</th><th>原预期</th></tr>')
        for s in case['steps']:out.append('<tr><td><pre>'+e(s.get('step',''))+'</pre></td><td><pre>'+e(s.get('expectedResult',''))+'</pre></td></tr>')
        out.append('</table>')
    (root/'preparation.html').write_text('\n'.join(out),encoding='utf8')
