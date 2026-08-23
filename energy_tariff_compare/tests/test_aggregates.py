#!/usr/bin/env python3
"""Aggregate split/coverage/Perfect regressions. Run directly with python3."""

from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/config") if Path("/config/energy_tariff_compare/tariffs.yaml").exists() else Path("/Volumes/config")
PKG = ROOT / "custom_components" / "energy_tariff_compare"


def load_pkg():
    name = "energy_tariff_compare_aggregate_test"
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


def interval_rows(T, cfg, day_value: date, count: int) -> list[dict]:
    start_local = datetime(day_value.year, day_value.month, day_value.day, tzinfo=T.TZ)
    rows = []
    for index in range(count):
        local = (start_local.astimezone(timezone.utc) + timedelta(minutes=15 * index)).astimezone(T.TZ)
        start = local.astimezone(timezone.utc)
        end = start + timedelta(minutes=15)
        spot = 0.05 + (index % 8) * 0.001
        kwh = 0.1 + (index % 4) * 0.01
        costs = {}
        for tariff_id in ("octopus_heat", "octopus_heat_loyalty", "naturwerke_fix", "dynamic", "dynamic_modul3"):
            price = T.energy_price_gross_eur_per_kwh(cfg, tariff_id, local, spot)
            costs[tariff_id] = None if price is None else round(kwh * price, 6)
        rows.append(
            {
                "interval_start": start.replace(microsecond=0).isoformat(),
                "interval_end": end.replace(microsecond=0).isoformat(),
                "local_start": local.replace(microsecond=0).isoformat(),
                "grid_import_kwh": kwh,
                "grid_export_kwh": 0.0,
                "counter_import": None,
                "counter_export": None,
                "nordpool_eur_kwh": spot,
                **{f"cost_{tariff_id}": value for tariff_id, value in costs.items()},
                "quality": "backfilled",
                "sources": "aggregate_test",
                "updated_at": datetime.now(T.TZ).isoformat(),
            }
        )
    return rows


def main():
    mods = load_pkg()
    Store = mods["store"].Store
    T = mods["tariffs"]
    C = mods["collector"]
    cfg = T.load_config(ROOT / "energy_tariff_compare" / "tariffs.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "energy.sqlite")
        full_day = date(2026, 8, 20)
        partial_day = date(2026, 8, 21)
        full_rows = interval_rows(T, cfg, full_day, T.expected_intervals(full_day))
        store.bulk_upsert_intervals(full_rows)
        daily = C.rebuild_day(store, cfg, full_day, complete=True, cascade=False)

        expected_energy_cost = round(sum(row["cost_octopus_heat"] for row in full_rows), 6)
        expected_standing = T.standing_eur_for_day(cfg, "octopus_heat", full_day)
        check(
            abs(daily["energy_cost_octopus_heat"] - expected_energy_cost) < 1e-9,
            "daily Arbeitspreiskosten exclude the standing charge",
        )
        check(
            abs(daily["standing_cost_octopus_heat"] - expected_standing) < 1e-9,
            "daily standing charge uses the exact invoice-derived amount",
        )
        check(
            abs(daily["cost_octopus_heat"] - expected_energy_cost - expected_standing) < 1e-6,
            "daily total equals energy cost plus standing charge without cent rounding loss",
        )
        check(daily["intervals_missing"] == 0 and daily["intervals_future"] == 0, "complete day coverage is exact")
        check(daily["cost_dynamic_perfect"] is not None, "Perfect exists for a fully covered day")
        check(daily["perfect_days"] == 1, "daily Perfect is exposed for the completed day")
        check(
            abs(daily["standing_cost_dynamic"] - daily["standing_cost_dynamic_modul3"]) < 1e-6,
            "Modul 3 Grund/Fix matches Dynamisch",
        )
        check(daily["paragraph_14a_eur"] < 0, "§14a credit is negative")
        check(
            abs(
                daily["cost_dynamic_modul3"]
                - (
                    daily["energy_cost_dynamic_modul3"]
                    + daily["standing_cost_dynamic_modul3"]
                    + daily["paragraph_14a_eur"]
                )
            )
            < 1e-6,
            "Modul 3 Gesamt = Arbeit + Grund/Fix + §14a",
        )

        orphan = date(2026, 8, 19)
        orphan_rows = interval_rows(T, cfg, orphan, T.expected_intervals(orphan))
        store.bulk_upsert_intervals(orphan_rows)
        check(store.get_daily(orphan) is None, "interval-only day has no daily row yet")

        store.bulk_upsert_intervals(interval_rows(T, cfg, partial_day, 1))
        C.rebuild_day(store, cfg, partial_day, complete=False, cascade=False)
        month = C.rebuild_month(store, cfg, 2026, 8)
        paired_actual = daily["cost_dynamic_modul3"]
        paired_perfect = daily["cost_dynamic_perfect"]
        check(month["perfect_days"] == 1, "monthly Perfect counts only the one complete paired day")
        check(
            abs(month["potential_eur"] - (paired_actual - paired_perfect)) < 1e-6,
            "monthly Perfect potential compares identical complete days only",
        )
        check(month["days_complete"] == 1 and month["days_incomplete"] == 1, "month separates complete and incomplete days")

        before_kwh = daily["grid_import_kwh"]
        before_heat = daily["energy_cost_octopus_heat"]
        cfg2 = copy.deepcopy(cfg)
        heat = T.tariff_by_id(cfg2, "octopus_heat")
        for key in heat["prices_gross_ct_per_kwh"]:
            heat["prices_gross_ct_per_kwh"][key] = 99.99
        rebuilt = C.apply_tariff_config_change(store, cfg2, None, "new-hash")
        check(rebuilt >= 1, "YAML hash change reprices and rebuilds aggregates")
        check(store.get_meta("config_sha256") == "new-hash", "successful reprice stores the new hash")
        after = store.get_daily(full_day)
        check(abs(float(after["grid_import_kwh"]) - before_kwh) < 1e-9, "reprice leaves measured kWh unchanged")
        check(
            float(after["energy_cost_octopus_heat"]) - before_heat > 0.5,
            "reprice updates Heat Arbeit after YAML Arbeitspreis change",
        )
        orphan_daily = store.get_daily(orphan)
        check(orphan_daily is not None, "interval-only day is discovered from intervals")
        check(
            abs(float(orphan_daily["grid_import_kwh"]) - sum(row["grid_import_kwh"] for row in orphan_rows))
            < 1e-6,
            "orphan-day kWh stays the measured interval sum",
        )
        check(
            float(orphan_daily["energy_cost_octopus_heat"])
            > sum(row["cost_octopus_heat"] for row in orphan_rows) + 0.5,
            "orphan-day Heat Arbeit is repriced from the new YAML",
        )
        unchanged = C.apply_tariff_config_change(store, cfg2, "new-hash", "new-hash")
        check(unchanged == 0, "same config hash does not reprice again")
        check(
            C.period_ranking_complete(
                {
                    "intervals_due": 2000,
                    "intervals_missing": 0,
                    "price_intervals_missing": 0,
                    "days_with_data": 22,
                    "days_expected": 23,
                },
                period="month",
            )
            is False,
            "month ranking stays closed when a whole day is missing",
        )
        check(
            C.period_ranking_complete(
                {
                    "intervals_due": 96,
                    "intervals_missing": 0,
                    "price_intervals_missing": 0,
                },
                period="today",
            )
            is True,
            "today ranking ignores days_expected",
        )

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
