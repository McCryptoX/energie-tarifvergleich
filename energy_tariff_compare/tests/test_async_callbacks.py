"""Static regression checks for Home Assistant event-loop callback safety."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


INIT_PATH = Path(
    "/config/custom_components/energy_tariff_compare/__init__.py"
)
if not INIT_PATH.exists():
    INIT_PATH = (
        Path(__file__).resolve().parents[2]
        / "custom_components"
        / "energy_tariff_compare"
        / "__init__.py"
    )

SENSOR_PATH = INIT_PATH.with_name("sensor.py")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"OK {message}")


tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"), filename=str(INIT_PATH))
helper_names = {
    "async_call_later",
    "async_track_state_change_event",
    "async_track_time_change",
}

for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        continue
    if node.func.id not in helper_names or len(node.args) < 2:
        continue
    callback_arg = node.args[2] if node.func.id == "async_track_state_change_event" else node.args[1]
    check(
        not isinstance(callback_arg, ast.Lambda),
        f"{node.func.id} uses a named async callback instead of an executor-bound lambda",
    )

source = INIT_PATH.read_text(encoding="utf-8")
check(
    "lambda now: hass.async_create_task" not in source
    and "lambda _n: hass.async_create_task" not in source,
    "event helpers never call hass.async_create_task from a worker thread",
)
check("tomorrow_retry_delay" in source, "retry chain uses tomorrow_retry_delay")
check("_tomorrow_ready_changed" in source, "READY flank has a named async callback")

reload_src = source.split("async def _reload_cfg", 1)[1].split("async def _fetch_tomorrow", 1)[0]
apply_at = reload_src.find("apply_tariff_config_change")
assign_at = reload_src.find('data["cfg"] = new_cfg')
check(apply_at != -1 and assign_at != -1, "_reload_cfg still loads config and reprices")
check(apply_at < assign_at, "in-memory config is replaced only after a successful reprice")

fill_src = source.split("async def _fill_perfect", 1)[1].split("unsub.append(async_call_later(hass, 5, _kickoff))", 1)[0]
check("async with collect_lock" in fill_src, "startup reprice/backfill runs under collect_lock")
check(
    "if stored_hash != data[\"cfg_hash\"]" in fill_src
    or "if stored_hash != data['cfg_hash']" in fill_src,
    "missing stored hash still triggers a startup reprice",
)

sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
interval_class = sensor_source.split("class IntervalKwhSensor", 1)[1].split(
    "class DynamicPriceSensor", 1
)[0]
check(
    "SensorDeviceClass.ENERGY" not in interval_class,
    "interval kWh is a slot sample, not an Energy-meter device class",
)
check(
    "SensorStateClass.MEASUREMENT" in interval_class,
    "interval kWh uses measurement without the Energy device class",
)

CONST_PATH = INIT_PATH.with_name("const.py")
_const_spec = importlib.util.spec_from_file_location("etc_const_uid", CONST_PATH)
const = importlib.util.module_from_spec(_const_spec)
_const_spec.loader.exec_module(const)
check(
    const.PRICE_UNIQUE_ID_MIGRATIONS.get("etc_price_naturwerke_fix") == "etc_price_fix_tarif",
    "naturwerke unique_id maps to fix_tarif",
)
check(
    const.PRICE_ENTITY_IDS.get("etc_price_fix_tarif") == "sensor.tarifvergleich_preis_fix",
    "fix_tarif keeps entity_id sensor.tarifvergleich_preis_fix",
)
check(const.leftover_unique_id_action(None, "sensor.x") == "skip", "no leftover unique_id: skip")
check(
    const.leftover_unique_id_action("sensor.old", None) == "retarget",
    "only old unique_id: retarget",
)
check(
    const.leftover_unique_id_action("sensor.a", "sensor.b") == "remove_old",
    "both unique_ids exist: remove leftover",
)
check(
    const.leftover_unique_id_action("sensor.same", "sensor.same") == "skip",
    "already the same registry row: skip",
)
mig_at = sensor_source.find("migrate_renamed_price_unique_ids(hass)")
add_at = sensor_source.find("async_add_entities(")
check(
    mig_at != -1 and add_at != -1 and mig_at < add_at,
    "registry unique_id migration runs before entities are added",
)
check("mdi:lock-outline" in sensor_source, "Fixer Tarif uses the lock icon")

print("ALL TESTS PASSED")
