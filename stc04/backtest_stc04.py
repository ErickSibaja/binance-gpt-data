"""STC-04 long-only daily backtest. No exchange account interaction."""
import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from strategy_stc04 import load_csv, signals


def run(bars,evaluation_start,evaluation_end,capital=10000.0,fee_bps=10.0,slippage_bps=5.0):
    if capital<=0 or fee_bps<0 or slippage_bps<0:raise ValueError('Invalid inputs')
    if not evaluation_start<evaluation_end:raise ValueError('Invalid evaluation period')
    fee=fee_bps/10000
    slip=slippage_bps/10000
    cash=capital; units=0.0; transactions=[]; curve=[]
    orders=signals(bars)
    for s in orders:
        i=s['execute_index']; t=bars[i].t
        if t<evaluation_start or t>=evaluation_end:continue
        active=s['active']
        if active and units==0:
            entry=bars[i].o*(1+slip)
            units=cash/(entry*(1+fee))
            cost=units*entry*(1+fee)
            if cost>cash+1e-8:raise AssertionError('Spot insufficient cash')
            cash=max(0.0,cash-cost)
            transactions.append({'type':'BUY','at_utc':t.isoformat(),'signal_at_utc':s['signal_at_utc'],
                                 'price':entry,'quantity':units,'fee':units*entry*fee})
        elif not active and units>0:
            exit_price=bars[i].o*(1-slip)
            proceeds=units*exit_price*(1-fee)
            transactions.append({'type':'SELL','at_utc':t.isoformat(),'signal_at_utc':s['signal_at_utc'],
                                 'price':exit_price,'quantity':units,'fee':units*exit_price*fee})
            cash+=proceeds; units=0.0
        curve.append({'at_utc':t.isoformat(),'equity_close_mark':cash+units*bars[i].c})
    # No fictional exit at last open. Mark to close of last evaluation day; unrealized holding reported.
    cutoff=[b for b in bars if evaluation_start<=b.t<evaluation_end]
    if not cutoff:raise ValueError('No evaluation candles')
    last=cutoff[-1]
    equity=cash+units*last.c
    if curve:curve[-1]['equity_close_mark']=equity
    peak=capital; dd=0.0
    for p in curve:
        peak=max(peak,p['equity_close_mark']);dd=max(dd,(peak-p['equity_close_mark'])/peak)
    # Passive benchmark: buy first eval day's open, mark final eval close, same assumed costs on buy;
    # no liquidation fee because end equity is marked to market (same convention as strategy).
    first=cutoff[0];buy_price=first.o*(1+slip)
    benchmark_units=capital/(buy_price*(1+fee))
    benchmark_equity=benchmark_units*last.c
    return {'schema':'stc04-backtest-v1','market':'binance_spot','evaluation_start_utc':evaluation_start.isoformat(),
            'evaluation_end_exclusive_utc':evaluation_end.isoformat(),'starting_equity':capital,
            'ending_equity_marked':round(equity,4),'net_return_pct':round(100*(equity/capital-1),4),
            'buy_and_hold_ending_marked':round(benchmark_equity,4),
            'buy_and_hold_return_pct':round(100*(benchmark_equity/capital-1),4),
            'max_drawdown_daily_close_fraction':round(dd,6),
            'closed_round_trips':sum(t['type']=='SELL' for t in transactions),
            'open_position_at_end':units>0,
            'unrealized_units':round(units,8),'cash':round(cash,6),
            'transactions':transactions,'daily_equity':curve,
            'assumptions':{'strategy':'close > SMA200 and close/close_60_days_ago - 1 > 0',
                           'decision':'closed previous daily bar', 'execution':'next daily open',
                           'fee_bps_each_side':fee_bps,'slippage_bps_each_side':slippage_bps,
                           'allocation':'100% cash when active, otherwise cash',
                           'unrealized_exit':'mark to final daily close; no fictional end sale',
                           'limits':'No tick execution, lot filters, interest, tax or real fills; daily close drawdown only'}}

def parse_utc(s):
    x=datetime.fromisoformat(s.replace('Z','+00:00'))
    if x.utcoffset()!=timedelta(0):raise ValueError('Date must include UTC')
    return x

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--csv',required=True)
    ap.add_argument('--evaluation-start',required=True);ap.add_argument('--evaluation-end',required=True)
    ap.add_argument('--capital',type=float,default=10000);ap.add_argument('--fee-bps',type=float,default=10)
    ap.add_argument('--slippage-bps',type=float,default=5);ap.add_argument('--output',required=True)
    a=ap.parse_args();b=load_csv(a.csv)
    r=run(b,parse_utc(a.evaluation_start),parse_utc(a.evaluation_end),a.capital,a.fee_bps,a.slippage_bps)
    Path(a.output).write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ('transactions','daily_equity')},indent=2))
if __name__=='__main__':main()
