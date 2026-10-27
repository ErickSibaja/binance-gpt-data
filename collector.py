#!/usr/bin/env python3
"""Public Binance candles -> validated, computed indicators. No account access or trading."""
import json, math, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

SYMBOLS = ('BTCUSDT', 'ETHUSDT')
INTERVALS = {'15m': 900_000, '1h': 3_600_000, '4h': 14_400_000}
MARKETS = {
    'spot': 'https://data-api.binance.vision/api/v3/klines',
    'futures_usdm': 'https://fapi.binance.com/fapi/v1/klines',
}
CANDLES_REQUESTED = 500
MIN_CLOSED = 300

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace('+00:00', 'Z')

def request_klines(url, symbol, interval):
    uri = url + '?' + urlencode({'symbol': symbol, 'interval': interval, 'limit': CANDLES_REQUESTED})
    req = Request(uri, headers={'User-Agent': 'BinanceGPTDataCollector/1.0', 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=20) as response:
                data = json.load(response)
            if not isinstance(data, list):
                raise ValueError('Respuesta JSON no es una lista de velas')
            return data, uri
        except (URLError, HTTPError, TimeoutError, ValueError) as exc:
            if attempt == 2: raise RuntimeError(f'Binance no respondiÃ³ correctamente: {type(exc).__name__}: {exc}') from exc
            time.sleep(1 + attempt * 2)

def validate(raw, interval_ms, now_ms):
    closed = []
    for item in raw:
        if not isinstance(item, list) or len(item) < 8:
            raise ValueError('Vela con formato incorrecto')
        t, close_time = int(item[0]), int(item[6])
        # Binance returns close time = open time + interval - 1 ms
        if close_time > now_ms:
            continue
        o, h, l, c, v, quote_v = map(float, (item[1],item[2],item[3],item[4],item[5],item[7]))
        if not all(map(math.isfinite, (o,h,l,c,v,quote_v))): raise ValueError('NÃºmeros no finitos')
        if min(o,h,l,c) <= 0 or v < 0 or quote_v < 0 or h < max(o,c,l) or l > min(o,c,h):
            raise ValueError('OHLCV inconsistente')
        closed.append({'open_ms':t, 'close_ms':close_time, 'open':o, 'high':h,'low':l,'close':c,'volume_base':v, 'volume_quote':quote_v})
    if len(closed) < MIN_CLOSED: raise ValueError(f'Solo {len(closed)} velas cerradas; se requieren {MIN_CLOSED}')
    for i in range(1,len(closed)):
        if closed[i]['open_ms'] - closed[i-1]['open_ms'] != interval_ms:
            raise ValueError('Tiempos no consecutivos o velas duplicadas')
    if closed[-1]['close_ms'] > now_ms: raise ValueError('Vela sin cerrar')
    if now_ms - closed[-1]['close_ms'] > 2 * interval_ms + 120_000:
        raise ValueError('Las velas estÃ¡n demasiado desactualizadas')
    return closed

def ema(values, span):
    if len(values) < span: raise ValueError('Muy pocos cierres')
    result = sum(values[:span]) / span
    alpha = 2 / (span + 1)
    for x in values[span:]: result = alpha * x + (1-alpha)*result
    return result

def rsi_wilder(values, period=14):
    if len(values) < period + 1: raise ValueError('Muy pocos cierres')
    deltas = [values[i]-values[i-1] for i in range(1,len(values))]
    gain = sum(max(d,0) for d in deltas[:period]) / period
    loss = sum(max(-d,0) for d in deltas[:period]) / period
    for d in deltas[period:]:
        gain = (gain*(period-1)+max(d,0))/period
        loss = (loss*(period-1)+max(-d,0))/period
    if loss == 0: return 100.0 if gain > 0 else 50.0
    return 100 - 100/(1 + gain/loss)

def summarise(candles, symbol, market, interval, source, now_ms):
    closes = [c['close'] for c in candles]
    volumes = [c['volume_base'] for c in candles]
    last = candles[-1]
    return {
        'status': 'verified', 'symbol':symbol,'market':market,'interval':interval,
        'source_url':source,'last_closed_candle_open_utc':iso(last['open_ms']),
        'last_closed_candle_close_utc':iso(last['close_ms']),
        'closed_candles_used':len(candles),
        'last_close':last['close'], 'last_open':last['open'],
        'last_high':last['high'],'last_low':last['low'],
        'last_volume_base':last['volume_base'],
        'last_volume_quote':last['volume_quote'],
        'ema9':round(ema(closes,9),8),'ema21':round(ema(closes,21),8),
        'ema200':round(ema(closes,200),8),'rsi14_wilder':round(rsi_wilder(closes,14),4),
        'volume_average_20':round(sum(volumes[-20:])/20,8),
        'volume_ratio_20':round(last['volume_base']/(sum(volumes[-20:])/20),4) if sum(volumes[-20:]) else None,
        'age_seconds_at_collection':round((now_ms-last['close_ms'])/1000,1),
        'method':{'ema':'SMA semilla de N cierres, luego EMA alfa=2/(N+1)', 'rsi':'Wilder 14; suavizado recursivo', 'volume':'Volumen base de la Ãºltima vela cerrada / promedio de 20 velas cerradas'},
    }

def main():
    now_ms=int(time.time()*1000)
    output={'schema':'binance-gpt-market-data-v1','generated_at_utc':iso(now_ms),
            'timezone_for_reports':'America/Costa_Rica','read_only':True,
            'warning':'Datos de velas cerradas, NO precio live ni seÃ±al de trading. No hay acceso a saldos ni posiciones de cuenta.',
            'results':[], 'errors':[], 'complete':False, 'status':'pending', 'expected_series':12}
    for market, url in MARKETS.items():
        for symbol in SYMBOLS:
            for interval,interval_ms in INTERVALS.items():
                key={'market':market,'symbol':symbol,'interval':interval}
                try:
                    raw, source=request_klines(url, symbol, interval)
                    candles=validate(raw, interval_ms, now_ms)
                    output['results'].append(summarise(candles,symbol,market,interval,source,now_ms))
                    print(f"OK {market} {symbol} {interval} ({len(candles)} velas cerradas)")
                except Exception as exc:
                    output['errors'].append({**key,'error':str(exc)})
                    print(f'ERROR {key}: {exc}', file=sys.stderr)
    output['complete']=len(output['results'])==12 and not output['errors']
    output['status']=('complete' if output['complete'] else 'partial' if output['results'] else 'unavailable')
    output['verified_series']=len(output['results'])
    output['unavailable_series']=len(output['errors'])
    Path('public').mkdir(exist_ok=True)
    Path('public/latest.json').write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    html='''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Binance GPT - Datos verificados</title><style>body{max-width:850px;margin:30px auto;padding:0 16px;font:16px system-ui;line-height:1.5} pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:14px;border-radius:8px}a{color:#1564bd}</style><h1>Binance GPT: datos pÃºblicos</h1><p>Solo datos histÃ³ricos de velas cerradas; no son recomendaciones financieras ni precios en tiempo real.</p><p><a href="latest.json">Abrir JSON actualizado</a></p><pre id="state">Cargando...</pre><script>fetch('latest.json').then(r=>r.json()).then(d=>document.getElementById('state').textContent=`Actualizado (UTC): ${d.generated_at_utc}\\nMercados vÃ¡lidos: ${d.results.length}/12\\nErrores: ${d.errors.length}\\nEstado: ${d.status}\nCompleto: ${d.complete}`).catch(e=>document.getElementById('state').textContent=e.message)</script></html>'''
    Path('public/index.html').write_text(html,encoding='utf-8')
    if not output['complete']:
        print(f"ATENCION: recolecciÃ³n {output['status']}: {len(output['results'])}/12 series vÃ¡lidas, {len(output['errors'])} errores.", file=sys.stderr)
    if not output['results']:
        print('No se publica: cero series verificadas.', file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': sys.exit(main())
