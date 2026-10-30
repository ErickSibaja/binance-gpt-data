"""STC-03 v1 experimental long-only mean reversion; closed candles exclusively.
No exchange access, no credentials, no order execution. Requires stc01.strategy_stc01 Bar objects.
"""
from bisect import bisect_right
from datetime import timedelta
from math import sqrt


def bollinger(bars, period=20, width=2.0):
    lo=[None]*len(bars); ma=[None]*len(bars)
    for i in range(period-1,len(bars)):
        xs=[x.c for x in bars[i-period+1:i+1]]
        mean=sum(xs)/period
        std=sqrt(sum((x-mean)**2 for x in xs)/period)
        ma[i]=mean; lo[i]=mean-width*std
    return ma,lo


def atr_adx(bars,n=14):
    """Wilder ATR/ADX, DX first available at bar n; first ADX at bar 2n-1."""
    length=len(bars); atr=[None]*length; adx=[None]*length
    if length<2*n: return atr,adx
    trs=[0.0]*length; plus=[0.0]*length; minus=[0.0]*length
    for i in range(1,length):
        b=bars[i]; prev=bars[i-1]
        up=b.h-prev.h; down=prev.l-b.l
        trs[i]=max(b.h-b.l,abs(b.h-prev.c),abs(b.l-prev.c))
        plus[i]=up if up>down and up>0 else 0.0
        minus[i]=down if down>up and down>0 else 0.0
    smtr=sum(trs[1:n+1]); smp=sum(plus[1:n+1]); smm=sum(minus[1:n+1])
    dx=[None]*length
    for i in range(n,length):
        if i>n:
            smtr=smtr-smtr/n+trs[i]
            smp=smp-smp/n+plus[i]
            smm=smm-smm/n+minus[i]
        atr[i]=smtr/n
        pdi=100*smp/smtr if smtr else 0
        mdi=100*smm/smtr if smtr else 0
        dx[i]=100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi else 0
        if i==2*n-1: adx[i]=sum(dx[n:2*n])/n
        elif i>2*n-1: adx[i]=(adx[i-1]*(n-1)+dx[i])/n
    return atr,adx


def rsi_wilder(values,n=14):
    out=[None]*len(values)
    if len(values)<=n:return out
    gains=sum(max(values[i]-values[i-1],0) for i in range(1,n+1))/n
    losses=sum(max(values[i-1]-values[i],0) for i in range(1,n+1))/n
    for i in range(n,len(values)):
        if i>n:
            change=values[i]-values[i-1]
            gains=(gains*(n-1)+max(change,0))/n
            losses=(losses*(n-1)+max(-change,0))/n
        out[i]=100 if losses==0 else 100-100/(1+gains/losses)
    return out


def candidates(b15,b1,b4,adx_limit=25,rsi_limit=30,atr_multiple=1.5):
    """On 15m close, last closed hour must be oversold below its lower band.
    The 15m candle must cross ABOVE fixed lower 1h band; enter NEXT 15m open.
    Last fully closed 4h ADX must be less than 25. Returns dict per candidate.
    """
    ma, lower=bollinger(b1)
    rsi=rsi_wilder([x.c for x in b1]); atr,_=atr_adx(b1)
    _,adx4=atr_adx(b4)
    close1=[x.t+timedelta(hours=1) for x in b1]
    close4=[x.t+timedelta(hours=4) for x in b4]
    for i in range(1,len(b15)-1):
        when=b15[i].t+timedelta(minutes=15)
        j=bisect_right(close1,when)-1
        k=bisect_right(close4,when)-1
        if j<0 or k<0 or any(x is None for x in (ma[j],lower[j],rsi[j],atr[j],adx4[k])):continue
        if not (adx4[k]<adx_limit and rsi[j]<rsi_limit and b1[j].c<lower[j]):continue
        if not (b15[i-1].c<=lower[j]<b15[i].c):continue
        entry_index=i+1
        yield {'signal_at_utc':when.isoformat(),'entry_index':entry_index,
               'band_lower':lower[j], 'mean_1h':ma[j], 'atr_1h':atr[j],
               'rsi_1h':rsi[j], 'adx_4h':adx4[k],
               'context_1h_close':close1[j].isoformat(),
               'context_4h_close':close4[k].isoformat()}
