"""STC-01 v1: deterministic multi-timeframe signals on CLOSED OHLCV bars.
All timestamps are UTC open times. No orders, keys, or live trading.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import csv

INTERVALS = {'15m': 900, '1h': 3600, '4h': 14400}

@dataclass(frozen=True)
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float

    def close_time(self, timeframe):
        return self.t + timedelta(seconds=INTERVALS[timeframe])


def parse_utc(s):
    d = datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timestamp must include UTC timezone (Z or +00:00)')
    d = d.astimezone(timezone.utc)
    return d


def load_csv(path, timeframe):
    out=[]
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            b=Bar(parse_utc(r['open_time_utc']), *(float(r[k]) for k in ('open','high','low','close','volume')))
            if b.o <= 0 or b.c <= 0 or b.l <= 0 or b.h < max(b.o,b.c,b.l) or b.l > min(b.o,b.c) or b.v < 0:
                raise ValueError(f'invalid OHLCV {path} {b.t}')
            if int(b.t.timestamp()) % INTERVALS[timeframe]:
                raise ValueError(f'misaligned {timeframe} bar at {b.t}')
            out.append(b)
    if not out:
        raise ValueError(f'no bars in {path}')
    for a,b in zip(out,out[1:]):
        if int((b.t-a.t).total_seconds()) != INTERVALS[timeframe]:
            raise ValueError(f'missing, duplicated or unordered {timeframe} bars: {a.t} -> {b.t}')
    return out


def ema(values, period):
    out=[None]*len(values)
    if len(values)<period: return out
    seed=sum(values[:period])/period
    out[period-1]=seed
    k=2/(period+1)
    for i in range(period,len(values)):
        out[i]=values[i]*k+out[i-1]*(1-k)
    return out


def rsi_wilder(values, period=14):
    out=[None]*len(values)
    if len(values)<=period: return out
    changes=[values[i]-values[i-1] for i in range(1,len(values))]
    gain=sum(max(x,0) for x in changes[:period])/period
    loss=sum(max(-x,0) for x in changes[:period])/period
    def val(g,l): return 100.0 if l==0 else 100-100/(1+g/l)
    out[period]=val(gain,loss)
    for i in range(period+1,len(values)):
        x=changes[i-1]
        gain=(gain*(period-1)+max(x,0))/period
        loss=(loss*(period-1)+max(-x,0))/period
        out[i]=val(gain,loss)
    return out


def indicators(bars):
    closes=[b.c for b in bars]
    volumes=[b.v for b in bars]
    ratios=[None]*len(bars)
    for i in range(20,len(bars)):
        avg=sum(volumes[i-20:i])/20 # preceding 20 bars; never future bars
        ratios[i]=volumes[i]/avg if avg>0 else None
    return {'e9':ema(closes,9), 'e21':ema(closes,21), 'e200':ema(closes,200),
            'rsi':rsi_wilder(closes,14), 'vr':ratios}


def candidate(b15, b1, b4, i, j, k, x15, x1, x4, allow_short=True):
    """Signal evaluated at 15m bar i's CLOSE, with hourly bars already closed."""
    if i<5 or j<0 or k<0 or i==0: return None
    need15=['e9','e21','vr']
    need1=['e9','e21','rsi']
    need4=['e21','e200']
    if any(x15[n][i] is None for n in need15) or any(x15[n][i-1] is None for n in ('e9','e21')): return None
    if any(x1[n][j] is None for n in need1) or any(x4[n][k] is None for n in need4): return None
    if x15['vr'][i]<1.20: return None
    prev_e9,prev_e21=x15['e9'][i-1],x15['e21'][i-1]
    e9,e21=x15['e9'][i],x15['e21'][i]
    bull=(b4[k].c>x4['e200'][k] and x4['e21'][k]>x4['e200'][k]
          and x1['e9'][j]>x1['e21'][j] and 50<=x1['rsi'][j]<=70
          and prev_e9<=prev_e21 and e9>e21 and b15[i].c>b15[i].o)
    bear=(b4[k].c<x4['e200'][k] and x4['e21'][k]<x4['e200'][k]
          and x1['e9'][j]<x1['e21'][j] and 30<=x1['rsi'][j]<=50
          and prev_e9>=prev_e21 and e9<e21 and b15[i].c<b15[i].o)
    if bull: return {'side':'LONG','stop':min(b.l for b in b15[i-5:i])}
    if bear and allow_short: return {'side':'SHORT','stop':max(b.h for b in b15[i-5:i])}
    return None


def aligned_signals(b15,b1,b4,allow_short=True):
    """Returns (index of signal bar, side, stop), rejects all lookahead."""
    x15,x1,x4=indicators(b15),indicators(b1),indicators(b4)
    j=k=-1
    out=[]
    for i in range(len(b15)-1):  # last bar has no next-bar entry
        ts=b15[i].close_time('15m')
        while j+1<len(b1) and b1[j+1].close_time('1h')<=ts: j+=1
        while k+1<len(b4) and b4[k+1].close_time('4h')<=ts: k+=1
        if j<0 or k<0: continue
        signal=candidate(b15,b1,b4,i,j,k,x15,x1,x4,allow_short)
        if signal: out.append((i,signal['side'],signal['stop']))
    return out
