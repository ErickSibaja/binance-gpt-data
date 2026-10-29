
"""Conservative STC-01 OHLCV backtest. Educational research; no trading access."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from strategy_stc01 import load_csv, aligned_signals


def run(b15,b1,b4,market='spot',starting_equity=10000.0,risk_fraction=0.005,
        fee_bps=10.0,slippage_bps=5.0, max_holding_bars=96, evaluation_start=None):
    if starting_equity<=0 or not 0<risk_fraction<=0.05 or fee_bps<0 or slippage_bps<0:
        raise ValueError('invalid capital/risk/cost parameters')
    if market not in ('spot','bitget_usdt_futures'):
        raise ValueError('unsupported market')

    # Futures funding is not simulated. These results are not net of funding.
    signals=aligned_signals(b15,b1,b4,allow_short=(market=='bitget_usdt_futures'))
    equity=starting_equity
    peak=equity
    max_dd=0.0
    trades=[]
    next_free=0
    slip=slippage_bps/10000
    fee=fee_bps/10000

    for idx, side, stop in signals:
        ent=idx+1
        if evaluation_start is not None and b15[ent].t < evaluation_start: continue
        if ent<next_free or ent>=len(b15):continue

        direction=1 if side=='LONG' else -1
        entry=b15[ent].o*(1+slip*direction)
        unit_risk=direction*(entry-stop)
        if unit_risk<=0: continue

        # Risk capital includes rough round-trip trading fees and adverse execution cost
        risk_per_unit=unit_risk+entry*fee+stop*fee+stop*slip

        # SPOT FIX:
        # Reserve entry commission within available equity.
        # A Spot purchase must not require more cash than available.
        qty = min(
            (equity * risk_fraction) / risk_per_unit,
            equity / (entry * (1 + fee))
        ) if market == 'spot' else (equity * risk_fraction) / risk_per_unit

        if qty<=0:continue

        target=entry+direction*2*unit_risk
        last=min(ent+max_holding_bars-1,len(b15)-1)
        exit_i=last
        exit_price=None
        exit_reason='TIME'

        for t in range(ent,last+1):
            bar=b15[t]
            hit_stop=(bar.l<=stop if side=='LONG' else bar.h>=stop)
            hit_target=(bar.h>=target if side=='LONG' else bar.l<=target)

            # Conservative handling: adverse gaps at open; stop wins if both touched.
            if hit_stop:
                raw=min(stop,bar.o) if side=='LONG' else max(stop,bar.o)
                exit_price=raw*(1-slip*direction)
                exit_i=t;exit_reason='STOP';break

            if hit_target:
                exit_price=target*(1-slip*direction)
                exit_i=t;exit_reason='TARGET';break

        if exit_price is None:
            exit_price=b15[last].c*(1-slip*direction)

        gross=direction*(exit_price-entry)*qty
        fees=(entry+exit_price)*qty*fee
        pnl=gross-fees
        equity+=pnl
        peak=max(peak,equity)
        max_dd=max(max_dd,(peak-equity)/peak)

        trades.append({
            'signal_closed_utc':b15[idx].close_time('15m').isoformat(),
            'entry_open_utc':b15[ent].t.isoformat(),
            'exit_open_utc':b15[exit_i].t.isoformat(),
            'side':side,
            'entry':round(entry,8),
            'stop':round(stop,8),
            'target':round(target,8),
            'exit':round(exit_price,8),
            'quantity':round(qty,8),
            'exit_reason':exit_reason,
            'pnl_net':round(pnl,4),
            'equity_after':round(equity,4)
        })

        next_free=exit_i+1

    wins=sum(t['pnl_net']>0 for t in trades)
    gains=sum(max(0,t['pnl_net']) for t in trades)
    losses=sum(max(0,-t['pnl_net']) for t in trades)

    return {
        'market':market,
        'starting_equity':starting_equity,
        'ending_equity':round(equity,4),
        'trades_count':len(trades),
        'wins':wins,
        'win_rate':round(wins/len(trades),4) if trades else None,
        'net_pnl':round(equity-starting_equity,4),
        'profit_factor':round(gains/losses,4) if losses else None,
        'max_drawdown_fraction_closed_trades':round(max_dd,6),
        'expectancy_currency_per_trade':round(
            (equity-starting_equity)/len(trades),4
        ) if trades else None,
        'assumptions':{
            'entry':'next 15m bar open',
            'ambiguous_bar':'stop first',
            'same_bar_exit':'allowed',
            'fee_bps_per_side':fee_bps,
            'slippage_bps_per_side':slippage_bps,
            'funding':'NOT INCLUDED; futures results are incomplete',
            'max_holding_15m_bars':max_holding_bars,
            'spot_shorts':'disabled',
            'drawdown':'closed-trade equity only; intratrade exposure omitted'
        },
        'trades':trades
    }


def main():
    ap=argparse.ArgumentParser(
        description='STC-01 v1 backtest: 3 actual OHLCV CSV files'
    )

    for tf in ('15m','1h','4h'):
        ap.add_argument('--'+tf,required=True)

    ap.add_argument(
        '--market',
        choices=['spot','bitget_usdt_futures'],
        required=True
    )
    ap.add_argument('--fee-bps',type=float,default=10)
    ap.add_argument('--slippage-bps',type=float,default=5)
    ap.add_argument('--capital',type=float,default=10000)
    ap.add_argument('--output',default='stc01-result.json')
    ap.add_argument(
        '--evaluation-start',
        default=None,
        help='UTC start of scoring period; older bars only warm up indicators'
    )

    args=ap.parse_args()

    data={
        tf:load_csv(getattr(args,tf),tf)
        for tf in ('15m','1h','4h')
    }

    evaluation_start=None

    if args.evaluation_start:
        evaluation_start=datetime.fromisoformat(
            args.evaluation_start.replace('Z','+00:00')
        )
        if (
            evaluation_start.tzinfo is None
            or evaluation_start.utcoffset().total_seconds()!=0
        ):
            raise ValueError('evaluation start must be UTC')

    result=run(
        data['15m'],
        data['1h'],
        data['4h'],
        market=args.market,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        starting_equity=args.capital,
        evaluation_start=evaluation_start
    )

    result['evaluation_start_utc']=args.evaluation_start
    result['historical_first_utc']=data['15m'][0].t.isoformat()
    result['historical_end_exclusive_utc']=(
        data['15m'][-1].close_time('15m')
    ).isoformat()

    Path(args.output).write_text(
        json.dumps(result,indent=2)+'\n',
        encoding='utf-8'
    )

    print(json.dumps(
        {k:v for k,v in result.items() if k!='trades'},
        indent=2
    ))
    print('Written:',args.output)


if __name__=='__main__':
    main()
