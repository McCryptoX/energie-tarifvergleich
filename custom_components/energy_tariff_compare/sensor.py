"""Sensors for the tariff comparison dashboard."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    LEFTOVER_PRICE_ENTITY_IDS,
    PLAT_NAME,
    PRICE_ENTITY_IDS,
    PRICE_UNIQUE_ID_MIGRATIONS,
    REFERENCE_ID,
    leftover_unique_id_action,
)

_LOGGER = logging.getLogger(__name__)

TARIFF_LABELS = {
    "octopus_heat": "Octopus Heat aktuell",
    "octopus_heat_loyalty": "Octopus Heat Loyalty",
    "fix_tarif": "Fixer Tarif",
    "dynamic": "Dynamisch",
    "dynamic_modul3": "Dynamisch + Modul 3",
    "dynamic_perfect": "Dynamisch perfekt (theoretisch)",
}

PRICE_NOW_ICONS = {
    "octopus_heat": "mdi:file-sign",
    "octopus_heat_loyalty": "mdi:star-outline",
    "fix_tarif": "mdi:lock-outline",
    "dynamic": "mdi:chart-timeline-variant",
    "dynamic_modul3": "mdi:sine-wave",
}


def migrate_renamed_price_unique_ids(hass: HomeAssistant) -> None:
    """Retarget or drop leftover unique_ids from a tariff-id rename.

    If this install already created the new unique_id (both rows exist), remove
    the leftover so the dashboard keeps `sensor.tarifvergleich_preis_fix`.
    If only the old unique_id exists, retarget it before entities are added.
    """
    registry = er.async_get(hass)
    for old_uid, new_uid in PRICE_UNIQUE_ID_MIGRATIONS.items():
        old_eid = registry.async_get_entity_id("sensor", DOMAIN, old_uid)
        new_eid = registry.async_get_entity_id("sensor", DOMAIN, new_uid)
        action = leftover_unique_id_action(old_eid, new_eid)
        if action == "skip":
            continue
        if action == "remove_old":
            registry.async_remove(old_eid)
            _LOGGER.info("Removed leftover price entity %s after tariff id rename", old_eid)
            continue
        updates: dict[str, str] = {"new_unique_id": new_uid}
        desired = PRICE_ENTITY_IDS.get(new_uid)
        if desired:
            occupant = registry.async_get(desired)
            if occupant is None or occupant.entity_id == old_eid:
                updates["new_entity_id"] = desired
        registry.async_update_entity(old_eid, **updates)
        _LOGGER.info("Retargeted price unique_id %s -> %s (%s)", old_uid, new_uid, old_eid)

    live_ids = set(PRICE_ENTITY_IDS.values())
    for leftover_eid in LEFTOVER_PRICE_ENTITY_IDS:
        if leftover_eid in live_ids:
            continue
        if registry.async_get(leftover_eid) is None:
            continue
        registry.async_remove(leftover_eid)
        _LOGGER.info("Removed leftover price entity %s", leftover_eid)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    migrate_renamed_price_unique_ids(hass)
    async_add_entities(
        [
            PricesSensor(hass),
            PriceNowSensor(hass, "octopus_heat", "tarifvergleich_preis_heat", "Octopus Heat"),
            PriceNowSensor(hass, "octopus_heat_loyalty", "tarifvergleich_preis_loyalty", "Heat Loyalty"),
            PriceNowSensor(hass, "fix_tarif", "tarifvergleich_preis_fix", "Fixer Tarif"),
            PriceNowSensor(hass, "dynamic", "tarifvergleich_preis_dynamisch", "Dynamisch"),
            PriceNowSensor(hass, "dynamic_modul3", "tarifvergleich_preis_modul3", "Dynamisch + Modul 3"),
            PeriodSensor(hass, "today", "Heute", "tarifvergleich_heute"),
            PeriodSensor(hass, "yesterday", "Gestern", "tarifvergleich_gestern"),
            PeriodSensor(hass, "month", "Monat", "tarifvergleich_monat"),
            PeriodSensor(hass, "year", "Jahr", "tarifvergleich_jahr"),
            PeriodSensor(hass, "total", "Gesamt", "tarifvergleich_gesamt"),
            IntervalKwhSensor(hass),
            DynamicPriceSensor(hass),
            Modul3PriceSensor(hass),
            WallboxSensor(hass),
        ]
    )


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _event_names = (f"{DOMAIN}_updated",)

    def __init__(self, hass: HomeAssistant, unique: str, name: str):
        self.hass = hass
        self._attr_unique_id = unique
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name=PLAT_NAME,
            manufacturer="Home Assistant",
            model="15-Minuten-Tarifvergleich",
        )

    async def async_added_to_hass(self) -> None:
        for event_name in self._event_names:
            self.async_on_remove(self.hass.bus.async_listen(event_name, self._handle))
        self.async_write_ha_state()

    @callback
    def _handle(self, _event) -> None:
        self.async_write_ha_state()

    def _data(self) -> dict:
        return self.hass.data.get(DOMAIN, {}).get("latest", {})

    def _prices(self) -> dict:
        cached = self._data().get("prices")
        if cached:
            return cached
        pack = self.hass.data.get(DOMAIN) or {}
        cfg = pack.get("cfg")
        if not cfg:
            return {}
        from .collector import current_prices, parse_float
        from homeassistant.util import dt as dt_util

        ents = cfg["entities"]
        spot = parse_float(getattr(self.hass.states.get(ents["nordpool_current"]), "state", None))
        nxt = parse_float(getattr(self.hass.states.get(ents["nordpool_next"]), "state", None))
        return current_prices(cfg, dt_util.now(), spot, nxt)


class PricesSensor(_Base):
    def __init__(self, hass):
        super().__init__(hass, "etc_prices", "Aktuelle Preise")
        self._attr_native_unit_of_measurement = "ct/kWh"
        self._attr_suggested_display_precision = 2
        self._attr_icon = "mdi:tag-multiple"
        self.entity_id = "sensor.tarifvergleich_preise"

    @property
    def native_value(self):
        return self._prices().get("octopus_heat")

    @property
    def extra_state_attributes(self):
        prices = dict(self._prices())
        prices["hypothetical"] = {
            "octopus_heat_loyalty": True,
            "fix_tarif": True,
            "dynamic": True,
            "dynamic_modul3": True,
        }
        prices["reference"] = "octopus_heat"
        prices["vat_note"] = "Dynamische Preise brutto, Spot ohne MwSt in Nord Pool"
        from .collector import COST_KEYS, cheapest_working_price_ids

        if "cheapest_current_ids" not in prices:
            ids = cheapest_working_price_ids(prices)
            prices["cheapest_current_ids"] = ids
            prices["cheapest_current_id"] = ids[0] if len(ids) == 1 else None
            prices["cheapest_current_value"] = None if not ids else prices.get(ids[0])
            prices["current_prices_complete"] = all(prices.get(tid) is not None for tid in COST_KEYS)
        err = self._data().get("error")
        if err:
            prices["collector_error"] = err
        snap = self._data().get("snap") or {}
        prices.update(snap.get("run_windows") or {})
        prices.update(snap.get("price_kpis") or {})
        return prices


class PriceNowSensor(_Base):
    def __init__(self, hass, tid, object_id, name):
        super().__init__(hass, f"etc_price_{tid}", name)
        self._tid = tid
        self._attr_native_unit_of_measurement = "ct/kWh"
        self._attr_suggested_display_precision = 2
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = PRICE_NOW_ICONS.get(tid, "mdi:cash")
        self.entity_id = f"sensor.{object_id}"

    @property
    def native_value(self):
        return self._prices().get(self._tid)

    @property
    def extra_state_attributes(self):
        prices = self._prices()
        return {
            "hypothetical": self._tid != "octopus_heat",
            "heat_slot": prices.get("heat_slot"),
            "modul3_slot": prices.get("modul3_slot"),
        }


class PeriodSensor(_Base):
    def __init__(self, hass, key, name, object_id):
        super().__init__(hass, f"etc_{key}", name)
        self._key = key
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_suggested_display_precision = 2
        self._attr_icon = "mdi:chart-bar"
        self.entity_id = f"sensor.{object_id}"

    @property
    def native_value(self):
        snap = (self._data().get("snap") or {}).get(self._key) or {}
        value = snap.get("grid_import_kwh")
        if value is None:
            return None
        return round(float(value), 2)

    @property
    def extra_state_attributes(self):
        snap_root = self._data().get("snap") or {}
        row = dict(snap_root.get(self._key) or {})
        kwh = float(row.get("grid_import_kwh") or 0.0)
        calendar_coverage_ok = True
        if self._key in {"month", "year", "total"}:
            days_with = row.get("days_with_data")
            days_expected = row.get("days_expected")
            calendar_coverage_ok = bool(
                days_with is not None
                and days_expected is not None
                and int(days_with) == int(days_expected)
            )
        out = {
            "quality": row.get("quality"),
            "intervals_ok": row.get("intervals_ok"),
            "intervals_due": row.get("intervals_due"),
            "intervals_missing": row.get("intervals_missing"),
            "intervals_future": row.get("intervals_future"),
            "price_intervals_missing": row.get("price_intervals_missing"),
            "intervals_expected": row.get("intervals_expected"),
            "days_ok": row.get("days_ok"),
            "days_with_data": row.get("days_with_data"),
            "days_complete": row.get("days_complete"),
            "days_incomplete": row.get("days_incomplete"),
            "days_expected": row.get("days_expected"),
            "perfect_days": row.get("perfect_days"),
            "data_through": row.get("data_through"),
            "data_from": row.get("data_from"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "required_intervals": row.get("intervals_due"),
            "selected_month": snap_root.get("selected_month"),
            "selected_label": snap_root.get("selected_label"),
            "reference": TARIFF_LABELS[REFERENCE_ID],
            "perfect_note": "wird nach Tagesabschluss berechnet"
            if self._key == "today"
            else "mathematische Untergrenze bei frei verschiebbaren Lastblöcken",
            "ranking_complete": self._ranking_complete(row),
        }
        ref_cost = row.get("cost_octopus_heat")
        if ref_cost is not None:
            ref_cost = round(float(ref_cost), 2)
        for tid, label in TARIFF_LABELS.items():
            cost_key = "cost_dynamic_perfect" if tid == "dynamic_perfect" else f"cost_{tid}"
            cost = row.get(cost_key)
            if cost is not None:
                cost = round(float(cost), 2)
            out[f"cost_{tid}"] = cost
            out[f"label_{tid}"] = label
            if tid == "dynamic_perfect":
                energy_cost = standing_cost = None
            else:
                energy_cost = row.get(f"energy_cost_{tid}")
                standing_cost = row.get(f"standing_cost_{tid}")
            out[f"energy_cost_{tid}"] = (
                None if energy_cost is None else round(float(energy_cost), 2)
            )
            out[f"standing_cost_{tid}"] = (
                None if standing_cost is None else round(float(standing_cost), 2)
            )
            out[f"energy_cost_eur_{tid}"] = out[f"energy_cost_{tid}"]
            out[f"standing_cost_eur_{tid}"] = out[f"standing_cost_{tid}"]
            required = row.get("intervals_due")
            missing_energy = int(row.get("intervals_missing") or 0)
            missing_price = (
                int(row.get("price_intervals_missing") or 0)
                if tid in {"dynamic", "dynamic_modul3", "dynamic_perfect"}
                else 0
            )
            if required is None:
                energy_present = price_present = None
            else:
                energy_present = max(0, int(required) - missing_energy)
                price_present = max(0, energy_present - missing_price)
            out[f"energy_intervals_present_{tid}"] = energy_present
            out[f"price_intervals_present_{tid}"] = price_present
            coverage_ok = (
                int(row.get("intervals_missing") or 0) == 0 and calendar_coverage_ok
            )
            if tid in {"dynamic", "dynamic_modul3", "dynamic_perfect"}:
                coverage_ok = coverage_ok and int(row.get("price_intervals_missing") or 0) == 0
            if energy_cost is not None and kwh > 0.001 and coverage_ok:
                work_price = round(100.0 * float(energy_cost) / kwh, 2)
            else:
                work_price = None
            out[f"work_price_ct_{tid}"] = work_price
            out[f"coverage_complete_{tid}"] = bool(
                required is not None
                and missing_energy == 0
                and missing_price == 0
                and calendar_coverage_ok
            )
            out[f"effective_total_ct_{tid}"] = (
                round(100.0 * float(cost) / kwh, 2)
                if cost is not None and kwh > 0.001 and coverage_ok
                else None
            )
            out[f"total_effective_ct_{tid}"] = out[f"effective_total_ct_{tid}"]
            if (
                cost is not None
                and ref_cost is not None
                and tid not in {REFERENCE_ID, "dynamic_perfect"}
            ):
                out[f"delta_vs_heat_{tid}"] = round(float(cost) - float(ref_cost), 2)
        if self._key == "today":
            out["hit_total_kwh"] = row.get("hit_total_kwh")
            for tid in ("octopus_heat", "dynamic", "dynamic_modul3"):
                share = row.get(f"hit_share_{tid}")
                kwh_hit = row.get(f"hit_kwh_{tid}")
                out[f"hit_share_{tid}"] = None if share is None else round(float(share), 4)
                out[f"hit_kwh_{tid}"] = None if kwh_hit is None else round(float(kwh_hit), 3)
            out["cost_dynamic_perfect"] = None
            out["cost_dynamic_flat_perfect"] = None
            out["effective_ct_dynamic_perfect"] = None
            out["delta_vs_heat_dynamic_perfect"] = None
            out["potential_eur"] = None
            out["potential_dynamic_eur"] = None
        else:
            flat_p = row.get("cost_dynamic_flat_perfect")
            if flat_p is not None:
                flat_p = round(float(flat_p), 2)
            out["cost_dynamic_flat_perfect"] = flat_p
            out["potential_eur"] = row.get("potential_eur")
            out["potential_dynamic_eur"] = row.get("potential_dynamic_eur")
            out["potential_pct"] = row.get("potential_pct")
        if self._key in {"year", "total"}:
            out["cheapest"] = row.get("cheapest")
        p14a = row.get("paragraph_14a_eur")
        out["paragraph_14a_eur"] = None if p14a is None else round(float(p14a), 2)
        tesla_kwh = row.get("tesla_kwh")
        out["tesla_kwh"] = None if tesla_kwh is None else round(float(tesla_kwh), 3)
        for tid in TARIFF_LABELS:
            if tid == "dynamic_perfect":
                out["tesla_cost_dynamic_perfect"] = None
                continue
            tesla_cost = row.get(f"tesla_cost_{tid}")
            out[f"tesla_cost_{tid}"] = None if tesla_cost is None else round(float(tesla_cost), 2)
        return out

    def _ranking_complete(self, row: dict) -> bool:
        from .collector import period_ranking_complete

        return period_ranking_complete(row, period=self._key)


class IntervalKwhSensor(_Base):
    _event_names = (f"{DOMAIN}_collected",)

    def __init__(self, hass):
        super().__init__(hass, "etc_interval_kwh", "Viertelstunden-Netzbezug")
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:flash"
        self.entity_id = "sensor.tarifvergleich_viertelstunde_kwh"

    @property
    def native_value(self):
        return self._row().get("grid_import_kwh")

    def _row(self) -> dict:
        live = self._data().get("interval") or {}
        if live.get("grid_import_kwh") is not None:
            return live
        snap = self._data().get("snap") or {}
        return snap.get("latest_interval") or live

    @property
    def extra_state_attributes(self):
        row = self._row()
        end_raw = row.get("interval_end")
        age_minutes = None
        if end_raw:
            try:
                end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                age_minutes = max(
                    0.0,
                    (datetime.now(timezone.utc) - end.astimezone(timezone.utc)).total_seconds() / 60.0,
                )
            except ValueError:
                age_minutes = None
        if age_minutes is None:
            freshness = "unbekannt"
        elif age_minutes <= 25:
            freshness = "aktuell"
        else:
            freshness = "veraltet"
        tesla_kwh = row.get("tesla_kwh")
        return {
            "local_start": row.get("local_start"),
            "interval_end": end_raw,
            "quality": row.get("quality"),
            "sources": row.get("sources"),
            "tesla_kwh": None if tesla_kwh is None else round(float(tesla_kwh), 4),
            "age_minutes": None if age_minutes is None else round(age_minutes, 1),
            "freshness": freshness,
        }


class DynamicPriceSensor(_Base):
    def __init__(self, hass):
        super().__init__(hass, "etc_dynamic_ct", "Dynamischer Endkundenpreis")
        self._attr_native_unit_of_measurement = "ct/kWh"
        self._attr_suggested_display_precision = 2
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:chart-timeline-variant"
        self.entity_id = "sensor.tarifvergleich_dynamisch_ct"

    @property
    def native_value(self):
        return self._prices().get("dynamic")

    @property
    def extra_state_attributes(self):
        prices = self._prices()
        return {
            "dynamic_modul3_ct": prices.get("dynamic_modul3"),
            "next_ct": prices.get("dynamic_next"),
            "slot_end": prices.get("slot_end"),
            "today_min_ct": prices.get("spot_min_ct"),
            "today_max_ct": prices.get("spot_max_ct"),
            "hypothetical": True,
        }


class Modul3PriceSensor(_Base):
    def __init__(self, hass):
        super().__init__(hass, "etc_modul3_ct", "Dynamisch + Modul 3 Endkundenpreis")
        self._attr_native_unit_of_measurement = "ct/kWh"
        self._attr_suggested_display_precision = 2
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:chart-timeline-variant"
        self.entity_id = "sensor.tarifvergleich_modul3_ct"

    @property
    def native_value(self):
        return self._prices().get("dynamic_modul3")

    @property
    def extra_state_attributes(self):
        prices = self._prices()
        return {
            "dynamic_ct": prices.get("dynamic"),
            "modul3_slot": prices.get("modul3_slot"),
            "modul3_label": prices.get("modul3_label"),
            "slot_end": prices.get("slot_end"),
            "hypothetical": True,
        }


def _round_attr(value, digits: int):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


class WallboxSensor(_Base):
    def __init__(self, hass):
        super().__init__(hass, "etc_wallbox", "Wallbox-Laden")
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_suggested_display_precision = 2
        self._attr_icon = "mdi:ev-station"
        self._attr_translation_key = "wallbox"
        self.entity_id = "sensor.tarifvergleich_wallbox"

    @property
    def native_value(self):
        snap = (self._data().get("snap") or {}).get("today") or {}
        value = snap.get("tesla_kwh")
        if value is None:
            return None
        return round(float(value), 2)

    @property
    def extra_state_attributes(self):
        snap = self._data().get("snap") or {}
        periods = {
            "today": snap.get("today") or {},
            "yesterday": snap.get("yesterday") or {},
            "week": snap.get("week") or {},
            "month": snap.get("month") or {},
            "year": snap.get("year") or {},
            "total": snap.get("total") or {},
        }
        out = {
            "note": (
                "Lade-kWh der Wallbox seit Zählbeginn, bewertet mit dem Arbeitspreis "
                "der jeweiligen Viertelstunde. Keine Grundgebühr, keine Rechnung, "
                "nicht der Inexogy-Netzbezug."
            ),
            "tesla_count_started_utc": snap.get("tesla_count_started_utc"),
            "tesla_pending_kwh": _round_attr(snap.get("tesla_pending_kwh"), 4),
            "week_start": periods["week"].get("week_start"),
            "week_end": periods["week"].get("week_end"),
            "week_sunday": periods["week"].get("week_sunday"),
            "week_quality": periods["week"].get("quality"),
        }
        for period, row in periods.items():
            out[f"tesla_kwh_{period}"] = _round_attr(row.get("tesla_kwh"), 3)
            for tid in TARIFF_LABELS:
                if tid == "dynamic_perfect":
                    continue
                out[f"tesla_cost_{tid}_{period}"] = _round_attr(row.get(f"tesla_cost_{tid}"), 2)
        return out
