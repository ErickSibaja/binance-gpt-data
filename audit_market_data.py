
#!/usr/bin/env python3
"""Binance GPT - Auditoria independiente v3.

Verifica:
- EMA 9, 21 y 200
- RSI 14 Wilder
- OHLCV y volumen
- Continuidad de velas
- Ultima vela cerrada esperada
- Frescura del JSON

Solo lectura. No utiliza claves ni opera en Binance.
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

INTERVALS = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

SYMBOLS = ("BTCUSDT", "ETHUSDT")

DATA_URL = (
    "https://ericksibaja.github.io/"
    "binance-gpt-data/latest.json"
)

API = (
    "https://data-api.binance.vision"
    "/api/v3/klines"
)


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "binance-gpt-audit/3.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        req, timeout=25
    ) as response:
        return json.load(response)


def parse_utc(value):
    parsed = dt.datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        raise ValueError("Timestamp sin zona horaria")

    return parsed.astimezone(UTC)


def epoch_ms(iso):
    return round(parse_utc(iso).timestamp() * 1000)


def iso_from_ms(value):
    return dt.datetime.fromtimestamp(
        value / 1000, UTC
    ).isoformat()


def ema(closes, period):
    if len(closes) < period:
        raise ValueError("Velas insuficientes para EMA")

    value = sum(closes[:period]) / period
    alpha = 2.0 / (period + 1)

    for close in closes[period:]:
        value = alpha * close + (1 - alpha) * value

    return value


def rsi_wilder(closes, period=14):
    if len(closes) <= period:
        raise ValueError("Velas insuficientes para RSI")

    changes = [
        b - a
        for a, b in zip(closes, closes[1:])
    ]

    gain = sum(
        max(0, x) for x in changes[:period]
    ) / period

    loss = sum(
        max(0, -x) for x in changes[:period]
    ) / period

    for change in changes[period:]:
        gain = (
            gain * (period - 1) + max(change, 0)
        ) / period

        loss = (
            loss * (period - 1) + max(-change, 0)
        ) / period

    if loss == 0:
        return 100.0 if gain > 0 else 50.0

    return 100 - 100 / (1 + gain / loss)


def audit_one(record):
    symbol = record["symbol"]
    interval = record["interval"]

    issues = []
    comparisons = {}

    try:
        duration_ms = INTERVALS[interval] * 1000
        count = int(record["closed_candles_used"])

        if count < 200 or count > 1000:
            raise ValueError(
                "Cantidad de velas fuera del rango admitido"
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
                "Duracion incorrecta de ultima vela"
            )

        first_open_ms = (
            last_open_ms - (count - 1) * duration_ms
        )

        params = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": interval,
            "startTime": first_open_ms,
            "endTime": last_close_ms,
            "limit": count,
        })

        rows = fetch(API + "?" + params)

        if not isinstance(rows, list):
            raise ValueError(
                "Respuesta de Binance no es lista"
            )

        if len(rows) != count:
            raise ValueError(
                f"Esperadas {count} velas, "
                f"recibidas {len(rows)}"
            )

        for index, row in enumerate(rows):
            expected_open = (
                first_open_ms + index * duration_ms
            )

            expected_close = (
                expected_open + duration_ms - 1
            )

            if (
                int(row[0]) != expected_open
                or int(row[6]) != expected_close
            ):
                raise ValueError(
                    f"Vela faltante, duplicada "
                    f"o irregular en indice {index}"
                )

            op, hi, lo, cl, vol = map(
                float, row[1:6]
            )

            values = (
                op, hi, lo, cl,
                vol, float(row[7])
            )

            if not all(map(math.isfinite, values)):
                raise ValueError(
                    f"Valor no finito en vela {index}"
                )

            if not (
                hi >= max(op, cl, lo)
                and lo <= min(op, cl, hi)
                and vol >= 0
                and float(row[7]) >= 0
            ):
                raise ValueError(
                    f"OHLCV inconsistente en vela {index}"
                )

        closes = [
            float(row[4]) for row in rows
        ]

        volumes = [
            float(row[5]) for row in rows
        ]

        avg20 = sum(volumes[-20:]) / 20

        actual = {
            "last_open": float(rows[-1][1]),
            "last_high": float(rows[-1][2]),
            "last_low": float(rows[-1][3]),
            "last_close": closes[-1],
            "last_volume_base": volumes[-1],
            "last_volume_quote": float(rows[-1][7]),
            "ema9": ema(closes, 9),
            "ema21": ema(closes, 21),
            "ema200": ema(closes, 200),
            "rsi14_wilder": rsi_wilder(closes),
            "volume_average_20": avg20,
            "volume_ratio_20": (
                volumes[-1] / avg20
                if avg20 > 0 else None
            ),
        }

        tolerances = {
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

        for field, tolerance in tolerances.items():
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

            difference = abs(
                float(published) - calculated
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

            if (
                not math.isfinite(float(published))
                or difference > tolerance
            ):
                issues.append(
                    f"{field}: diferencia "
                    f"{difference:.8f}; "
                    f"tolerancia {tolerance}"
                )

        return {
            "series": f"{symbol} spot {interval}",
            "status": (
                "PASS" if not issues else "FAIL"
            ),
            "candles_checked": count,
            "issues": issues,
            "comparisons": comparisons,
        }

    except Exception as exc:
        return {
            "series": f"{symbol} spot {interval}",
            "status": "UNAVAILABLE",
            "issues": [
                f"{type(exc).__name__}: {exc}"
            ],
            "comparisons": {},
        }


def audit_integrity(record, generated):
    """Verifica la ultima vela esperada al publicar."""

    symbol = record["symbol"]
    interval = record["interval"]

    duration_ms = INTERVALS[interval] * 1000
    generated_ms = round(generated.timestamp() * 1000)

    expected_open_ms = (
        (generated_ms // duration_ms - 1)
        * duration_ms
    )

    actual_open_ms = epoch_ms(
        record["last_closed_candle_open_utc"]
    )

    actual_close_ms = epoch_ms(
        record["last_closed_candle_close_utc"]
    )

    boundary_delay_ms = (
        generated_ms % duration_ms
    )

    issues = []

    if actual_close_ms != (
        actual_open_ms + duration_ms - 1
    ):
        issues.append(
            "Timestamp de cierre inconsistente"
        )

    if actual_open_ms % duration_ms != 0:
        issues.append(
            "Timestamp de apertura desalineado"
        )

    if actual_close_ms >= generated_ms:
        issues.append(
            "Vela aun abierta al generar JSON"
        )

    candle_lag = (
        expected_open_ms - actual_open_ms
    ) // duration_ms

    if issues:
        status = "FAIL"

    elif actual_open_ms == expected_open_ms:
        status = "PASS"

    elif (
        boundary_delay_ms < 60000
        and actual_open_ms
        == expected_open_ms - duration_ms
    ):
        status = "REVIEW_BOUNDARY"
        issues.append(
            "Una vela de retraso durante los "
            "primeros 60 segundos del intervalo"
        )

    else:
        status = "FAIL"
        issues.append(
            "Ultima vela diferente a la esperada"
        )

    return {
        "series": f"{symbol} spot {interval}",
        "status": status,
        "expected_open_utc": iso_from_ms(
            expected_open_ms
        ),
        "actual_open_utc": (
            record["last_closed_candle_open_utc"]
        ),
        "actual_close_utc": (
            record["last_closed_candle_close_utc"]
        ),
        "candle_lag": int(candle_lag),
        "seconds_since_boundary": round(
            boundary_delay_ms / 1000, 2
        ),
        "issues": issues,
    }


def save_report(path, report):
    Path(path).write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--json-url",
        default=DATA_URL,
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
    now = dt.datetime.now(UTC)

    try:
        payload = fetch(args.json_url)

        if payload.get("schema") != (
            "binance-gpt-market-data-v1"
        ):
            raise ValueError(
                "Esquema JSON inesperado"
            )

        generated = parse_utc(
            payload["generated_at_utc"]
        )

        age = (now - generated).total_seconds()

        records = [
            r for r in payload.get("results", [])
            if (
                r.get("status") == "verified"
                and r.get("market") == "spot"
                and r.get("symbol") in SYMBOLS
                and r.get("interval") in INTERVALS
            )
        ]

        details = [
            audit_one(record)
            for record in records
        ]

        integrity_details = [
            audit_integrity(record, generated)
            for record in records
        ]

        n_pass = sum(
            x["status"] == "PASS"
            for x in details
        )

        n_fail = sum(
            x["status"] == "FAIL"
            for x in details
        )

        n_unavailable = sum(
            x["status"] == "UNAVAILABLE"
            for x in details
        )

        series_keys = {
            (r["symbol"], r["interval"])
            for r in records
        }

        expected_keys = {
            (symbol, interval)
            for symbol in SYMBOLS
            for interval in INTERVALS
        }

        coverage_ok = (
            len(records) == 6
            and series_keys == expected_keys
        )

        mathematical_pass = (
            coverage_ok
            and n_pass == 6
            and n_fail == 0
            and n_unavailable == 0
        )

        integrity_pass_count = sum(
            x["status"] == "PASS"
            for x in integrity_details
        )

        integrity_review_count = sum(
            x["status"] == "REVIEW_BOUNDARY"
            for x in integrity_details
        )

        integrity_fail_count = sum(
            x["status"] == "FAIL"
            for x in integrity_details
        )

        if (
            coverage_ok
            and integrity_pass_count == 6
        ):
            integrity_status = "PASS"

        elif (
            coverage_ok
            and integrity_fail_count == 0
            and integrity_review_count > 0
        ):
            integrity_status = "REVIEW"

        else:
            integrity_status = "FAIL"

        fresh = (
            0 <= age <= args.max_json_age
        )

        futures_errors = sum(
            e.get("market") == "futures_usdm"
            for e in payload.get("errors", [])
        )

        if (
            mathematical_pass
            and integrity_status == "PASS"
            and fresh
        ):
            operational_status = "DATA_CHECKS_OK"

        elif (
            mathematical_pass
            and integrity_status == "PASS"
            and not fresh
        ):
            operational_status = "REVIEW_ONLY"

        else:
            operational_status = "NOT_READY"

        report = {
            "audit_version": "3.0",
            "audited_at_utc": now.isoformat(),
            "json_generated_at_utc": (
                generated.isoformat()
            ),
            "json_age_seconds_at_audit_start": (
                round(age, 1)
            ),
            "json_current": fresh,
            "mathematical_status": (
                "PASS" if mathematical_pass
                else "FAIL"
            ),
            "integrity_status": integrity_status,
            "integrity_pass": integrity_pass_count,
            "integrity_review": integrity_review_count,
            "integrity_fail": integrity_fail_count,
            "freshness_status": (
                "CURRENT" if fresh else "STALE"
            ),
            "spot_series_audited": len(details),
            "pass": n_pass,
            "fail": n_fail,
            "unavailable": n_unavailable,
            "futures_errors_declared": futures_errors,
            "operational_status": operational_status,
            "note": (
                "Datos Spot de velas cerradas. "
                "La validacion no constituye "
                "una senal de trading. "
                "Futuros no auditados."
            ),
            "details": details,
            "integrity_details": integrity_details,
        }

        save_report(args.output, report)

        print(json.dumps(
            {
                k: v for k, v in report.items()
                if k not in (
                    "details", "integrity_details"
                )
            },
            indent=2,
            ensure_ascii=False,
        ))

        # El codigo de salida refleja tambien
        # la integridad de las velas.
        # La frescura queda como control separado.
        return 0 if (
            mathematical_pass
            and integrity_status == "PASS"
        ) else 1

    except Exception as exc:
        report = {
            "audit_version": "3.0",
            "mathematical_status": "UNAVAILABLE",
            "integrity_status": "UNAVAILABLE",
            "operational_status": "NOT_READY",
            "reason": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

        save_report(args.output, report)

        print(
            json.dumps(
                report,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    sys.exit(main())
