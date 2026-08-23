#!/usr/bin/env python3
"""Closed-slot collector tests. Run: python3 tests/test_collector_slots.py"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/config") if Path("/config/energy_tariff_compare/tariffs.yaml").exists() else Path("/Volumes/config")
PKG = ROOT / "custom_components" / "energy_tariff_compare"
TZ = ZoneInfo("Europe/Berlin")
IMP = "sensor.electricity_bogenstr_5_gesamtbezug"
EXP = "sensor.electricity_bogenstr_5_gesamteinspeisung"


def load_pkg():
    name = "energy_tariff_compare"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(PKG)]
        sys.modules[name] = pkg
    mods = {}
    for mod_name in ("store", "tariffs", "collector"):
        full = f"{name}.{mod_name}"
        if full in sys.modules:
            mods[mod_name] = sys.modules[full]
            continue
        spec = importlib.util.spec_from_file_location(full, PKG / f"{mod_name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        mods[mod_name] = mod
    return mods


def check(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    print("OK", msg)


def dt(h, m, s=20):
    return datetime(2026, 8, 22, h, m, s, tzinfo=TZ)


def readings(kwh, when, exp=0.0):
    return {
        IMP: {"state": str(kwh), "last_updated": when},
        EXP: {"state": str(exp), "last_updated": when},
    }


def main():
    mods = load_pkg()
    Store = mods["store"].Store
    T = mods["tariffs"]
    coll = mods["collector"]
    cfg = T.load_config(ROOT / "energy_tariff_compare" / "tariffs.yaml")

    tmp = tempfile.TemporaryDirectory()
    store = Store(Path(tmp.name) / "energy.sqlite")

    t045 = dt(1, 45)
    t200 = dt(2, 0)
    start_145, end_200 = T.closed_interval_utc(t200)
    check(start_145.astimezone(TZ) == dt(1, 45, 0), "closed slot start 01:45")
    check(end_200.astimezone(TZ) == dt(2, 0, 0), "closed slot end 02:00")

    first = coll.collect_tick(store, cfg, t045, readings(100.0, t045))
    check(first["quality"] == "bootstrap", f"first sample is baseline, got {first['quality']}")
    check(store.get_interval(coll.utc_iso(start_145)) is None, "bootstrap does not invent a kWh row")
    check(store.get_meta("last_import") == "100.0", "baseline stores last_import")

    second = coll.collect_tick(store, cfg, t200, readings(101.0, t200))
    row = store.get_interval(coll.utc_iso(start_145))
    check(row is not None, "closed slot was written")
    check(abs(float(row["grid_import_kwh"]) - 1.0) < 1e-9, f"1.0 kWh for [01:45, 02:00), got {row['grid_import_kwh']}")
    check(row["interval_end"] == coll.utc_iso(end_200), "interval_end is 02:00")
    open_start, _ = T.closed_interval_utc(dt(2, 15))
    check(store.get_interval(coll.utc_iso(open_start)) is None, "did not write the still-open [02:00, 02:15)")

    third = coll.collect_tick(store, cfg, dt(2, 0, 25), readings(101.0, t200))
    fourth = coll.collect_tick(store, cfg, dt(2, 0, 40), readings(101.0, t200))
    check(abs(float(store.get_interval(coll.utc_iso(start_145))["grid_import_kwh"]) - 1.0) < 1e-9, "same source ts does not change kWh")
    check(third["grid_import_kwh"] == second["grid_import_kwh"], "second repeat returns same energy")
    check(fourth["interval_start"] == second["interval_start"], "third repeat stays on same slot")

    tmp2 = tempfile.TemporaryDirectory()
    store2 = Store(Path(tmp2.name) / "energy.sqlite")
    boot = coll.collect_tick(store2, cfg, t200, readings(250.0, t200))
    check(boot["quality"] == "bootstrap", "restart without prior sample is baseline")
    check(boot.get("grid_import_kwh") is None, "restart does not invent consumption")
    check(store2.get_interval(coll.utc_iso(start_145)) is None, "no fantom interval after restart")

    tmp3 = tempfile.TemporaryDirectory()
    store3 = Store(Path(tmp3.name) / "energy.sqlite")
    backfill = {
        "interval_start": coll.utc_iso(start_145),
        "interval_end": coll.utc_iso(end_200),
        "local_start": start_145.astimezone(TZ).replace(microsecond=0).isoformat(),
        "grid_import_kwh": 5.0,
        "grid_export_kwh": 0.0,
        "counter_import": 100.0,
        "counter_export": 0.0,
        "nordpool_eur_kwh": 0.08,
        "cost_octopus_heat": 1.0,
        "cost_octopus_heat_loyalty": 1.0,
        "cost_naturwerke_fix": 1.0,
        "cost_dynamic": 1.0,
        "cost_dynamic_modul3": 1.0,
        "quality": "backfilled",
        "sources": "inexogy_csv",
        "updated_at": datetime.now(TZ).isoformat(),
    }
    store3.upsert_interval(backfill)
    coll.collect_tick(store3, cfg, t045, readings(100.0, t045))
    live_zero = coll.collect_tick(store3, cfg, t200, readings(100.0, t200))
    kept = store3.get_interval(coll.utc_iso(start_145))
    check(kept["quality"] == "backfilled", f"backfill quality kept, got {kept['quality']}")
    check(abs(float(kept["grid_import_kwh"]) - 5.0) < 1e-9, f"backfill 5.0 kWh not replaced by 0, got {kept['grid_import_kwh']}")
    check(live_zero["grid_import_kwh"] == 5.0, "live upsert returned protected row")

    tmp4 = tempfile.TemporaryDirectory()
    store4 = Store(Path(tmp4.name) / "energy.sqlite")
    coll.collect_tick(store4, cfg, t045, readings(100.0, t045))
    late = coll.collect_tick(store4, cfg, dt(2, 30), readings(103.0, dt(2, 30)))
    start_215, _ = T.closed_interval_utc(dt(2, 30))
    late_row = store4.get_interval(coll.utc_iso(start_215))
    check(late["quality"] == "unallocated", f"45-min gap is unallocated, got {late['quality']}")
    check(late_row is not None and late_row["grid_import_kwh"] is None, "45-min delta is not dumped into one slot")

    tmp6 = tempfile.TemporaryDirectory()
    store6 = Store(Path(tmp6.name) / "energy.sqlite")
    store6.set_meta("last_import", "100.0")
    store6.set_meta("last_export", "0.0")
    store6.set_meta("last_interval_start", coll.utc_iso(start_145))
    restart = coll.collect_tick(store6, cfg, dt(2, 30), readings(104.0, dt(2, 30)))
    restart_row = store6.get_interval(coll.utc_iso(start_215))
    check(restart["quality"] == "unallocated", f"reboot without last_sample is unallocated, got {restart['quality']}")
    check(restart_row is not None and restart_row["grid_import_kwh"] is None, "reboot gap is not dumped into one slot")

    tmp5 = tempfile.TemporaryDirectory()
    store5 = Store(Path(tmp5.name) / "energy.sqlite")
    live_spot_readings = readings(100.0, t045)
    live_spot_readings[cfg["entities"]["nordpool_current"]] = {"state": "0.96", "last_updated": t045}
    coll.collect_tick(store5, cfg, t045, live_spot_readings)
    live_spot_readings2 = readings(101.0, t200)
    live_spot_readings2[cfg["entities"]["nordpool_current"]] = {"state": "0.96", "last_updated": t200}
    closed = coll.collect_tick(store5, cfg, t200, live_spot_readings2)
    check(closed.get("nordpool_eur_kwh") is None, "live current sensor is not applied to the previous closed slot")
    check("nordpool_missing" in (closed.get("sources") or ""), "closed slot waits for stored EUR/kWh spots")

    tmp7 = tempfile.TemporaryDirectory()
    store7 = Store(Path(tmp7.name) / "energy.sqlite")
    coll.collect_tick(store7, cfg, t045, readings(100.0, t045))
    coll.collect_tick(store7, cfg, t200, readings(101.0, t200))
    same_slot_new_source = dt(2, 0, 40)
    coll.collect_tick(store7, cfg, same_slot_new_source, readings(101.2, same_slot_new_source))
    check(
        store7.get_meta("last_import") == "101.0",
        "new source report in same slot does not advance the counter baseline",
    )
    next_slot = dt(2, 15)
    carried = coll.collect_tick(store7, cfg, next_slot, readings(102.2, next_slot))
    check(
        abs(float(carried["grid_import_kwh"]) - 1.2) < 1e-9,
        f"same-slot 0.2 kWh is carried into next closed slot, got {carried['grid_import_kwh']}",
    )

    tmp8 = tempfile.TemporaryDirectory()
    store8 = Store(Path(tmp8.name) / "energy.sqlite")
    old_source = dt(1, 35)
    coll.collect_tick(store8, cfg, t200, readings(100.0, old_source))
    source_25m_later = dt(2, 0)
    source_gap = coll.collect_tick(store8, cfg, dt(2, 15), readings(101.0, source_25m_later))
    check(
        source_gap["quality"] == "unallocated",
        f"25-minute source span is unallocated even with 15-minute collector calls, got {source_gap['quality']}",
    )
    check(
        "unallocated_import_kwh=1.000000" in (source_gap.get("sources") or ""),
        "unallocated source delta remains visible for diagnostics",
    )

    tmp9 = tempfile.TemporaryDirectory()
    store9 = Store(Path(tmp9.name) / "energy.sqlite")
    zero_first = readings(0.0, t045)
    zero_first[IMP]["last_reported"] = t045
    coll.collect_tick(store9, cfg, t045, zero_first)
    zero_next = readings(0.0, t200)
    zero_next[IMP]["last_updated"] = t045
    zero_next[IMP]["last_reported"] = t200
    fresh_zero = coll.collect_tick(store9, cfg, t200, zero_next)
    check(
        fresh_zero.get("grid_import_kwh") == 0.0,
        "fresh last_reported records an unchanged real zero instead of treating it as stale",
    )

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
