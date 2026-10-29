
#!/usr/bin/env python3
"""
Binance GPT - Recolector de datos publicos.

Fuentes:
- Binance Spot
- Binance Futures USD-M (registra errores si no esta disponible)
- Bitget USDT Futures (mercado independiente)

Solo lectura. No accede a cuentas ni ejecuta operaciones.
"""

import json
import math
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError


# ============================================================
# CONFIGURACION
# ============================================================

SYMBOLS = ("BTCUSDT", "ETHUSDT")

INTERVALS = {
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
}

BINANCE_MARKETS = {
    "spot": "https://data-api.binance.vision/api/v3/klines",
    "futures_usdm": "https://fapi.binance.com/fapi/v1/klines",
}

BITGET_URL = "https://api.bitget.com/api/v3/market/candles"

BITGET_INTERVALS = {
    "15m": "15m",
    "1h": "1H",
    "4h": "4H",
}

CANDLES_REQUESTED = 500

MIN_CLOSED_BINANCE = 300
MIN_CLOSED_BITGET = 499


# ============================================================
# UTILIDADES
# ============================================================

def iso(ms):
    return datetime.fromtimestamp(
        ms / 1000,
        tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def download_json(url, provider):
    req = Request(
        url,
        headers={
            "User-Agent": "BinanceGPTDataCollector/2.0",
            "Accept": "application/json",
        },
    )

    for attempt in range(3):
        try:
            with urlopen(req, timeout=20) as response:
                return json.load(response)

        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            if attempt == 2:
                raise RuntimeError(
                    f"{provider}: {type(exc).__name__}: {exc}"
                ) from exc

            time.sleep(1 + attempt * 2)


# ============================================================
# BINANCE
# ============================================================

def request_binance(url, symbol, interval):
    source = url + "?" + urlencode({
        "symbol": symbol,
        "interval": interval,
        "limit": CANDLES_REQUESTED,
    })

    raw = download_json(source, "Binance")

    if not isinstance(raw, list):
        raise ValueError(
            "Binance: respuesta no contiene una lista de velas"
        )

    return raw, source


# ============================================================
# BITGET
# ============================================================

def request_bitget(symbol, interval):
    source = BITGET_URL + "?" + urlencode({
        "category": "USDT-FUTURES",
        "symbol": symbol,
        "interval": BITGET_INTERVALS[interval],
        "limit": CANDLES_REQUESTED,
    })

    payload = download_json(source, "Bitget")

    if not isinstance(payload, dict):
        raise ValueError(
            "Bitget: respuesta JSON incorrecta"
        )

    if payload.get("code") != "00000":
        raise ValueError(
            f"Bitget: codigo {payload.get('code')}, "
            f"mensaje {payload.get('msg')}"
        )

    rows = payload.get("data")

    if not isinstance(rows, list):
        raise ValueError(
            "Bitget: data no es una lista"
        )

    return rows, source


# ============================================================
# VALIDACION GENERAL OHLCV
# ============================================================

def validate_ohlcv(candles, interval_ms, now_ms, minimum):
    candles.sort(
        key=lambda item: item["open_ms"]
    )

    if len(candles) < minimum:
        raise ValueError(
            f"Solo {len(candles)} velas cerradas; "
            f"se requieren {minimum}"
        )

    for candle in candles:
        t = candle["open_ms"]

        if t % interval_ms != 0:
            raise ValueError(
                "Apertura fuera del intervalo UTC"
            )

        if candle["close_ms"] != t + interval_ms - 1:
            raise ValueError(
                "Hora de cierre incoherente"
            )

        values = [
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            candle["volume_base"],
            candle["volume_quote"],
        ]

        if not all(math.isfinite(x) for x in values):
            raise ValueError(
                "Valores no finitos"
            )

        o, h, l, c, v, q = values

        if min(o, h, l, c) <= 0:
            raise ValueError(
                "Precio invalido"
            )

        if v < 0 or q < 0:
            raise ValueError(
                "Volumen negativo"
            )

        if h < max(o, c, l) or l > min(o, c, h):
            raise ValueError(
                "OHLC inconsistente"
            )

    for previous, current in zip(
        candles,
        candles[1:]
    ):
        difference = (
            current["open_ms"] - previous["open_ms"]
        )

        if difference != interval_ms:
            raise ValueError(
                "Tiempos no consecutivos o velas duplicadas"
            )

    last = candles[-1]

    if last["close_ms"] > now_ms:
        raise ValueError(
            "La ultima vela aun esta abierta"
        )

    return candles


# ============================================================
# VALIDACION BINANCE
# ============================================================

def validate_binance(raw, interval_ms, now_ms):
    candles = []

    for row in raw:
        if not isinstance(row, list) or len(row) < 8:
            raise ValueError(
                "Binance: formato de vela incorrecto"
            )

        opening = int(row[0])
        closing = int(row[6])

        # Excluir la vela actualmente abierta.
        if closing > now_ms:
            continue

        candles.append({
            "open_ms": opening,
            "close_ms": closing,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume_base": float(row[5]),
            "volume_quote": float(row[7]),
        })

    candles = validate_ohlcv(
        candles,
        interval_ms,
        now_ms,
        MIN_CLOSED_BINANCE,
    )

    if now_ms - candles[-1]["close_ms"] > (
        2 * interval_ms + 120_000
    ):
        raise ValueError(
            "Binance: velas demasiado antiguas"
        )

    return candles


# ============================================================
# COMPATIBILIDAD CON LAS PRUEBAS ORIGINALES
# ============================================================

def validate(raw, interval_ms, now_ms):
    """
    Mantiene el nombre original de la funcion validate.

    Las pruebas anteriores utilizan:
        from collector import ema, rsi_wilder, validate

    Esta funcion conserva esa interfaz y delega
    la validacion al procesador de Binance.
    """
    return validate_binance(
        raw,
        interval_ms,
        now_ms
    )


# ============================================================
# VALIDACION BITGET
# ============================================================

def validate_bitget(raw, interval_ms, now_ms):
    candles = []

    for row in raw:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError(
                "Bitget: formato de vela incorrecto"
            )

        opening = int(row[0])
        closing = opening + interval_ms - 1

        # Excluir la vela actualmente abierta.
        if closing > now_ms:
            continue

        candles.append({
            "open_ms": opening,
            "close_ms": closing,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume_base": float(row[5]),
            "volume_quote": float(row[6]),
        })

    candles = validate_ohlcv(
        candles,
        interval_ms,
        now_ms,
        MIN_CLOSED_BITGET,
    )

    expected_open = (
        (now_ms // interval_ms) - 1
    ) * interval_ms

    last_open = candles[-1]["open_ms"]

    if last_open != expected_open:
        raise ValueError(
            "Bitget: falta la ultima vela cerrada esperada. "
            f"Esperada: {iso(expected_open)}. "
            f"Disponible: {iso(last_open)}"
        )

    return candles


# ============================================================
# INDICADORES TECNICOS
# ============================================================

def ema(values, span):
    if len(values) < span:
        raise ValueError(
            "Muy pocos cierres para EMA"
        )

    result = sum(values[:span]) / span
    alpha = 2 / (span + 1)

    for value in values[span:]:
        result = (
            alpha * value +
            (1 - alpha) * result
        )

    return result


def rsi_wilder(values, period=14):
    if len(values) < period + 1:
        raise ValueError(
            "Muy pocos cierres para RSI"
        )

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gain = sum(
        max(d, 0) for d in changes[:period]
    ) / period

    loss = sum(
        max(-d, 0) for d in changes[:period]
    ) / period

    for change in changes[period:]:
        gain = (
            gain * (period - 1) +
            max(change, 0)
        ) / period

        loss = (
            loss * (period - 1) +
            max(-change, 0)
        ) / period

    if loss == 0:
        return 100.0 if gain > 0 else 50.0

    return 100 - 100 / (1 + gain / loss)


# ============================================================
# RESUMEN DE INDICADORES
# ============================================================

def summarise(
    candles,
    symbol,
    market,
    interval,
    source,
    now_ms,
    exchange
):
    closes = [
        row["close"] for row in candles
    ]

    volumes = [
        row["volume_base"] for row in candles
    ]

    last = candles[-1]

    volume_avg = sum(volumes[-20:]) / 20

    return {
        "status": "verified",
        "exchange": exchange,
        "symbol": symbol,
        "market": market,
        "interval": interval,
        "source_url": source,
        "last_closed_candle_open_utc": iso(
            last["open_ms"]
        ),
        "last_closed_candle_close_utc": iso(
            last["close_ms"]
        ),
        "closed_candles_used": len(candles),
        "last_close": last["close"],
        "last_open": last["open"],
        "last_high": last["high"],
        "last_low": last["low"],
        "last_volume_base": last["volume_base"],
        "last_volume_quote": last["volume_quote"],
        "ema9": round(
            ema(closes, 9), 8
        ),
        "ema21": round(
            ema(closes, 21), 8
        ),
        "ema200": round(
            ema(closes, 200), 8
        ),
        "rsi14_wilder": round(
            rsi_wilder(closes), 4
        ),
        "volume_average_20": round(
            volume_avg, 8
        ),
        "volume_ratio_20": (
            round(
                last["volume_base"] / volume_avg,
                4
            )
            if volume_avg > 0
            else None
        ),
        "age_seconds_at_collection": round(
            (now_ms - last["close_ms"]) / 1000,
            1
        ),
        "method": {
            "ema": "SMA inicial y EMA recursiva",
            "rsi": "Wilder 14 recursivo",
            "volume": (
                "Volumen base / promedio 20 velas"
            ),
        },
    }


# ============================================================
# RECOLECCION PRINCIPAL
# ============================================================

def main():
    now_ms = int(time.time() * 1000)

    output = {
        "schema": "binance-gpt-market-data-v1",
        "generated_at_utc": iso(now_ms),
        "timezone_for_reports": "America/Costa_Rica",
        "read_only": True,
        "warning": (
            "Datos de velas cerradas, no precios live. "
            "Binance Spot y Bitget Futuros son mercados "
            "distintos. Sin acceso a cuentas u operaciones."
        ),

        # Binance original
        "results": [],
        "errors": [],
        "complete": False,
        "status": "pending",
        "expected_series": 12,

        # Bitget independiente
        "bitget_futures_results": [],
        "bitget_futures_errors": [],
        "bitget_futures_expected_series": 6,
    }

    # --------------------------------------------------------
    # 1. BINANCE SPOT Y FUTUROS
    # --------------------------------------------------------

    for market, url in BINANCE_MARKETS.items():
        for symbol in SYMBOLS:
            for interval, interval_ms in INTERVALS.items():

                key = {
                    "market": market,
                    "symbol": symbol,
                    "interval": interval,
                }

                try:
                    raw, source = request_binance(
                        url,
                        symbol,
                        interval,
                    )

                    candles = validate_binance(
                        raw,
                        interval_ms,
                        now_ms,
                    )

                    record = summarise(
                        candles,
                        symbol,
                        market,
                        interval,
                        source,
                        now_ms,
                        "binance",
                    )

                    output["results"].append(
                        record
                    )

                    print(
                        f"OK Binance {market} "
                        f"{symbol} {interval}: "
                        f"{len(candles)} cerradas"
                    )

                except Exception as exc:
                    output["errors"].append({
                        **key,
                        "error": str(exc),
                    })

                    print(
                        f"ERROR Binance {key}: {exc}",
                        file=sys.stderr,
                    )

    # --------------------------------------------------------
    # 2. BITGET FUTUROS USDT
    # --------------------------------------------------------

    for symbol in SYMBOLS:
        for interval, interval_ms in INTERVALS.items():

            key = {
                "exchange": "bitget",
                "market": "bitget_usdt_futures",
                "symbol": symbol,
                "interval": interval,
            }

            try:
                raw, source = request_bitget(
                    symbol,
                    interval,
                )

                candles = validate_bitget(
                    raw,
                    interval_ms,
                    now_ms,
                )

                record = summarise(
                    candles,
                    symbol,
                    "bitget_usdt_futures",
                    interval,
                    source,
                    now_ms,
                    "bitget",
                )

                record["contract_category"] = (
                    "USDT-FUTURES"
                )

                record["candle_type"] = "market"

                output["bitget_futures_results"].append(
                    record
                )

                print(
                    f"OK Bitget Futures "
                    f"{symbol} {interval}: "
                    f"{len(candles)} cerradas"
                )

            except Exception as exc:
                output["bitget_futures_errors"].append({
                    **key,
                    "error": str(exc),
                })

                print(
                    f"ERROR Bitget {key}: {exc}",
                    file=sys.stderr,
                )

    # --------------------------------------------------------
    # 3. ESTADO BINANCE ORIGINAL
    # --------------------------------------------------------

    output["complete"] = (
        len(output["results"]) == 12
        and not output["errors"]
    )

    output["status"] = (
        "complete"
        if output["complete"]
        else "partial"
        if output["results"]
        else "unavailable"
    )

    output["verified_series"] = len(
        output["results"]
    )

    output["unavailable_series"] = len(
        output["errors"]
    )

    # --------------------------------------------------------
    # 4. ESTADO BITGET
    # --------------------------------------------------------

    output["bitget_futures_verified_series"] = len(
        output["bitget_futures_results"]
    )

    output["bitget_futures_unavailable_series"] = len(
        output["bitget_futures_errors"]
    )

    output["bitget_futures_complete"] = (
        len(output["bitget_futures_results"]) == 6
        and not output["bitget_futures_errors"]
    )

    output["bitget_futures_status"] = (
        "complete"
        if output["bitget_futures_complete"]
        else "partial"
        if output["bitget_futures_results"]
        else "unavailable"
    )

    # No equivale a una auditoria matematica independiente.
    output["independent_audit_confirmed"] = False

    # Timestamp de finalizacion de recoleccion.
    output["generated_at_utc"] = iso(
        int(time.time() * 1000)
    )

    # --------------------------------------------------------
    # 5. PUBLICAR JSON
    # --------------------------------------------------------

    Path("public").mkdir(
        exist_ok=True
    )

    Path("public/latest.json").write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 6. PAGINA PUBLICA
    # --------------------------------------------------------

    html = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Binance GPT - Datos publicos</title>
</head>
<body>
<h1>Binance GPT</h1>
<p>
Binance Spot y Bitget USDT Futures.
Mercados independientes.
Sin operaciones.
</p>
<p>
<a href="latest.json">Abrir JSON</a>
</p>
<pre id="state">Cargando...</pre>
<script>
fetch('latest.json')
.then(r => r.json())
.then(d => {
  document.getElementById('state').textContent =
    'Generado UTC: ' + d.generated_at_utc + '\\n' +
    'Binance: ' + d.verified_series + '/12\\n' +
    'Bitget Futures: ' +
    d.bitget_futures_verified_series + '/6\\n' +
    'Auditoria independiente: no confirmada';
})
.catch(e => {
  document.getElementById('state').textContent =
    e.message;
});
</script>
</body>
</html>"""

    Path("public/index.html").write_text(
        html,
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 7. RESUMEN EN GITHUB ACTIONS
    # --------------------------------------------------------

    print(
        "RESUMEN BINANCE:",
        output["verified_series"],
        "/12",
        output["status"],
    )

    print(
        "RESUMEN BITGET:",
        output["bitget_futures_verified_series"],
        "/6",
        output["bitget_futures_status"],
    )

    if not output["results"]:
        print(
            "Sin series Binance verificadas.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
