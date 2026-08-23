#!/usr/bin/env python3
"""Nord Pool service unit conversion. Run: python3 tests/test_price_units.py"""

from __future__ import annotations

import importlib.util
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

def find_root() -> Path:
    for candidate in (Path("/config"), Path("/Volumes/config"), Path(__file__).resolve().parents[2]):
        if (candidate / "custom_components" / "energy_tariff_compare" / "tariffs.py").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


ROOT = find_root()
TZ = ZoneInfo("Europe/Berlin")
_spec = importlib.util.spec_from_file_location(
    "etc_tariffs", ROOT / "custom_components/energy_tariff_compare/tariffs.py"
)
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)


def check(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    print("OK", msg)


def main():
    conv = T.nordpool_service_price_to_eur_kwh
    cases = {
        100.00: 0.10000,
        0.96: 0.00096,
        -1.73: -0.00173,
        -50.00: -0.05000,
        2.00: 0.00200,
        -2.00: -0.00200,
        0.0: 0.0,
    }
    for src, expect in cases.items():
        got = conv(src)
        check(got is not None and abs(got - expect) < 1e-12, f"{src} EUR/MWh -> {got} EUR/kWh (expected {expect})")

    check(conv(None) is None, "None rejected")
    check(conv("n/a") is None, "non-numeric rejected")
    check(conv(math.nan) is None, "NaN rejected")
    check(conv(math.inf) is None, "Inf rejected")
    check(conv(-math.inf) is None, "-Inf rejected")

    now = datetime(2026, 8, 22, 2, 0, 20, tzinfo=TZ)
    start, end = T.closed_interval_utc(now)
    check(start.astimezone(TZ) == datetime(2026, 8, 22, 1, 45, tzinfo=TZ), "02:00:20 local closes [01:45, 02:00)")
    check(end.astimezone(TZ) == datetime(2026, 8, 22, 2, 0, tzinfo=TZ), "02:00:20 local slot end 02:00")

    exact = datetime(2026, 8, 22, 2, 0, 0, tzinfo=TZ)
    start, end = T.closed_interval_utc(exact)
    check(start.astimezone(TZ) == datetime(2026, 8, 22, 1, 45, tzinfo=TZ), "exact 02:00 still closes previous slot")

    mid = datetime(2026, 8, 22, 1, 59, 59, tzinfo=TZ)
    start, end = T.closed_interval_utc(mid)
    check(start.astimezone(TZ) == datetime(2026, 8, 22, 1, 30, tzinfo=TZ), "01:59:59 closes [01:30, 01:45)")

    utc_sample = datetime(2026, 8, 22, 0, 15, 20, tzinfo=timezone.utc)
    start, end = T.closed_interval_utc(utc_sample)
    check(start == datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc), "UTC floor 00:15:20 -> start 00:00Z")
    check(end == datetime(2026, 8, 22, 0, 15, tzinfo=timezone.utc), "UTC floor 00:15:20 -> end 00:15Z")

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
