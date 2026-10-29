"""Independent STC-01 signal/time-alignment audit. Python stdlib only.
Read-only: never sends orders or mutates source data. Requires full historical CSVs.
"""
import argparse,csv,json,math,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
from bisect import bisect_right

SEC={'15m':900,'1h':3600,'4h':14400}
def stamp(s):
    d=datetime.fromisoformat(str(s).replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset()!=timedelta(0):raise ValueError('timestamp not UTC: '+str(s))
    return d.astimezone(timezone.utc)

def bars(path,tf):
    rows=[]
    with open(path,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            t=stamp(r['open_time_utc']); o,h,l,c,v=(float(r[x]) for x in ('open','high','low','close','volume'))
            if not all(map(math.isfinite,(o,h,l,c,v))) or min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c,h):raise ValueError('invalid OHLCV '+str(t))
            if int(t.timestamp())%SEC[tf]:raise ValueError('bar misalignment '+str(t))
            rows.append((t,o,h,l,c,v))
    if not rows:raise ValueError('no data '+path)
    for a,b in zip(rows,rows[1:]):
        if int((b[0]-a[0]).total_seconds())!=SEC[tf]:raise ValueError('missing/out-of-order candles '+str(a[0]))
    return rows

def ecalc(vals,n):
    a=[None]*len(vals)
    if len(vals)>=n:
        x=sum(vals[:n])/n;a[n-1]=x;k=2/(n+1)
        for i in range(n,len(vals)):
            x=x+k*(vals[i]-x);a[i]=x
    return a

def rcalc(vals,n=14):
    a=[None]*len(vals)
    if len(vals)<=n:return a
    dg=[vals[i]-vals[i-1] for i in range(1,len(vals))]
    gain=sum(max(q,0) for q in dg[:n])/n
    loss=sum(max(-q,0) for q in dg[:n])/n
    def r(g,l):return 100 if l==0 else 100-100/(1+g/l)
    a[n]=r(gain,loss)
    for i in range(n+1,len(vals)):
        gain=(gain*(n-1)+max(dg[i-1],0))/n
        loss=(loss*(n-1)+max(-dg[i-1],0))/n
        a[i]=r(gain,loss)
    return a

def ind(b):
    c=[x[4] for x in b];v=[x[5] for x in b];vr=[None]*len(b)
    for i in range(20,len(b)):
        mean=sum(v[i-20:i])/20
        vr[i]=v[i]/mean if mean else None
    return dict(e9=ecalc(c,9),e21=ecalc(c,21),e200=ecalc(c,200),rsi=rcalc(c),vr=vr)

def near(a,b,rel=1e-7):return math.isclose(float(a),float(b),rel_tol=rel,abs_tol=1e-7)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--m15',required=True);p.add_argument('--h1',required=True);p.add_argument('--h4',required=True)
    p.add_argument('--result',required=True);p.add_argument('--output',required=True)
    a=p.parse_args(); ds={t:bars(getattr(a,arg),t) for t,arg in [('15m','m15'),('1h','h1'),('4h','h4')]}
    b=ds['15m']; h=ds['1h']; f=ds['4h']; x=ind(b);y=ind(h);z=ind(f)
    source=json.loads(Path(a.result).read_text(encoding='utf-8'))
    trades=source.get('trades')
    if not isinstance(trades,list):raise ValueError('missing trades')
    errs=[];details=[]
    def fail(msg):
        if len(errs)<100:errs.append(msg)
    expected=[]; j=k=-1
    for i in range(len(b)-1):
        when=b[i][0]+timedelta(seconds=900)
        while j+1<len(h) and h[j+1][0]+timedelta(seconds=3600)<=when:j+=1
        while k+1<len(f) and f[k+1][0]+timedelta(seconds=14400)<=when:k+=1
        if i<5 or j<0 or k<0:continue
        if any(x[n][i] is None for n in ('e9','e21','vr')) or any(x[n][i-1] is None for n in ('e9','e21')):continue
        if any(y[n][j] is None for n in ('e9','e21','rsi')) or any(z[n][k] is None for n in ('e21','e200')):continue
        if x['vr'][i]<1.2:continue
        bull=(f[k][4]>z['e200'][k] and z['e21'][k]>z['e200'][k]
             and y['e9'][j]>y['e21'][j] and 50<=y['rsi'][j]<=70
             and x['e9'][i-1]<=x['e21'][i-1] and x['e9'][i]>x['e21'][i]
             and b[i][4]>b[i][1])
        bear=(f[k][4]<z['e200'][k] and z['e21'][k]<z['e200'][k]
             and y['e9'][j]<y['e21'][j] and 30<=y['rsi'][j]<=50
             and x['e9'][i-1]>=x['e21'][i-1] and x['e9'][i]<x['e21'][i]
             and b[i][4]<b[i][1])
        if not bull and not bear:continue
        side='LONG' if bull else 'SHORT'
        if side=='SHORT' and source.get('market')=='spot':continue
        stop=min(q[3] for q in b[i-5:i]) if bull else max(q[2] for q in b[i-5:i])
        expected.append((when,side,stop,i,j,k))
    expmap={(q[0],q[1]):q for q in expected}
    first_eval=stamp(source['evaluation_start_utc']) if source.get('evaluation_start_utc') else b[0][0]
    times=[q[0] for q in b]
    last_exit=-1
    for n,t in enumerate(trades,1):
        try:
            when=stamp(t['signal_closed_utc']); entry=stamp(t['entry_open_utc']);exit_t=stamp(t['exit_open_utc']);side=t['side']
            if when!=entry:fail(f'trade {n}: signal time not equal next bar open')
            if entry<first_eval:fail(f'trade {n}: entry before evaluation window')
            i=bisect_right(times,entry)-1
            if i<1 or times[i]!=entry:fail(f'trade {n}: entry not at 15m candle open');continue
            if i<=last_exit:fail(f'trade {n}: overlapping entry')
            if (when,side) not in expmap:fail(f'trade {n}: no independently reconstructed technical signal');continue
            q=expmap[(when,side)]
            if q[3]!=i-1:fail(f'trade {n}: signal uses unexpected candle')
            if not near(t['stop'],q[2]):fail(f'trade {n}: stop differs from 5 preceding candles')
            j,k=q[4],q[5]
            if h[j][0]+timedelta(seconds=3600)>when or f[k][0]+timedelta(seconds=14400)>when:fail(f'trade {n}: lookahead higher timeframe')
            if j+1<len(h) and h[j+1][0]+timedelta(seconds=3600)<=when:fail(f'trade {n}: stale 1h alignment')
            if k+1<len(f) and f[k+1][0]+timedelta(seconds=14400)<=when:fail(f'trade {n}: stale 4h alignment')
            exit_i=bisect_right(times,exit_t)-1
            if exit_i<0 or times[exit_i]!=exit_t or exit_i<i:fail(f'trade {n}: impossible exit')
            last_exit=exit_i
            details.append({'trade':n,'signal_utc':when.isoformat(),'side':side,'hour_candle_closed_utc':(h[j][0]+timedelta(seconds=3600)).isoformat(),'four_hour_candle_closed_utc':(f[k][0]+timedelta(seconds=14400)).isoformat(),'independent_signal':'MATCH'})
        except Exception as e:fail(f'trade {n}: {e}')
    if len(details)!=len(trades):fail('not every trade independently validated')
    out={'schema':'stc01-signals-audit-v1','audit_status':'PASS_WITH_LIMITATIONS' if not errs else 'FAIL','trades_reported':len(trades),'signals_reconstructed':len(expected),'trades_matched':len(details),'errors':errs,'coverage':{'15m_bars':len(b),'1h_bars':len(h),'4h_bars':len(f)},'scope':'Recomputes EMA SMA-seed, Wilder RSI, relative volume and 4h/1h/15m timing independently from CSV; checks each recorded trade.', 'limitations':['Does not independently prove every skipped candidate was correctly excluded by capital/position rules.','Does not independently verify CSV prices against exchange.','Does not validate intrabar mark-to-market drawdown, fill realism or futures funding.'],'details':details}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k!='details'},indent=2))
    return 0 if not errs else 1
if __name__=='__main__':sys.exit(main())
