"""STC-02 v1: long-only hourly breakout with closed 4h trend filter.
Uses the STC-01 Bar and load_csv format; standard library only.
No live trading, keys, or broker access.
"""
from datetime import timedelta
from bisect import bisect_right


def ema(values, n):
    out = [None] * len(values)
    if len(values) < n:
        return out
    out[n-1] = sum(values[:n])/n
    alpha = 2/(n+1)
    for i in range(n, len(values)):
        out[i] = alpha*values[i] + (1-alpha)*out[i-1]
    return out


def atr_wilder(bars, n=14):
    """First ATR at index n-1 uses first n true ranges; no future observations."""
    tr = []
    for i,b in enumerate(bars):
        tr.append(max(b.h-b.l, abs(b.h-bars[i-1].c), abs(b.l-bars[i-1].c)) if i else b.h-b.l)
    result = [None]*len(bars)
    if len(bars) < n:
        return result
    result[n-1] = sum(tr[:n])/n
    for i in range(n,len(bars)):
        result[i] = (result[i-1]*(n-1)+tr[i])/n
    return result


def signals(b15,b1,b4,volume_multiplier=1.5,range_bars=20):
    """Yield signals with 1h close time and 15m entry index.
    The current 1h bar is excluded from the preceding 20-bar range/volume means.
    The selected 4h bar must have CLOSED by the signal timestamp.
    """
    close4 = [b.t+timedelta(hours=4) for b in b4]
    open15 = [b.t for b in b15]
    e200 = ema([b.c for b in b4],200)
    a14 = atr_wilder(b1,14)
    for j in range(max(20,range_bars),len(b1)):
        hour_close = b1[j].t+timedelta(hours=1)
        k = bisect_right(close4,hour_close)-1
        if k < 200 or e200[k] is None or e200[k-1] is None:
            continue
        if not (b4[k].c>e200[k]>e200[k-1]):
            continue
        prev=b1[j-range_bars:j]
        resistance=max(b.h for b in prev)
        avg_v=sum(b.v for b in prev)/range_bars
        if avg_v<=0 or not (b1[j].c>resistance and b1[j].v>=volume_multiplier*avg_v):
            continue
        atr=a14[j]
        if atr is None or atr<=0:
            continue
        ent=bisect_right(open15,hour_close)-1
        # The next 15m candle opens precisely at the close of the signal 1h candle.
        if ent<0 or ent>=len(b15) or b15[ent].t!=hour_close:
            continue
        yield {'signal_1h_open':b1[j].t,'signal_at':hour_close,
               'entry_index':ent,'atr':atr,'resistance':resistance,
               'volume_ratio':b1[j].v/avg_v,'context_4h_close':close4[k]}
