#!/usr/bin/env python3
"""A2 missing-spot totals and A4 cheapest-id ranking. python3 tests/test_incomplete_prices.py"""

from __future__ import annotations

import importlib.util
import math
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

def find_root() -> Path:
    for candidate in (Path("/config"), Path("/Volumes/config"), Path(__file__).resolve().parents[2]):
        if (candidate / "custom_components" / "energy_tariff_compare" / "tariffs.py").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


ROOT = find_root()
PKG = ROOT / "custom_components" / "energy_tariff_compare"


def load_pkg():
    name = "energy_tariff_compare_incomplete_price_test"
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(PKG)]
    sys.modules[name] = pkg
    mods = {}
    for mod_name in ("store", "tariffs", "collector"):
        full = f"{name}.{mod_name}"
        spec = importlib.util.spec_from_file_location(full, PKG / f"{mod_name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        mods[mod_name] = mod
    return mods


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print("OK", message)


def interval_rows(T, cfg, day_value: date, count: int, *, blank_spot_at: int | None = None) -> list[dict]:
    start_local = datetime(day_value.year, day_value.month, day_value.day, tzinfo=T.TZ)
    rows = []
    for index in range(count):
        local = (start_local.astimezone(timezone.utc) + timedelta(minutes=15 * index)).astimezone(T.TZ)
        start = local.astimezone(timezone.utc)
        end = start + timedelta(minutes=15)
        spot = None if index == blank_spot_at else 0.05 + (index % 8) * 0.001
        kwh = 0.1 + (index % 4) * 0.01
        costs = {}
        for tariff_id in ("octopus_heat", "octopus_heat_loyalty", "fix_tarif", "dynamic", "dynamic_modul3"):
            price = T.energy_price_gross_eur_per_kwh(cfg, tariff_id, local, spot)
            costs[tariff_id] = None if price is None else round(kwh * price, 6)
        rows.append(
            {
                "interval_start": start.replace(microsecond=0).isoformat(),
                "interval_end": end.replace(microsecond=0).isoformat(),
                "local_start": local.replace(microsecond=0).isoformat(),
                "grid_import_kwh": kwh,
                "grid_export_kwh": 0.0,
                "tesla_kwh": None,
                "counter_import": None,
                "counter_export": None,
                "nordpool_eur_kwh": spot,
                **{f"cost_{tariff_id}": value for tariff_id, value in costs.items()},
                "quality": "incomplete" if spot is None else "backfilled",
                "sources": "incomplete_price_test",
                "updated_at": datetime.now(T.TZ).isoformat(),
            }
        )
    return rows


def main():
    mods = load_pkg()
    Store = mods["store"].Store
    T = mods["tariffs"]
    C = mods["collector"]
    tariff_file = (
        ROOT / "energy_tariff_compare" / "tariffs.yaml"
        if (ROOT / "energy_tariff_compare" / "tariffs.yaml").exists()
        else ROOT / "energy_tariff_compare" / "tariffs.example.yaml"
    )
    cfg = T.load_config(tariff_file)
    day_value = date(2026, 8, 20)

    check(C.cheapest_working_price_ids({}) == [], "no prices -> no cheapest ids")
    check(
        C.cheapest_working_price_ids(
            {tid: None for tid in C.COST_KEYS}
        )
        == [],
        "all None -> no green winner",
    )
    check(
        C.cheapest_working_price_ids(
            {tid: float("nan") for tid in C.COST_KEYS}
        )
        == [],
        "NaN is not a cheapest price",
    )
    ids = C.cheapest_working_price_ids(
        {
            "octopus_heat": 28.0,
            "octopus_heat_loyalty": 35.0,
            "fix_tarif": 31.0,
            "dynamic": 21.0,
            "dynamic_modul3": 21.0,
        }
    )
    check(set(ids) == {"dynamic", "dynamic_modul3"}, f"tie keeps both cheapest, got {ids}")
    check("octopus_heat" not in ids, "more expensive Heat is not cheapest")

    now = datetime(2026, 8, 20, 12, 0, tzinfo=T.TZ)
    none_spot = C.current_prices(cfg, now, None, None)
    check(none_spot["dynamic"] is None, "no live spot -> no dynamic Arbeitspreis")
    check(none_spot["octopus_heat"] is not None, "Heat stays without spot")
    check(none_spot["current_prices_complete"] is False, "incomplete live set")
    check(none_spot["cheapest_current_ids"], "Heat-family still ranks if numeric")
    check("dynamic" not in none_spot["cheapest_current_ids"], "missing dynamic is not cheapest")

    all_none_live = dict(none_spot)
    for tid in C.COST_KEYS:
        all_none_live[tid] = None
    check(C.cheapest_working_price_ids(all_none_live) == [], "total outage -> empty cheapest list")

    check(C.parse_float("1.5") == 1.5, "parse_float keeps finite numbers")
    check(C.parse_float("nan") is None, "parse_float rejects nan")
    check(C.parse_float("inf") is None, "parse_float rejects inf")
    check(C.parse_float("-inf") is None, "parse_float rejects -inf")
    check(C.parse_float(float("nan")) is None, "parse_float rejects float nan")
    check(C.parse_float(float("inf")) is None, "parse_float rejects float inf")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "energy.sqlite")
        check(C.spot_day_complete(store, day_value) is False, "empty store is not spot-complete")
        start_local = datetime(day_value.year, day_value.month, day_value.day, tzinfo=T.TZ)
        fetched = datetime.now(T.TZ).isoformat()
        spots = []
        for index in range(T.expected_intervals(day_value)):
            start = start_local.astimezone(timezone.utc) + timedelta(minutes=15 * index)
            end = start + timedelta(minutes=15)
            spots.append((C.utc_iso(start), C.utc_iso(end), 0.05, fetched))
        store.bulk_upsert_spots(spots)
        check(C.spot_day_complete(store, day_value) is True, "96 slots complete a normal Berlin day")
        check(C.spot_day_complete(store, date(2026, 3, 29)) is False, "spring DST day without rows is incomplete")
        check(C.tomorrow_retry_delay(0) == 120, "first tomorrow retry is 2 minutes")
        check(C.tomorrow_retry_delay(1) == 300, "second tomorrow retry is 5 minutes")
        check(C.tomorrow_retry_delay(2) == 600, "third tomorrow retry is 10 minutes")
        check(C.tomorrow_retry_delay(3) == 900, "fourth tomorrow retry is 15 minutes")
        check(C.tomorrow_retry_delay(4) is None, "no fifth tomorrow retry")

        five_min_store = Store(Path(tmp) / "five_min.sqlite")
        five_start = start_local.astimezone(timezone.utc)
        five_spots = []
        for index in range(96):
            start = five_start + timedelta(minutes=5 * index)
            end = start + timedelta(minutes=5)
            five_spots.append((C.utc_iso(start), C.utc_iso(end), 0.05, fetched))
        five_min_store.bulk_upsert_spots(five_spots)
        check(
            C.spot_day_complete(five_min_store, day_value) is False,
            "96 five-minute points are not a complete 15-minute raster",
        )

        spring = date(2026, 3, 29)
        fall = date(2026, 10, 25)
        check(T.expected_intervals(spring) == 92, "spring DST day has 92 slots")
        check(T.expected_intervals(fall) == 100, "autumn DST day has 100 slots")
        for dst_day, label in ((spring, "92"), (fall, "100")):
            dst_store = Store(Path(tmp) / f"dst_{label}.sqlite")
            dst_start = datetime(dst_day.year, dst_day.month, dst_day.day, tzinfo=T.TZ)
            dst_spots = []
            for index in range(T.expected_intervals(dst_day)):
                start = dst_start.astimezone(timezone.utc) + timedelta(minutes=15 * index)
                end = start + timedelta(minutes=15)
                dst_spots.append((C.utc_iso(start), C.utc_iso(end), 0.05, fetched))
            dst_store.bulk_upsert_spots(dst_spots)
            check(
                C.spot_day_complete(dst_store, dst_day) is True,
                f"{label} raster slots complete that DST day",
            )
            dropped = dst_spots[10][0]
            dst_store._exec("DELETE FROM spot_prices WHERE interval_start=?", (dropped,))
            check(
                C.spot_day_complete(dst_store, dst_day) is False,
                f"{label} day with one missing raster slot is incomplete",
            )

        rows = interval_rows(T, cfg, day_value, T.expected_intervals(day_value), blank_spot_at=10)
        rows[10]["tesla_kwh"] = 2.0
        store.bulk_upsert_intervals(rows)
        daily = C.rebuild_day(store, cfg, day_value, complete=True, cascade=False)
        check(daily["price_intervals_missing"] == 1, "one consumed slot without spot is counted")
        check(daily["energy_cost_octopus_heat"] is not None, "Heat Arbeit remains with missing spot")
        check(daily["cost_octopus_heat"] is not None, "Heat Gesamt remains with missing spot")
        check(daily["energy_cost_dynamic"] is None, "Dynamisch Arbeit is None, not a cheap partial sum")
        check(daily["cost_dynamic"] is None, "Dynamisch Gesamt is None, not a cheap partial sum")
        check(daily["energy_cost_dynamic_modul3"] is None, "Modul 3 Arbeit is None")
        check(daily["cost_dynamic_modul3"] is None, "Modul 3 Gesamt is None")
        check(daily["cost_dynamic_perfect"] is None, "Perfect stays withheld")
        ranking_ok = (
            int(daily["intervals_missing"] or 0) == 0
            and int(daily["price_intervals_missing"] or 0) == 0
        )
        check(ranking_ok is False, "ranking is not complete with a missing spot")

        check(daily["tesla_cost_dynamic"] is None, "day wallbox Dynamisch is None when that slot has no spot")
        check(daily["tesla_cost_octopus_heat"] is not None, "day wallbox Heat still prices without spot")
        month = C.rebuild_month(store, cfg, 2026, 8)
        check(month["cost_dynamic"] is None, "month Dynamisch does not inherit a partial daily total")
        check(month["cost_octopus_heat"] is not None, "month Heat still sums")
        check(month["tesla_cost_dynamic"] is None, "month wallbox Dynamisch is unknown, not 0.00")
        check(month["tesla_cost_octopus_heat"] is not None, "month wallbox Heat still sums")
        check(
            C.period_ranking_complete(month, period="month") is False,
            "month ranking is closed while a day is price-incomplete",
        )

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
