"""Single seven-stage STC04 coordinator; strictly read-only against Binance."""
import argparse,json,subprocess,sys,hashlib
from datetime import datetime,timezone,timedelta
from pathlib import Path
from paper_suite import evaluate

SYMBOLS=('BTCUSDT','ETHUSDT')
WINDOWS=(('2024-2025','2024-10-01T00:00:00Z','2025-10-01T00:00:00Z'),('2025-2026','2025-10-01T00:00:00Z','2026-10-01T00:00:00Z'))
HERE=Path(__file__).resolve().parent

def call(script,*args):
    subprocess.run([sys.executable,str(HERE/script),*map(str,args)],check=True,cwd=HERE)

def jsonload(p):return json.loads(p.read_text())

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['full','daily'],required=True)
    p.add_argument('--state-dir',required=True);p.add_argument('--output-dir',required=True)
    p.add_argument('--data-dir',default=None);p.add_argument('--asof',default=None)
    a=p.parse_args();out=Path(a.output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    state=Path(a.state_dir).resolve();state.mkdir(parents=True,exist_ok=True)
    data=Path(a.data_dir).resolve() if a.data_dir else out/'history';data.mkdir(parents=True,exist_ok=True)
    now=datetime.fromisoformat(a.asof.replace('Z','+00:00')) if a.asof else datetime.now(timezone.utc)
    cutoff=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if a.mode=='full' and cutoff<datetime(2026,10,1,tzinfo=timezone.utc):raise ValueError('Historical end not yet closed')
    stages={str(i):{'name':name,'status':'PENDING'} for i,name in enumerate(('Backtesting','Independent audit','Sensitivity','Prospective paper trading','Prospective performance','Risk controls','Readiness decision'),1)}
    notes=[]
    try:
        if a.mode=='full':
            base=out/'baseline';base.mkdir(exist_ok=True)
            for s in SYMBOLS:
                call('fetch_daily_stc04.py','--symbol',s,'--start','2024-01-01T00:00:00Z','--end','2026-10-01T00:00:00Z','--outdir',base)
                csv=base/(s+'_spot_1d.csv');manifest=base/(s+'-manifest.json')
                for label,start,end in WINDOWS:
                    result=base/(s+'-'+label+'.json');aud=base/(s+'-'+label+'-audit.json');sens=base/(s+'-'+label+'-sensitivity.json')
                    call('backtest_stc04.py','--csv',csv,'--evaluation-start',start,'--evaluation-end',end,'--output',result)
                    call('audit_stc04.py','--csv',csv,'--manifest',manifest,'--result',result,'--output',aud)
                    if jsonload(aud)['audit_status']!='PASS_WITH_LIMITATIONS':raise ValueError('Audit failed '+str(aud))
                    call('sensitivity_stc04.py','--csv',csv,'--evaluation-start',start,'--evaluation-end',end,'--output',sens)
                    if not jsonload(sens).get('base_reproduces_original',False):
                        # check report structure below; fail closed rather than silently approve
                        raise ValueError('Sensitivity baseline mismatch '+str(sens))
            for n in ('1','2','3'):stages[n]['status']='PASS_WITH_LIMITATIONS' if n=='2' else 'PASS'
            stamp={'schema':'stc04-baseline-v1','generated_utc':now.isoformat(),'windows':[w[0] for w in WINDOWS],
                   'status':'PASS_WITH_LIMITATIONS','strategy_sha256':hashlib.sha256((HERE/'strategy_stc04.py').read_bytes()).hexdigest(),
                   'backtest_sha256':hashlib.sha256((HERE/'backtest_stc04.py').read_bytes()).hexdigest(),
                   'audit_sha256':hashlib.sha256((HERE/'audit_stc04.py').read_bytes()).hexdigest(),
                   'sensitivity_sha256':hashlib.sha256((HERE/'sensitivity_stc04.py').read_bytes()).hexdigest()}
            (state/'baseline-certification.json').write_text(json.dumps(stamp,indent=2)+'\n')
        else:
            cert=state/'baseline-certification.json'
            if not cert.exists():raise ValueError('Run full mode once before daily mode')
            v=jsonload(cert)
            if v.get('status')!='PASS_WITH_LIMITATIONS':raise ValueError('Baseline not certified')
            for key,filename in [('strategy_sha256','strategy_stc04.py'),('backtest_sha256','backtest_stc04.py'),('audit_sha256','audit_stc04.py'),('sensitivity_sha256','sensitivity_stc04.py')]:
                if hashlib.sha256((HERE/filename).read_bytes()).hexdigest()!=v.get(key):raise ValueError('Baseline code changed: run full mode again: '+filename)
            for n in ('1','2','3'):stages[n]['status']='CACHED_VERIFIED'
        # fresh data always fetched; never historical snapshots used for paper
        for s in SYMBOLS:
            call('fetch_daily_stc04.py','--symbol',s,'--start','2024-01-01T00:00:00Z',
                 '--end',cutoff.strftime('%Y-%m-%dT00:00:00Z'),'--outdir',data)
        report=evaluate({s:data/(s+'_spot_1d.csv') for s in SYMBOLS},state,now)
        stages['4']['status']=report['status']
        days=report['prospective_candles_total']
        # Fixed predeclared readiness gate: no auto-approval to operate real money
        stages['5']['status']='INSUFFICIENT_DATA' if days<180 else 'REVIEW_REQUIRED'
        allocation_marked=sum(report['units'][s]*__import__('strategy_stc04').load_csv(data/(s+'_spot_1d.csv'))[-1].c for s in SYMBOLS)
        stages['6']={'name':'Risk controls','status':'PASS_WITH_LIMITATIONS' if report['cash']>=-1e-5 and allocation_marked<=10000*2 else 'FAIL',
                     'daily_drawdown_pct':report['max_drawdown_daily_close_pct'],
                     'risk_note':'Daily marks only, position caps based on initial capital; no intraday stop, slippage shock or exchange filters.'}
        stages['7']['status']='NOT_READY';notes.append('Stage 7 never authorizes orders; manual committee review and external execution checks required.')
    except Exception as exc:
        notes.append(type(exc).__name__+': '+str(exc));stages['7']['status']='BLOCKED'
        summary={'schema':'stc04-validation-suite-v1','mode':a.mode,'generated_utc':now.isoformat(),'stages':stages,'errors':notes}
        (out/'suite-report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
        raise
    summary={'schema':'stc04-validation-suite-v1','mode':a.mode,'generated_utc':now.isoformat(),'stages':stages,'paper':report,'notes':notes,
             'explicit_limit':'Paper monitoring is not live exchange execution; historical PASS does not prove future profitability.'}
    (out/'suite-report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'mode':a.mode,'stage_statuses':{k:v['status'] for k,v in stages.items()},'paper_days':days},indent=2))
if __name__=='__main__':main()
