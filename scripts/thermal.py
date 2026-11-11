"""Descriptive bandwidth windows; no invented thermal acceptance threshold."""
import csv, math

def bandwidth_windows(paths, runtime_seconds):
    if len(paths)!=1: raise ValueError('62需要唯一单job带宽日志，不能把多job样本直接平均')
    duration=runtime_seconds*1000; samples=[]; previous=0
    with paths[0].open(encoding='utf8') as stream:
        for row in csv.reader(stream):
            if not row: continue
            if len(row)<4: raise ValueError('fio带宽日志字段不足')
            end=float(row[0]); bw=float(row[1])*1024
            if not math.isfinite(end) or not math.isfinite(bw) or bw<0 or end<=previous: raise ValueError('无效带宽样本或时间倒退')
            # Windowed log direction is not interpreted as individual I/O direction.
            samples.append((previous,end,bw)); previous=end
    if not samples or previous<duration*.95 or previous>duration*1.1: raise ValueError('带宽时间序列未覆盖负载时长')
    def window(left,right):
        area=covered=0
        for start,end,bw in samples:
            overlap=max(0,min(end,right)-max(start,left)); area+=bw*overlap; covered+=overlap
        if covered<(right-left)*.95: raise ValueError('分析窗口缺少带宽样本')
        return area/covered
    early=window(0,duration*.2); late=window(duration*.8,duration)
    return {'method':'按时间加权比较前20%与后20%，仅描述变化，不归因于温度',
            'early_bw_bytes':early,'late_bw_bytes':late,
            'drop_fraction':(early-late)/early if early>0 else None,
            'samples':len(samples),'evidence':[p.name for p in paths],
            'performance_verdict':'未做性能达标判定；限速、缓存等因素均可能影响变化'}
