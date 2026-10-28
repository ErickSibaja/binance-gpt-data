
#!/usr/bin/env python3
"""Auditoria independiente de Binance Spot.
Solo lectura. No usa credenciales ni realiza operaciones.
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
            "User-Agent": "binance-gpt-audit/2.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(
        req, timeout=25
    ) as response:
        return json.load(response)


def epoch_ms(iso):
    value = dt.datetime.fromisoformat(
        iso.replace("Z", "+00:00")
    )
    return round(value.timestamp() * 1000)


def ema(closes, period):
    assert len(closes) >= period

    value = sum(closes[:period]) / period
    alpha = 2.0 / (period + 1)

    for close in closes[period:]:
        value = (
            alpha * close
            + (1 - alpha) * value
        )

    return value


def rsi_wilder(closes, period=14):
    assert len(closes) > period

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
            gain * (period - 1)
            + max(change, 0)
        ) / period

        loss = (
            loss * (period - 1)
            + max(-change, 0)
        ) / period

    if loss == 0:
        return 100.0 if gain > 0 else 50.0

    return 100 - 100 / (1 + gain / loss)


def audit_one(record):
    symbol = record["symbol"]
    interval = record["interval"]

    duration_ms = INTERVALS[interval] * 1000
    count = int(record["closed_candles_used"])

    last_close_ms = epoch_ms(
        record["last_closed_candle_close_utc"]
    )

    last_open_ms = epoch_ms(
        record["last_closed_candle_open_utc"]
    )

    # La ventana se determina por fechas,
    # nunca por el resultado esperado de EMA.
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

    issues = []
    comparisons = {}

    try:
        rows = fetch(API + "?" + params)

        if not isinstance(rows, list):
            raise ValueError(
                "Respuesta no es lista de velas"
            )

        if len(rows) != count:
            raise ValueError(
                f"Esperadas {count}, recibidas {len(rows)}"
            )

        for index, row in enumerate(rows):
            expected_open = (
                first_open_ms + index * duration_ms
            )

            if (
                int(row[0]) != expected_open
                or int(row[6])
                != expected_open + duration_ms - 1
            ):
                raise ValueError(
                    f"Vela faltante o duplicada: {index}"
                )

            op, hi, lo, cl, vol = map(
                float, row[1:6]
            )

            values = (op, hi, lo, cl, vol)

            if not all(map(math.isfinite, values)):
                raise ValueError(
                    f"Valor no finito: {index}"
                )

            if not (
                hi >= max(op, cl, lo)
                and lo <= min(op, cl, hi)
                and vol >= 0
            ):
                raise ValueError(
                    f"OHLCV inconsistente: {index}"
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
                not isinstance(
                    published, (float, int)
                )
                or calculated is None
            ):
                issues.append(
                    f"{field}: valor ausente"
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
                    f"{difference:.8f}, "
                    f"tolerancia {tolerance}"
                )

        return {
            "series": (
                f"{symbol} spot {interval}"
            ),
            "status": (
                "PASS" if not issues else "FAIL"
            ),
            "candles_checked": count,
            "issues": issues,
            "comparisons": comparisons,
        }

    except Exception as exc:
        return {
            "series": (
                f"{symbol} spot {interval}"
            ),
            "status": "UNAVAILABLE",
            "issues": [
                f"{type(exc).__name__}: {exc}"
            ],
            "comparisons": {},
        }


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

        generated = dt.datetime.fromisoformat(
            payload["generated_at_utc"].replace(
                "Z", "+00:00"
            )
        )

        age = (now - generated).total_seconds()

        records = [
            r for r in payload.get("results", [])
            if (
                r.get("status") == "verified"
                and r.get("market") == "spot"
                and r.get("symbol")
                in ("BTCUSDT", "ETHUSDT")
                and r.get("interval") in INTERVALS
            )
        ]

        details = [
            audit_one(record)
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

        uniqueness = (
            len({
                x["series"] for x in details
            }) == len(details)
        )

        mathematical_pass = (
            len(details) == 6
            and uniqueness
            and n_pass == 6
        )

        fresh = (
            0 <= age <= args.max_json_age
        )

        report = {
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
            "freshness_status": (
                "CURRENT" if fresh else "STALE"
            ),
            "spot_series_audited": len(details),
            "pass": n_pass,
            "fail": n_fail,
            "unavailable": n_unavailable,
            "futures_errors_declared": sum(
                e.get("market") == "futures_usdm"
                for e in payload.get("errors", [])
            ),
            "operational_status": (
                "REVIEW_ONLY"
                if mathematical_pass and not fresh
                else "DATA_CHECKS_OK"
                if mathematical_pass
                else "NOT_READY"
            ),
            "note": (
                "PASS matematico no equivale a "
                "senal financiera ni precio live. "
                "Futuros no auditados."
            ),
            "details": details,
        }

        Path(args.output).write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )

        print(json.dumps(
            {
                k: v for k, v in report.items()
                if k != "details"
            },
            indent=2,
            ensure_ascii=False,
        ))

        return 0 if mathematical_pass else 1

    except Exception as exc:
        report = {
            "mathematical_status": "UNAVAILABLE",
            "reason": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

        Path(args.output).write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )

        print(
            json.dumps(
                report, ensure_ascii=False
            ),
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    sys.exit(main())
