import json, os
from datetime import datetime

PATH=os.path.join(os.path.dirname(__file__),'model_state.json')
DEFAULT={'weights':{'nation':.28,'local':.11,'motor':.17,'st':.18,'exhibition':.08,'exhibition_st':.03,'history':.15},'samples':0,'hits':0,'updated_at':None,'generation':1,'last_promotion':None}

def _pg():
    url=os.getenv('DATABASE_URL','').strip()
    if not url: return None
    try:
        import psycopg2
        return psycopg2.connect(url, connect_timeout=5)
    except Exception: return None

def load():
    conn=_pg()
    if conn:
        try:
            with conn.cursor() as c:
                c.execute('CREATE TABLE IF NOT EXISTS model_state (id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)')
                c.execute('SELECT state_json FROM model_state WHERE id=1')
                row=c.fetchone()
                if row:
                    conn.close(); return json.loads(row[0])
                state=json.loads(json.dumps(DEFAULT))
                c.execute('INSERT INTO model_state(id,state_json) VALUES(1,%s)',(json.dumps(state,ensure_ascii=False),))
                conn.commit(); conn.close(); return state
        except Exception:
            try: conn.close()
            except Exception: pass
    if not os.path.exists(PATH): return json.loads(json.dumps(DEFAULT))
    try:
        with open(PATH,'r',encoding='utf-8') as f: s=json.load(f)
        w=DEFAULT['weights'].copy(); w.update(s.get('weights',{})); s['weights']=w
        for k,v in DEFAULT.items():
            if k not in s: s[k]=v
        return s
    except Exception: return json.loads(json.dumps(DEFAULT))

def save(s):
    s['updated_at']=datetime.now().isoformat(timespec='seconds')
    conn=_pg()
    if conn:
        try:
            with conn.cursor() as c:
                c.execute('CREATE TABLE IF NOT EXISTS model_state (id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)')
                c.execute('INSERT INTO model_state(id,state_json) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET state_json=EXCLUDED.state_json',(json.dumps(s,ensure_ascii=False),))
                conn.commit(); conn.close(); return
        except Exception:
            try: conn.close()
            except Exception: pass
    with open(PATH,'w',encoding='utf-8') as f: json.dump(s,f,ensure_ascii=False,indent=2)

def normalize_weights(w):
    w={k:max(.005,float(v)) for k,v in w.items()}
    total=sum(w.values()) or 1
    return {k:round(v/total,4) for k,v in w.items()}

def learn_from_record(state, predicted, actual, feature_scores=None):
    hit=int(predicted and predicted[0]==actual)
    state['samples']+=1; state['hits']+=hit
    delta=.0015 if hit else -.00075
    factors={'nation':1.0,'local':.8,'motor':.9,'st':1.1,'exhibition':.7,'exhibition_st':.6,'history':.5}
    for k,factor in factors.items():
        state['weights'][k]+=delta*factor
    state['weights']=normalize_weights(state['weights'])
    save(state); return state,hit

def candidate_weights(state, feature, delta):
    w=state['weights'].copy()
    w[feature]=max(.005,w[feature]+delta)
    return normalize_weights(w)

def promote(state, weights, reason='validation_improved'):
    state['weights']=normalize_weights(weights)
    state['generation']=int(state.get('generation',1))+1
    state['last_promotion']=datetime.now().isoformat(timespec='seconds')
    state['promotion_reason']=reason
    save(state)
    return state
