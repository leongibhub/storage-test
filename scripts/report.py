"""Regenerate an escaped, standalone report from the durable state."""
import collections, hashlib, html, json, pathlib

def save(path,data):
    path=pathlib.Path(path); tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2); f.flush()
        import os
        os.fsync(f.fileno())
    tmp.replace(path)

def generate(directory):
    root=pathlib.Path(directory); state=json.loads((root/'state.json').read_text(encoding='utf8'))
    rows=state['results']; counts=collections.Counter(x['status'] for x in rows)
    for status in ('Pass','Fail','Blocked','Skipped'): counts.setdefault(status,0)
    conclusion='全部通过' if rows and counts['Pass']==len(rows) and state.get('completed') and not state.get('retained_resources') and not any(not x.get('removed') for x in state.get('cleanup',[])) else '未全部通过；查看失败、阻塞、未覆盖范围及清理记录'
    if any(r.get('performance_assessment',{}).get('mode')=='record_only' for r in rows): conclusion+='；性能项仅记录实测数据，Pass表示采集完成，不代表性能达标'
    summary={'run_id':state['run_id'],'validation_only':state['config'].get('validation_only',False),'counts':dict(counts),'planned':len(rows),'completed_tests':counts['Pass']+counts['Fail'],'conclusion':conclusion,'results':rows}
    summary['platform']=state['config'].get('platform')
    summary['architecture']=state.get('environment',{}).get('platform_identity',{}).get('arch')
    save(root/'summary.json',summary)
    from jira_coverage import write
    coverage=write(root,state)
    esc=lambda x:html.escape(str(x))
    out=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>存储测试报告</title><style>body{font:15px system-ui;margin:36px;color:#182536}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd6df;padding:9px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f8;padding:14px}h1,h2{color:#16456d}</style>', '<h1>存储自动化测试报告</h1>']
    if summary['validation_only']: out.append('<h2>开发验证数据，不是产品验收结果</h2>')
    out.append('<p>产品平台：'+esc(state['config'].get('platform',{}).get('product_type','未记录'))+' / '+esc(state['config'].get('platform',{}).get('model','未记录'))+'；CPU架构：'+esc(state.get('environment',{}).get('platform_identity',{}).get('arch','未记录'))+'</p>')
    out += ['<p>'+esc(conclusion)+'</p>','<h2>测试概况与结果汇总</h2><pre>'+esc(json.dumps({k:v for k,v in state.items() if k not in {'results','environment','config'}},ensure_ascii=False,indent=2))+'</pre>','<pre>'+esc(dict(counts))+'</pre>','<h2>设备、环境及执行参数</h2><pre>'+esc(json.dumps({'environment':state.get('environment'),'config':state['config']},ensure_ascii=False,indent=2))+'</pre>','<h2>逐项结果、健康变化与阻塞处理</h2><table><tr><th>测试项</th><th>状态</th><th>标准、实测、证据与处理</th></tr>']
    for r in rows: out.append('<tr><td>'+esc(r['id']+' '+r['title'])+'</td><td>'+esc(r['status'])+'</td><td><pre>'+esc(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></td></tr>')
    out.append('</table><h2>Jira用例覆盖映射</h2><p>原步骤ID、预期、实际结果和证据见jira-coverage.json。此表不写回Jira；开发验证不计产品覆盖。</p><table><tr><th>Jira用例</th><th>覆盖程度</th><th>标注建议与差异</th></tr>')
    for r in coverage['cases']: out.append('<tr><td>'+esc(r['jira_key']+' '+r['title'])+'</td><td>'+esc(r['coverage'])+'</td><td>'+esc(r['recommendation']+'；'+r['reason'])+'</td></tr>')
    out += ['</table><h2>范围与限制</h2><p>仅覆盖记录的目标、区域、OS和实际步骤。文件测试不代表整盘、重启、断电、RAID或GUI验收。缺失健康值和日志权限不等于零错误。历史错误与本次增量分别保留。重试历史不计作新测试项。</p><h2>后续处理</h2><p>按各项reason和repair记录补齐条件；修复后以rerun-from新建运行目录，保留本次失败证据。自动计划内未完成项保留实际阻塞原因；人工项和机型不适用项单列。原始命令与输出见本目录文件。</p></html>']
    (root/'report.html').write_text('\n'.join(out),encoding='utf8')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file() and p.name!='sha256.json'}
    save(root/'sha256.json',hashes)
    return summary
