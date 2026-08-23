#!/usr/bin/env python3
"""Window and price tests. Run: python3 tests/test_windows.py"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import importlib.util

ROOT = Path("/config") if Path("/config/energy_tariff_compare/tariffs.yaml").exists() else Path("/Volumes/config")
_spec = importlib.util.spec_from_file_location(
    "etc_tariffs", ROOT / "custom_components/energy_tariff_compare/tariffs.py"
)
Tmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Tmod)
TZ = Tmod.TZ
base_eur_for_day = Tmod.base_eur_for_day
capped_standing_eur_for_period = Tmod.capped_standing_eur_for_period
dynamic_gross_eur_per_kwh = Tmod.dynamic_gross_eur_per_kwh
energy_price_gross_eur_per_kwh = Tmod.energy_price_gross_eur_per_kwh
expected_intervals = Tmod.expected_intervals
heat_slot = Tmod.heat_slot
interval_floor = Tmod.interval_floor
load_config = Tmod.load_config
modul3_slot = Tmod.modul3_slot
perfect_energy_cost = Tmod.perfect_energy_cost
standing_components_eur_for_day = Tmod.standing_components_eur_for_day
standing_eur_for_day = Tmod.standing_eur_for_day
standing_eur_for_month = Tmod.standing_eur_for_month
tariff_by_id = Tmod.tariff_by_id
validate_config = Tmod.validate_config

CFG = load_config(ROOT / "energy_tariff_compare" / "tariffs.yaml")


def dt(h, m=0, day=22, month=8, year=2026):
    return datetime(year, month, day, h, m, tzinfo=TZ)


def check(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    print("OK", msg)


def check_raises(fn, contains, msg):
    try:
        fn()
    except ValueError as err:
        check(contains in str(err), f"{msg}: {err}")
        return
    raise SystemExit(f"FAIL: {msg}: no ValueError")


def main():
    check(validate_config(CFG) is CFG, "production tariff config validates")
    missing_id = deepcopy(CFG)
    missing_id["tariffs"] = [t for t in missing_id["tariffs"] if t["id"] != "dynamic_perfect"]
    check_raises(
        lambda: validate_config(missing_id),
        "missing required tariff ids",
        "required tariff IDs enforced",
    )
    bad_dates = deepcopy(CFG)
    tariff_by_id(bad_dates, "dynamic")["valid_to"] = "2026-01-01"
    check_raises(
        lambda: validate_config(bad_dates),
        "exclusive end",
        "valid_to must be later than valid_from",
    )
    missing_dynamic_end = deepcopy(CFG)
    tariff_by_id(missing_dynamic_end, "dynamic")["valid_to"] = None
    check_raises(
        lambda: validate_config(missing_dynamic_end),
        "valid_to is required",
        "year-specific dynamic tariff needs an end",
    )
    late_dynamic_end = deepcopy(CFG)
    tariff_by_id(late_dynamic_end, "dynamic")["valid_to"] = "2027-01-02"
    check_raises(
        lambda: validate_config(late_dynamic_end),
        "must not exceed 2027-01-01",
        "2026 dynamic components cannot leak into 2027",
    )

    heat_cases = {
        (1, 59): "standard",
        (2, 0): "niedrig",
        (5, 59): "niedrig",
        (6, 0): "standard",
        (11, 59): "standard",
        (12, 0): "niedrig",
        (15, 59): "niedrig",
        (16, 0): "standard",
        (17, 59): "standard",
        (18, 0): "hoch",
        (20, 59): "hoch",
        (21, 0): "standard",
    }
    for (h, m), expect in heat_cases.items():
        got = heat_slot(CFG, dt(h, m), "octopus_heat")
        check(got == expect, f"Octopus {h:02d}:{m:02d} -> {got} (expected {expect})")

    modul_cases = {
        (6, 59): "nt",
        (7, 0): "st",
        (14, 59): "st",
        (15, 0): "ht",
        (19, 59): "ht",
        (20, 0): "st",
        (23, 59): "st",
        (0, 0): "nt",
    }
    for (h, m), expect in modul_cases.items():
        got = modul3_slot(CFG, dt(h, m))
        check(got == expect, f"Modul3 {h:02d}:{m:02d} -> {got} (expected {expect})")

    unknown = []
    for h in range(24):
        for m in (0, 15, 30, 45):
            if heat_slot(CFG, dt(h, m), "octopus_heat") is None:
                unknown.append(f"heat {h}:{m}")
            try:
                modul3_slot(CFG, dt(h, m))
            except ValueError:
                unknown.append(f"modul3 {h}:{m}")
    check(not unknown, f"all 96 slots mapped ({unknown})")

    p = energy_price_gross_eur_per_kwh(CFG, "octopus_heat", dt(3, 0), None)
    check(abs(p - 0.2040) < 1e-9, f"heat niedrig 20.40 ct -> {p}")
    p = energy_price_gross_eur_per_kwh(CFG, "octopus_heat", dt(19, 0), None)
    check(abs(p - 0.3239) < 1e-9, f"heat hoch 32.39 ct -> {p}")
    p = energy_price_gross_eur_per_kwh(CFG, "naturwerke_fix", dt(19, 0), None)
    check(abs(p - 0.3160) < 1e-9, f"naturwerke 31.60 ct -> {p}")

    replay_day = dt(3, 0, day=15, month=7)
    replay_price = energy_price_gross_eur_per_kwh(CFG, "octopus_heat_loyalty", replay_day, None)
    check(abs(replay_price - 0.2889) < 1e-9, "scenario_replay permits historical Loyalty calculation")
    no_replay = deepcopy(CFG)
    tariff_by_id(no_replay, "octopus_heat_loyalty")["scenario_replay"] = False
    check(
        energy_price_gross_eur_per_kwh(no_replay, "octopus_heat_loyalty", replay_day, None) is None,
        "historical Loyalty price is unavailable without scenario_replay",
    )
    check(
        standing_eur_for_day(no_replay, "octopus_heat_loyalty", replay_day.date()) is None,
        "historical Loyalty standing charge is unavailable without scenario_replay",
    )

    last_dynamic_day = datetime(2026, 12, 31, 12, 0, tzinfo=TZ)
    first_2027 = datetime(2027, 1, 1, 0, 0, tzinfo=TZ)
    check(
        energy_price_gross_eur_per_kwh(CFG, "dynamic", last_dynamic_day, 0.10) is not None,
        "dynamic 2026 is valid through 2026-12-31",
    )
    check(
        energy_price_gross_eur_per_kwh(CFG, "dynamic", first_2027, 0.10) is None,
        "dynamic 2026 ends at exclusive 2027-01-01",
    )
    replay_after_end = deepcopy(CFG)
    tariff_by_id(replay_after_end, "dynamic")["scenario_replay"] = True
    check(
        energy_price_gross_eur_per_kwh(replay_after_end, "dynamic", first_2027, 0.10) is None,
        "scenario_replay never bypasses valid_to",
    )

    august_day = date(2026, 8, 22)
    heat_base = base_eur_for_day(CFG, "octopus_heat", august_day)
    heat_components = standing_components_eur_for_day(CFG, "octopus_heat", august_day)
    check(abs(heat_base - 0.45336025) < 1e-12, f"Heat exact daily base {heat_base}")
    check(
        abs(standing_eur_for_day(CFG, "octopus_heat", august_day) - 0.45336025) < 1e-12,
        "Heat standing prefers exact daily invoice value",
    )
    check(
        heat_components["network"] == 0.0 and heat_components["total"] == heat_base,
        "all-in base remains separate from network components",
    )
    check(
        abs(standing_eur_for_month(CFG, "octopus_heat", 2026, 8) - 31 * 0.45336025) < 1e-10,
        "monthly Heat standing sums exact daily invoice values",
    )
    dynamic_components = standing_components_eur_for_day(CFG, "dynamic", august_day)
    check(
        dynamic_components["network"] > 0 and dynamic_components["total"] > dynamic_components["base"],
        "dynamic supplier base and network standing are independently calculable",
    )
    check(
        standing_eur_for_day(CFG, "dynamic", date(2027, 1, 1)) is None,
        "invalid dynamic standing charge is None",
    )
    m3_components = standing_components_eur_for_day(CFG, "dynamic_modul3", august_day)
    m3_zero_usage = capped_standing_eur_for_period(
        CFG, "dynamic_modul3", august_day, fraction=1.0, variable_network_gross_eur=0.0
    )
    check(
        abs(m3_zero_usage - m3_components["base"] - m3_components["metering"]) < 1e-12,
        "§14a reduction cannot make the network block negative at zero usage",
    )
    m3_with_network_usage = capped_standing_eur_for_period(
        CFG, "dynamic_modul3", august_day, fraction=1.0, variable_network_gross_eur=1.0
    )
    check(
        abs(m3_with_network_usage - m3_components["total"]) < 1e-12,
        "full §14a reduction applies when network charges cover it",
    )
    check(
        abs(
            standing_eur_for_day(CFG, "dynamic", august_day)
            - standing_eur_for_day(CFG, "dynamic_modul3", august_day)
        )
        < 1e-12,
        "Modul 3 Grund/Fix equals Dynamisch (no §14a in Grund/Fix)",
    )
    check(
        standing_eur_for_day(CFG, "dynamic_modul3", august_day)
        > capped_standing_eur_for_period(
            CFG, "dynamic_modul3", august_day, fraction=1.0, variable_network_gross_eur=1.0
        ),
        "§14a still lowers Gesamt-Fixanteil below Grund/Fix",
    )

    # VAT once: spot net + markup net (2,15 ct brutto / 1,19) + grid + levies
    lev = (2.05 + 0.446 + 1.559 + 0.941 + 1.59) / 100.0
    markup = CFG["dynamic_supplier"]["markup_net_ct_per_kwh"] / 100.0
    net = 0.10 + markup + 0.0953 + lev
    expect = net * 1.19
    got = dynamic_gross_eur_per_kwh(CFG, dt(10, 0), 0.10, modul3=False)
    check(abs(got - expect) < 1e-9, f"dynamic vat-once {got:.6f} == {expect:.6f}")
    check(abs(CFG["dynamic_supplier"]["markup_gross_ct_per_kwh"] - 2.15) < 1e-12, "Tibber markup 2.15 ct brutto")
    check(abs(markup * 1.19 * 100.0 - 2.15) < 1e-9, "net markup * VAT = 2.15 ct brutto")
    taxes_block_ct = (0.0953 + lev) * 100.0 * 1.19 + 2.15
    check(abs(taxes_block_ct - 21.32) < 0.02, f"Tibber Steuern/Abgaben-Block {taxes_block_ct:.2f} ct")

    # Negative spot still allowed
    got_neg = dynamic_gross_eur_per_kwh(CFG, dt(13, 0), -0.02, modul3=False)
    check(got_neg < got, f"negative spot lowers price {got_neg:.4f} < {got:.4f}")

    kwhs = [2.0, 1.0, 0.5]
    prices = [0.40, 0.20, 0.10]
    perfect = perfect_energy_cost(kwhs, prices)
    check(abs(perfect - (2.0 * 0.10 + 1.0 * 0.20 + 0.5 * 0.40)) < 1e-9, f"perfect {perfect}")

    spot = 0.10
    p_dyn_nt = energy_price_gross_eur_per_kwh(CFG, "dynamic", dt(3, 0), spot)
    p_dyn_ht = energy_price_gross_eur_per_kwh(CFG, "dynamic", dt(16, 0), spot)
    p_m3_nt = energy_price_gross_eur_per_kwh(CFG, "dynamic_modul3", dt(3, 0), spot)
    p_m3_ht = energy_price_gross_eur_per_kwh(CFG, "dynamic_modul3", dt(16, 0), spot)
    check(abs(p_dyn_nt - p_dyn_ht) < 1e-9, "flat dynamic Arbeitspreis unabhängig von NT/HT")
    check(p_m3_nt < p_m3_ht, f"Modul3 NT {p_m3_nt:.4f} < HT {p_m3_ht:.4f}")
    k2 = [2.0, 0.5]
    actual_m3 = 2.0 * p_m3_ht + 0.5 * p_m3_nt
    perfect_m3 = perfect_energy_cost(k2, [p_m3_ht, p_m3_nt])
    actual_dyn = 2.0 * p_dyn_ht + 0.5 * p_dyn_nt
    perfect_dyn = perfect_energy_cost(k2, [p_dyn_ht, p_dyn_nt])
    check(perfect_m3 < actual_m3, f"Modul3-Perfekt {perfect_m3:.4f} < Ist {actual_m3:.4f}")
    check(abs(perfect_dyn - actual_dyn) < 1e-9, "Dynamisch-Perfekt = Ist bei gleichem Spot über den Tag")
    check(perfect_m3 < perfect_dyn, "Modul3-Perfekt günstiger als Dynamisch-Perfekt (NT-Stunden)")

    check(expected_intervals(datetime(2026, 8, 22).date()) == 96, "normal day 96")
    check(expected_intervals(datetime(2026, 3, 29).date()) == 92, "DST spring 92")
    check(expected_intervals(datetime(2026, 10, 25).date()) == 100, "DST autumn 100")

    for instant, expected_fold, label in (
        (datetime(2026, 10, 25, 0, 50, tzinfo=timezone.utc), 0, "first autumn 02 hour"),
        (datetime(2026, 10, 25, 1, 5, tzinfo=timezone.utc), 1, "second autumn 02 hour"),
    ):
        start, end = interval_floor(instant)
        seconds = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
        check(seconds == 900, f"{label} live slot is exactly 900 UTC seconds")
        check(start.fold == expected_fold, f"{label} preserves fold={expected_fold}")

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
