"""Reconcile every STC-01 technical candidate with the backtest ledger.
Read-only, Python standard library. Signal formula is shared with strategy_stc01;
this is a decision-path audit, not an independent mathematical signal audit.
"""
import argparse, json, math
from collections import Counter
from datetime import datetime
from pathlib import Path
from strategy_stc01 import load_csv, aligned_signals


def audit(m15, h1, h4, result):
    b15=load_csv(m15,'15m'); b1=load_csv(h1,'1h'); b4=load_csv(h4,'4h')
    report=json.loads(Path(result).read_text(encoding='utf-8'))
    trades=report['trades']; market=report['market']
    signals=aligned_signals(b15,b1,b4,allow_short=(market=='bitget_usdt_futures'))
    start=datetime.fromisoformat(report['evaluation_start_utc'].replace('Z','+00:00')) if report.get('evaluation_start_utc') else b15[0].t
    fee=report.get('assumptions',{}).get('fee_bps_per_side',10)/10000
    slip=report.get('assumptions',{}).get('slippage_bps_per_side',5)/10000
    risk_fraction=0.005
    max_bars=report.get('assumptions',{}).get('max_holding_15m_bars',96)
    equity=float(report['starting_equity']); next_free=0; trade_idx=0
    details=[]; errors=[]; counts=Counter()
    for idx,side,stop in signals:
        ent=idx+1
        event={'signal_utc':b15[idx].close_time('15m').isoformat(),'side':side,'entry_open_utc':b15[ent].t.isoformat()}
        if b15[ent].t<start:reason='BEFORE_EVALUATION'
        elif ent<next_free:reason='POSITION_OPEN'
        else:
            direction=1 if side=='LONG' else -1
            entry=b15[ent].o*(1+slip*direction)
            unit_risk=direction*(entry-stop)
            risk_per_unit=unit_risk+entry*fee+stop*fee+stop*slip
            qty=min(equity*risk_fraction/risk_per_unit,equity/entry) if market=='spot' and risk_per_unit>0 else (equity*risk_fraction/risk_per_unit if risk_per_unit>0 else 0)
            if unit_risk<=0:reason='INVALID_STOP'
            elif qty<=0:reason='NO_CAPITAL'
            else:
                if trade_idx>=len(trades):
                    errors.append('Extra executed candidate not present in trades: '+event['signal_utc']);break
                t=trades[trade_idx]
                if t['entry_open_utc']!=event['entry_open_utc'] or t['side']!=side:
                    errors.append('Trade mismatch at '+event['entry_open_utc']);break
                exit_time=datetime.fromisoformat(t['exit_open_utc'].replace('Z','+00:00'))
                exit_idx=ent
                while exit_idx<len(b15) and b15[exit_idx].t<exit_time:exit_idx+=1
                if exit_idx>=len(b15) or b15[exit_idx].t!=exit_time:
                    errors.append('Exit time not on candle: '+t['exit_open_utc']);break
                if exit_idx>min(ent+max_bars-1,len(b15)-1):errors.append('Exit beyond max holding for '+event['signal_utc'])
                next_free=exit_idx+1
                equity=float(t['equity_after']);trade_idx+=1
                reason='EXECUTED'
                event['trade_number']=trade_idx
        event['classification']=reason
        if reason!='EXECUTED':event['explanation']={
            'BEFORE_EVALUATION':'Candidate generated during indicator warm-up, before evaluation window.',
            'POSITION_OPEN':'A previous position had not yet completed its exit candle; one position at a time.',
            'INVALID_STOP':'Stop is not on the protective side of entry.',
            'NO_CAPITAL':'Risk/available capital yields no positive quantity.'}[reason]
        counts[reason]+=1;details.append(event)
    if trade_idx!=len(trades):errors.append(f'Only reconciled {trade_idx}/{len(trades)} ledger trades')
    return {'schema':'stc01-skipped-candidates-audit-v1','audit_status':'PASS_WITH_LIMITATIONS' if not errors else 'FAIL',
            'candidate_count':len(details),'trades_reported':len(trades),'trades_reconciled':trade_idx,
            'rejected_count':len(details)-trade_idx,'classification_counts':dict(counts), 'errors':errors,
            'details':details, 'limitations':['Uses the same technical signal generator as the strategy; independent signal math is checked in audit_signals_stc01.py.','Replay uses recorded exit timestamps and equity from the trade ledger; this is not an independent fill/PnL audit.','Does not validate intrabar drawdown, exchange prices or futures funding.']}


def main():
    p=argparse.ArgumentParser();
    for arg in ('m15','h1','h4','result','output'):p.add_argument('--'+arg,required=True)
    a=p.parse_args();r=audit(a.m15,a.h1,a.h4,a.result)
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in r.items() if k!='details'},indent=2))
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
