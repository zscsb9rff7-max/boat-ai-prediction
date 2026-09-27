from flask import Flask, jsonify, request, send_from_directory
from itertools import permutations
from datetime import datetime, timedelta
from pathlib import Path
import json
import re, requests, math, time
from bs4 import BeautifulSoup
from odds_parser import parse_odds
from model import load as load_model, save as save_model, learn_from_record

app=Flask(__name__,static_folder='static')
BASE='https://www.boatrace.jp/owpc/pc/race/'
HEAD={'User-Agent':'Mozilla/5.0 (compatible; BOAT-AI/4.0)'}
LEDGER=Path('performance_ledger.json')

STADIUMS={'01':'桐生','02':'戸田','03':'江戸川','04':'平和島','05':'多摩川','06':'浜名湖','07':'蒲郡','08':'常滑','09':'津','10':'三国','11':'びわこ','12':'住之江','13':'尼崎','14':'鳴門','15':'丸亀','16':'児島','17':'宮島','18':'徳山','19':'下関','20':'若松','21':'芦屋','22':'福岡','23':'唐津','24':'大村'}



def load_ledger():
    if not LEDGER.exists(): return []
    try: return json.loads(LEDGER.read_text(encoding='utf-8'))
    except Exception: return []

def save_ledger(rows):
    LEDGER.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

def ledger_stats(rows):
    bets=sum(float(r.get('investment',0) or 0) for r in rows)
    payouts=sum(float(r.get('payout',0) or 0) for r in rows)
    profit=payouts-bets
    wins=sum(1 for r in rows if r.get('hit'))
    settled=sum(1 for r in rows if r.get('settled',True))
    current=0; max_loss=0; streaks=[]
    for r in rows:
        if not r.get('settled',True): continue
        if r.get('hit'): current=0
        else: current+=1; max_loss=max(max_loss,current)
    roi=(payouts/bets*100) if bets else None
    hit_rate=(wins/settled*100) if settled else None
    by_month={}
    for r in rows:
        key=str(r.get('date',''))[:6] or 'unknown'; z=by_month.setdefault(key,{'investment':0,'payout':0,'profit':0,'races':0,'wins':0})
        inv=float(r.get('investment',0) or 0); pay=float(r.get('payout',0) or 0)
        z['investment']+=inv; z['payout']+=pay; z['profit']+=pay-inv; z['races']+=1; z['wins']+=1 if r.get('hit') else 0
    return {'races':len(rows),'settled':settled,'wins':wins,'hit_rate':hit_rate,'investment':bets,'payout':payouts,'profit':profit,'roi':roi,'current_losing_streak':current,'max_losing_streak':max_loss,'by_month':by_month}

def get(url):
    r=requests.get(url,headers=HEAD,timeout=20); r.raise_for_status(); r.encoding=r.apparent_encoding or 'utf-8'; return r.text

def num(s):
    m=re.search(r'-?\d+(?:\.\d+)?',str(s).replace(',',''))
    return float(m.group()) if m else None

def boats_from(html):
    soup=BeautifulSoup(html,'html.parser'); out={}
    for tr in soup.find_all('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['th','td'])]
        if cells:
            m=re.match(r'^([1-6])(?:\s|$)',cells[0])
            if m:
                b=int(m.group(1)); out[b]=max(out.get(b,[]),cells,key=len)
    return out

def value(cells,labels):
    for i,c in enumerate(cells):
        if any(x in c for x in labels):
            for x in cells[i+1:i+6]:
                v=num(x)
                if v is not None:return v
    return None

def parse_before(html):
    soup=BeautifulSoup(html,'html.parser'); text=soup.get_text(' ',strip=True)
    out={'wind':None,'wave':None,'air':None,'water':None,'exhibition':{},'exhibition_st':{}}
    patterns={'wind':r'風速\s*([0-9.]+)\s*m','wave':r'波高\s*([0-9.]+)\s*cm','air':r'気温\s*([0-9.]+)℃','water':r'水温\s*([0-9.]+)℃'}
    for k,p in patterns.items():
        m=re.search(p,text)
        if m: out[k]=float(m.group(1))
    for tr in soup.find_all('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['th','td'])]
        if not cells: continue
        m=re.match(r'^([1-6])(?:\s|$)',cells[0])
        if not m: continue
        b=int(m.group(1))
        ex=next((num(x) for x in cells if re.fullmatch(r'6\.\d{2}',x)),None)
        if ex is not None: out['exhibition'][b]=ex
        sts=[float(x.lstrip('.'))/100 for x in cells if re.fullmatch(r'\.?\d{2}',x) and not x.startswith('F')]
        if sts: out['exhibition_st'][b]=sts[-1]
    return out

def parse_resultlist(html):
    soup=BeautifulSoup(html,'html.parser'); rows=[]
    for tr in soup.find_all('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['th','td'])]
        if not cells: continue
        m=re.match(r'^(\d{1,2})R$',cells[0])
        if not m: continue
        txt=' '.join(cells)
        tri=re.search(r'([1-6])\s*[-－]\s*([1-6])\s*[-－]\s*([1-6])',txt)
        if tri and len(set(tri.groups()))==3:
            payout=re.search(r'¥\s*([0-9,]+)',txt)
            rows.append({'race':int(m.group(1)),'combo':''.join(tri.groups()),'payout':int(payout.group(1).replace(',','')) if payout else None})
    return rows

def historical_stats(jcd,days=30):
    days=max(1,min(int(days),90)); end=datetime.now().date(); start=end-timedelta(days=days-1)
    first=[0]*7; combo={}; races=0; payouts=[]; dates=0
    d=start
    while d<=end:
        url=f'{BASE}resultlist?hd={d.strftime("%Y%m%d")}&jcd={jcd}'
        try:
            rs=parse_resultlist(get(url))
            if rs:
                dates+=1
                for r in rs:
                    races+=1; a,b,c=map(int,r['combo']); first[a]+=1; combo[r['combo']]=combo.get(r['combo'],0)+1
                    if r['payout'] is not None: payouts.append(r['payout'])
        except Exception:
            pass
        d+=timedelta(days=1)
    rates={str(i):round(first[i]/races*100,2) if races else 0 for i in range(1,7)}
    top_combos=sorted(combo.items(),key=lambda x:x[1],reverse=True)[:10]
    return {'days':days,'dates':dates,'races':races,'first_win_rate':rates,'top_combos':top_combos,'avg_payout':round(sum(payouts)/len(payouts)) if payouts else None,'max_payout':max(payouts) if payouts else None}

def analyze(raw,fixed,before,hist):
    rows=[]
    for b in range(1,7):
        c=raw.get(b,[])
        rows.append({'boat':b,'nation':value(c,['全国']),'local':value(c,['当地']),'motor':value(c,['モーター']),'st':value(c,['平均ST']), 'exhibition':before['exhibition'].get(b),'exhibition_st':before['exhibition_st'].get(b)})
    def norm(vals,x,rev=False):
        v=[z for z in vals if isinstance(z,(int,float))]
        if x is None or len(v)<2 or max(v)==min(v): return 50
        z=(x-min(v))/(max(v)-min(v))*100
        return 100-z if rev else z
    for key,rev in [('nation',0),('local',0),('motor',0),('st',1),('exhibition',1),('exhibition_st',1)]:
        vals=[r[key] for r in rows]
        for r in rows:r[key+'s']=norm(vals,r[key],rev)
    for r in rows:
        hist_rate=hist['first_win_rate'].get(str(r['boat']),0)
        # 履歴は直近データの補正として最大±10点。履歴が少ない場合は中立寄り。
        hist_adj=max(-10,min(10,(hist_rate-16.67)*0.65)) if hist['races']>=12 else 0
        r['history_rate']=hist_rate; r['history_adjustment']=round(hist_adj,1)
        mw=load_model()['weights']
        r['score']=round(r['nations']*mw['nation']+r['locals']*mw['local']+r['motors']*mw['motor']+r['sts']*mw['st']+r['exhibitions']*mw['exhibition']+r['exhibition_sts']*mw['exhibition_st']+hist_adj,1)
        if fixed!='none' and r['boat']==int(fixed): r['score']+=8
    return rows

def scenario(boats,before):
    top=max(boats,key=lambda x:x['score'])
    wind=before.get('wind') or 0
    if top['boat']==1 and wind<=4:return '逃げ'
    center=max(boats[2:4],key=lambda x:x['score'])
    if center['score']>=boats[0]['score']-4:return 'まくり・まくり差し'
    return '差し'

def build_bets(boats,odds,fixed):
    score={x['boat']:x['score'] for x in boats}; combos=[]
    for a,b,c in permutations(range(1,7),3):
        if fixed!='none' and a!=int(fixed):continue
        raw=max(score[a],1)*max(score[b],1)*max(score[c],1)
        key=f'{a}{b}{c}'; x={'bet':f'{a}-{b}-{c}','raw':raw,'odds':odds.get(key)}; combos.append(x)
    total=sum(x['raw'] for x in combos) or 1
    for x in combos:
        x['probability']=x['raw']/total
        x['ev']=None if x['odds'] is None else x['probability']*x['odds']
        x['judgement']='オッズ未取得' if x['ev'] is None else ('候補' if x['ev']>=1 else ('慎重' if x['ev']>=.8 else '見送り'))
    combos.sort(key=lambda x:x['ev'] if x['ev'] is not None else -1,reverse=True); return combos



@app.get('/api/performance')
def api_performance():
    rows=load_ledger(); return jsonify({'ok':True,'stats':ledger_stats(rows),'records':rows[-100:]})

@app.post('/api/performance')
def api_performance_add():
    data=request.get_json(force=True)
    try:
        inv=float(data.get('investment',0) or 0); payout=float(data.get('payout',0) or 0)
        if inv < 0 or payout < 0: raise ValueError('投資額・払戻は0以上で入力してください')
        combo=str(data.get('combo','')).replace('-','')
        actual=str(data.get('actual_combo','')).replace('-','')
        if actual and (len(actual)!=3 or not actual.isdigit()): raise ValueError('実結果3連単は例: 123 の形式で入力してください')
        row={'id':datetime.now().strftime('%Y%m%d%H%M%S%f'),'date':str(data.get('date') or datetime.now().strftime('%Y%m%d')).replace('/',''),'stadium':str(data.get('stadium','15')),'race':int(data.get('race',1)),'combo':combo,'actual_combo':actual,'investment':inv,'payout':payout,'profit':payout-inv,'hit':bool(actual and combo==actual),'settled':True,'note':str(data.get('note',''))[:300]}
        rows=load_ledger(); rows.append(row); save_ledger(rows)
        return jsonify({'ok':True,'record':row,'stats':ledger_stats(rows)})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),400

@app.delete('/api/performance')
def api_performance_delete():
    rows=load_ledger(); save_ledger([]); return jsonify({'ok':True,'stats':ledger_stats([])})

@app.get('/api/model')
def api_model():
    s=load_model(); return jsonify({'ok':True,'model':s})

@app.post('/api/learn')
def api_learn():
    data=request.get_json(force=True)
    predicted=[int(x) for x in data.get('predicted_order',[])][:6]
    actual=int(data.get('actual_first'))
    if not predicted or actual not in range(1,7): return jsonify({'ok':False,'error':'予測順位と実際の1着艇を指定してください'}),400
    s,hit=learn_from_record(load_model(),predicted,actual)
    return jsonify({'ok':True,'hit':bool(hit),'model':s})

@app.get('/api/backtest')
def api_backtest():
    jcd=request.args.get('stadium','15'); days=int(request.args.get('days','30')); topn=max(1,min(int(request.args.get('topn','3')),12))
    h=historical_stats(jcd,days)
    total=h['races']; combos=h['top_combos']
    # Historical-frequency benchmark: use the most frequent combinations as a transparent baseline.
    hit_rate=(sum(v for _,v in combos[:topn])/total*100) if total else 0
    return jsonify({'ok':True,'venue':STADIUMS.get(jcd,jcd),'days':days,'races':total,'top_n':topn,'benchmark_hit_rate':round(hit_rate,2),'note':'過去の3連単出現頻度を使ったベンチマークです。現在のAIモデルの的中率を意味しません。','top_combos':combos})

@app.get('/')
def home():return send_from_directory('static','index.html')

@app.get('/api/history')
def api_history():
    jcd=request.args.get('stadium','15'); days=int(request.args.get('days','30'))
    return jsonify({'ok':True,'venue':STADIUMS.get(jcd,jcd),'stats':historical_stats(jcd,days)})

@app.get('/api/daily')
def api_daily():
    """Return only venues that actually have a program on the requested date.
    One lightweight racelist request per venue; no historical backtest fan-out.
    """
    date=request.args.get('date',datetime.now().strftime('%Y%m%d')).replace('/','')
    active=[]
    checked=0
    errors=[]
    for jcd,name in STADIUMS.items():
        checked+=1
        url=f'{BASE}racelist?hd={date}&jcd={jcd}&rno=1'
        try:
            html=get(url)
            soup=BeautifulSoup(html,'html.parser')
            text=soup.get_text(' ',strip=True)
            # A non-race/closed venue normally does not expose the 12-race navigation.
            race_links=[a.get_text(' ',strip=True) for a in soup.find_all('a')]
            has_race_nav=any(re.fullmatch(r'(?:1[0-2]|[1-9])R',x) for x in race_links)
            has_entries=len(boats_from(html))>=6
            if has_race_nav or has_entries:
                active.append({
                    'venue_code':jcd,
                    'venue':name,
                    'races':12,
                    'race_urls':[f'{BASE}racelist?hd={date}&jcd={jcd}&rno={r}' for r in range(1,13)]
                })
        except Exception as e:
            errors.append({'venue_code':jcd,'venue':name,'error':str(e)})
    return jsonify({
        'ok':True,
        'date':date,
        'checked_venues':checked,
        'active_venues':len(active),
        'target_races':sum(x['races'] for x in active),
        'venues':active,
        'errors':errors,
        'note':'開催判定は公式出走表を場ごとに1回確認し、非開催場を予想対象から除外します。各レースの詳細取得は必要なRだけ実行します。'
    })

@app.get('/api/today')
def api_today():
    return api_daily()

@app.get('/api/analyze')
def api_analyze():
    date=request.args.get('date',datetime.now().strftime('%Y%m%d')).replace('/',''); jcd=request.args.get('stadium','15'); race=int(request.args.get('race','9')); fixed=request.args.get('fixed','none'); days=int(request.args.get('history_days','30'))
    source=f'{BASE}racelist?hd={date}&jcd={jcd}&rno={race:02d}'; before_source=f'{BASE}beforeinfo?hd={date}&jcd={jcd}&rno={race:02d}'; odds_source=f'{BASE}odds3t?hd={date}&jcd={jcd}&rno={race:02d}'
    try:
        hist=historical_stats(jcd,days); before=parse_before(get(before_source)); boats=analyze(boats_from(get(source)),fixed,before,hist); odds=parse_odds(get(odds_source)); combos=build_bets(boats,odds,fixed); boats.sort(key=lambda x:x['score'],reverse=True)
        return jsonify({'ok':True,'venue':STADIUMS.get(jcd,jcd),'boats':boats,'main':boats[0]['boat'],'second':boats[1]['boat'],'hole':boats[2]['boat'],'scenario':scenario(boats,before),'bets':combos[:12],'history':hist,'weather':{'wind':before['wind'],'wave':before['wave'],'air':before['air'],'water':before['water']},'odds_count':sum(v is not None for v in odds.values()),'notice':f'公式出走表・直前情報・公式3連単オッズに加え、直近{days}日・{hist["races"]}レースの場別結果を補正に使用しています。','source':source,'before_source':before_source,'odds_source':odds_source})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e),'source':source,'before_source':before_source,'odds_source':odds_source}),502

@app.get('/api/daily-predict')
def api_daily_predict():
    """Run real-data predictions for the active day's races.
    Work is deliberately bounded to a small thread pool to avoid Render memory spikes.
    """
    date=request.args.get('date',datetime.now().strftime('%Y%m%d')).replace('/','')
    history_days=max(1,min(int(request.args.get('history_days','3')),7))
    limit=max(1,min(int(request.args.get('limit','288')),288))
    try:
        daily=api_daily().get_json()
        venues=daily.get('venues',[])
        targets=[]
        for v in venues:
            for race in range(1,13):
                targets.append((v['venue_code'],v['venue'],race))
        targets=targets[:limit]

        # Cache one small history sample per active venue instead of refetching it per race.
        histories={}
        for jcd,_,_ in targets:
            if jcd not in histories:
                histories[jcd]=historical_stats(jcd,history_days)

        def one(item):
            jcd,venue,race=item
            source=f'{BASE}racelist?hd={date}&jcd={jcd}&rno={race:02d}'
            before_source=f'{BASE}beforeinfo?hd={date}&jcd={jcd}&rno={race:02d}'
            odds_source=f'{BASE}odds3t?hd={date}&jcd={jcd}&rno={race:02d}'
            try:
                raw=boats_from(get(source))
                if len(raw)<6:
                    return {'ok':False,'venue_code':jcd,'venue':venue,'race':race,'error':'6艇データ未取得','source':source}
                before=parse_before(get(before_source))
                boats=analyze(raw,'none',before,histories[jcd])
                odds=parse_odds(get(odds_source))
                combos=build_bets(boats,odds,'none')
                boats.sort(key=lambda x:x['score'],reverse=True)
                return {
                    'ok':True,'date':date,'venue_code':jcd,'venue':venue,'race':race,
                    'race_id':f'{date}-{jcd}-{race:02d}',
                    'main':boats[0]['boat'],'second':boats[1]['boat'],'hole':boats[2]['boat'],
                    'scenario':scenario(boats,before),'boats':boats,'bets':combos[:120],
                    'odds_count':sum(v is not None for v in odds.values()),
                    'weather':{k:before.get(k) for k in ('wind','wave','air','water')},
                    'source':source,'before_source':before_source,'odds_source':odds_source
                }
            except Exception as e:
                return {'ok':False,'venue_code':jcd,'venue':venue,'race':race,'error':str(e),'source':source}

        from concurrent.futures import ThreadPoolExecutor, as_completed
        results=[]
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures=[ex.submit(one,t) for t in targets]
            for fut in as_completed(futures):
                results.append(fut.result())
        results.sort(key=lambda x:(x.get('venue_code',''),int(x.get('race',0))))
        ok=[x for x in results if x.get('ok')]
        failed=[x for x in results if not x.get('ok')]
        return jsonify({
            'ok':True,'date':date,'target_races':len(targets),'generated_races':len(ok),
            'failed_races':len(failed),'trifecta_combinations':len(ok)*120,
            'history_days':history_days,'venues_checked':len(venues),
            'predictions':ok,'errors':failed,
            'note':'公式出走表・直前情報・3連単オッズを取得して各レースを解析。予想は情報提供用で、購入を自動実行しません。'
        })
    except Exception as e:
        return jsonify({'ok':False,'error':str(e),'date':date}),502


# --- Prediction / result / evaluation store (SQLite interim persistence) ---
import sqlite3
import threading
import os

STORE=Path('prediction_store.sqlite3')
STORE_LOCK=threading.Lock()
DATABASE_URL=os.getenv('DATABASE_URL','').strip()
# Use PostgreSQL only when Render has injected a real connection URL.
# The temporary manual value used during setup is not a resolvable DB URL.
USE_POSTGRES=bool(DATABASE_URL and '@' in DATABASE_URL and '://' in DATABASE_URL and 'boat-ai-db' not in DATABASE_URL.split('@',1)[-1].split('/',1)[0])

class PGCompat:
    def __init__(self,url):
        import psycopg2
        from psycopg2.extras import DictCursor
        self.conn=psycopg2.connect(url,connect_timeout=10)
        self.cursor_factory=DictCursor
    def execute(self,sql,args=()):
        return self.conn.cursor(cursor_factory=self.cursor_factory).execute(sql.replace('?','%s'),args)
    def commit(self): self.conn.commit()
    def close(self): self.conn.close()

def db():
    if USE_POSTGRES:
        try:
            return PGCompat(DATABASE_URL)
        except Exception:
            # Keep the API available until Blueprint-managed DB wiring is active.
            USE_POSTGRES=False
    conn=sqlite3.connect(str(STORE),timeout=30)
    conn=sqlite3.connect(str(STORE),timeout=30)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS predictions(
        race_id TEXT PRIMARY KEY, date TEXT NOT NULL, venue_code TEXT NOT NULL, venue TEXT,
        race INTEGER NOT NULL, generated_at TEXT NOT NULL, model_version TEXT,
        main INTEGER, second INTEGER, hole INTEGER, scenario TEXT,
        boats_json TEXT NOT NULL, bets_json TEXT NOT NULL,
        weather_json TEXT, source_json TEXT, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS results(
        race_id TEXT PRIMARY KEY, date TEXT NOT NULL, venue_code TEXT NOT NULL, venue TEXT,
        race INTEGER NOT NULL, actual_combo TEXT NOT NULL, payout INTEGER,
        fetched_at TEXT NOT NULL, source TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS evaluations(
        race_id TEXT PRIMARY KEY, predicted_combo TEXT, actual_combo TEXT,
        exact_hit INTEGER NOT NULL, first_hit INTEGER NOT NULL,
        actual_probability REAL, logloss REAL, brier REAL,
        evaluated_at TEXT NOT NULL, learned INTEGER NOT NULL DEFAULT 0)''')
    conn.commit()
    return conn

def init_db():
    if not USE_POSTGRES: db().close(); return
    conn=db()
    conn.execute('''CREATE TABLE IF NOT EXISTS predictions(
        race_id TEXT PRIMARY KEY, date TEXT NOT NULL, venue_code TEXT NOT NULL, venue TEXT,
        race INTEGER NOT NULL, generated_at TEXT NOT NULL, model_version TEXT,
        main INTEGER, second INTEGER, hole INTEGER, scenario TEXT,
        boats_json TEXT NOT NULL, bets_json TEXT NOT NULL,
        weather_json TEXT, source_json TEXT, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS results(
        race_id TEXT PRIMARY KEY, date TEXT NOT NULL, venue_code TEXT NOT NULL, venue TEXT,
        race INTEGER NOT NULL, actual_combo TEXT NOT NULL, payout INTEGER,
        fetched_at TEXT NOT NULL, source TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS evaluations(
        race_id TEXT PRIMARY KEY, predicted_combo TEXT, actual_combo TEXT,
        exact_hit INTEGER NOT NULL, first_hit INTEGER NOT NULL,
        actual_probability REAL, logloss REAL, brier REAL,
        evaluated_at TEXT NOT NULL, learned INTEGER NOT NULL DEFAULT 0)''')
    conn.commit(); conn.close()

init_db()

def save_prediction_row(p):
    rid=str(p.get('race_id') or '')
    if not rid: raise ValueError('race_id is required')
    now=datetime.now().isoformat(timespec='seconds')
    with STORE_LOCK:
        conn=db()
        conn.execute('''INSERT INTO predictions
            (race_id,date,venue_code,venue,race,generated_at,model_version,main,second,hole,scenario,
             boats_json,bets_json,weather_json,source_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(race_id) DO UPDATE SET
             generated_at=excluded.generated_at, model_version=excluded.model_version,
             main=excluded.main, second=excluded.second, hole=excluded.hole, scenario=excluded.scenario,
             boats_json=excluded.boats_json, bets_json=excluded.bets_json,
             weather_json=excluded.weather_json, source_json=excluded.source_json''',
            (rid,str(p.get('date','')),str(p.get('venue_code','')),str(p.get('venue','')),
             int(p.get('race',0)),str(p.get('generated_at') or now),str(p.get('model_version') or 'baseline-v1'),
             p.get('main'),p.get('second'),p.get('hole'),str(p.get('scenario','')),
             json.dumps(p.get('boats',[]),ensure_ascii=False),
             json.dumps(p.get('bets',[]),ensure_ascii=False),
             json.dumps(p.get('weather',{}),ensure_ascii=False),
             json.dumps({'source':p.get('source'),'before_source':p.get('before_source'),'odds_source':p.get('odds_source')},ensure_ascii=False),
             now))
        conn.commit(); conn.close()
    return rid

def parse_prediction_bets(row):
    try:return json.loads(row['bets_json'])
    except Exception:return []

def evaluate_one(pred,result):
    bets=parse_prediction_bets(pred)
    actual=str(result['actual_combo']).replace('-','')
    top=str(bets[0].get('bet','')).replace('-','') if bets else ''
    actual_p=0.0
    probs=[]
    for b in bets:
        combo=str(b.get('bet','')).replace('-','')
        p=float(b.get('probability',0) or 0)
        probs.append(p)
        if combo==actual: actual_p=p
    logloss=-math.log(max(actual_p,1e-9))
    brier=(actual_p-1.0)**2 + sum(p*p for p in probs if p>=0 and p<=1)
    predicted_first=top[:1] if top else ''
    exact=int(bool(top and top==actual))
    first=int(bool(predicted_first and predicted_first==actual[:1]))
    return {'predicted_combo':top,'actual_combo':actual,'exact_hit':exact,'first_hit':first,
            'actual_probability':actual_p,'logloss':logloss,'brier':brier}

@app.post('/api/predictions/save')
def api_prediction_save():
    data=request.get_json(force=True)
    if not isinstance(data,dict): return jsonify({'ok':False,'error':'JSON object required'}),400
    try:
        rid=save_prediction_row(data)
        return jsonify({'ok':True,'race_id':rid,'stored':True})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)}),400

@app.get('/api/predictions')
def api_predictions():
    date=request.args.get('date')
    conn=db()
    if date:
        rows=conn.execute('SELECT race_id,date,venue_code,venue,race,generated_at,model_version,main,second,hole,scenario FROM predictions WHERE date=? ORDER BY venue_code,race',(date.replace('/',''),)).fetchall()
    else:
        rows=conn.execute('SELECT race_id,date,venue_code,venue,race,generated_at,model_version,main,second,hole,scenario FROM predictions ORDER BY date DESC,venue_code,race LIMIT 500').fetchall()
    conn.close()
    return jsonify({'ok':True,'count':len(rows),'predictions':[dict(x) for x in rows]})

@app.get('/api/predictions/stats')
def api_prediction_stats():
    conn=db()
    prediction_count=conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0]
    finished=conn.execute('SELECT COUNT(*) FROM results').fetchone()[0]
    evaluated=conn.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]
    exact=conn.execute('SELECT COALESCE(SUM(exact_hit),0) FROM evaluations').fetchone()[0]
    first=conn.execute('SELECT COALESCE(SUM(first_hit),0) FROM evaluations').fetchone()[0]
    avg_log=conn.execute('SELECT AVG(logloss) FROM evaluations').fetchone()[0]
    avg_brier=conn.execute('SELECT AVG(brier) FROM evaluations').fetchone()[0]
    conn.close()
    return jsonify({'ok':True,'prediction_count':prediction_count,'finished_races':finished,'evaluated_races':evaluated,
                    'exact_hits':exact,'first_hits':first,
                    'exact_hit_rate':round(exact/evaluated*100,2) if evaluated else None,
                    'first_hit_rate':round(first/evaluated*100,2) if evaluated else None,
                    'avg_logloss':round(avg_log,6) if avg_log is not None else None,
                    'avg_brier':round(avg_brier,6) if avg_brier is not None else None,
                    'storage':'SQLite interim; cloud persistent DB is required for durable production history'})

@app.post('/api/daily-predict-save')
def api_daily_predict_save():
    data=api_daily_predict().get_json()
    if not data.get('ok'): return jsonify(data),502
    saved=0
    errors=[]
    for p in data.get('predictions',[]):
        try:
            p['generated_at']=datetime.now().isoformat(timespec='seconds')
            p['model_version']=str(load_model().get('updated_at') or 'baseline-v1')
            save_prediction_row(p); saved+=1
        except Exception as e:
            errors.append({'race_id':p.get('race_id'),'error':str(e)})
    data['saved_predictions']=saved
    data['save_errors']=errors
    return jsonify(data)

@app.post('/api/results/fetch')
def api_results_fetch():
    date=request.args.get('date',datetime.now().strftime('%Y%m%d')).replace('/','')
    venue_code=request.args.get('venue_code') or request.args.get('stadium')
    try:
        targets=[]
        if venue_code:
            if venue_code not in STADIUMS: raise ValueError('venue_code must be 01-24')
            targets=[(venue_code,STADIUMS[venue_code])]
        else:
            daily=api_daily().get_json()
            targets=[(v['venue_code'],v['venue']) for v in daily.get('venues',[])]
        saved=[]; errors=[]
        for jcd,venue in targets:
            url=f'{BASE}resultlist?hd={date}&jcd={jcd}'
            try:
                rows=parse_resultlist(get(url))
                for r in rows:
                    rid=f'{date}-{jcd}-{int(r["race"]):02d}'
                    with STORE_LOCK:
                        conn=db()
                        conn.execute('''INSERT INTO results(race_id,date,venue_code,venue,race,actual_combo,payout,fetched_at,source)
                            VALUES(?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(race_id) DO UPDATE SET actual_combo=excluded.actual_combo,payout=excluded.payout,
                            fetched_at=excluded.fetched_at,source=excluded.source''',
                            (rid,date,jcd,venue,int(r['race']),str(r['combo']),r.get('payout'),
                             datetime.now().isoformat(timespec='seconds'),url))
                        conn.commit(); conn.close()
                    saved.append(rid)
            except Exception as e:
                errors.append({'venue_code':jcd,'venue':venue,'error':str(e)})
        return jsonify({'ok':True,'date':date,'venues_checked':len(targets),'results_saved':len(saved),
                        'race_ids':saved,'errors':errors,'note':'公式競走成績から確定した3連単結果だけを保存します。'})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)}),400

@app.post('/api/evaluate/day')
def api_evaluate_day():
    date=request.args.get('date',datetime.now().strftime('%Y%m%d')).replace('/','')
    learn=request.args.get('learn','1') in ('1','true','yes')
    conn=db()
    rows=conn.execute('''SELECT p.*,r.actual_combo,r.payout FROM predictions p
                         JOIN results r ON p.race_id=r.race_id
                         WHERE p.date=? ORDER BY p.venue_code,p.race''',(date,)).fetchall()
    evaluated=0; exact=0; first=0; logs=[]; state=load_model()
    for row in rows:
        ev=evaluate_one(row,row)
        learned=0
        if learn:
            try:
                predicted_order=[int(x) for x in re.findall(r'\d',ev['predicted_combo'])][:3]
                if predicted_order:
                    state,hit=learn_from_record(state,predicted_order,int(ev['actual_combo'][0]))
                    learned=1
            except Exception:
                pass
        conn.execute('''INSERT INTO evaluations
            (race_id,predicted_combo,actual_combo,exact_hit,first_hit,actual_probability,logloss,brier,evaluated_at,learned)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(race_id) DO UPDATE SET predicted_combo=excluded.predicted_combo,
            actual_combo=excluded.actual_combo,exact_hit=excluded.exact_hit,first_hit=excluded.first_hit,
            actual_probability=excluded.actual_probability,logloss=excluded.logloss,brier=excluded.brier,
            evaluated_at=excluded.evaluated_at,learned=excluded.learned''',
            (row['race_id'],ev['predicted_combo'],ev['actual_combo'],ev['exact_hit'],ev['first_hit'],
             ev['actual_probability'],ev['logloss'],ev['brier'],datetime.now().isoformat(timespec='seconds'),learned))
        conn.commit()
        evaluated+=1; exact+=ev['exact_hit']; first+=ev['first_hit']; logs.append({'race_id':row['race_id'],**ev,'learned':learned})
    conn.close()
    return jsonify({'ok':True,'date':date,'evaluated_races':evaluated,'exact_hits':exact,'first_hits':first,
                    'exact_hit_rate':round(exact/evaluated*100,2) if evaluated else None,
                    'first_hit_rate':round(first/evaluated*100,2) if evaluated else None,
                    'learned_records':sum(x['learned'] for x in logs),
                    'model':load_model(),'evaluations':logs,
                    'note':'評価指標はモデル検証用です。的中率や払戻を将来の成果として保証するものではありません。'})


@app.get('/api/analytics')
def api_analytics():
    """Break evaluation quality down by venue, predicted first boat, scenario and odds band."""
    date=request.args.get('date')
    conn=db()
    q='''SELECT p.*,r.actual_combo,e.predicted_combo,e.exact_hit,e.first_hit,
                e.actual_probability,e.logloss,e.brier
         FROM predictions p
         JOIN results r ON p.race_id=r.race_id
         JOIN evaluations e ON p.race_id=e.race_id'''
    args=[]
    if date:
        q+=' WHERE p.date=?'
        args.append(date.replace('/',''))
    q+=' ORDER BY p.date,p.venue_code,p.race'
    rows=conn.execute(q,args).fetchall()
    conn.close()

    def avg(a):
        return round(sum(a)/len(a),6) if a else None
    def group(items,key):
        out={}
        for x in items:
            k=str(key(x))
            z=out.setdefault(k,{'races':0,'exact_hits':0,'first_hits':0,'logloss':[],'brier':[]})
            z['races']+=1; z['exact_hits']+=int(x['exact_hit']); z['first_hits']+=int(x['first_hit'])
            z['logloss'].append(float(x['logloss'] or 0)); z['brier'].append(float(x['brier'] or 0))
        for z in out.values():
            n=z['races']; z['exact_hit_rate']=round(z['exact_hits']/n*100,2) if n else None
            z['first_hit_rate']=round(z['first_hits']/n*100,2) if n else None
            z['avg_logloss']=avg(z.pop('logloss')); z['avg_brier']=avg(z.pop('brier'))
        return out

    items=[]
    for r in rows:
        try: bets=json.loads(r['bets_json'])
        except Exception: bets=[]
        top=bets[0] if bets else {}
        bet=str(top.get('bet','')).replace('-','')
        odds=top.get('odds')
        try: odds=float(odds) if odds is not None else None
        except Exception: odds=None
        main=str(r['main'] or (bet[:1] if bet else '—'))
        prob=float(top.get('probability',0) or 0)
        if odds is None: band='オッズ未取得'
        elif odds<10: band='〜9.9'
        elif odds<20: band='10〜19.9'
        elif odds<50: band='20〜49.9'
        elif odds<100: band='50〜99.9'
        else: band='100〜'
        items.append({'venue':r['venue'] or r['venue_code'],'main':main,
                      'scenario':r['scenario'] or '不明','odds_band':band,
                      'odds':odds,'probability':prob,'exact_hit':int(r['exact_hit']),
                      'first_hit':int(r['first_hit']),'logloss':float(r['logloss'] or 0),
                      'brier':float(r['brier'] or 0)})

    def rows_for(d):
        return [{'group':k,**v} for k,v in sorted(d.items(),key=lambda kv:kv[0])]

    overall={'races':len(items),'exact_hits':sum(x['exact_hit'] for x in items),
             'first_hits':sum(x['first_hit'] for x in items),
             'exact_hit_rate':round(sum(x['exact_hit'] for x in items)/len(items)*100,2) if items else None,
             'first_hit_rate':round(sum(x['first_hit'] for x in items)/len(items)*100,2) if items else None,
             'avg_logloss':avg([x['logloss'] for x in items]),
             'avg_brier':avg([x['brier'] for x in items])}
    return jsonify({'ok':True,'date':date,'overall':overall,
                    'by_venue':rows_for(group(items,lambda x:x['venue'])),
                    'by_main_boat':rows_for(group(items,lambda x:x['main'])),
                    'by_scenario':rows_for(group(items,lambda x:x['scenario'])),
                    'by_odds_band':rows_for(group(items,lambda x:x['odds_band'])),
                    'note':'保存済み予測と確定結果の評価データから条件別に集計しています。件数が少ない区分は参考値として扱ってください。'})


def score_prediction_with_weights(row, weights):
    try: boats=json.loads(row['boats_json'])
    except Exception: boats=[]
    scored=[]
    for b in boats:
        s=(float(b.get('nations',50) or 50)*weights['nation']+
           float(b.get('locals',50) or 50)*weights['local']+
           float(b.get('motors',50) or 50)*weights['motor']+
           float(b.get('sts',50) or 50)*weights['st']+
           float(b.get('exhibitions',50) or 50)*weights['exhibition']+
           float(b.get('exhibition_sts',50) or 50)*weights['exhibition_st']+
           float(b.get('history_adjustment',0) or 0))
        scored.append((int(b.get('boat',0)),max(s,0.1)))
    score=dict(scored)
    combos=[]
    for a,b,c in permutations(range(1,7),3):
        raw=score.get(a,0.1)*score.get(b,0.1)*score.get(c,0.1)
        combos.append((f'{a}{b}{c}',raw))
    total=sum(x[1] for x in combos) or 1
    actual=str(row['actual_combo']).replace('-','')
    actual_p=next((raw/total for combo,raw in combos if combo==actual),1e-9)
    top=max(combos,key=lambda x:x[1])[0]
    return top,actual_p,-math.log(max(actual_p,1e-9)),int(top[:1]==actual[:1]),int(top==actual)

def model_validation(rows, weights):
    vals=[score_prediction_with_weights(r,weights) for r in rows]
    if not vals:return {'races':0}
    return {'races':len(vals),
            'avg_logloss':sum(x[2] for x in vals)/len(vals),
            'first_hit_rate':sum(x[3] for x in vals)/len(vals)*100,
            'exact_hit_rate':sum(x[4] for x in vals)/len(vals)*100}

def auto_promote_model(date=None, min_samples=100):
    conn=db()
    q='''SELECT p.boats_json,p.date,p.race_id,e.actual_combo,e.evaluated_at
         FROM predictions p JOIN evaluations e ON p.race_id=e.race_id'''
    args=[]
    if date:
        q+=' WHERE p.date<=?'
        args.append(str(date).replace('/',''))
    q+=' ORDER BY p.date,p.venue_code,p.race'
    rows=conn.execute(q,args).fetchall()
    conn.close()
    if len(rows)<min_samples:
        return {'promoted':False,'reason':'insufficient_validation_data','samples':len(rows)}
    split=max(50,int(len(rows)*0.7))
    train=list(rows[:split]); valid=list(rows[split:])
    state=load_model(); base=state['weights']
    baseline=model_validation(valid,base)
    candidates=[('baseline',base)]
    for feature in base:
        for delta in (-0.03,-0.015,0.015,0.03):
            candidates.append((feature+('+' if delta>0 else '')+str(delta),candidate_weights(state,feature,delta)))
    # Choose the candidate that improves training logloss, then require validation improvement.
    scored=[]
    for name,w in candidates:
        tr=model_validation(train,w)
        scored.append((tr['avg_logloss'],name,w,tr))
    scored.sort(key=lambda x:x[0])
    best_train=scored[0]
    candidate_valid=model_validation(valid,best_train[2])
    improvement=(baseline['avg_logloss']-candidate_valid['avg_logloss'])/max(baseline['avg_logloss'],1e-9)
    if best_train[1]!='baseline' and improvement>=0.005:
        new_state=promote(state,best_train[2],reason='champion_validation_improved')
        return {'promoted':True,'generation':new_state.get('generation'),'candidate':best_train[1],
                'baseline_validation':baseline,'candidate_validation':candidate_valid,
                'validation_improvement_pct':round(improvement*100,3),'train_samples':len(train),'validation_samples':len(valid)}
    return {'promoted':False,'reason':'validation_not_improved','candidate':best_train[1],
            'baseline_validation':baseline,'candidate_validation':candidate_valid,
            'validation_improvement_pct':round(improvement*100,3),'train_samples':len(train),'validation_samples':len(valid)}

@app.post('/api/model/auto-promote')
def api_model_auto_promote():
    date=request.args.get('date')
    return jsonify({'ok':True,**auto_promote_model(date=date)})



@app.post('/api/daily-close')
def api_daily_close():
    date=request.args.get('date',datetime.now().strftime('%Y%m%d')).replace('/','')
    r=api_results_fetch().get_json()
    e=api_evaluate_day().get_json()
    promotion=auto_promote_model(date=date)
    return jsonify({'ok':bool(r.get('ok') and e.get('ok')),'date':date,
                    'results_saved':r.get('results_saved',0),'evaluated_races':e.get('evaluated_races',0),
                    'exact_hit_rate':e.get('exact_hit_rate'),'first_hit_rate':e.get('first_hit_rate'),
                    'learned_records':e.get('learned_records',0),'result_errors':r.get('errors',[]),
                    'model_promotion':promotion,
                    'note':'日次クローズ：結果取得→予想照合→評価→オンライン学習→時系列検証によるモデル昇格判定の順で処理しました。'})


if __name__=='__main__':app.run(host='0.0.0.0',port=8000)