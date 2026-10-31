"""Prospective STC04 paper ledger. No trading endpoints, no authentication.
A new daily candle is only processed AFTER its close; its open fill is an
observational simulation, not a fill known or attainable in real time.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from strategy_stc04 import load_csv

SYMBOLS=('BTCUSDT','ETHUSDT')
FEE=.001
SLIP=.0005
CAPITAL=10000.0
WEIGHT=.25

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()

def signal(bars,index):
    if index<199: raise ValueError('Missing SMA warmup')
    sma=sum(b.c for b in bars[index-199:index+1])/200
    mom=bars[index].c/bars[index-60].c-1
    return bars[index].c>sma and mom>0

def read_ledger(path):
    if not path.exists():return []
    rows=[json.loads(s) for s in path.read_text().splitlines() if s.strip()]
    previous='GENESIS'
    for index,row in enumerate(rows):
        if row.get('previous_hash')!=previous:raise ValueError('Ledger hash-chain broken at '+str(index))
        content={k:v for k,v in row.items() if k!='record_hash'}
        if digest(content)!=row.get('record_hash'):raise ValueError('Ledger content tampered at '+str(index))
        previous=row['record_hash']
    return rows

def record(rows,event):
    item={**event,'previous_hash':rows[-1]['record_hash'] if rows else 'GENESIS'}
    item['record_hash']=digest(item)
    rows.append(item)

def evaluate(csvs,state_dir,asof):
    bars={s:load_csv(csvs[s]) for s in SYMBOLS}
    stamps=[b.t for b in bars['BTCUSDT']]
    if stamps!=[b.t for b in bars['ETHUSDT']]:raise ValueError('BTC/ETH daily timestamps differ')
    if stamps[-1]+timedelta(days=1)>asof:raise ValueError('Unclosed daily candle detected')
    if (asof-stamps[-1])>timedelta(days=3):raise ValueError('Market data too stale')
    state_dir.mkdir(parents=True,exist_ok=True)
    ledger_path=state_dir/'ledger.jsonl'
    rows=read_ledger(ledger_path)
    if not rows:
        # Initialize at last fully closed daily candle. NO backfilled profits.
        record(rows,{'event':'INITIALIZE','observed_at_utc':asof.isoformat(),
                     'last_processed_candle_utc':stamps[-1].isoformat(),
                     'starting_cash':CAPITAL,'exposure_limit_per_asset':WEIGHT,
                     'execution_model':'retrospective next-open paper fills, registered after candle close'})
    start_index=stamps.index(datetime.fromisoformat(rows[0]['last_processed_candle_utc']))
    last_seen=rows[-1].get('candle_utc', rows[0]['last_processed_candle_utc'])
    if datetime.fromisoformat(last_seen) not in stamps:raise ValueError('Ledger timestamp not in verified candles')
    cash=CAPITAL;units={s:0.0 for s in SYMBOLS};peak=CAPITAL;drawdown=0.0
    # Reconstruct ledger economic state and hash-chain; never trust stored balances alone.
    for r in rows:
        if r['event']=='DAY':
            for tx in r['transactions']:
                s=tx['symbol'];q=tx['quantity'];p=tx['price'];fee=tx['fee']
                if tx['side']=='BUY':cash-=q*p+fee;units[s]+=q
                else:cash+=q*p-fee;units[s]-=q
            if abs(cash-r['cash'])>1e-5:raise ValueError('Cash ledger mismatch')
            for s in SYMBOLS:
                if abs(units[s]-r['units'][s])>1e-9:raise ValueError('Units ledger mismatch')
            peak=max(peak,r['equity_close'])
            drawdown=max(drawdown,(peak-r['equity_close'])/peak)
    n_new=0
    for i in range(stamps.index(datetime.fromisoformat(last_seen))+1,len(stamps)):
        # decision from previous closed candle; execution at current candle open, registered after its close
        txs=[];decisions={s:signal(bars[s],i-1) for s in SYMBOLS}
        # sell before buying to free cash; never rebalance existing position
        for s in SYMBOLS:
            b=bars[s][i]
            if units[s]>1e-12 and not decisions[s]:
                price=b.o*(1-SLIP);q=units[s];fee=q*price*FEE
                txs.append({'side':'SELL','symbol':s,'price':price,'quantity':q,'fee':fee})
                cash+=q*price-fee;units[s]=0.0
        # Fixed budget per asset of initial capital, not duplicated cash allocation.
        for s in SYMBOLS:
            b=bars[s][i]
            if units[s]<1e-12 and decisions[s]:
                budget=min(CAPITAL*WEIGHT,cash)
                if budget<=0:raise ValueError('No cash for allocation')
                price=b.o*(1+SLIP);q=budget/(price*(1+FEE));fee=q*price*FEE
                txs.append({'side':'BUY','symbol':s,'price':price,'quantity':q,'fee':fee})
                cash-=q*price+fee;units[s]=q
        equity=cash+sum(units[s]*bars[s][i].c for s in SYMBOLS)
        peak=max(peak,equity);drawdown=max(drawdown,(peak-equity)/peak)
        record(rows,{'event':'DAY','candle_utc':stamps[i].isoformat(),
                     'observed_after_close_utc':(stamps[i]+timedelta(days=1)).isoformat(),
                     'signals_from_utc':stamps[i-1].isoformat(), 'active':decisions,
                     'transactions':txs,'cash':round(cash,8),
                     'units':{s:units[s] for s in SYMBOLS},
                     'equity_close':round(equity,8),
                     'max_drawdown_daily_close_pct':round(drawdown*100,5)})
        n_new+=1
    # Initialize / no new days: report 10K equity, or mark last verified candle
    last=bars['BTCUSDT'][-1].t
    last_index=stamps.index(last)
    equity=cash+sum(units[s]*bars[s][last_index].c for s in SYMBOLS)
    # prospective buy-hold benchmark is only possible with new candles; don't pretend initial investment
    days=[r for r in rows if r['event']=='DAY']
    benchmark={}
    first_index=start_index+1
    if days and first_index<len(stamps):
        for s in SYMBOLS:
            init=CAPITAL*WEIGHT
            qty=init/(bars[s][first_index].o*(1+SLIP)*(1+FEE))
            benchmark[s]=round(qty*bars[s][-1].c,6)
    report={'schema':'stc04-paper-suite-v1','status':'INITIALIZED' if not days else 'ACTIVE',
            'new_candles_processed':n_new,'prospective_candles_total':len(days),
            'equity_marked':round(equity,4),'return_pct':round(100*(equity/CAPITAL-1),4),
            'max_drawdown_daily_close_pct':round(drawdown*100,4),
            'cash':round(cash,4),'units':units,'last_closed_candle_utc':last.isoformat(),
            'record_count':len(rows),'ledger_tip_sha256':rows[-1]['record_hash'],
            'benchmark_passive_components_marked':benchmark,
            'limitations':['Fills at next candle open are registered only after that daily candle closes; not real-time executable fills.',
                           'One joint portfolio and no actual orders. No intraday drawdown or gap protection.',
                           'SHA-256 detects changes; Git history requires branch protection to resist rewriting.']}
    ledger_path.write_text(''.join(canonical(r)+'\n' for r in rows))
    (state_dir/'paper-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--btc',required=True);p.add_argument('--eth',required=True)
    p.add_argument('--state-dir',required=True);p.add_argument('--asof',default=None)
    a=p.parse_args(); now=datetime.fromisoformat(a.asof.replace('Z','+00:00')) if a.asof else datetime.now(timezone.utc)
    print(json.dumps(evaluate({'BTCUSDT':a.btc,'ETHUSDT':a.eth},Path(a.state_dir),now),indent=2))
if __name__=='__main__':main()
