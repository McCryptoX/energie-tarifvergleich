#!/usr/bin/env python3
"""One-time historical spot repair regression. Run directly with python3."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/config") if Path("/config/energy_tariff_compare/tariffs.yaml").exists() else Path("/Volumes/config")
PKG = ROOT / "custom_components" / "energy_tariff_compare"


def load_pkg():
    name = "energy_tariff_compare_spot_repair_test"
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


def main():
    mods = load_pkg()
    Store = mods["store"].Store
    T = mods["tariffs"]
    C = mods["collector"]
    cfg = T.load_config(ROOT / "energy_tariff_compare" / "tariffs.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        db = Store(base / "energy.sqlite")
        first = 1767222000
        second = first + 900
        db.bulk_upsert_spots(
            [
                (datetime.fromtimestamp(first, timezone.utc).isoformat(), datetime.fromtimestamp(second, timezone.utc).isoformat(), 1.0, "bad"),
                (datetime.fromtimestamp(second, timezone.utc).isoformat(), datetime.fromtimestamp(second + 900, timezone.utc).isoformat(), -2.0, "bad"),
            ]
        )
        source = base / "spots.json"
        source.write_text(
            json.dumps(
                {
                    "unix_seconds": [first, second],
                    "price": [1.0, -2.0],
                    "unit": "EUR / MWh",
                }
            ),
            encoding="utf-8",
        )
        result = C.repair_spot_prices_from_energy_charts(db, cfg, source)
        spots = db.all_spots()
        check(result["changed_slots"] == 2, "both factor-1000 rows are detected")
        check(abs(spots[datetime.fromtimestamp(first, timezone.utc).isoformat()] - 0.001) < 1e-12, "positive EUR/MWh is repaired")
        check(abs(spots[datetime.fromtimestamp(second, timezone.utc).isoformat()] + 0.002) < 1e-12, "negative EUR/MWh is repaired")
        check(
            C.repair_spot_prices_from_energy_charts(db, cfg, source)["changed_slots"] == 0,
            "version marker makes the repair idempotent",
        )

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
