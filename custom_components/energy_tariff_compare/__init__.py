"""Lightweight 15-minute energy tariff comparison for Home Assistant OS."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import CONF_PATH, DB_PATH, DOMAIN, PLAT_NAME
from .collector import (
    AGGREGATE_SCHEMA_VERSION,
    apply_tariff_config_change,
    backfill_perfect,
    collect_tick,
    current_prices,
    parse_float,
    rebuild_all_aggregates,
    rebuild_day,
    repair_gaps_from_counter_series,
    repair_spot_prices_from_energy_charts,
    reprice_day,
    snapshot,
    spot_day_complete,
    tomorrow_retry_delay,
)
from .store import Store, utc_iso
from . import tariffs as T

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data={})
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    cfg_path = Path(hass.config.path(CONF_PATH))
    db_path = Path(hass.config.path(DB_PATH))
    if not cfg_path.exists():
        _LOGGER.error("Missing %s", cfg_path)
        return False

    cfg = await hass.async_add_executor_job(T.load_config, str(cfg_path))
    store = await hass.async_add_executor_job(Store, str(db_path))
    now_local = dt_util.now().astimezone(T.TZ)
    selected_month = await hass.async_add_executor_job(store.get_meta, "selected_month")
    if not selected_month:
        await hass.async_add_executor_job(
            store.set_meta, "selected_month", now_local.strftime("%Y-%m")
        )
    cfg_hash = await hass.async_add_executor_job(
        lambda: hashlib.sha256(cfg_path.read_bytes()).hexdigest()
    )
    collect_lock = asyncio.Lock()
    spot_fetch_lock = asyncio.Lock()
    data = {
        "cfg": cfg,
        "cfg_hash": cfg_hash,
        "store": store,
        "latest": {},
        "unsub": [],
        "collect_lock": collect_lock,
        "spot_fetch_lock": spot_fetch_lock,
    }
    data["latest"]["snap"] = await hass.async_add_executor_job(snapshot, store, now_local, cfg)
    hass.data[DOMAIN] = data

    async def _readings() -> dict:
        cfg_local = data["cfg"]
        out = {}
        for eid in cfg_local["entities"].values():
            state = hass.states.get(eid)
            if state is None:
                out[eid] = {"state": None, "last_updated": None, "last_reported": None}
            else:
                out[eid] = {
                    "state": state.state,
                    "last_updated": getattr(state, "last_updated", None),
                    "last_reported": getattr(state, "last_reported", None),
                }
        return out

    async def _collect(now: datetime | None = None) -> None:
        async with collect_lock:
            stamp = now or dt_util.now()
            readings = await _readings()
            try:
                row = await hass.async_add_executor_job(
                    collect_tick, store, data["cfg"], stamp, readings
                )
                data["latest"]["error"] = None
            except Exception as err:
                _LOGGER.exception("collect_tick failed")
                data["latest"]["error"] = str(err)
                await _refresh_prices()
                return
            data["latest"]["interval"] = row
            data["latest"]["snap"] = await hass.async_add_executor_job(snapshot, store, stamp, data["cfg"])
            await _refresh_prices()
            hass.bus.async_fire(f"{DOMAIN}_collected", {"quality": row.get("quality")})

    async def _refresh_prices() -> None:
        cfg_local = data["cfg"]
        ents = cfg_local["entities"]
        now = dt_util.now()
        spot = parse_float(getattr(hass.states.get(ents["nordpool_current"]), "state", None))
        nxt = parse_float(getattr(hass.states.get(ents["nordpool_next"]), "state", None))
        low = parse_float(getattr(hass.states.get(ents["nordpool_low"]), "state", None))
        high = parse_float(getattr(hass.states.get(ents["nordpool_high"]), "state", None))
        prices = current_prices(cfg_local, now, spot, nxt)
        if low is not None:
            prices["spot_min_ct"] = round(
                T.dynamic_gross_eur_per_kwh(cfg_local, now.astimezone(T.TZ), low, modul3=False) * 100.0, 2
            )
        if high is not None:
            prices["spot_max_ct"] = round(
                T.dynamic_gross_eur_per_kwh(cfg_local, now.astimezone(T.TZ), high, modul3=False) * 100.0, 2
            )
        api = hass.states.get(ents.get("octopus_price", ""))
        if api is not None:
            prices["octopus_api_ct"] = None if parse_float(api.state) is None else round(parse_float(api.state) * 100.0, 2)
        data["latest"]["prices"] = prices
        hass.bus.async_fire(f"{DOMAIN}_updated")

    async def _fetch_spots(days: list) -> dict:
        counts: dict = {}
        async with spot_fetch_lock:
            entries = hass.config_entries.async_entries("nordpool")
            if not entries:
                return {day: 0 for day in days}
            entry_id = entries[0].entry_id
            cfg_local = data["cfg"]
            area = cfg_local["nordpool"]["area"]
            currency = cfg_local["nordpool"]["currency"]
            fetched_at = datetime.now(T.TZ).isoformat()
            repriced = 0
            for day in sorted(set(days)):
                counts[day] = 0
                try:
                    result = await hass.services.async_call(
                        "nordpool",
                        "get_prices_for_date",
                        {
                            "config_entry": entry_id,
                            "date": day.isoformat(),
                            "areas": area,
                            "currency": currency,
                        },
                        blocking=True,
                        return_response=True,
                    )
                except Exception:
                    _LOGGER.exception("nordpool.get_prices_for_date failed for %s", day)
                    continue
                series = None
                if isinstance(result, dict):
                    series = result.get(area) or result.get(str(area))
                    if series is None and result:
                        first = next(iter(result.values()))
                        series = first if isinstance(first, list) else (
                            first.get(area) if isinstance(first, dict) else None
                        )
                if not series:
                    continue
                rows = []
                for item in series:
                    try:
                        start = datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00"))
                        end = datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00"))
                    except (KeyError, TypeError, ValueError):
                        continue
                    price = T.nordpool_service_price_to_eur_kwh(
                        item.get("price") if isinstance(item, dict) else None
                    )
                    if price is None:
                        continue
                    rows.append((utc_iso(start), utc_iso(end), price, fetched_at))
                counts[day] = len(rows)
                if rows:
                    await hass.async_add_executor_job(store.bulk_upsert_spots, rows)
                    repriced += await hass.async_add_executor_job(reprice_day, store, cfg_local, day)
                _LOGGER.info(
                    "Stored Nord Pool slots for %s (%s points, service EUR/MWh / 1000 -> EUR/kWh)",
                    day,
                    len(rows),
                )
            if repriced:
                data["latest"]["snap"] = await hass.async_add_executor_job(
                    snapshot, store, dt_util.now(), data["cfg"]
                )
                hass.bus.async_fire(f"{DOMAIN}_updated")
        return counts

    async def _close_yesterday(now: datetime) -> None:
        yesterday = now.astimezone(T.TZ).date() - timedelta(days=1)
        await hass.async_add_executor_job(rebuild_day, store, data["cfg"], yesterday, True)

    async def _reload_cfg() -> None:
        async with collect_lock:
            new_cfg = await hass.async_add_executor_job(T.load_config, str(cfg_path))
            if new_cfg["entities"] != data["cfg"]["entities"]:
                raise ValueError("Entity-IDs geändert; für Listener-Neubindung Home Assistant neu starten")
            old_hash = data["cfg_hash"]
            new_hash = await hass.async_add_executor_job(
                lambda: hashlib.sha256(cfg_path.read_bytes()).hexdigest()
            )
            repriced = await hass.async_add_executor_job(
                apply_tariff_config_change, store, new_cfg, old_hash, new_hash
            )
            if not repriced:
                await hass.async_add_executor_job(rebuild_all_aggregates, store, new_cfg)
                await hass.async_add_executor_job(
                    store.set_meta_many,
                    {"config_sha256": new_hash, "aggregate_schema_version": AGGREGATE_SCHEMA_VERSION},
                )
            data["cfg"] = new_cfg
            data["cfg_hash"] = new_hash
            data["latest"]["snap"] = await hass.async_add_executor_job(
                snapshot, store, dt_util.now(), data["cfg"]
            )
            hass.bus.async_fire(f"{DOMAIN}_updated")

    async def _fetch_tomorrow(now: datetime) -> None:
        data["tomorrow_spot_retries"] = 0
        await _ensure_tomorrow_spots()

    async def _spot_state_changed(_event) -> None:
        await _refresh_prices()

    def _cancel_tomorrow_retry() -> None:
        unsub_retry = data.get("tomorrow_retry_unsub")
        if unsub_retry:
            unsub_retry()
            data["tomorrow_retry_unsub"] = None

    def _schedule_tomorrow_retry(delay: float) -> None:
        _cancel_tomorrow_retry()
        data["tomorrow_retry_unsub"] = async_call_later(hass, delay, _ensure_tomorrow_spots)

    async def _ensure_tomorrow_spots(_now=None) -> None:
        tomorrow = dt_util.now().astimezone(T.TZ).date() + timedelta(days=1)
        complete = await hass.async_add_executor_job(spot_day_complete, store, tomorrow)
        if complete:
            data["tomorrow_spot_retries"] = 0
            _cancel_tomorrow_retry()
            return
        await _fetch_spots([tomorrow])
        complete = await hass.async_add_executor_job(spot_day_complete, store, tomorrow)
        if complete:
            data["tomorrow_spot_retries"] = 0
            _cancel_tomorrow_retry()
            _LOGGER.info("Tomorrow Nord Pool slots complete for %s", tomorrow)
            return
        attempt = int(data.get("tomorrow_spot_retries") or 0)
        delay = tomorrow_retry_delay(attempt)
        if delay is None:
            _LOGGER.warning(
                "Tomorrow Nord Pool slots still incomplete for %s after %s attempts",
                tomorrow,
                attempt,
            )
            return
        data["tomorrow_spot_retries"] = attempt + 1
        _schedule_tomorrow_retry(delay)

    async def _tomorrow_ready_changed(event) -> None:
        new_state = event.data.get("new_state") if event and event.data else None
        old_state = event.data.get("old_state") if event and event.data else None
        def _on(state) -> bool:
            return state is not None and str(getattr(state, "state", "")).lower() in ("on", "true", "1")
        if _on(new_state) and not _on(old_state):
            data["tomorrow_spot_retries"] = 0
            await _ensure_tomorrow_spots()

    unsub = []
    unsub.append(
        async_track_time_change(
            hass, _collect, minute=[0, 15, 30, 45], second=20
        )
    )
    unsub.append(
        async_track_time_change(
            hass, _close_yesterday, hour=0, minute=5, second=30
        )
    )
    unsub.append(
        async_track_time_change(
            hass, _fetch_tomorrow, hour=14, minute=5, second=0
        )
    )
    price_eid = cfg["entities"]["nordpool_current"]
    unsub.append(
        async_track_state_change_event(hass, [price_eid], _spot_state_changed)
    )
    ready_eid = cfg["entities"].get("nordpool_tomorrow_ready")
    if ready_eid:
        unsub.append(
            async_track_state_change_event(hass, [ready_eid], _tomorrow_ready_changed)
        )
    unsub.append(_cancel_tomorrow_retry)
    data["unsub"] = unsub
    data["collect"] = _collect
    data["refresh"] = _refresh_prices
    data["fetch_spots"] = _fetch_spots
    data["reload_cfg"] = _reload_cfg

    async def _svc_collect(call: ServiceCall) -> None:
        await _collect()

    async def _svc_shift(call: ServiceCall) -> None:
        try:
            await _shift_month(call)
        except Exception:
            _LOGGER.exception("shift_month failed")

    async def _shift_month(call: ServiceCall) -> None:
        delta = int(call.data.get("delta", 0) or 0)
        year = call.data.get("year")
        month = call.data.get("month")
        current = await hass.async_add_executor_job(store.get_meta, "selected_month")
        now = dt_util.now().astimezone(T.TZ)
        if year and month:
            y, m = int(year), int(month)
        elif delta == 0:
            y, m = now.year, now.month
        elif current and "-" in str(current):
            y, m = [int(x) for x in str(current).split("-")[:2]]
        else:
            y, m = now.year, now.month
        m += delta
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        if (y, m) > (now.year, now.month):
            y, m = now.year, now.month
        await hass.async_add_executor_job(store.set_meta, "selected_month", f"{y:04d}-{m:02d}")
        data["latest"]["snap"] = await hass.async_add_executor_job(snapshot, store, now, data["cfg"])
        hass.bus.async_fire(f"{DOMAIN}_updated")

    async def _svc_reload(call: ServiceCall) -> None:
        await _reload_cfg()
        await _refresh_prices()

    hass.services.async_register(DOMAIN, "collect_now", _svc_collect)
    hass.services.async_register(DOMAIN, "shift_month", _svc_shift)
    hass.services.async_register(DOMAIN, "reload_tariffs", _svc_reload)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _kickoff(_now=None) -> None:
        await _refresh_prices()
        await _collect()
        today = dt_util.now().astimezone(T.TZ).date()
        await _fetch_spots([today, today + timedelta(days=1)])
        data["tomorrow_spot_retries"] = 0
        tomorrow_ok = await hass.async_add_executor_job(
            spot_day_complete, store, today + timedelta(days=1)
        )
        if not tomorrow_ok:
            _schedule_tomorrow_retry(120)

    async def _repair_gaps_from_recorder() -> dict[str, int]:
        empty = {"changed_slots": 0, "changed_days": 0}
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
        except ImportError:
            _LOGGER.warning("Recorder-Statistik nicht verfügbar; Lücken bleiben")
            return empty
        try:
            instance = get_instance(hass)
        except Exception:
            return empty
        entity = data["cfg"]["entities"]["grid_import"]
        now = dt_util.now()
        start = now - timedelta(days=7)

        def _load():
            return statistics_during_period(
                hass,
                start,
                now,
                {entity},
                "5minute",
                None,
                {"state"},
            )

        try:
            stats = await instance.async_add_executor_job(_load)
        except Exception:
            _LOGGER.exception("5-Minuten-Statistik für Lückenfüllung fehlgeschlagen")
            return empty
        series = []
        if isinstance(stats, dict):
            series = stats.get(entity) or []
            if not series:
                for value in stats.values():
                    if isinstance(value, list):
                        series = value
                        break
        samples: list[tuple[datetime, float]] = []
        for item in series or []:
            if not isinstance(item, dict):
                continue
            raw_start = item.get("start")
            state = parse_float(item.get("state"))
            when = None
            if isinstance(raw_start, datetime):
                when = raw_start
            elif isinstance(raw_start, (int, float)):
                when = datetime.fromtimestamp(float(raw_start), tz=timezone.utc)
            if when is None or state is None:
                continue
            samples.append((when, state))
        if not samples:
            return empty
        return await hass.async_add_executor_job(
            repair_gaps_from_counter_series, store, data["cfg"], samples
        )

    async def _fill_perfect(_now=None) -> None:
        spot_result = {"changed_slots": 0, "changed_days": 0}
        gap_result = {"changed_slots": 0, "changed_days": 0}
        repair_path = Path(
            hass.config.path(
                "energy_tariff_compare/imports/energy_charts_DE-LU_2026-01-01_2026-08-23.json"
            )
        )
        if repair_path.exists():
            try:
                spot_result = await hass.async_add_executor_job(
                    repair_spot_prices_from_energy_charts,
                    store,
                    data["cfg"],
                    repair_path,
                )
            except Exception:
                _LOGGER.exception("one-time Energy Charts spot repair failed")
        try:
            async with collect_lock:
                try:
                    gap_result = await _repair_gaps_from_recorder()
                except Exception:
                    _LOGGER.exception("gap repair from recorder failed")
                stored_hash = await hass.async_add_executor_job(store.get_meta, "config_sha256")
                if stored_hash != data["cfg_hash"]:
                    n_reprice = await hass.async_add_executor_job(
                        apply_tariff_config_change,
                        store,
                        data["cfg"],
                        stored_hash,
                        data["cfg_hash"],
                    )
                    _LOGGER.info("Startup reprice after tariffs.yaml change: %s days", n_reprice)
                n = await hass.async_add_executor_job(backfill_perfect, store, data["cfg"])
                await hass.async_add_executor_job(
                    store.set_meta, "config_sha256", data["cfg_hash"]
                )
            _LOGGER.info(
                "Startup maintenance: %s aggregate days, %s repaired spots over %s days, "
                "%s gap slots over %s days",
                n,
                spot_result["changed_slots"],
                spot_result["changed_days"],
                gap_result["changed_slots"],
                gap_result["changed_days"],
            )
            data["latest"]["snap"] = await hass.async_add_executor_job(
                snapshot, store, dt_util.now(), data["cfg"]
            )
            hass.bus.async_fire(f"{DOMAIN}_updated")
        except Exception:
            _LOGGER.exception("startup maintenance failed")

    unsub.append(async_call_later(hass, 5, _kickoff))
    unsub.append(async_call_later(hass, 45, _fill_perfect))
    data["unsub"] = unsub
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data.pop(DOMAIN, None)
    if data:
        for unsub in data.get("unsub", []):
            unsub()
        store = data.get("store")
        if store is not None:
            await hass.async_add_executor_job(store.close)
    for service in ("collect_now", "shift_month", "reload_tariffs"):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    return unload
