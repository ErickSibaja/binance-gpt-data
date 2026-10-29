
#!/usr/bin/env python3
"""
Binance GPT - Auditoria independiente v4.0

Audita por separado:
- Binance Spot: BTCUSDT y ETHUSDT
- Bitget USDT Futures: BTCUSDT y ETHUSDT

Temporalidades: 15m, 1h y 4h.

Verifica:
- OHLCV
- 499 velas cerradas por serie
- Continuidad y alineacion temporal
- EMA 9, 21, 200
- RSI 14 Wilder
- Volumen promedio 20 y volumen relativo
- Ultima vela cerrada esperada
- Frescura de la instantanea

Binance Futures permanece separado y sin auditar
cuando devuelve HTTP 451.

Solo lectura. No utiliza credenciales ni opera.
"""

import argparse
import datetime as dt
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UTC = dt.timezone.utc

SYMBOLS = ("BTCUSDT", "ETHUSDT")

INTERVALS = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

BITGET_INTERVALS = {
    "15m": "15m",
    "1h": "1H",
    "4h": "4H",
}

PAGES_URL = (
    "https://ericksibaja.github.io/"
    "binance-gpt-data/latest.json"
)

RAW_URL = (
    "https://raw.githubusercontent.com/"
    "ErickSibaja/binance-gpt-data/"
    "data/latest.json"
)

BINANCE_API = (
    "https://data-api.binance.vision"
    "/api/v3/klines"
)

BITGET_API = (
    "https://api.bitget.com"
    "/api/v3/market/candles"
)

TOLERANCES = {
    "last_open": 0.011,
    "last_high": 0.011,
    "last_low": 0.011,
    "last_close": 0.011,
    "last_volume_base": 0.00002,
    "last_volume_quote": 0.2,
    "ema9": 0.0003,
    "ema21": 0.0003,
    "ema200": 0.0003,
    "rsi14_wilder": 0.0003,
    "volume_average_20": 0.0003,
    "volume_ratio_20": 0.0003,
}


# ============================================================
# UTILIDADES
# ============================================================

def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BinanceGPTAudit/4.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request, timeout=25
    ) as response:
        return json.load(response)


def parse_utc(value):
    parsed = dt.datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        raise ValueError(
            "Timestamp sin zona horaria"
        )

    return parsed.astimezone(UTC)


def epoch_ms(value):
    return round(
        parse_utc(value).timestamp() * 1000
    )


def iso_ms(value):
    return dt.datetime.fromtimestamp(
        value / 1000, UTC
    ).isoformat()


def save_report(path, report):
    Path(path).write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )


def series_name(record):
    return (
        f"{record['symbol']} "
        f"{record['market']} "
        f"{record['interval']}"
    )


def expected_keys():
    return {
        (symbol, interval)
        for symbol in SYMBOLS
        for interval in INTERVALS
    }


# ============================================================
# INDICADORES INDEPENDIENTES
# ============================================================

def ema(closes, period):
    if len(closes) < period:
        raise ValueError(
            "Velas insuficientes para EMA"
        )

    value = sum(closes[:period]) / period
    alpha = 2 / (period + 1)

    for close in closes[period:]:
        value = (
            alpha * close
            + (1 - alpha) * value
        )

    return value


def rsi_wilder(closes, period=14):
    if len(closes) <= period:
        raise ValueError(
            "Velas insuficientes para RSI"
        )

    changes = [
        b - a
        for a, b in zip(
            closes, closes[1:]
        )
    ]

    gain = sum(
        max(0, x)
        for x in changes[:period]
    ) / period

    loss = sum(
        max(0, -x)
        for x in changes[:period]
    ) / period

    for change in changes[period:]:
        gain = (
            gain * (period - 1)
            + max(change, 0)
        ) / period

        loss = (
            loss * (period - 1)
            + max(-change, 0)
        ) / period

    if loss == 0:
        return (
            100.0 if gain > 0
            else 50.0
        )

    return 100 - 100 / (
        1 + gain / loss
    )


# ============================================================
# DESCARGAR VELAS INDEPENDIENTEMENTE
# ============================================================

def fetch_binance(record, first_ms, last_ms, count):
    params = urllib.parse.urlencode({
        "symbol": record["symbol"],
        "interval": record["interval"],
        "startTime": first_ms,
        "endTime": (
            last_ms
            + INTERVALS[record["interval"]] * 1000
            - 1
        ),
        "limit": count,
    })

    rows = fetch(
        BINANCE_API + "?" + params
    )

    if not isinstance(rows, list):
        raise ValueError(
            "Binance no devolvio lista de velas"
        )

    return rows


def fetch_bitget(record, first_ms, last_ms, count):
    interval = record["interval"]

    params = urllib.parse.urlencode({
        "category": "USDT-FUTURES",
        "symbol": record["symbol"],
        "interval": BITGET_INTERVALS[interval],
        "type": "market",
        "endTime": (
            last_ms
            + INTERVALS[interval] * 1000
            - 1
        ),
        "limit": 1000,
    })

    payload = fetch(
        BITGET_API + "?" + params
    )

    if not isinstance(payload, dict):
        raise ValueError(
            "Bitget: respuesta JSON invalida"
        )

    if payload.get("code") != "00000":
        raise ValueError(
            f"Bitget API: {payload.get('code')} "
            f"{payload.get('msg')}"
        )

    rows = payload.get("data")

    if not isinstance(rows, list):
        raise ValueError(
            "Bitget no devolvio lista de velas"
        )

    # Filtrar la ventana temporal exacta.
    rows = [
        row for row in rows
        if (
            isinstance(row, list)
            and len(row) >= 7
            and first_ms <= int(row[0]) <= last_ms
        )
    ]

    return rows


# ============================================================
# NORMALIZAR Y VALIDAR OHLCV
# ============================================================

def normalize_rows(
    rows, exchange, first_ms, count, duration_ms
):
    normalized = []

    for row in rows:
        if not isinstance(row, list):
            raise ValueError(
                "Formato de vela invalido"
            )

        minimum_fields = (
            8 if exchange == "binance"
            else 7
        )

        if len(row) < minimum_fields:
            raise ValueError(
                "Vela con campos insuficientes"
            )

        opening_ms = int(row[0])

        if exchange == "binance":
            closing_ms = int(row[6])
            quote_volume = float(row[7])
        else:
            closing_ms = (
                opening_ms + duration_ms - 1
            )
            quote_volume = float(row[6])

        normalized.append({
            "open_ms": opening_ms,
            "close_ms": closing_ms,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume_base": float(row[5]),
            "volume_quote": quote_volume,
        })

    normalized.sort(
        key=lambda candle: candle["open_ms"]
    )

    if len(normalized) != count:
        raise ValueError(
            f"Se esperaban {count} velas; "
            f"se recibieron {len(normalized)}"
        )

    for index, candle in enumerate(normalized):
        expected_open = (
            first_ms + index * duration_ms
        )

        expected_close = (
            expected_open + duration_ms - 1
        )

        if (
            candle["open_ms"] != expected_open
            or candle["close_ms"] != expected_close
        ):
            raise ValueError(
                "Vela faltante, duplicada o "
                f"irregular en indice {index}"
            )

        op = candle["open"]
        hi = candle["high"]
        lo = candle["low"]
        cl = candle["close"]
        vol = candle["volume_base"]
        quote = candle["volume_quote"]

        values = (
            op, hi, lo, cl,
            vol, quote
        )

        if not all(
            math.isfinite(value)
            for value in values
        ):
            raise ValueError(
                f"Valor no finito en vela {index}"
            )

        if (
            min(op, hi, lo, cl) <= 0
            or hi < max(op, cl, lo)
            or lo > min(op, cl, hi)
            or vol < 0
            or quote < 0
        ):
            raise ValueError(
                f"OHLCV inconsistente en vela {index}"
            )

    return normalized


# ============================================================
# AUDITORIA MATEMATICA DE UNA SERIE
# ============================================================

def audit_one(record, exchange):
    label = series_name(record)

    issues = []
    comparisons = {}

    try:
        interval = record["interval"]
        duration_ms = INTERVALS[interval] * 1000
        count = int(
            record["closed_candles_used"]
        )

        if count < 200 or count > 1000:
            raise ValueError(
                "Cantidad de velas fuera de rango"
            )

        if exchange == "bitget" and count != 499:
            raise ValueError(
                "Bitget: se esperaban 499 "
                "velas cerradas"
            )

        last_open_ms = epoch_ms(
            record["last_closed_candle_open_utc"]
        )

        last_close_ms = epoch_ms(
            record["last_closed_candle_close_utc"]
        )

        if last_close_ms != (
            last_open_ms + duration_ms - 1
        ):
            raise ValueError(
                "Duracion de ultima vela incorrecta"
            )

        first_ms = (
            last_open_ms
            - (count - 1) * duration_ms
        )

        if exchange == "binance":
            raw = fetch_binance(
                record, first_ms,
                last_open_ms, count
            )
        else:
            raw = fetch_bitget(
                record, first_ms,
                last_open_ms, count
            )

        candles = normalize_rows(
            raw,
            exchange,
            first_ms,
            count,
            duration_ms,
        )

        closes = [
            candle["close"]
            for candle in candles
        ]

        volumes = [
            candle["volume_base"]
            for candle in candles
        ]

        last = candles[-1]

        avg20 = (
            sum(volumes[-20:]) / 20
        )

        actual = {
            "last_open": last["open"],
            "last_high": last["high"],
            "last_low": last["low"],
            "last_close": last["close"],
            "last_volume_base": (
                last["volume_base"]
            ),
            "last_volume_quote": (
                last["volume_quote"]
            ),
            "ema9": ema(closes, 9),
            "ema21": ema(closes, 21),
            "ema200": ema(closes, 200),
            "rsi14_wilder": rsi_wilder(
                closes
            ),
            "volume_average_20": avg20,
            "volume_ratio_20": (
                last["volume_base"] / avg20
                if avg20 > 0 else None
            ),
        }

        for field, tolerance in TOLERANCES.items():
            published = record.get(field)
            calculated = actual[field]

            if (
                isinstance(published, bool)
                or not isinstance(
                    published, (float, int)
                )
                or calculated is None
            ):
                issues.append(
                    f"{field}: valor ausente o invalido"
                )
                continue

            published = float(published)

            if not math.isfinite(published):
                issues.append(
                    f"{field}: valor no finito"
                )
                continue

            difference = abs(
                published - calculated
            )

            comparisons[field] = {
                "published": published,
                "recalculated": round(
                    calculated, 10
                ),
                "absolute_difference": round(
                    difference, 10
                ),
            }

            if difference > tolerance:
                issues.append(
                    f"{field}: diferencia "
                    f"{difference:.8f}; "
                    f"tolerancia {tolerance}"
                )

        return {
            "series": label,
            "exchange": exchange,
            "status": (
                "PASS" if not issues
                else "FAIL"
            ),
            "candles_checked": count,
            "issues": issues,
            "comparisons": comparisons,
        }

    except Exception as exc:
        return {
            "series": label,
            "exchange": exchange,
            "status": "UNAVAILABLE",
            "issues": [
                f"{type(exc).__name__}: {exc}"
            ],
            "comparisons": {},
        }


# ============================================================
# INTEGRIDAD TEMPORAL
# ============================================================

def audit_integrity(record, generated):
    interval = record["interval"]
    duration_ms = INTERVALS[interval] * 1000

    generated_ms = round(
        generated.timestamp() * 1000
    )

    expected_open_ms = (
        (generated_ms // duration_ms) - 1
    ) * duration_ms

    actual_open_ms = epoch_ms(
        record["last_closed_candle_open_utc"]
    )

    actual_close_ms = epoch_ms(
        record["last_closed_candle_close_utc"]
    )

    boundary_ms = (
        generated_ms % duration_ms
    )

    issues = []

    if actual_close_ms != (
        actual_open_ms + duration_ms - 1
    ):
        issues.append(
            "Cierre temporal inconsistente"
        )

    if actual_open_ms % duration_ms != 0:
        issues.append(
            "Apertura desalineada"
        )

    if actual_close_ms >= generated_ms:
        issues.append(
            "Vela aun abierta al generar JSON"
        )

    if issues:
        status = "FAIL"

    elif actual_open_ms == expected_open_ms:
        status = "PASS"

    elif (
        boundary_ms < 60000
        and actual_open_ms
        == expected_open_ms - duration_ms
    ):
        status = "REVIEW_BOUNDARY"
        issues.append(
            "Una vela de retraso cerca "
            "del limite temporal"
        )

    else:
        status = "FAIL"
        issues.append(
            "Falta la ultima vela esperada"
        )

    lag = (
        expected_open_ms - actual_open_ms
    ) // duration_ms

    return {
        "series": series_name(record),
        "status": status,
        "expected_open_utc": iso_ms(
            expected_open_ms
        ),
        "actual_open_utc": (
            record["last_closed_candle_open_utc"]
        ),
        "actual_close_utc": (
            record["last_closed_candle_close_utc"]
        ),
        "candle_lag": int(lag),
        "issues": issues,
    }


# ============================================================
# AUDITORIA POR MERCADO
# ============================================================

def audit_market(records, exchange, generated):
    expected = expected_keys()

    filtered = [
        record for record in records
        if (
            record.get("status") == "verified"
            and record.get("exchange", "binance")
            == exchange
            and record.get("symbol") in SYMBOLS
            and record.get("interval") in INTERVALS
        )
    ]

    keys = [
        (
            record["symbol"],
            record["interval"]
        )
        for record in filtered
    ]

    coverage_ok = (
        len(filtered) == 6
        and len(set(keys)) == 6
        and set(keys) == expected
    )

    details = [
        audit_one(record, exchange)
        for record in filtered
    ]

    integrity = []

    for record in filtered:
        try:
            integrity.append(
                audit_integrity(
                    record, generated
                )
            )
        except Exception as exc:
            integrity.append({
                "series": series_name(record),
                "status": "FAIL",
                "issues": [
                    f"{type(exc).__name__}: {exc}"
                ],
            })

    passed = sum(
        x["status"] == "PASS"
        for x in details
    )

    failed = sum(
        x["status"] == "FAIL"
        for x in details
    )

    unavailable = sum(
        x["status"] == "UNAVAILABLE"
        for x in details
    )

    integrity_pass = sum(
        x["status"] == "PASS"
        for x in integrity
    )

    integrity_review = sum(
        x["status"] == "REVIEW_BOUNDARY"
        for x in integrity
    )

    integrity_fail = sum(
        x["status"] == "FAIL"
        for x in integrity
    )

    math_ok = (
        coverage_ok
        and passed == 6
        and failed == 0
        and unavailable == 0
    )

    if coverage_ok and integrity_pass == 6:
        integrity_status = "PASS"

    elif (
        coverage_ok
        and integrity_fail == 0
        and integrity_review > 0
    ):
        integrity_status = "REVIEW"

    else:
        integrity_status = "FAIL"

    return {
        "exchange": exchange,
        "series_audited": len(details),
        "coverage_ok": coverage_ok,
        "mathematical_status": (
            "PASS" if math_ok else "FAIL"
        ),
        "integrity_status": integrity_status,
        "pass": passed,
        "fail": failed,
        "unavailable": unavailable,
        "integrity_pass": integrity_pass,
        "integrity_review": integrity_review,
        "integrity_fail": integrity_fail,
        "details": details,
        "integrity_details": integrity,
    }


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--json-url",
        default=PAGES_URL,
    )

    parser.add_argument(
        "--max-json-age",
        type=int,
        default=300,
    )

    parser.add_argument(
        "--output",
        default="audit-result.json",
    )

    args = parser.parse_args()

    audit_started = dt.datetime.now(UTC)

    try:
        payload = fetch(args.json_url)

        if not isinstance(payload, dict):
            raise ValueError(
                "El JSON publicado no es un objeto"
            )

        if payload.get("schema") != (
            "binance-gpt-market-data-v1"
        ):
            raise ValueError(
                "Esquema JSON inesperado"
            )

        generated = parse_utc(
            payload["generated_at_utc"]
        )

        age = (
            audit_started - generated
        ).total_seconds()

        if not math.isfinite(age):
            raise ValueError(
                "Antiguedad invalida"
            )

        # La captura puede actualizarse durante
        # una auditoria, pero los calculos deben
        # usar la misma instantanea publicada.

        spot_records = [
            record
            for record in payload.get("results", [])
            if record.get("market") == "spot"
        ]

        bitget_records = (
            payload.get(
                "bitget_futures_results", []
            )
        )

        if not isinstance(bitget_records, list):
            raise ValueError(
                "bitget_futures_results invalido"
            )

        spot_audit = audit_market(
            spot_records,
            "binance",
            generated,
        )

        bitget_audit = audit_market(
            bitget_records,
            "bitget",
            generated,
        )

        if age < 0:
            freshness = "INVALID_FUTURE"

        elif age <= args.max_json_age:
            freshness = "CURRENT"

        elif age <= 600:
            freshness = "DELAYED"

        else:
            freshness = "STALE"

        spot_ok = (
            spot_audit["mathematical_status"]
            == "PASS"
            and spot_audit["integrity_status"]
            == "PASS"
        )

        bitget_ok = (
            bitget_audit["mathematical_status"]
            == "PASS"
            and bitget_audit["integrity_status"]
            == "PASS"
        )

        if (
            spot_ok
            and bitget_ok
            and freshness == "CURRENT"
        ):
            operational_status = (
                "DATA_CHECKS_OK"
            )

        elif spot_ok or bitget_ok:
            operational_status = (
                "REVIEW_ONLY"
            )

        else:
            operational_status = (
                "NOT_READY"
            )

        futures_errors = sum(
            error.get("market")
            == "futures_usdm"
            for error in payload.get(
                "errors", []
            )
        )

        report = {
            "audit_version": "4.0",
            "audited_at_utc": (
                audit_started.isoformat()
            ),
            "source_json_url": args.json_url,
            "json_generated_at_utc": (
                generated.isoformat()
            ),
            "json_age_seconds_at_audit_start": (
                round(age, 1)
            ),
            "freshness_status": freshness,
            "json_current": (
                freshness == "CURRENT"
            ),

            # Resultado general de mercados auditados
            "mathematical_status": (
                "PASS"
                if spot_ok and bitget_ok
                else "FAIL"
            ),
            "integrity_status": (
                "PASS"
                if (
                    spot_audit["integrity_status"]
                    == "PASS"
                    and bitget_audit["integrity_status"]
                    == "PASS"
                )
                else "FAIL"
            ),

            # Binance Spot original
            "spot_series_audited": (
                spot_audit["series_audited"]
            ),
            "spot_mathematical_status": (
                spot_audit["mathematical_status"]
            ),
            "spot_integrity_status": (
                spot_audit["integrity_status"]
            ),

            # Bitget Futures independiente
            "bitget_futures_series_audited": (
                bitget_audit["series_audited"]
            ),
            "bitget_futures_mathematical_status": (
                bitget_audit["mathematical_status"]
            ),
            "bitget_futures_integrity_status": (
                bitget_audit["integrity_status"]
            ),

            "spot": spot_audit,
            "bitget_futures": bitget_audit,

            # Binance Futures permanece restringido
            "binance_futures_errors_declared": (
                futures_errors
            ),
            "binance_futures_audited": False,

            "operational_status": (
                operational_status
            ),
            "note": (
                "Auditoria de Binance Spot y "
                "Bitget USDT Futures por separado. "
                "Los datos de Bitget no sustituyen "
                "ni certifican Binance Futures. "
                "No constituye una senal de trading."
            ),
        }

        save_report(
            args.output,
            report,
        )

        # Resumen breve para GitHub Actions.
        summary = {
            key: value
            for key, value in report.items()
            if key not in (
                "spot", "bitget_futures"
            )
        }

        print(
            json.dumps(
                summary,
                indent=2,
                ensure_ascii=False,
            )
        )

        # Frescura separada:
        # Una auditoria historica puede pasar
        # matematicamente aunque el JSON sea viejo.
        #
        # El codigo 0 requiere ambos mercados
        # auditados y temporalmente coherentes.

        return 0 if (
            spot_ok and bitget_ok
        ) else 1

    except Exception as exc:
        report = {
            "audit_version": "4.0",
            "mathematical_status": (
                "UNAVAILABLE"
            ),
            "integrity_status": (
                "UNAVAILABLE"
            ),
            "operational_status": (
                "NOT_READY"
            ),
            "reason": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

        save_report(
            args.output,
            report,
        )

        print(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    sys.exit(main())
