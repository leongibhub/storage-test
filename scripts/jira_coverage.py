"""Evidence mapping only. Never writes Jira or upgrades development results."""
import json, pathlib

def mapping(state,originals,catalog):
    rows={r['id']:r for r in state['results']}; active={c['id']:c for c in catalog}
    output=[]; development=state['config'].get('validation_only',False)
    peripheral=json.loads((pathlib.Path(__file__).resolve().parents[1]/'references/manual-cases.json').read_text(encoding='utf8'))
    disk_ids={x['id'] for x in json.loads((pathlib.Path(__file__).resolve().parents[1]/'references/catalog.json').read_text(encoding='utf8'))+peripheral}
    manual={x['id'] for x in peripheral}
    for case in originals:
        key=case['key']
        if key not in disk_ids: continue
        row=rows.get(key); status=row.get('status') if row else None
        coverage='未执行'; reason='不在本轮执行范围'; recommendation='不写回'
        if key in manual and row is None:
            coverage='人工执行，未执行';reason=next(x['scope_reason'] for x in peripheral if x['id']==key)
            if state['config'].get('platform'):
                from platform_support import applicability
                scope=applicability(next(x for x in peripheral if x['id']==key),state['config'])
                if scope['status']=='Skipped':coverage='不适用（机型/硬件）';reason=scope['reason']
        if row:
            reason=row.get('reason','')
            if development: coverage='开发验证，不计产品覆盖'
            elif status=='Skipped': coverage='不适用或已明确排除'
            elif status=='Blocked': coverage='未完成'
            elif status=='Fail': coverage='已执行发现异常'; recommendation='Fail（核对实际步骤和证据后）'
            elif status=='Pass':
                coverage='完整覆盖' if key in ('AISTD-51','AISTD-52','AISTD-55','AISTD-56','AISTD-69','AISTD-70','AISTD-209') else '按本轮调整范围完成，原用例部分覆盖'
                if row.get('coverage_limit'):coverage='按本轮调整范围完成，原用例部分覆盖';reason+='；'+row['coverage_limit']
                recommendation='Pass候选，写回前核对当前Jira修订' if coverage=='完整覆盖' else '不得直接按原定义标Pass；确认方法/范围差异'
                if row.get('performance_assessment',{}).get('mode')=='record_only': reason+='；原用例性能规格/阈值要求未评估'
                if key in ('AISTD-63','AISTD-64'): reason+='；写入速率受预算上限限制，需确认与原长稳负载等效'
                if key=='AISTD-65': reason+='；已改为完整写/回读循环，原time_based方法不同'
                if key=='AISTD-174': reason+='；经用户确认补充libaio引擎，QD64保留'
        output.append({'jira_key':key,'title':case['title'],'source_updated':case.get('updated'),'coverage':coverage,'run_status':status,
                       'recommendation':recommendation,'reason':reason,'automatic_support':key in active,
                       'original_steps':case['steps'],'run_evidence':row})
    return {'run_id':state['run_id'],'validation_only':development,'jira_write_performed':False,'cases':output}

def write(root,state):
    base=pathlib.Path(__file__).resolve().parents[1]/'references'
    original=json.loads((base/'jira-original.json').read_text(encoding='utf8'))['cases']
    catalog=json.loads((base/'catalog.json').read_text(encoding='utf8'))
    result=mapping(state,original,catalog)
    (root/'jira-coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result
