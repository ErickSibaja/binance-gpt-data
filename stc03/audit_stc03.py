"""STC-03 research auditor; standard library; no credentials or trading.
Independently rebuilds signal conditions and ledger from supplied closed OHLCV CSV.
"""
import argparse,csv,json,math,sys
from datetime import datetime,timezone,timedelta
from bisect import bisect_right
from pathlib import Path
from hashlib import sha256

SECONDS={'15m':900,'1h':3600,'4h':14400}
def parse(t):
    d=datetime.fromisoformat(t.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('timezone missing')
    return d.astimezone(timezone.utc)

def bars(path,tf):
    d=[];step=SECONDS[tf]
    with open(path,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            t=parse(r['open_time_utc']);o,h,l,c,v=(float(r[k]) for k in ('open','high','low','close','volume'))
            if not all(map(math.isfinite,(o,h,l,c,v))) or min(o,h,l,c)<=0 or v<0 or h<max(o,c) or l>min(o,c):raise ValueError('invalid OHLCV')
            sec=int(t.timestamp())
            if sec%step or (d and sec-int(d[-1][0].timestamp())!=step):raise ValueError('unaligned, missing, or duplicate candles')
            d.append((t,o,h,l,c,v))
    if not d:raise ValueError('empty candles')
    return d

def bb(data,n=20):
    out=[]; sm=sm2=0.
    for i,b in enumerate(data):
        x=b[4];sm+=x;sm2+=x*x
        if i>=n: y=data[i-n][4];sm-=y;sm2-=y*y
        if i<n-1:out.append((None,None));continue
        mean=sm/n;dev=math.sqrt(max(0,sm2/n-mean*mean))
        out.append((mean,mean-2*dev))
    return out

def rsi(data,n=14):
    out=[None]*len(data)
    if len(data)<=n:return out
    up=dn=0.
    for i in range(1,n+1):
        v=data[i][4]-data[i-1][4];up+=max(v,0);dn+=max(-v,0)
    up/=n;dn/=n
    for i in range(n,len(data)):
        if i>n:
            v=data[i][4]-data[i-1][4];up=(up*(n-1)+max(v,0))/n;dn=(dn*(n-1)+max(-v,0))/n
        out[i]=100 if dn==0 else 100-100/(1+up/dn)
    return out

def atr_adx(data,n=14):
    length=len(data);a=[None]*length;adx=[None]*length
    if length<2*n:return a,adx
    tr=[0.]*length;pos=[0.]*length;neg=[0.]*length
    for i in range(1,length):
        p=data[i-1];c=data[i]
        rise=c[2]-p[2];fall=p[3]-c[3]
        tr[i]=max(c[2]-c[3],abs(c[2]-p[4]),abs(c[3]-p[4]))
        pos[i]=rise if rise>fall and rise>0 else 0.
        neg[i]=fall if fall>rise and fall>0 else 0.
    st=sum(tr[1:n+1]);sp=sum(pos[1:n+1]);sn=sum(neg[1:n+1]);dx=[]
    for i in range(n,length):
        if i>n:
            st+=tr[i]-st/n;sp+=pos[i]-sp/n;sn+=neg[i]-sn/n
        a[i]=st/n
        pd=100*sp/st if st else 0.;md=100*sn/st if st else 0.
        dx_now=100*abs(pd-md)/(pd+md) if pd+md else 0.
        dx.append(dx_now)
        if i==2*n-1:adx[i]=sum(dx[:n])/n
        elif i>2*n-1:adx[i]=(adx[i-1]*(n-1)+dx_now)/n
    return a,adx

def near(x,y,tol=0.00015):return abs(x-y)<=tol+1e-8*max(abs(x),abs(y))

def verify(symbol,m15,h1,h4,result,evaluation_start):
    d15=bars(m15,'15m');d1=bars(h1,'1h');d4=bars(h4,'4h')
    r=json.loads(Path(result).read_text(encoding='utf8'))
    errors=[];warnings=[]
    if r.get('schema')!='stc03-research-v1' or r.get('symbol')!=symbol or r.get('market')!='spot':errors.append('schema/symbol/market mismatch')
    start=parse(evaluation_start)
    if r.get('evaluation_start_utc')!=start.isoformat():errors.append('evaluation_start mismatch')
    if r.get('assumptions',{}).get('fee_bps_per_side')!=10 or r.get('assumptions',{}).get('slippage_bps_per_side')!=5:errors.append('different costs')
    v1=bb(d1);ri=rsi(d1);atr,_=atr_adx(d1);_,adx=atr_adx(d4)
    close1=[x[0]+timedelta(hours=1) for x in d1];close4=[x[0]+timedelta(hours=4) for x in d4]
    candidates=[]
    for i in range(1,len(d15)-1):
        time=d15[i][0]+timedelta(minutes=15)
        j=bisect_right(close1,time)-1;k=bisect_right(close4,time)-1
        if j<0 or k<0:continue
        mean,lower=v1[j]
        if any(x is None for x in (mean,lower,ri[j],atr[j],adx[k])):continue
        if adx[k]>=25 or ri[j]>=30 or d1[j][4]>=lower:continue
        if not (d15[i-1][4]<=lower<d15[i][4]):continue
        candidates.append((i,time,j,k,mean,lower,atr[j]))
    if r.get('candidate_count')!=len(candidates):errors.append(f'candidate_count {r.get("candidate_count")} != {len(candidates)}')
    expected=[];balance=10000.;peak=balance;dd=0.;free=0;skips={'warmup':0,'position_open':0,'invalid_reward':0}
    for i,time,j,k,mean,lower,atrval in candidates:
        ent=i+1
        if d15[ent][0]<start:skips['warmup']+=1;continue
        if ent<free:skips['position_open']+=1;continue
        entry=d15[ent][1]*1.0005;stop=entry-1.5*atrval;target=mean
        if stop<=0 or target<=entry or target-entry<entry-stop:skips['invalid_reward']+=1;continue
        risk_unit=entry-stop+.001*(entry+stop)+.0005*stop
        qty=min(balance*.005/risk_unit,balance/(entry*1.001))
        if qty<=0:errors.append('non-positive quantity');continue
        last=min(ent+95,len(d15)-1)
        exi=last;reason='TIME';out=d15[last][4]*.9995
        for ti in range(ent,last+1):
            bar=d15[ti]
            if bar[3]<=stop:exi=ti;reason='STOP';out=min(stop,bar[1])*.9995;break
            if bar[2]>=target:exi=ti;reason='TARGET';out=target*.9995;break
        pnl=(out-entry)*qty-(entry+out)*qty*.001
        expected.append({'signal_at_utc':time.isoformat(),'entry_open_utc':d15[ent][0].isoformat(),'exit_open_utc':d15[exi][0].isoformat(),'side':'LONG','entry':entry,'stop':stop,'target':target,'exit':out,'quantity':qty,'pnl_net':pnl,'equity_after':balance+pnl,'exit_reason':reason,'context_1h_close':close1[j].isoformat(),'context_4h_close':close4[k].isoformat(),'cash_before':balance})
        if entry*qty*1.001>balance+.01:errors.append('unfunded spot position')
        if close1[j]>time or close4[k]>time:errors.append('lookahead context')
        balance+=pnl;peak=max(peak,balance);dd=max(dd,(peak-balance)/peak);free=exi+1
    actual=r.get('trades',[])
    if len(expected)!=len(actual) or r.get('trades_count')!=len(actual):errors.append(f'trades {len(actual)} reported, {len(expected)} reconstructed')
    numeric=('entry','stop','target','exit','quantity','pnl_net','equity_after')
    textual=('signal_at_utc','entry_open_utc','exit_open_utc','side','exit_reason','context_1h_close','context_4h_close')
    for n,(a,b) in enumerate(zip(actual,expected),1):
        for key in textual:
            if a.get(key)!=b[key]:errors.append(f'trade {n} {key}: mismatch')
        for key in numeric:
            if not near(a.get(key,float('nan')),b[key]):errors.append(f'trade {n} {key}: mismatch')
    if not near(r.get('ending_equity',float('nan')),balance):errors.append('ending equity mismatch')
    if not near(r.get('net_pnl',float('nan')),balance-10000):errors.append('net PNL mismatch')
    if not near(r.get('max_drawdown_closed_fraction',float('nan')),dd):errors.append('closed drawdown mismatch')
    if not actual:warnings.append('no trades')
    warnings+=['CSV prices not independently cross-checked against exchange','15m OHLC cannot establish tick path or exact intrabar drawdown','Spot lot size/min-notional filters and changing fees not simulated','In-sample backtest; no proof of future profitability']
    report={'schema':'stc03-audit-v1','symbol':symbol,'status':'FAIL' if errors else 'PASS_WITH_LIMITATIONS','evaluated_at_utc':datetime.now(timezone.utc).isoformat(),'validation':{'historical_bars':{'15m':len(d15),'1h':len(d1),'4h':len(d4)},'candidate_count':len(candidates),'trades_reconstructed':len(expected),'trades_reported':len(actual),'skipped_candidates':skips,'reconstructed_ending_equity':round(balance,4),'reconstructed_net_pnl':round(balance-10000,4),'reconstructed_drawdown_closed_fraction':round(dd,6)},'errors':errors[:60],'warnings':warnings}
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',required=True,choices=('BTCUSDT','ETHUSDT'))
    p.add_argument('--m15',required=True);p.add_argument('--h1',required=True);p.add_argument('--h4',required=True);p.add_argument('--result',required=True);p.add_argument('--output',required=True)
    p.add_argument('--evaluation-start',default='2025-10-01T00:00:00Z')
    a=p.parse_args();r=verify(a.symbol,a.m15,a.h1,a.h4,a.result,a.evaluation_start)
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(r,indent=2,ensure_ascii=False))
    if r['status']=='FAIL':sys.exit(1)
if __name__=='__main__':main()
