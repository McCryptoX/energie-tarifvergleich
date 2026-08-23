#!/usr/bin/env python3
"""Tesla wallbox deltas and recorder gap repair. Run: python3 tests/test_tesla_and_gaps.py"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

def find_root() -> Path:
    for candidate in (Path("/config"), Path("/Volumes/config"), Path(__file__).resolve().parents[2]):
        if (candidate / "custom_components" / "energy_tariff_compare" / "tariffs.py").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


ROOT = find_root()
PKG = ROOT / "custom_components" / "energy_tariff_compare"
TZ = ZoneInfo("Europe/Berlin")
IMP = "sensor.electricity_smartmeter_gesamtbezug"
EXP = "sensor.electricity_smartmeter_gesamteinspeisung"
TESLA = "sensor.tesla_wall_connector_energy"


def load_pkg():
    name = "energy_tariff_compare_tesla_test"
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


def dt(day: date, h, m, s=20):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=TZ)


def readings(kwh, when, exp=0.0, tesla=None):
    out = {
        IMP: {"state": str(kwh), "last_updated": when},
        EXP: {"state": str(exp), "last_updated": when},
    }
    if tesla is not None:
        out[TESLA] = {"state": str(tesla), "last_updated": when}
    return out


def main():
    global IMP, EXP, TESLA
    mods = load_pkg()
    Store = mods["store"].Store
    T = mods["tariffs"]
    coll = mods["collector"]
    tariff_file = (
        ROOT / "energy_tariff_compare" / "tariffs.yaml"
        if (ROOT / "energy_tariff_compare" / "tariffs.yaml").exists()
        else ROOT / "energy_tariff_compare" / "tariffs.example.yaml"
    )
    cfg = T.load_config(tariff_file)
    IMP = cfg["entities"]["grid_import"]
    EXP = cfg["entities"].get("grid_export", "sensor.grid_export")
    TESLA = cfg["entities"].get("tesla_energy", "sensor.tesla_wall_connector_energy")
    check(cfg["entities"].get("tesla_energy") == TESLA, "tesla_energy is configured")

    day = date(2026, 8, 22)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "tesla.sqlite")
        t045 = dt(day, 1, 45)
        t200 = dt(day, 2, 0)
        first = coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=50.0))
        check(first["quality"] == "bootstrap", f"tesla first sample is baseline, got {first['quality']}")
        check(store.get_meta("last_tesla") == "50.0", "baseline stores last_tesla")
        check(store.get_meta("tesla_count_started_utc") is not None, "tesla count start is recorded")

        second = coll.collect_tick(store, cfg, t200, readings(101.0, t200, tesla=52.5))
        start_145, _ = T.closed_interval_utc(t200)
        row = store.get_interval(coll.utc_iso(start_145))
        check(row is not None, "closed slot was written")
        check(abs(float(row["tesla_kwh"]) - 2.5) < 1e-9, f"tesla delta 2.5 kWh, got {row['tesla_kwh']}")
        check(abs(float(row["grid_import_kwh"]) - 1.0) < 1e-9, "grid import still 1.0 kWh")
        daily = coll.rebuild_day(store, cfg, day, complete=True, cascade=False)
        check(abs(float(daily["tesla_kwh"]) - 2.5) < 1e-9, "daily tesla_kwh sums the slot")
        heat_price = T.energy_price_gross_eur_per_kwh(cfg, "octopus_heat", start_145.astimezone(TZ), None)
        check(heat_price is not None, "heat price exists")
        check(
            abs(float(daily["tesla_cost_octopus_heat"]) - 2.5 * heat_price) < 1e-6,
            "tesla cost is Arbeitspreis × kWh without standing",
        )
        standing = float(daily["standing_cost_octopus_heat"])
        check(standing > 0, "grid standing remains on the day total")
        check(
            abs(float(daily["cost_octopus_heat"]) - float(daily["energy_cost_octopus_heat"]) - standing) < 1e-6,
            "grid total still includes standing; tesla does not add standing",
        )

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "tesla_import_idle.sqlite")
        t045 = dt(day, 1, 45)
        t200 = dt(day, 2, 0)
        coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=50.0))
        idle = {
            IMP: {"state": "100.0", "last_updated": t045, "last_reported": t045},
            EXP: {"state": "0.0", "last_updated": t045, "last_reported": t045},
            TESLA: {"state": "53.0", "last_updated": t200, "last_reported": t200},
        }
        only_tesla = coll.collect_tick(store, cfg, t200, idle)
        start_145, _ = T.closed_interval_utc(t200)
        row = store.get_interval(coll.utc_iso(start_145))
        check(row is not None, "tesla-only tick still writes the closed slot")
        check(abs(float(row["tesla_kwh"]) - 3.0) < 1e-9, f"tesla 3.0 kWh while grid idle, got {row['tesla_kwh']}")
        check(abs(float(row["grid_import_kwh"]) - 0.0) < 1e-9, "grid import stays 0 when Discovergy did not move")
        check("import_unchanged" in (only_tesla.get("sources") or ""), "import_unchanged is recorded")
        repeat = coll.collect_tick(store, cfg, dt(day, 2, 0, 40), idle)
        row2 = store.get_interval(coll.utc_iso(start_145))
        check(abs(float(row2["tesla_kwh"]) - 3.0) < 1e-9, "same tesla sample is not booked twice")
        check(repeat.get("tesla_kwh") == row2.get("tesla_kwh"), "repeat returns the protected same-slot row")

    def run_protected_tesla(quality: str, label: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / f"tesla_{quality}.sqlite")
            t045 = dt(day, 1, 45)
            t200 = dt(day, 2, 0)
            coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=50.0))
            start_145, end_145 = T.closed_interval_utc(t200)
            local = start_145.astimezone(TZ)
            store.upsert_interval(
                {
                    "interval_start": coll.utc_iso(start_145),
                    "interval_end": coll.utc_iso(end_145),
                    "local_start": local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": 0.4,
                    "grid_export_kwh": 0.0,
                    "tesla_kwh": None,
                    "counter_import": 100.0,
                    "counter_export": 0.0,
                    "nordpool_eur_kwh": 0.05,
                    "cost_octopus_heat": 0.1,
                    "cost_octopus_heat_loyalty": 0.1,
                    "cost_fix_tarif": 0.1,
                    "cost_dynamic": 0.1,
                    "cost_dynamic_modul3": 0.1,
                    "quality": quality,
                    "sources": f"{quality}_seed",
                    "updated_at": datetime.now(TZ).isoformat(),
                }
            )
            idle = {
                IMP: {"state": "100.0", "last_updated": t045, "last_reported": t045},
                EXP: {"state": "0.0", "last_updated": t045, "last_reported": t045},
                TESLA: {"state": "53.0", "last_updated": t200, "last_reported": t200},
            }
            coll.collect_tick(store, cfg, t200, idle)
            row = store.get_interval(coll.utc_iso(start_145))
            check(row is not None, f"{label}: target interval still exists")
            check(
                abs(float(row["grid_import_kwh"]) - 0.4) < 1e-9,
                f"{label}: grid kWh of protected/repaired slot is kept",
            )
            check(
                abs(float(row["tesla_kwh"]) - 3.0) < 1e-9,
                f"{label}: tesla 3.0 kWh merged onto the slot, got {row.get('tesla_kwh')}",
            )
            check(
                abs(float(store.get_meta("last_tesla")) - 53.0) < 1e-9,
                f"{label}: last_tesla advances only after tesla is booked",
            )

    run_protected_tesla("backfilled", "backfilled target")
    run_protected_tesla("repaired", "repaired target")

    def run_occupied_tesla(quality: str, label: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / f"tesla_occupied_{quality}.sqlite")
            t045 = dt(day, 1, 45)
            t200 = dt(day, 2, 0)
            coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=50.0))
            start_145, end_145 = T.closed_interval_utc(t200)
            local = start_145.astimezone(TZ)
            store.upsert_interval(
                {
                    "interval_start": coll.utc_iso(start_145),
                    "interval_end": coll.utc_iso(end_145),
                    "local_start": local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": 0.4,
                    "grid_export_kwh": 0.0,
                    "tesla_kwh": 1.0,
                    "counter_import": 100.0,
                    "counter_export": 0.0,
                    "nordpool_eur_kwh": 0.05,
                    "cost_octopus_heat": 0.1,
                    "cost_octopus_heat_loyalty": 0.1,
                    "cost_fix_tarif": 0.1,
                    "cost_dynamic": 0.1,
                    "cost_dynamic_modul3": 0.1,
                    "quality": quality,
                    "sources": f"{quality}_occupied",
                    "updated_at": datetime.now(TZ).isoformat(),
                }
            )
            idle = {
                IMP: {"state": "100.0", "last_updated": t045, "last_reported": t045},
                EXP: {"state": "0.0", "last_updated": t045, "last_reported": t045},
                TESLA: {"state": "53.0", "last_updated": t200, "last_reported": t200},
            }
            coll.collect_tick(store, cfg, t200, idle)
            row = store.get_interval(coll.utc_iso(start_145))
            check(
                abs(float(row["grid_import_kwh"]) - 0.4) < 1e-9,
                f"{label}: grid kWh kept on occupied slot",
            )
            check(
                abs(float(row["tesla_kwh"]) - 4.0) < 1e-9,
                f"{label}: existing 1 kWh plus new 3 kWh, got {row.get('tesla_kwh')}",
            )
            check(
                abs(float(store.get_meta("last_tesla")) - 53.0) < 1e-9,
                f"{label}: last_tesla advances only after the added delta is stored",
            )
            check(store.get_meta("tesla_pending_kwh") is None, f"{label}: no pending after add")

    run_occupied_tesla("backfilled", "occupied backfilled")
    run_occupied_tesla("repaired", "occupied repaired")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "unalloc.sqlite")
        t045 = dt(day, 1, 45)
        coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=10.0))
        late = coll.collect_tick(store, cfg, dt(day, 2, 30), readings(103.0, dt(day, 2, 30), tesla=16.0))
        check(late["quality"] == "unallocated", f"multi-slot still unallocated, got {late['quality']}")
        check(late.get("tesla_kwh") is None, "tesla kWh is not dumped into an unallocated slot")
        check("unallocated_tesla_kwh=6.000000" in (late.get("sources") or ""), "unallocated tesla delta is visible")
        check(
            abs(float(store.get_meta("last_tesla")) - 10.0) < 1e-9,
            f"unallocated tesla does not advance last_tesla, got {store.get_meta('last_tesla')}",
        )
        pending = store.get_meta("tesla_pending_kwh")
        check(
            pending is not None and abs(float(pending) - 6.0) < 1e-6,
            f"unallocated tesla is pending 6 kWh, got {pending}",
        )
        flushed = coll.collect_tick(
            store, cfg, dt(day, 2, 45), readings(103.2, dt(day, 2, 45), tesla=16.0)
        )
        start_230, _ = T.closed_interval_utc(dt(day, 2, 45))
        row_flush = store.get_interval(coll.utc_iso(start_230))
        check(row_flush is not None, "next safe slot exists after unallocated gap")
        check(
            abs(float(row_flush["tesla_kwh"]) - 6.0) < 1e-9,
            f"pending 6 kWh flushed onto next safe slot, got {row_flush.get('tesla_kwh')}",
        )
        check("tesla_pending_flushed" in (flushed.get("sources") or ""), "flush is visible in sources")
        check(
            abs(float(store.get_meta("last_tesla")) - 16.0) < 1e-9,
            "last_tesla advances only after the flush is booked",
        )
        check(store.get_meta("tesla_pending_kwh") is None, "pending is cleared after flush")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "tesla_bootstrap_gap.sqlite")
        t045 = dt(day, 1, 45)
        t200 = dt(day, 2, 0)
        t215 = dt(day, 2, 15)
        missing_grid = {
            IMP: {"state": None, "last_updated": t045, "last_reported": t045},
            EXP: {"state": "0.0", "last_updated": t045, "last_reported": t045},
            TESLA: {"state": "10.0", "last_updated": t045, "last_reported": t045},
        }
        first = coll.collect_tick(store, cfg, t045, missing_grid)
        check(first["quality"] == "bootstrap", "first collect without grid is bootstrap")
        check(abs(float(store.get_meta("last_tesla")) - 10.0) < 1e-9, "first tesla baseline is 10")
        second = coll.collect_tick(store, cfg, t200, readings(100.0, t200, tesla=16.0))
        check(second["quality"] == "bootstrap", "still bootstrap until last_import exists after this tick")
        check(
            abs(float(store.get_meta("last_tesla")) - 10.0) < 1e-9,
            f"second bootstrap does not jump last_tesla, got {store.get_meta('last_tesla')}",
        )
        pending = store.get_meta("tesla_pending_kwh")
        check(
            pending is not None and abs(float(pending) - 6.0) < 1e-6,
            f"tesla 10→16 during grid-less bootstrap is pending 6, got {pending}",
        )
        third = coll.collect_tick(store, cfg, t215, readings(100.1, t215, tesla=16.0))
        start_200, _ = T.closed_interval_utc(t215)
        row = store.get_interval(coll.utc_iso(start_200))
        check(row is not None, "first real slot after bootstrap exists")
        check(
            abs(float(row["tesla_kwh"]) - 6.0) < 1e-9,
            f"bootstrap gap 6 kWh is booked, got {row.get('tesla_kwh')}",
        )
        check(abs(float(store.get_meta("last_tesla")) - 16.0) < 1e-9, "last_tesla advances after booking")
        check(store.get_meta("tesla_pending_kwh") is None, "pending cleared after bootstrap flush")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "tesla_reset_pending.sqlite")
        t045 = dt(day, 1, 45)
        coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=10.0))
        coll.collect_tick(store, cfg, dt(day, 2, 30), readings(103.0, dt(day, 2, 30), tesla=16.0))
        check(abs(float(store.get_meta("last_tesla")) - 10.0) < 1e-9, "pending path last_tesla still 10")
        check(abs(float(store.get_meta("tesla_pending_kwh")) - 6.0) < 1e-6, "pending 6 before reset")
        reset = coll.collect_tick(store, cfg, dt(day, 3, 0), readings(104.0, dt(day, 3, 0), tesla=1.0))
        check("tesla_counter_reset" in (reset.get("sources") or ""), "reset is recorded")
        check(abs(float(store.get_meta("last_tesla")) - 1.0) < 1e-9, "reset rebaselines last_tesla to 1")
        check(store.get_meta("tesla_pending_extra") == "1", "pending 6 is marked extra across reset")
        check(abs(float(store.get_meta("tesla_pending_kwh")) - 6.0) < 1e-6, "pending 6 survives reset")
        after = coll.collect_tick(store, cfg, dt(day, 3, 15), readings(104.2, dt(day, 3, 15), tesla=11.0))
        start_300, _ = T.closed_interval_utc(dt(day, 3, 15))
        row = store.get_interval(coll.utc_iso(start_300))
        check(row is not None, "slot after reset exists")
        check(
            abs(float(row["tesla_kwh"]) - 16.0) < 1e-9,
            f"pending 6 plus new 10 kWh booked, got {row.get('tesla_kwh')}",
        )
        check(abs(float(store.get_meta("last_tesla")) - 11.0) < 1e-9, "last_tesla is 11 after new counter")
        check(store.get_meta("tesla_pending_kwh") is None, "pending cleared after reset flush")
        check(store.get_meta("tesla_pending_extra") is None, "extra flag cleared")

    def run_occupied_zero_grid(quality: str, label: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / f"tesla_zero_{quality}.sqlite")
            t045 = dt(day, 1, 45)
            t200 = dt(day, 2, 0)
            coll.collect_tick(store, cfg, t045, readings(100.0, t045, tesla=50.0))
            start_145, end_145 = T.closed_interval_utc(t200)
            local = start_145.astimezone(TZ)
            store.upsert_interval(
                {
                    "interval_start": coll.utc_iso(start_145),
                    "interval_end": coll.utc_iso(end_145),
                    "local_start": local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": 0.0,
                    "grid_export_kwh": 0.0,
                    "tesla_kwh": 1.0,
                    "counter_import": 100.0,
                    "counter_export": 0.0,
                    "nordpool_eur_kwh": 0.05,
                    "cost_octopus_heat": 0.0,
                    "cost_octopus_heat_loyalty": 0.0,
                    "cost_fix_tarif": 0.0,
                    "cost_dynamic": 0.0,
                    "cost_dynamic_modul3": 0.0,
                    "quality": quality,
                    "sources": f"{quality}_zero_grid",
                    "updated_at": datetime.now(TZ).isoformat(),
                }
            )
            idle = {
                IMP: {"state": "100.0", "last_updated": t045, "last_reported": t045},
                EXP: {"state": "0.0", "last_updated": t045, "last_reported": t045},
                TESLA: {"state": "53.0", "last_updated": t200, "last_reported": t200},
            }
            coll.collect_tick(store, cfg, t200, idle)
            row = store.get_interval(coll.utc_iso(start_145))
            check(abs(float(row["grid_import_kwh"])) < 1e-12, f"{label}: grid stays 0")
            check(
                abs(float(row["tesla_kwh"]) - 4.0) < 1e-9,
                f"{label}: tesla 1+3=4, got {row.get('tesla_kwh')}",
            )
            check(abs(float(store.get_meta("last_tesla")) - 53.0) < 1e-9, f"{label}: last_tesla 53")

    run_occupied_zero_grid("repaired", "zero-grid repaired")
    run_occupied_zero_grid("ok", "zero-grid ok")

    monday = date(2026, 8, 24)
    tuesday = date(2026, 8, 25)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "week.sqlite")
        for day_value, tesla_kwh, hour in ((monday, 3.0, 12), (tuesday, 1.5, 12)):
            local = datetime(day_value.year, day_value.month, day_value.day, hour, 0, tzinfo=TZ)
            start = local.astimezone(timezone.utc)
            end = start + timedelta(minutes=15)
            spot = 0.05
            price = T.energy_price_gross_eur_per_kwh(cfg, "octopus_heat", local, spot)
            store.upsert_interval(
                {
                    "interval_start": start.replace(microsecond=0).isoformat(),
                    "interval_end": end.replace(microsecond=0).isoformat(),
                    "local_start": local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": 1.0,
                    "grid_export_kwh": 0.0,
                    "tesla_kwh": tesla_kwh,
                    "counter_import": None,
                    "counter_export": None,
                    "nordpool_eur_kwh": spot,
                    "cost_octopus_heat": None if price is None else round(1.0 * price, 6),
                    "cost_octopus_heat_loyalty": 0.0,
                    "cost_fix_tarif": 0.0,
                    "cost_dynamic": 0.0,
                    "cost_dynamic_modul3": 0.0,
                    "quality": "ok",
                    "sources": "tesla_week_test",
                    "updated_at": datetime.now(TZ).isoformat(),
                }
            )
            coll.rebuild_day(store, cfg, day_value, complete=True, cascade=True)
        snap = coll.snapshot(store, datetime(2026, 8, 25, 18, 0, tzinfo=TZ))
        check(snap["week"]["week_start"] == "2026-08-24", "week starts Monday")
        check(snap["week"]["week_sunday"] == "2026-08-30", "week ends Sunday")
        check(abs(float(snap["week"]["tesla_kwh"]) - 4.5) < 1e-9, f"week tesla 4.5, got {snap['week']['tesla_kwh']}")

    full_day = date(2026, 8, 20)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "gaps.sqlite")
        start_local = datetime(full_day.year, full_day.month, full_day.day, tzinfo=TZ)
        rows = []
        counter = 1000.0
        samples = []
        hole = {20, 21, 22, 23}
        for index in range(T.expected_intervals(full_day)):
            local = (start_local.astimezone(timezone.utc) + timedelta(minutes=15 * index)).astimezone(TZ)
            start = local.astimezone(timezone.utc)
            end = start + timedelta(minutes=15)
            spot = 0.04 + (index % 12) * 0.002
            store.upsert_spot(start, end, spot)
            samples.append((start, counter))
            kwh = 0.1
            if index in hole:
                quality = "missing" if index < 23 else "unallocated"
                row_kwh = None
            else:
                quality = "backfilled"
                row_kwh = kwh
                counter += kwh
            samples.append((end, counter if index not in hole else counter))
            price = T.energy_price_gross_eur_per_kwh(cfg, "octopus_heat", local, spot)
            rows.append(
                {
                    "interval_start": start.replace(microsecond=0).isoformat(),
                    "interval_end": end.replace(microsecond=0).isoformat(),
                    "local_start": local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": row_kwh,
                    "grid_export_kwh": 0.0,
                    "tesla_kwh": None,
                    "counter_import": None if row_kwh is None else counter,
                    "counter_export": 0.0,
                    "nordpool_eur_kwh": spot,
                    "cost_octopus_heat": None if row_kwh is None or price is None else round(row_kwh * price, 6),
                    "cost_octopus_heat_loyalty": 0.0,
                    "cost_fix_tarif": 0.0,
                    "cost_dynamic": None if row_kwh is None else 0.01,
                    "cost_dynamic_modul3": None if row_kwh is None else 0.01,
                    "quality": quality,
                    "sources": "gap" if row_kwh is None else "aggregate_test",
                    "updated_at": datetime.now(TZ).isoformat(),
                }
            )
            if index in hole:
                counter += 0.1
                samples[-1] = (end, counter)
        store.bulk_upsert_intervals(rows)
        broken = coll.rebuild_day(store, cfg, full_day, complete=True, cascade=False)
        check(broken["intervals_missing"] == 4, f"gap day has 4 missing, got {broken['intervals_missing']}")
        check(broken["cost_dynamic_perfect"] is None, "Perfect is withheld on an incomplete day")

        result = coll.repair_gaps_from_counter_series(store, cfg, samples, days=[full_day])
        check(result["changed_slots"] == 4, f"repaired 4 slots, got {result}")
        fixed = store.get_daily(full_day)
        check(fixed is not None, "daily row exists after repair")
        check(int(fixed["intervals_missing"]) == 0, f"missing is 0 after repair, got {fixed['intervals_missing']}")
        check(fixed["cost_dynamic_perfect"] is not None, "Perfect is available after recorder repair")
        check(fixed["perfect_days"] == 1, "repaired complete day counts as Perfect")
        repaired = [
            row
            for row in store.intervals_for_day(full_day)
            if row["quality"] == "repaired"
        ]
        check(len(repaired) == 4, f"four repaired rows, got {len(repaired)}")
        check(all(abs(float(row["grid_import_kwh"]) - 0.1) < 1e-9 for row in repaired), "repaired kWh match counter deltas")

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
