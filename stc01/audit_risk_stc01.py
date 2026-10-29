"""STC-01 risk audit on 15m OHLCV, independently reading recorded trades.
Research only. Intrabar path unknown; extrema are stress bounds, not tick equity.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from strategy_stc01 import load_csv


def dt(s):
    x=datetime.fromisoformat(s.replace('Z','+00:00'))
    if x.tzinfo is None: raise ValueError('timestamp without timezone')
    return x.astimezone(timezone.utc)


def audit(csv15, result_file, output_file):
    bars=load_csv(csv15,'15m')
    lookup={b.t:i for i,b in enumerate(bars)}
    result=json.loads(Path(result_file).read_text(encoding='utf-8'))
    trades=result.get('trades')
    if not isinstance(trades,list): raise ValueError('missing trades array')
    capital=float(result['starting_equity'])
    fee=float(result['assumptions']['fee_bps_per_side'])/10000
    slip=float(result['assumptions']['slippage_bps_per_side'])/10000
    equity=capital; peak=capital; worst=0.; max_exposure=0.; rows=[]; errors=[]
    last_exit_idx=-1
    def note(value):
        nonlocal peak,worst
        peak=max(peak,value)
        worst=max(worst,(peak-value)/peak)
    for num,t in enumerate(trades,1):
        side=t['side']; direction={'LONG':1,'SHORT':-1}.get(side)
        if direction is None: errors.append(f'{num}: invalid side'); continue
        start=lookup.get(dt(t['entry_open_utc'])); end=lookup.get(dt(t['exit_open_utc']))
        if start is None or end is None or start>end or start<=last_exit_idx:
            errors.append(f'{num}: missing/overlapping or invalid trade time'); continue
        last_exit_idx=end
        entry=float(t['entry']); qty=float(t['quantity']); px_exit=float(t['exit'])
        if entry<=0 or qty<=0 or px_exit<=0: errors.append(f'{num}: invalid prices/quantity');continue
        if result['market']=='spot' and side!='LONG': errors.append(f'{num}: short in spot'); continue
        eq_before=equity
        entry_fee=entry*qty*fee
        cost=entry*qty
        max_exposure=max(max_exposure,cost)
        if result['market']=='spot' and cost>eq_before+0.02:
            errors.append(f'{num}: position exceeds spot cash')
        trade_worst=0.; worst_value=None
        for i in range(start,end+1):
            b=bars[i]
            # Full 15m bar extremes before recorded exit may contain prices AFTER exit.
            # For exit bar, use only open and execution exit; omit high/low to avoid post-exit look-ahead.
            # Entry bar includes high/low because exit bar is handled separately.
            if i==end:
                marks=[b.o,px_exit]
            else:
                marks=[b.o,b.h,b.l,b.c]
            for p in marks:
                # Mark-to-market liquidation at pessimistic execution cost, inclusive of entry fee.
                hypothetical_exit=p*(1-slip*direction)
                estimated_exit_fee=hypothetical_exit*qty*fee
                marked=eq_before-entry_fee+direction*(hypothetical_exit-entry)*qty-estimated_exit_fee
                if marked<0: errors.append(f'{num}: negative simulated equity')
                note(marked)
                adverse=(eq_before-marked)/eq_before if eq_before else 0
                if adverse>trade_worst:
                    trade_worst=adverse;worst_value={'bar_open_utc':b.t.isoformat(),'mark_price':p,'estimated_equity':round(marked,4)}
        pnl=direction*(px_exit-entry)*qty-(entry+px_exit)*qty*fee
        equity=eq_before+pnl
        note(equity)
        if abs(equity-float(t['equity_after']))>0.15:
            errors.append(f'{num}: equity mismatch {equity} vs {t["equity_after"]}')
        rows.append({'trade':num,'entry_open_utc':t['entry_open_utc'],'exit_open_utc':t['exit_open_utc'],
                     'side':side,'quantity':qty,'notional_at_entry':round(cost,4),
                     'equity_before':round(eq_before,4),'equity_after_recalculated':round(equity,4),
                     'maximum_adverse_marked_drawdown_from_entry_fraction':round(trade_worst,6),
                     'worst_observed_mark':worst_value})
    if len(rows)!=len(trades):errors.append('not all trades evaluated')
    if abs(equity-float(result['ending_equity']))>0.15:errors.append('final equity mismatch')
    report={
        'schema':'stc01-intratrade-risk-audit-v1',
        'audit_status':'FAIL' if errors else 'PASS_WITH_LIMITATIONS',
        'market':result.get('market'),'trades_audited':len(rows),
        'starting_equity':capital,'ending_equity_reproduced':round(equity,4),
        'reported_closed_trade_drawdown_fraction':result.get('max_drawdown_fraction_closed_trades'),
        'ohlc_marked_drawdown_fraction':round(worst,6),
        'max_entry_notional':round(max_exposure,4),
        'max_entry_notional_pct_of_starting_equity':round(100*max_exposure/capital,2),
        'errors':errors,
        'limitations':[
            'Uses 15-minute OHLC, not tick or order-book data; exact intrabar path is unknown.',
            'For each exit candle only open and recorded exit are marked; its high/low are excluded to avoid including post-exit prices, potentially understating excursion.',
            'All non-exit-candle high/low observations are stressed as if reachable; their chronological order is unknown, so drawdown is a conservative OHLC estimate, not an exact live equity path.',
            'Reported exit is assumed to occur at the simulator price; no independent trade execution validation.',
            'Mark-to-market estimates exit slippage and fee at each hypothetical price; funding/borrowing, liquidation and margin are excluded.',
            'Peaks and troughs within the same candle may have occurred in reverse order; drawdown is an upper-envelope stress estimate.',
            'Spot inventory and free-cash treatment is approximated by marked liquidation equity.'
        ],'per_trade':rows}
    Path(output_file).parent.mkdir(parents=True,exist_ok=True)
    Path(output_file).write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='per_trade'},indent=2,ensure_ascii=False))
    if errors: raise SystemExit(1)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--m15',required=True)
    p.add_argument('--result',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    audit(a.m15,a.result,a.output)
