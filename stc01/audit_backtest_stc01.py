"""Independent trade-ledger audit of STC-01. Read-only; never places orders."""
import argparse,csv,hashlib,json,math,sys
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path

def rows(path,secs):
    with open(path,encoding='utf-8-sig',newline='') as f: data=list(csv.DictReader(f))
    out=[]
    for r in data:
        t=datetime.fromisoformat(r['open_time_utc'].replace('Z','+00:00'))
        if t.tzinfo is None or t.utcoffset()!=timedelta(0): raise ValueError('Not UTC')
        nums=tuple(float(r[k]) for k in ('open','high','low','close','volume'))
        o,h,l,c,v=nums
        if not (0<l<=min(o,c)<=max(o,c)<=h and v>=0 and all(math.isfinite(a) for a in nums)):
            raise ValueError('OHLCV failure '+str(t))
        if int(t.timestamp())%secs: raise ValueError('Misaligned '+str(t))
        if out and (t-out[-1][0]).total_seconds()!=secs: raise ValueError('Gap or duplication '+str(t))
        out.append((t,o,h,l,c,v))
    return out

def near(a,b,tol=0.00025):return abs(a-b)<=tol

def audit(args):
    d={tf:rows(getattr(args,tf),v) for tf,v in [('m15',900),('h1',3600),('h4',14400)]}
    rep=json.loads(Path(args.result).read_text())
    checks=[];errors=[];warnings=[]
    def test(label,ok,detail=''):
        checks.append({'check':label,'pass':bool(ok),'detail':detail})
        if not ok:errors.append(label+((': '+detail) if detail else ''))
    test('Market Spot',rep.get('market')=='spot')
    test('Data synchronized at start',all(x[0][0]==d['m15'][0][0] for x in [d['h1'],d['h4']]))
    test('Data synchronized at end',all(x[-1][0]+timedelta(seconds=s)==d['m15'][-1][0]+timedelta(minutes=15) for x,s in [(d['h1'],3600),(d['h4'],14400)]))
    test('Trade count',rep['trades_count']==len(rep['trades']))
    fees=rep['assumptions']['fee_bps_per_side']/10000
    eq=rep['starting_equity'];peak=eq;drawdown=0; prev_exit=None; gaps=0; ambiguous=0; firstbar_ambiguous=0; mismatches=0
    bytime={bar[0].isoformat():i for i,bar in enumerate(d['m15'])}
    for n,tr in enumerate(rep['trades'],1):
        ent=datetime.fromisoformat(tr['entry_open_utc']); end=datetime.fromisoformat(tr['exit_open_utc']); signal=datetime.fromisoformat(tr['signal_closed_utc'])
        if ent not in [signal]: errors.append(f'Trade {n}: signal close not equal next entry open')
        if ent.isoformat() not in bytime or end.isoformat() not in bytime:errors.append(f'Trade {n}: entry/exit outside CSV');continue
        ei=bytime[ent.isoformat()];xi=bytime[end.isoformat()]
        if prev_exit is not None and ei<=prev_exit:errors.append(f'Trade {n}: overlapped or same bar entry')
        prev_exit=xi
        if xi<ei or xi>ei+95:errors.append(f'Trade {n}: invalid holding length')
        if not near(tr['entry'],d['m15'][ei][1]*1.0005):errors.append(f'Trade {n}: wrong entry with slippage')
        if tr['side']!='LONG':errors.append(f'Trade {n}: Spot short not permitted')
        if tr['quantity']<=0 or tr['quantity']*tr['entry']>eq+0.01:errors.append(f'Trade {n}: insufficient spot buying power')
        if not near(tr['target'],tr['entry']+2*(tr['entry']-tr['stop'])):errors.append(f'Trade {n}: target mismatch')
        if not tr['stop']<tr['entry']:errors.append(f'Trade {n}: invalid long stop')
        # Track stop/target touches and verify the first triggering candle.
        first=None
        for k in range(ei,xi+1):
            _,o,h,l,c,_=d['m15'][k]
            hs=l<=tr['stop']; ht=h>=tr['target']
            if hs and ht:ambiguous+=1
            if k==ei and (hs or ht):firstbar_ambiguous+=1
            if first is None and (hs or ht):first=(k,'STOP' if hs else 'TARGET')
        if tr['exit_reason'] in ('STOP','TARGET') and first!=(xi,tr['exit_reason']):errors.append(f'Trade {n}: first-touch exit mismatch {first}')
        if tr['exit_reason']=='TIME' and (first is not None or xi!=min(ei+95,len(d['m15'])-1)):errors.append(f'Trade {n}: TIME exit mismatch')
        if tr['exit_reason']=='TARGET': expected=tr['target']*0.9995
        elif tr['exit_reason']=='STOP':expected=min(tr['stop'],d['m15'][xi][1])*0.9995
        else:expected=d['m15'][xi][4]*0.9995
        if not near(tr['exit'],expected):errors.append(f'Trade {n}: exit price mismatch')
        net=(tr['exit']-tr['entry'])*tr['quantity']-(tr['entry']+tr['exit'])*tr['quantity']*fees
        if not near(net,tr['pnl_net'],0.001):errors.append(f'Trade {n}: pnl mismatch')
        eq+=net;peak=max(peak,eq);drawdown=max(drawdown,(peak-eq)/peak)
        if not near(eq,tr['equity_after'],0.01):errors.append(f'Trade {n}: equity mismatch')
        if ei>0 and d['m15'][ei-1][0]+timedelta(minutes=15)!=ent:errors.append(f'Trade {n}: next bar alignment')
    test('Net return reproduced independently',near(eq,rep['ending_equity'],0.01),f'computed={eq:.4f}')
    test('Drawdown reproduced (closed trades)',near(drawdown,rep['max_drawdown_fraction_closed_trades'],0.00001),f'computed={drawdown:.6f}')
    test('Every trade passes ledger/chronology/OHLCV checks',not errors,'; '.join(errors[:10]))
    if firstbar_ambiguous:warnings.append(f'{firstbar_ambiguous} trades touch stop or target in entry candle: OHLC does not show within-candle sequence; same-bar fills not independently observable')
    if ambiguous:warnings.append(f'{ambiguous} candles touch both stop and target; backtest chooses stop, a conservative assumption')
    warnings += ['No independent exchange price cross-check: SHA-256 self-consistency is not proof of source authenticity.', 'Signals have not been independently rederived from indicators in this ledger audit.', 'Risk sizing depends on assumed costs; review gap/slippage realism and potential fees not modeled.', '12 months of data are in-sample historical testing, not a forward or out-of-sample validation.']
    return {'schema':'stc01-ledger-audit-v1','audit_status':'PASS_WITH_LIMITATIONS' if not errors else 'FAIL','trades_audited':len(rep['trades']),'reproduced_equity':round(eq,4),'reproduced_closed_trade_drawdown':round(drawdown,6),'ambiguous_bars_stop_and_target':ambiguous,'entry_candles_touching_exit':firstbar_ambiguous,'checks':checks,'errors':errors,'limitations':warnings}

def main():
    p=argparse.ArgumentParser()
    for nm in ('m15','h1','h4','result'):p.add_argument('--'+nm,required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args();r=audit(a);Path(a.output).write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n');print(json.dumps({k:v for k,v in r.items() if k not in ('checks','limitations')},indent=2));sys.exit(0 if not r['errors'] else 1)
if __name__=='__main__':main()
