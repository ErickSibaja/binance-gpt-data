"""Download historical *closed* Binance Spot daily candles, no credentials."""
import argparse
import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DAY_MS=86400000
BASES=('https://data-api.binance.vision','https://api.binance.com')

def ms(s):
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    if t.utcoffset()!=timedelta(0) or t.hour or t.minute or t.second or t.microsecond:
        raise ValueError('Dates must be midnight UTC')
    return int(t.timestamp()*1000)

def retrieve(symbol, start, end):
    all_rows=[]
    cursor=start
    while cursor<end:
        params=urllib.parse.urlencode({'symbol':symbol,'interval':'1d','startTime':cursor,'endTime':end-1,'limit':1000})
        errors=[]
        for root in BASES:
            try:
                req=urllib.request.Request(root+'/api/v3/klines?'+params,headers={'User-Agent':'Binance-GPT-STC04-Research/1.0'})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    chunk=json.loads(resp.read())
                if not isinstance(chunk,list): raise ValueError('Unexpected API response')
                break
            except (urllib.error.URLError,ValueError,TimeoutError) as e:
                errors.append(str(e))
        else:
            raise RuntimeError('Binance daily candles unavailable: '+'; '.join(errors))
        if not chunk: raise ValueError('Missing candles starting at '+str(cursor))
        for r in chunk:
            t=int(r[0]); close=int(r[6])
            if t!=cursor: raise ValueError(f'Daily candle gap: expected {cursor}, got {t}')
            if close!=t+DAY_MS-1: raise ValueError('Unexpected close time')
            if t>=end: break
            o,h,l,c,v=map(float,r[1:6]);
            if not(l>0 and l<=min(o,c)<=max(o,c)<=h and v>=0):raise ValueError('Invalid OHLCV')
            all_rows.append((datetime.fromtimestamp(t/1000,timezone.utc).isoformat(),o,h,l,c,v))
            cursor+=DAY_MS
        time.sleep(0.12)
    return all_rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbol',required=True,choices=['BTCUSDT','ETHUSDT'])
    ap.add_argument('--start',required=True)
    ap.add_argument('--end',required=True)
    ap.add_argument('--outdir',default='stc04/history')
    a=ap.parse_args()
    start,end=ms(a.start),ms(a.end)
    if not 261*DAY_MS<=end-start<=2500*DAY_MS:raise ValueError('Require 261–2500 days')
    if end>int(datetime.now(timezone.utc).timestamp()*1000)//DAY_MS*DAY_MS:
        raise ValueError('End must exclude current unfinished UTC day')
    rows=retrieve(a.symbol,start,end)
    if len(rows)!=(end-start)//DAY_MS:raise ValueError('Incomplete history')
    out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    path=out/(a.symbol+'_spot_1d.csv')
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['open_time_utc','open','high','low','close','volume']);w.writerows(rows)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'schema':'stc04-history-v1','symbol':a.symbol,'market':'binance_spot','start':a.start,'end_exclusive':a.end,'candles':len(rows),'sha256':digest,'file':path.name}
    (out/(a.symbol+'-manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
