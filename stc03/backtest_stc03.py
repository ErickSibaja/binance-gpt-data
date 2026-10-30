"""Research-only STC-03 backtest. Long-only, next 15m open, no live trading."""
import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
from strategy_stc03 import candidates
from strategy_stc01 import load_csv


def run(b15,b1,b4,starting_equity=10000.,risk_fraction=.005,fee_bps=10.,slippage_bps=5.,evaluation_start=None,max_bars=96):
    if starting_equity<=0 or not 0<risk_fraction<=.05 or min(fee_bps,slippage_bps)<0:raise ValueError('invalid parameters')
    cash=starting_equity; peak=cash; dd=0; trades=[]; free=0
    fee=fee_bps/10000; slip=slippage_bps/10000
    count=0
    for s in candidates(b15,b1,b4):
        count+=1
        ent=s['entry_index']
        if ent>=len(b15) or (evaluation_start and b15[ent].t<evaluation_start) or ent<free:continue
        entry=b15[ent].o*(1+slip)
        stop=entry-1.5*s['atr_1h']
        target=s['mean_1h']
        # Do not trade if expected reversion is insufficient to produce 1R gross reward.
        if stop<=0 or not (target>entry and (target-entry)>=(entry-stop)):continue
        risk_unit=entry-stop+fee*(entry+stop)+slip*stop
        qty=min(cash*risk_fraction/risk_unit,cash/(entry*(1+fee)))
        if qty<=0:continue
        max_t=min(ent+max_bars-1,len(b15)-1)
        exit_i=max_t;reason='TIME';out=b15[max_t].c*(1-slip)
        for t in range(ent,max_t+1):
            candle=b15[t]
            if candle.l<=stop: exit_i=t;reason='STOP';out=min(stop,candle.o)*(1-slip);break
            if candle.h>=target: exit_i=t;reason='TARGET';out=target*(1-slip);break
        pnl=(out-entry)*qty-(entry+out)*qty*fee
        cash+=pnl;peak=max(peak,cash);dd=max(dd,(peak-cash)/peak)
        trades.append({'signal_at_utc':s['signal_at_utc'],'entry_open_utc':b15[ent].t.isoformat(),
                       'exit_open_utc':b15[exit_i].t.isoformat(),'side':'LONG',
                       'entry':round(entry,8),'stop':round(stop,8),'target':round(target,8),
                       'exit':round(out,8),'quantity':round(qty,8),'pnl_net':round(pnl,4),
                       'equity_after':round(cash,4),'exit_reason':reason,
                       'context_1h_close':s['context_1h_close'],'context_4h_close':s['context_4h_close']})
        free=exit_i+1
    gains=sum(max(0,x['pnl_net']) for x in trades)
    losses=sum(max(0,-x['pnl_net']) for x in trades)
    return {'schema':'stc03-research-v1','market':'spot','starting_equity':starting_equity,
            'ending_equity':round(cash,4),'net_pnl':round(cash-starting_equity,4),
            'return_pct':round(100*(cash/starting_equity-1),4),
            'trades_count':len(trades),'candidate_count':count,
            'win_rate':round(sum(x['pnl_net']>0 for x in trades)/len(trades),4) if trades else None,
            'profit_factor':round(gains/losses,4) if losses else None,
            'max_drawdown_closed_fraction':round(dd,6),
            'assumptions':{'fee_bps_per_side':fee_bps,'slippage_bps_per_side':slippage_bps,
                           'entry':'next 15m open','exit':'stop-first if ambiguous',
                           'target':'1h SMA20 recorded at signal','max_hold_15m_bars':max_bars,
                           'intrabar_path':'unknown','exchange_filters':'not simulated'},
            'trades':trades}


def main():
    p=argparse.ArgumentParser()
    for tf in ('15m','1h','4h'):p.add_argument('--'+tf,required=True)
    p.add_argument('--evaluation-start',required=True)
    p.add_argument('--symbol',required=True,choices=['BTCUSDT','ETHUSDT'])
    p.add_argument('--fee-bps',type=float,default=10)
    p.add_argument('--slippage-bps',type=float,default=5)
    p.add_argument('--output',required=True)
    a=p.parse_args();data={tf:load_csv(getattr(a,tf),tf) for tf in ('15m','1h','4h')}
    start=datetime.fromisoformat(a.evaluation_start.replace('Z','+00:00'))
    if start.tzinfo is None or start.utcoffset().total_seconds()!=0:raise ValueError('UTC required')
    result=run(data['15m'],data['1h'],data['4h'],fee_bps=a.fee_bps,slippage_bps=a.slippage_bps,evaluation_start=start)
    result['symbol']=a.symbol;result['evaluation_start_utc']=start.isoformat()
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='trades'},indent=2))
if __name__=='__main__':main()
