"""STC-04 sensitivity research: fixed signals, varied friction and allocation.
No orders, no API keys. Reuses official strategy signals and independently tracks cash/units.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from strategy_stc04 import load_csv, signals
from backtest_stc04 import run as original_run

SCENARIOS = (
    ('base_100', 1.0, 10.0, 5.0),
    ('high_cost_100', 1.0, 20.0, 10.0),
    ('half_50', 0.5, 10.0, 5.0),
    ('quarter_25', 0.25, 10.0, 5.0),
)

def parse_time(value):
    d=datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None or d.utcoffset().total_seconds()!=0:
        raise ValueError('Dates require UTC timezone')
    return d

def replay(bars, start, end, weight, fee_bps, slip_bps, initial=10000.0):
    if not 0 < weight <= 1 or initial <= 0 or fee_bps < 0 or slip_bps < 0:
        raise ValueError('Invalid scenario parameters')
    fee=fee_bps/10000.0
    slip=slip_bps/10000.0
    cash=initial; units=0.0; curve=[]; transactions=[]; invested_days=0
    for sig in signals(bars):
        i=sig['execute_index']; b=bars[i]
        if not(start<=b.t<end): continue
        if sig['active'] and units==0:
            entry=b.o*(1+slip)
            budget=cash*weight
            amount=budget/(entry*(1+fee))
            actual=amount*entry*(1+fee)
            if actual>cash+1e-7: raise AssertionError('Insufficient spot cash')
            cash-=actual; units=amount
            transactions.append({'type':'BUY','time':b.t.isoformat(),'price':entry,'quantity':amount,'fee':amount*entry*fee})
        elif not sig['active'] and units>0:
            price=b.o*(1-slip)
            proceeds=units*price*(1-fee)
            transactions.append({'type':'SELL','time':b.t.isoformat(),'price':price,'quantity':units,'fee':units*price*fee})
            cash+=proceeds; units=0.0
        if units>0: invested_days+=1
        curve.append(cash+units*b.c)
    evaluation=[b for b in bars if start<=b.t<end]
    if not evaluation or len(curve)!=len(evaluation):
        raise ValueError('Insufficient signals or warmup for period')
    capital_end=cash+units*evaluation[-1].c
    curve[-1]=capital_end
    peak=initial; dd=0.0
    for equity in curve:
        peak=max(peak,equity)
        dd=max(dd,(peak-equity)/peak)
    return {
        'allocation_fraction':weight,'fee_bps_each_side':fee_bps,'slippage_bps_each_side':slip_bps,
        'ending_equity_marked':round(capital_end,4),
        'net_return_pct':round(100*(capital_end/initial-1),4),
        'max_drawdown_daily_close_pct':round(dd*100,4),
        'transactions':len(transactions),'closed_round_trips':sum(x['type']=='SELL' for x in transactions),
        'open_position_at_end':units>0,'invested_days':invested_days,'evaluation_days':len(curve),
        'fraction_days_invested':round(invested_days/len(curve),6),
        'total_fees':round(sum(x['fee'] for x in transactions),4),
    }

def run_sensitivity(bars, start, end):
    scenario_results={name:replay(bars,start,end,w,f,s) for name,w,f,s in SCENARIOS}
    reference=original_run(bars,start,end,capital=10000.0,fee_bps=10.0,slippage_bps=5.0)
    base=scenario_results['base_100']
    matched=(abs(base['ending_equity_marked']-reference['ending_equity_marked'])<=0.01 and
             abs(base['max_drawdown_daily_close_pct']/100-reference['max_drawdown_daily_close_fraction'])<=1e-5 and
             base['transactions']==len(reference['transactions']))
    if not matched:
        raise AssertionError('Base sensitivity differs from frozen original STC04 backtest')
    return {'schema':'stc04-sensitivity-v1', 'period_start_utc':start.isoformat(),
            'period_end_exclusive_utc':end.isoformat(), 'base_reproduces_original':matched,
            'scenario_results':scenario_results,
            'limitations':['Exploratory historical sensitivity; not out-of-sample proof.',
                           'Entry and exit conditions frozen; different allocations are risk scenarios, not optimized choices.',
                           'No intraday drawdown, exchange lot size, taxes or guaranteed execution.',
                           'Open end positions marked to close without hypothetical exit fee.',
                           'No stop loss; capital is exposed to potentially large adverse daily moves.']}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--csv',required=True)
    parser.add_argument('--evaluation-start',required=True)
    parser.add_argument('--evaluation-end',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    report=run_sensitivity(load_csv(args.csv),parse_time(args.evaluation_start),parse_time(args.evaluation_end))
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
