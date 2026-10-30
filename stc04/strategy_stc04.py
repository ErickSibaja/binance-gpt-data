"""STC-04 v1, fixed daily momentum rule. Read-only research."""
import csv
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

@dataclass(frozen=True)
class Candle:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


def load_csv(path):
    rows=[]
    with Path(path).open(newline='', encoding='utf-8') as handle:
        reader=csv.DictReader(handle)
        for row in reader:
            date=row.get('open_time_utc', row.get('timestamp_utc', row.get('time', '')))
            if not date:
                raise ValueError('CSV needs open_time_utc column')
            t=datetime.fromisoformat(date.replace('Z','+00:00'))
            if t.utcoffset()!=timedelta(0):
                raise ValueError('Only UTC timestamps are supported')
            candle=Candle(t,*(float(row[k]) for k in ('open','high','low','close','volume')))
            if not (candle.l>0 and candle.l<=min(candle.o,candle.c)<=max(candle.o,candle.c)<=candle.h and candle.v>=0):
                raise ValueError('Invalid OHLCV on '+date)
            if rows and t-rows[-1].t!=timedelta(days=1):
                raise ValueError('Duplicate or missing 1D candle near '+date)
            rows.append(candle)
    if len(rows)<261:
        raise ValueError('Insufficient warmup: require at least 261 daily candles')
    return rows


def signals(bars):
    """At close of day i, signal is known; trade executes at day i+1 open."""
    output=[]
    for i in range(199,len(bars)-1):
        sma=sum(x.c for x in bars[i-199:i+1])/200
        previous=bars[i-60].c
        momentum=bars[i].c/previous-1.0
        active=(bars[i].c>sma and momentum>0)
        output.append({'signal_index':i,'signal_at_utc':(bars[i].t+timedelta(days=1)).isoformat(),
                       'execute_index':i+1,'sma200':sma,'momentum60':momentum,'active':active})
    return output
