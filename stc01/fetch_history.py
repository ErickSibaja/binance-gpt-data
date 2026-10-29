"""Download Binance SPOT closed historical OHLCV for STC-01, no credentials/orders."""
import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen, Request

INTERVALS={'15m':900_000,'1h':3_600_000,'4h':14_400_000}
BASE='https://data-api.binance.vision/api/v3/klines'

def fetch(symbol, interval, start_ms, end_ms, retries=3):
    output=[]; cursor=start_ms; span=INTERVALS[interval]
    while cursor<end_ms:
        params=urlencode(dict(symbol=symbol,interval=interval,startTime=cursor,endTime=end_ms-1,limit=1000))
        for attempt in range(retries):
            try:
                with urlopen(Request(BASE+'?'+params,headers={'User-Agent':'BinanceGPT-STC01-research/1.0'}),timeout=25) as r:
                    payload=json.load(r)
                if not isinstance(payload,list): raise ValueError('non-list API response')
                break
            except (HTTPError,URLError,TimeoutError,ValueError) as exc:
                if attempt==retries-1: raise RuntimeError(f'Binance historical fetch failed {symbol} {interval}: {exc}') from exc
                time.sleep(2**attempt)
        if not payload: raise ValueError(f'No candles returned for {interval} at {cursor}')
        batch=[x for x in payload if cursor<=int(x[0])<end_ms]
        if not batch: raise ValueError('No candles within requested range')
        output.extend(batch)
        next_cursor=int(batch[-1][0])+span
        if next_cursor<=cursor: raise ValueError('API pagination did not advance')
        cursor=next_cursor
        time.sleep(0.15)
    return output

def validate(candles,interval,start_ms,end_ms):
    span=INTERVALS[interval]
    if not candles: raise ValueError('No OHLCV')
    expected=list(range(start_ms,end_ms,span))
    got=[int(x[0]) for x in candles]
    if got!=expected: raise ValueError(f'Continuity/coverage error {interval}: expected {len(expected)} received {len(got)}')
    for row in candles:
        ts=int(row[0]); op,high,low,close,vol=map(float,row[1:6])
        if int(row[6])!=ts+span-1: raise ValueError('Wrong candle close timestamp')
        if not (0<low<=min(op,close)<=max(op,close)<=high and vol>=0): raise ValueError('Invalid OHLCV')

def iso_ms(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat().replace('+00:00','Z')

def run(symbol,start_utc,end_utc,outdir):
    def parse(s):
        dt=datetime.fromisoformat(s.replace('Z','+00:00'))
        if dt.tzinfo is None: raise ValueError('UTC timezone required: use Z')
        return int(dt.timestamp()*1000)
    begin=parse(start_utc); finish=parse(end_utc)
    if finish<=begin or finish-begin>500*86400000: raise ValueError('Choose period between 1 minute and 180 days')
    current=int(time.time()*1000)
    for tf,ms in INTERVALS.items():
        if begin%ms or finish%ms: raise ValueError(f'Start/end must align with {tf} UTC boundary; use 00:00Z or 04:00Z')
    if finish>current-60_000: raise ValueError('End must be at least 60 seconds in the past to exclude open candles')
    root=Path(outdir); root.mkdir(parents=True,exist_ok=True)
    manifest={'schema':'binance-gpt-stc01-history-v1','source':BASE,'exchange':'binance','market':'spot', 'symbol':symbol,'start_utc':iso_ms(begin),'end_exclusive_utc':iso_ms(finish),'generated_at_utc':iso_ms(current),'series':[]}
    for tf in INTERVALS:
        rows=fetch(symbol,tf,begin,finish)
        validate(rows,tf,begin,finish)
        path=root/f'{symbol}_spot_{tf}.csv'
        with path.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f,lineterminator='\n')
            w.writerow(['open_time_utc','open','high','low','close','volume'])
            for r in rows:w.writerow([iso_ms(int(r[0])),*r[1:6]])
        payload=path.read_bytes()
        manifest['series'].append({'interval':tf,'candles':len(rows),'first_open_utc':iso_ms(int(rows[0][0])),'last_open_utc':iso_ms(int(rows[-1][0])),'filename':path.name,'sha256':hashlib.sha256(payload).hexdigest()})
        print(f'{tf}: {len(rows)} complete candles; saved {path}')
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',choices=['BTCUSDT','ETHUSDT'],default='BTCUSDT')
    p.add_argument('--start',required=True,help='UTC ISO-8601 e.g. 2026-08-01T00:00:00Z')
    p.add_argument('--end',required=True,help='UTC ISO-8601 exclusive, e.g. 2026-10-01T00:00:00Z')
    p.add_argument('--outdir',default='history')
    a=p.parse_args();run(a.symbol,a.start,a.end,a.outdir)
