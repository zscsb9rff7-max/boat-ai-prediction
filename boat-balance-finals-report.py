import json,gzip,base64,glob,collections,datetime
from pathlib import Path
root=Path('archive/history')
venues='桐生 戸田 江戸川 平和島 多摩川 浜名湖 蒲郡 常滑 津 三国 びわこ 住之江 尼崎 鳴門 丸亀 児島 宮島 徳山 下関 若松 芦屋 福岡 唐津 大村'.split()
data={v:{} for v in venues}; unknown=collections.Counter(); labels=collections.Counter(); excluded=collections.Counter()
def read(path):return json.loads(gzip.decompress(path.read_bytes()))
p=json.loads((root/'progress.json').read_text())
available=sorted(d for d,x in p['days'].items() if p['summary']['earliestAvailable']<=d<=p['summary']['latestAvailable'] and x['status']=='available')
for f in sorted((root/'records').glob('*.json.gz')):
    kinds=read(root/'race-kinds'/f.name); records=read(f)
    for d,day in records.items():
        if d not in available:continue
        for venue,rs in kinds.get(d,{}).items():
            for race,name in rs.items():
                if isinstance(name,str) and name.startswith('優勝戦'):labels[venue]+=1
        for r in day['rows']:
            if r['type']!='3連単':continue
            v=r['venue']; key=(d,str(r['race']))
            name=kinds.get(d,{}).get(v,{}).get(str(r['race']))
            if not name:
                unknown[v]+=1;continue
            if not name.startswith('優勝戦'):continue
            assert v in data, v
            winners=data[v].setdefault(key,{})
            assert r['payout']>0
            result='-'.join(str(int(s)) for s in r['result'].split('-'))
            if result in winners:assert winners[result]==r['payout']
            winners[result]=r['payout']
out={'from':available[0],'to':available[-1],'snapshot':p['updatedAt'],'availableDays':len(available),'firstSaved':available[0],'lastSaved':available[-1],'missingPastDays':p['summary']['targetErrorDays'],'venues':[]}

for v,races in data.items():
    n=len(races); counts=collections.Counter(); payouts=collections.Counter()
    for winners in races.values():
        for result,paid in winners.items():counts[result]+=1;payouts[result]+=paid
    rows=[dict(result=k,count=c,probability=c/n*100,recoveryRate=payouts[k]/n,payout=payouts[k]) for k,c in counts.items()]
    freq=sorted(rows,key=lambda r:(-r['count'],r['result']))[:10]
    roi=sorted(rows,key=lambda r:(-r['recoveryRate'],-r['count'],r['result']))[:10]
    dates=sorted(k[0] for k in races)
    out['venues'].append(dict(name=v,totalRaces=n,first=dates[0] if dates else None,last=dates[-1] if dates else None,officialFinalLabels=labels[v],unusableFinals=labels[v]-n,unknownRaceNames=unknown[v],frequency=freq,recovery=roi))
assert len(out['venues'])==24
(root/'reports').mkdir(exist_ok=True)
(root/'reports'/'finals-24-all-history.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='venues'},ensure_ascii=False))
print([(v['name'],v['totalRaces'],v['unusableFinals']) for v in out['venues']])
