"""Independent STC-04 daily backtest auditor; Python stdlib only.
No imports from strategy_stc04 or backtest_stc04. Read-only, no orders.
"""
import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def dt(s):
    x = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if x.utcoffset() != timedelta(0):
        raise ValueError('Non-UTC or naive timestamp: ' + s)
    return x


def near(a, b, abs_tol=0.0002, rel_tol=1e-9):
    return math.isclose(float(a), float(b), abs_tol=abs_tol, rel_tol=rel_tol)


def audit(csv_path, result_path, manifest_path=None):
    errors = []
    checks = {}
    raw = Path(csv_path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(Path(manifest_path).read_text()) if manifest_path else None
    if manifest is not None:
        checks['historical_csv_sha256_matches_manifest'] = digest == manifest.get('sha256')
        if not checks['historical_csv_sha256_matches_manifest']:
            errors.append('CSV SHA256 differs from manifest')
    else:
        checks['historical_csv_sha256_matches_manifest'] = None
    with Path(csv_path).open(newline='', encoding='utf8') as fh:
        rows = list(csv.DictReader(fh))
    bars = []
    for row in rows:
        t = dt(row['open_time_utc'])
        o, h, l, c, v = (float(row[k]) for k in ('open','high','low','close','volume'))
        if not (0 < l <= min(o,c) <= max(o,c) <= h and v >= 0):
            errors.append('Invalid OHLCV at '+t.isoformat())
        if bars and t - bars[-1][0] != timedelta(days=1):
            errors.append('Missing or duplicate day near '+t.isoformat())
        bars.append((t,o,h,l,c,v))
    report=json.loads(Path(result_path).read_text())
    start=dt(report['evaluation_start_utc'])
    end=dt(report['evaluation_end_exclusive_utc'])
    capital=float(report['starting_equity'])
    fee=float(report['assumptions']['fee_bps_each_side'])/10000
    slip=float(report['assumptions']['slippage_bps_each_side'])/10000
    cash=capital
    qty=0.0
    expected_transactions=[]
    expected_curve=[]
    excluded_days=0
    # Independent indicator implementation: only closes up through i-1 at day i open.
    for i, bar in enumerate(bars):
        t, o, h, l, c, v = bar
        if not (start <= t < end):
            continue
        if i < 200:
            errors.append('Insufficient historical warmup at '+t.isoformat())
            continue
        prior_closes=[r[4] for r in bars[i-200:i]]
        sma=sum(prior_closes)/200
        momentum=prior_closes[-1]/prior_closes[-61]-1
        should_hold=prior_closes[-1]>sma and momentum>0
        if should_hold and qty == 0:
            entry=o*(1+slip)
            qty=cash/(entry*(1+fee))
            fee_amount=qty*entry*fee
            cost=qty*entry+fee_amount
            if cost>cash+1e-8:
                errors.append('Purchase exceeds free cash: '+t.isoformat())
            cash=max(0.0,cash-cost)
            expected_transactions.append(dict(type='BUY',at_utc=t.isoformat(),signal_at_utc=t.isoformat(),price=entry,quantity=qty,fee=fee_amount))
        elif not should_hold and qty > 0:
            exit_price=o*(1-slip)
            fee_amount=qty*exit_price*fee
            cash+=qty*exit_price-fee_amount
            expected_transactions.append(dict(type='SELL',at_utc=t.isoformat(),signal_at_utc=t.isoformat(),price=exit_price,quantity=qty,fee=fee_amount))
            qty=0.0
        expected_curve.append(dict(at_utc=t.isoformat(),equity_close_mark=cash+qty*c))
    if not expected_curve:
        errors.append('No evaluation candles')
        return {'audit_status':'FAIL','errors':errors}
    final=expected_curve[-1]['equity_close_mark']
    first=next(b for b in bars if start<=b[0]<end)
    last=next(b for b in reversed(bars) if start<=b[0]<end)
    passive_qty=capital/(first[1]*(1+slip)*(1+fee))
    passive_equity=passive_qty*last[4]
    peak=capital
    drawdown=0
    for p in expected_curve:
        peak=max(peak,p['equity_close_mark'])
        drawdown=max(drawdown,(peak-p['equity_close_mark'])/peak)
    actual_tx=report.get('transactions',[])
    checks['transaction_count_matches']=len(actual_tx)==len(expected_transactions)
    if not checks['transaction_count_matches']:
        errors.append('Transaction count mismatch: expected %d, got %d'%(len(expected_transactions),len(actual_tx)))
    for n,(a,e) in enumerate(zip(actual_tx,expected_transactions),1):
        for field in ('type','at_utc','signal_at_utc'):
            if a.get(field)!=e[field]:errors.append('TX %d %s differs'%(n,field))
        for field in ('price','quantity','fee'):
            if not near(a.get(field, float('nan')),e[field],abs_tol=1e-5 if field=='fee' else 1e-7):
                errors.append('TX %d %s differs'%(n,field))
    actual_curve=report.get('daily_equity',[])
    checks['daily_curve_length_matches']=len(actual_curve)==len(expected_curve)
    if not checks['daily_curve_length_matches']:
        errors.append('Daily curve length mismatch')
    for n,(a,e) in enumerate(zip(actual_curve,expected_curve),1):
        if a.get('at_utc')!=e['at_utc'] or not near(a.get('equity_close_mark',float('nan')),e['equity_close_mark']):
            errors.append('Daily equity mismatch at index %d'%n)
            if len(errors)>25: break
    numeric_checks={
        'ending_equity_marked':final,
        'net_return_pct':100*(final/capital-1),
        'buy_and_hold_ending_marked':passive_equity,
        'buy_and_hold_return_pct':100*(passive_equity/capital-1),
        'max_drawdown_daily_close_fraction':drawdown,
        'unrealized_units':qty,
        'cash':cash,
    }
    for name,value in numeric_checks.items():
        checks[name+'_matches']=near(report.get(name,float('nan')),value,abs_tol=1e-4 if name!='max_drawdown_daily_close_fraction' else 2e-6)
        if not checks[name+'_matches']:errors.append(name+' mismatch')
    expected_round_trips=sum(t['type']=='SELL' for t in expected_transactions)
    checks['closed_round_trips_matches']=report.get('closed_round_trips')==expected_round_trips
    checks['open_position_at_end_matches']=report.get('open_position_at_end')==(qty>0)
    checks['schema_and_market_match']=report.get('schema')=='stc04-backtest-v1' and report.get('market')=='binance_spot'
    for name in ('closed_round_trips_matches','open_position_at_end_matches','schema_and_market_match'):
        if not checks[name]:errors.append(name)
    status='FAIL' if errors else 'PASS_WITH_LIMITATIONS'
    return {
        'schema':'stc04-independent-audit-v1',
        'audit_status':status,
        'source_csv_sha256':digest,
        'evaluation_start_utc':start.isoformat(),
        'evaluation_end_exclusive_utc':end.isoformat(),
        'historical_candles':len(bars),
        'evaluated_days':len(expected_curve),
        'transactions_reconstructed':len(expected_transactions),
        'closed_round_trips_reconstructed':expected_round_trips,
        'open_position_at_end_reconstructed':qty>0,
        'ending_equity_reconstructed':round(final,4),
        'buy_and_hold_ending_reconstructed':round(passive_equity,4),
        'max_drawdown_daily_close_reconstructed':round(drawdown,6),
        'checks':checks,
        'errors':errors,
        'limitations':[
            'Audits the CSV supplied; does not independently fetch and compare Binance exchange candles.',
            'Daily OHLCV cannot reconstruct intraday drawdown, order-book fills or price gaps within a session.',
            'Uses the same documented strategy specification but separate calculations; historical performance is not prospective validation.',
            'No lot-size/min-notional exchange filters, taxes, yield, funding or operational outages simulated.',
            'Open positions are marked at final close without hypothetical liquidation fees.'
        ]
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--csv',required=True)
    p.add_argument('--result',required=True)
    p.add_argument('--manifest')
    p.add_argument('--output',required=True)
    a=p.parse_args()
    try:
        report=audit(a.csv,a.result,a.manifest)
    except Exception as e:
        report={'schema':'stc04-independent-audit-v1','audit_status':'FAIL','errors':[type(e).__name__+': '+str(e)]}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    sys.exit(0 if report['audit_status']=='PASS_WITH_LIMITATIONS' else 1)

if __name__=='__main__':
    main()
