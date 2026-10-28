#!/usr/bin/env python3
"""Auditoria independiente de latest.json (Spot) sin credenciales ni ordenes."""
import argparse
import datetime as dt
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UTC=dt.timezone.utc
INTERVALS={'15m':900,'1h':3600,'4h':14400}
URL='https://data-api.binance.vision/api/v3/klines'
DATA_URL='https://ericksibaja.github.io/binance-gpt-data/latest.json'

def get_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'binance-gpt-audit/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as resp:
        return json.load(resp)

def ema(values,n):
    if len(values)<n:return None
    a=2/(n+1)
    e=sum(values[:n])/n
    for v in values[n:]:e=a*v+(1-a)*e
    return e

def rsi_wilder(values,n=14):
    if len(values)<n+1:return None
    diffs=[values[i]-values[i-1] for i in range(1,len(values))]
    gain=sum(max(x,0) for x in diffs[:n])/n
    loss=sum(max(-x,0) for x in diffs[:n])/n
    for d in diffs[n:]:
        gain=(gain*(n-1)+max(d,0))/n
        loss=(loss*(n-1)+max(-d,0))/n
    if loss==0:return 100.0 if gain>0 else 50.0
    return 100-100/(1+gain/loss)

def iso_millis(s):
    return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)

def compare(name,actual,expected,tol,findings):
    if expected is None or actual is None or not math.isfinite(float(expected)) or abs(float(actual)-float(expected))>tol:
        findings.append(f'{name}: JSON={actual}, calculado={expected}')

def audit_series(record, now, generated):
    symbol=record['symbol']; interval=record['interval']; s=INTERVALS[interval]
    desc=f'{symbol} spot {interval}'
    issues=[]
    close_ms=iso_millis(record['last_closed_candle_close_utc'])
    params=urllib.parse.urlencode({'symbol':symbol,'interval':interval,'limit':500,'endTime':close_ms})
    try:
        rows=get_json(URL+'?'+params)
        if not isinstance(rows,list) or len(rows)<201:raise ValueError('Menos de 201 velas')
        # La ventana original consta de 499 velas cerradas: la consulta habitual de 500 incluye la vela en curso.
        # Con endTime historico obtenemos hasta 500 cerradas. Tomamos las 499 ultimas y replicamos la misma ventana
        # de tiempo original siempre que el primer cierre coincida con la ventana de origen.
        # La EMA200 puede diferir si la ventana original de 499 empieza una vela antes. Requerimos OHLCV originales
        # para un cotejo exacto. Por eso probamos una ventana desplazada y elegimos SOLO coincidencia por los cierres.
        candidates=[rows[-499:],rows[-500:-1]] if len(rows)>=500 else [rows]
        matched=None; calc=None
        for candidate in candidates:
            if not candidate or int(candidate[-1][6])!=close_ms:continue
            closes=[float(x[4]) for x in candidate]
            volumes=[float(x[5]) for x in candidate]
            values={
                'last_close':closes[-1],
                'last_open':float(candidate[-1][1]),
                'last_high':float(candidate[-1][2]),
                'last_low':float(candidate[-1][3]),
                'last_volume_base':volumes[-1],
                'last_volume_quote':float(candidate[-1][7]),
                'ema9':ema(closes,9),
                'ema21':ema(closes,21),
                'ema200':ema(closes,200),
                'rsi14_wilder':rsi_wilder(closes),
                'volume_average_20':sum(volumes[-20:])/20,
                'volume_ratio_20':volumes[-1]/(sum(volumes[-20:])/20) if sum(volumes[-20:]) else None,
            }
            # elegir coincidencia de valor publicado para detectar ventana originaria; no sustituye validacion de OHLCV originales
            if matched is None or abs(values['ema200']-float(record['ema200']))<abs(matched['ema200']-float(record['ema200'])):
                matched=values;calc=candidate
        if matched is None:
            raise ValueError('No se encontro la ultima vela cerrada historica requerida')
        for key,tol in [('last_close',0.011),('last_open',0.011),('last_high',0.011),('last_low',0.011),('last_volume_base',0.00001),('last_volume_quote',0.1),('ema9',0.0002),('ema21',0.0002),('ema200',0.0002),('rsi14_wilder',0.0002),('volume_average_20',0.0002),('volume_ratio_20',0.0002)]:
            compare(key,record.get(key),matched.get(key),tol,issues)
        if len(calc)!=int(record['closed_candles_used']):issues.append('Cantidad de velas diferente')
        if close_ms>=int(generated.timestamp()*1000):issues.append('Vela posterior a generacion')
        expected_close=(int(generated.timestamp())//s)*s*1000-1
        if expected_close-close_ms>120000:
            issues.append(f'Ultima vela atrasada {round((expected_close-close_ms)/1000)}s respecto a generacion')
        return {'series':desc,'audit':'PASS' if not issues else 'FAIL','issues':issues,'source':URL}
    except Exception as exc:
        return {'series':desc,'audit':'UNAVAILABLE','issues':[f'{type(exc).__name__}: {exc}'],'source':URL}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--json-url',default=DATA_URL)
    parser.add_argument('--max-json-age',type=int,default=300)
    parser.add_argument('--output',default='audit-result.json')
    args=parser.parse_args()
    try:
        payload=get_json(args.json_url)
        generated=dt.datetime.fromisoformat(payload['generated_at_utc'].replace('Z','+00:00'))
        now=dt.datetime.now(UTC)
        age=(now-generated).total_seconds()
        entries=[r for r in payload.get('results',[]) if r.get('market')=='spot' and r.get('status')=='verified']
        findings=[audit_series(r,now,generated) for r in entries]
        summary={'audited_at_utc':now.isoformat(),'json_generated_at_utc':generated.isoformat(),
                 'json_age_seconds':round(age,1),'json_current':0<=age<=args.max_json_age,
                 'declared_verified':payload.get('verified_series'),'spot_series_audited':len(entries),
                 'pass':sum(x['audit']=='PASS' for x in findings),'fail':sum(x['audit']=='FAIL' for x in findings),
                 'unavailable':sum(x['audit']=='UNAVAILABLE' for x in findings),
                 'futures_errors_declared':sum(e.get('market')=='futures_usdm' for e in payload.get('errors',[])),
                 'status':'PASS' if entries and all(x['audit']=='PASS' for x in findings) and 0<=age<=args.max_json_age else 'NOT_READY',
                 'details':findings}
        Path(args.output).write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
        print(json.dumps(summary,indent=2,ensure_ascii=False))
        return 0 if summary['status']=='PASS' else 1
    except Exception as exc:
        print(f'AUDITORIA NO DISPONIBLE: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
