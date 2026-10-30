"""STC-02 v1 research backtest. Spot LONG only. Never submits orders."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from strategy_stc02 import signals
# Reuse the *CSV reader* from STC-01 without altering STC-01.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'stc01'))
from strategy_stc01 import load_csv


def run(b15,b1,b4,evaluation_start,capital=10000.,risk_fraction=.005,
        fee_bps=10.,slippage_bps=5.,max_holding_bars=96):
    if capital<=0 or not 0<risk_fraction<=.05 or fee_bps<0 or slippage_bps<0 or max_holding_bars<1:
        raise ValueError('Invalid risk/cost inputs')
    fee=fee_bps/10000; slip=slippage_bps/10000
    equity=capital; peak=capital; dd=0.; next_free=0; trades=[]; candidates=0
    for s in signals(b15,b1,b4):
        e=s['entry_index']; candidates+=1
        if b15[e].t<evaluation_start or e<next_free: continue
        entry=b15[e].o*(1+slip)
        stop=entry-1.5*s['atr']
        if stop<=0: continue
        risk_per_unit=entry-stop+entry*fee+stop*fee+stop*slip
        qty=min(equity*risk_fraction/risk_per_unit, equity/(entry*(1+fee)))
        if qty<=0: continue
        target=entry+2*(entry-stop)
        end=min(e+max_holding_bars-1,len(b15)-1)
        exit_idx=end; reason='TIME'; exit_price=b15[end].c*(1-slip)
        for i in range(e,end+1):
            b=b15[i]
            # Conservative: price gaps through stop are filled at worse opening price.
            if b.l<=stop:
                exit_idx=i;exit_price=min(stop,b.o)*(1-slip);reason='STOP';break
            if b.h>=target:
                exit_idx=i;exit_price=target*(1-slip);reason='TARGET';break
        pnl=(exit_price-entry)*qty-(entry+exit_price)*qty*fee
        before=equity; equity+=pnl; peak=max(peak,equity);dd=max(dd,1-equity/peak)
        trades.append({'signal_at_utc':s['signal_at'].isoformat(),
            'context_4h_closed_utc':s['context_4h_close'].isoformat(),
            'entry_open_utc':b15[e].t.isoformat(),
            'exit_open_utc':b15[exit_idx].t.isoformat(),
            'entry':round(entry,8),'stop':round(stop,8),'target':round(target,8),
            'exit':round(exit_price,8),'quantity':round(qty,8),'reason':reason,
            'equity_before':round(before,4),'pnl_net':round(pnl,4),
            'equity_after':round(equity,4),'atr_1h':round(s['atr'],8),
            'volume_ratio_1h':round(s['volume_ratio'],5)})
        next_free=exit_idx+1
    profits=sum(max(0,t['pnl_net']) for t in trades)
    losses=sum(max(0,-t['pnl_net']) for t in trades)
    wins=sum(t['pnl_net']>0 for t in trades)
    return {'schema':'stc02-breakout-backtest-v1','market':'binance_spot',
        'evaluation_start_utc':evaluation_start.isoformat(),
        'starting_equity':capital,'ending_equity':round(equity,4),
        'net_pnl':round(equity-capital,4),'return_pct':round(100*(equity/capital-1),4),
        'candidate_count_including_warmup':candidates,'trades_count':len(trades),
        'wins':wins,'win_rate_pct':round(100*wins/len(trades),4) if trades else None,
        'profit_factor':round(profits/losses,4) if losses else None,
        'closed_trades_drawdown_pct':round(100*dd,4),
        'assumptions':{'strategy':'STC-02 v1 fixed: 4h EMA200 rising, 1h 20-high breakout, volume >=1.5, 1.5 ATR14 stop, 2R target',
          'trade_direction':'LONG only','fee_bps_each_side':fee_bps,
          'slippage_bps_each_side':slippage_bps,'intrabar':'stop first if both hit; gaps at adverse open',
          'holding_limit_15m_bars':max_holding_bars,'position_limit':'one open position',
          'drawdown':'closed positions only; intratrade drawdown not measured',
          'exchange_filters':'tick/lot/min-notional not modelled',
          'certification':'UNVALIDATED - exploratory backtest, no independent audit'},
        'trades':trades}


def main():
    p=argparse.ArgumentParser()
    for tf in ('15m','1h','4h'):p.add_argument('--'+tf,required=True)
    p.add_argument('--evaluation-start',default='2025-10-01T00:00:00Z')
    p.add_argument('--output',default='stc02-result.json')
    a=p.parse_args()
    when=datetime.fromisoformat(a.evaluation_start.replace('Z','+00:00'))
    if when.tzinfo is None or when.utcoffset().total_seconds()!=0:
        raise ValueError('evaluation-start must be UTC')
    data={tf:load_csv(getattr(a,tf),tf) for tf in ('15m','1h','4h')}
    result=run(data['15m'],data['1h'],data['4h'],when)
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='trades'},indent=2))

if __name__=='__main__':main()
