"""15-minute collect + daily/monthly/yearly aggregation. No Home Assistant imports."""

from __future__ import annotations

import bisect
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import Store
from . import tariffs as T

COST_KEYS = (
    "octopus_heat",
    "octopus_heat_loyalty",
    "fix_tarif",
    "dynamic",
    "dynamic_modul3",
)

AGGREGATE_SCHEMA_VERSION = "split-costs-v3-tesla-v2"
TOMORROW_SPOT_RETRY_DELAYS = (120, 300, 600, 900)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


SLOT_SECONDS = 900
MULTI_SLOT_SECONDS = 20 * 60


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "unknown", "unavailable", "none"):
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _reading_payload(readings: dict[str, Any], entity_id: str) -> tuple[Any, datetime | None]:
    raw = readings.get(entity_id)
    if isinstance(raw, dict):
        source_time = raw.get("last_reported") or raw.get("last_updated")
        return raw.get("state"), _as_datetime(source_time)
    return raw, None


def collect_tick(
    store: Store,
    cfg: dict,
    now: datetime,
    readings: dict[str, Any],
) -> dict:
    entities = cfg["entities"]
    sample_utc = now.astimezone(timezone.utc)
    start_utc, end_utc = T.closed_interval_utc(now)
    start_local = start_utc.astimezone(T.TZ)
    start_key = utc_iso(start_utc)

    import_raw, import_updated = _reading_payload(readings, entities["grid_import"])
    export_raw, export_updated = _reading_payload(readings, entities["grid_export"])
    import_now = parse_float(import_raw)
    export_now = parse_float(export_raw)
    tesla_entity = entities.get("tesla_energy")
    tesla_now = None
    tesla_updated = None
    if tesla_entity:
        tesla_raw, tesla_updated = _reading_payload(readings, tesla_entity)
        tesla_now = parse_float(tesla_raw)
    context = store.collect_context(start_key)
    meta = context["meta"]
    last_import = parse_float(meta.get("last_import"))
    last_export = parse_float(meta.get("last_export"))
    last_tesla = parse_float(meta.get("last_tesla"))
    last_start = meta.get("last_closed_interval_start_utc") or meta.get("last_interval_start")
    last_import_source = meta.get("last_import_source_updated_utc") or meta.get("last_source_updated_utc")
    last_export_source = meta.get("last_export_source_updated_utc")
    last_tesla_source = meta.get("last_tesla_source_updated_utc")
    last_sample = _as_datetime(meta.get("last_sample_utc"))
    existing = context["existing"]
    tesla_pending = parse_float(meta.get("tesla_pending_kwh")) or 0.0
    tesla_pending_extra = meta.get("tesla_pending_extra") == "1"

    def baseline_meta(*, touch_sample: bool, write_tesla: bool = True) -> dict[str, str | None]:
        values: dict[str, str | None] = {}
        if import_now is not None:
            values["last_import"] = str(import_now)
        if export_now is not None:
            values["last_export"] = str(export_now)
        if tesla_now is not None and write_tesla:
            values["last_tesla"] = str(tesla_now)
            if not meta.get("tesla_count_started_utc"):
                values["tesla_count_started_utc"] = utc_iso(sample_utc)
            if tesla_updated is not None:
                values["last_tesla_source_updated_utc"] = utc_iso(tesla_updated)
        if touch_sample:
            values["last_sample_utc"] = utc_iso(sample_utc)
        if import_updated is not None:
            values["last_import_source_updated_utc"] = utc_iso(import_updated)
            values["last_source_updated_utc"] = utc_iso(import_updated)
        if export_updated is not None:
            values["last_export_source_updated_utc"] = utc_iso(export_updated)
        return values

    def skeleton(
        quality: str,
        sources: list[str],
        kwh_in=None,
        kwh_out=None,
        tesla_kwh=None,
        spot=None,
        costs=None,
    ) -> dict:
        costs = costs or {}
        return {
            "interval_start": start_key,
            "interval_end": utc_iso(end_utc),
            "local_start": start_local.replace(microsecond=0).isoformat(),
            "grid_import_kwh": kwh_in,
            "grid_export_kwh": kwh_out,
            "tesla_kwh": tesla_kwh,
            "counter_import": import_now,
            "counter_export": export_now,
            "nordpool_eur_kwh": spot,
            "cost_octopus_heat": costs.get("octopus_heat"),
            "cost_octopus_heat_loyalty": costs.get("octopus_heat_loyalty"),
            "cost_fix_tarif": costs.get("fix_tarif"),
            "cost_dynamic": costs.get("dynamic"),
            "cost_dynamic_modul3": costs.get("dynamic_modul3"),
            "quality": quality,
            "sources": ",".join(sources),
            "updated_at": datetime.now(T.TZ).isoformat(),
        }

    import_source_unchanged = (
        import_updated is not None
        and last_import_source is not None
        and utc_iso(import_updated) == last_import_source
        and last_import is not None
    )
    tesla_needs = False
    if tesla_entity and tesla_now is not None:
        if last_tesla is None:
            tesla_needs = True
        elif (
            tesla_updated is not None
            and last_tesla_source is not None
            and utc_iso(tesla_updated) == last_tesla_source
        ):
            tesla_needs = False
        elif tesla_updated is None and abs(tesla_now - last_tesla) < 1e-12:
            tesla_needs = False
        else:
            tesla_needs = True
    if import_source_unchanged and not tesla_needs:
        return existing if existing is not None else skeleton("unchanged_source", ["discovergy", "same_source_ts"])

    if last_start == start_key and existing is not None:
        # A manual/repeated collect in the same closed slot must not move the
        # counter baseline. The next slot then receives the complete delta.
        return existing

    if last_start:
        try:
            last_dt_ahead = datetime.fromisoformat(last_start)
        except ValueError:
            last_dt_ahead = None
        if last_dt_ahead is not None:
            if last_dt_ahead.tzinfo is None:
                last_dt_ahead = last_dt_ahead.replace(tzinfo=timezone.utc)
            if last_dt_ahead.astimezone(timezone.utc) >= end_utc:
                return existing if existing is not None else skeleton(
                    "already_ahead", ["discovergy", "closed_slot_already_passed"]
                )

    if last_import is None:
        write_tesla = last_tesla is None
        tesla_reset = False
        bootstrap_meta = baseline_meta(touch_sample=True, write_tesla=write_tesla)
        if tesla_now is not None and last_tesla is not None:
            leftover = tesla_now - last_tesla
            if leftover > 1e-12:
                bootstrap_meta["tesla_pending_kwh"] = str(round(leftover, 8))
            elif leftover < -0.02:
                tesla_reset = True
                bootstrap_meta = baseline_meta(touch_sample=True, write_tesla=True)
                if tesla_pending > 1e-12:
                    bootstrap_meta["tesla_pending_kwh"] = str(round(tesla_pending, 8))
                    bootstrap_meta["tesla_pending_extra"] = "1"
        store.commit_live_collect(None, bootstrap_meta)
        return existing if existing is not None else skeleton("bootstrap", ["discovergy", "baseline_only"])

    quality = "ok"
    sources = ["discovergy"]
    kwh_in = None
    kwh_out = None
    span_ok = True
    span = None
    span_from = _as_datetime(last_import_source) or last_sample or _as_datetime(last_start)
    span_to = import_updated or sample_utc
    if span_from is not None:
        span = (span_to.astimezone(timezone.utc) - span_from.astimezone(timezone.utc)).total_seconds()
        if span <= 0 and not tesla_needs:
            return existing if existing is not None else skeleton(
                "stale_source", ["discovergy", "source_time_not_newer"]
            )
        if span is not None and span > MULTI_SLOT_SECONDS:
            span_ok = False
            quality = "unallocated"
            sources.append("delayed_multi_slot")

    import_idle = import_source_unchanged or (span is not None and span <= 0)
    if import_now is None:
        quality = "unavailable"
        sources.append("import_unavailable")
    elif import_idle and tesla_needs:
        kwh_in = 0.0
        span_ok = True
        if quality == "unallocated":
            quality = "ok"
        sources.append("import_unchanged")
    elif not span_ok:
        kwh_in = None
        delta = import_now - last_import
        if delta >= 0:
            sources.append(f"unallocated_import_kwh={delta:.6f}")
    else:
        delta = import_now - last_import
        if delta < -0.02:
            quality = "counter_reset"
            kwh_in = 0.0
            sources.append("negative_delta_dropped")
        elif delta < 0:
            kwh_in = 0.0
        else:
            kwh_in = delta

    if export_now is None:
        kwh_out = None
    elif last_export is None or not span_ok:
        kwh_out = None if not span_ok else 0.0
    else:
        d_out = export_now - last_export
        kwh_out = 0.0 if d_out < 0 else d_out

    missing_rows: list[dict] = []
    if last_start:
        try:
            last_dt = datetime.fromisoformat(last_start)
        except ValueError:
            last_dt = None
        if last_dt is not None:
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            last_dt = last_dt.astimezone(timezone.utc)
            cursor = last_dt + timedelta(minutes=15)
            while cursor < start_utc - timedelta(seconds=1):
                c_end = cursor + timedelta(minutes=15)
                missing_rows.append(
                    {
                        "interval_start": utc_iso(cursor),
                        "interval_end": utc_iso(c_end),
                        "local_start": cursor.astimezone(T.TZ).replace(microsecond=0).isoformat(),
                        "updated_at": datetime.now(T.TZ).isoformat(),
                    }
                )
                cursor = c_end
            if quality == "ok" and (start_utc - last_dt).total_seconds() > SLOT_SECONDS + 30:
                quality = "delayed"

    tesla_kwh = None
    tesla_reset = False
    if tesla_entity:
        tesla_span_ok = True
        tesla_from = _as_datetime(last_tesla_source) or last_sample or _as_datetime(last_start)
        tesla_to = tesla_updated or sample_utc
        if tesla_now is not None and last_tesla is not None and tesla_from is not None:
            tesla_span = (
                tesla_to.astimezone(timezone.utc) - tesla_from.astimezone(timezone.utc)
            ).total_seconds()
            if tesla_span <= 0:
                tesla_span_ok = False
                sources.append("tesla_source_not_newer")
            elif tesla_span > MULTI_SLOT_SECONDS:
                tesla_span_ok = False
                sources.append("tesla_delayed_multi_slot")
        if tesla_now is None:
            sources.append("tesla_unavailable")
        elif last_tesla is None:
            sources.append("tesla_baseline")
        elif not tesla_span_ok:
            tesla_delta = tesla_now - last_tesla
            if tesla_delta < -0.02:
                tesla_reset = True
                sources.append("tesla_counter_reset")
                if tesla_pending > 1e-12:
                    tesla_pending_extra = True
            elif tesla_delta >= 0:
                sources.append(f"unallocated_tesla_kwh={tesla_delta:.6f}")
            flush_delta = tesla_delta if tesla_delta > 1e-12 else 0.0
            if tesla_pending_extra and tesla_pending > 1e-12:
                flush_delta += tesla_pending
            if (
                flush_delta > 1e-12
                and span_ok
                and quality not in {"unallocated", "unavailable"}
            ):
                tesla_kwh = flush_delta
                sources.append("tesla_pending_flushed")
        else:
            tesla_delta = tesla_now - last_tesla
            if tesla_delta < -0.02:
                tesla_reset = True
                sources.append("tesla_counter_reset")
                tesla_kwh = tesla_pending if tesla_pending > 1e-12 else 0.0
                if tesla_kwh > 1e-12:
                    sources.append("tesla_pending_flushed")
            elif tesla_delta < 0:
                tesla_kwh = 0.0
            else:
                tesla_kwh = tesla_delta
                if tesla_pending_extra and tesla_pending > 1e-12:
                    tesla_kwh = tesla_delta + tesla_pending
                    sources.append("tesla_pending_flushed")

    spot = context["spot"]
    if spot is not None:
        sources.append("nordpool_stored")
    else:
        if quality == "ok":
            quality = "incomplete"
        sources.append("nordpool_missing")

    costs: dict[str, float | None] = {}
    for tid in COST_KEYS:
        price = T.energy_price_gross_eur_per_kwh(cfg, tid, start_local, spot)
        if kwh_in is None or price is None:
            costs[tid] = None
        else:
            costs[tid] = round(kwh_in * price, 6)

    row = skeleton(
        quality,
        sources,
        kwh_in=kwh_in,
        kwh_out=kwh_out,
        tesla_kwh=tesla_kwh,
        spot=spot,
        costs=costs,
    )
    tesla_booked = tesla_kwh is not None
    tesla_is_baseline = tesla_now is not None and last_tesla is None
    write_tesla = tesla_booked or tesla_is_baseline or tesla_reset
    meta_updates = baseline_meta(
        touch_sample=import_now is not None, write_tesla=write_tesla
    )
    if tesla_booked:
        meta_updates["tesla_pending_kwh"] = None
        meta_updates["tesla_pending_extra"] = None
    elif tesla_now is not None and last_tesla is not None and tesla_kwh is None:
        leftover = tesla_now - last_tesla
        if tesla_reset:
            if tesla_pending > 1e-12:
                meta_updates["tesla_pending_kwh"] = str(round(tesla_pending, 8))
                meta_updates["tesla_pending_extra"] = "1"
        elif leftover > 1e-12:
            meta_updates["tesla_pending_kwh"] = str(round(leftover, 8))
    if quality != "unavailable":
        meta_updates["last_closed_interval_start_utc"] = start_key
    written, changed = store.commit_live_collect(row, meta_updates, missing_rows)

    if changed or missing_rows:
        affected_days = {start_local.date()}
        affected_days.update(
            datetime.fromisoformat(item["local_start"]).date() for item in missing_rows
        )
        for affected_day in sorted(affected_days):
            rebuild_day(store, cfg, affected_day, complete=False)
    return written or row


def _sum(rows, field: str) -> float:
    total = 0.0
    for row in rows:
        value = row[field]
        if value is not None:
            total += float(value)
    return total


def _sum_if_complete(rows, field: str) -> float | None:
    """Sum a field, but never turn an unavailable tariff into a cheap partial total."""
    values: list[float] = []
    for row in rows:
        value = row.get(field)
        if value is None:
            return None
        values.append(float(value))
    return sum(values)


def tomorrow_retry_delay(attempt: int) -> int | None:
    """Seconds until the next tomorrow-spot retry, or None after the last attempt."""
    if 0 <= attempt < len(TOMORROW_SPOT_RETRY_DELAYS):
        return TOMORROW_SPOT_RETRY_DELAYS[attempt]
    return None


def period_ranking_complete(row: dict | None, *, period: str) -> bool:
    """Trophy/ranking is allowed only when due slots, prices and expected days exist."""
    if not row:
        return False
    if row.get("intervals_due") is None:
        return False
    if int(row.get("intervals_missing") or 0) != 0:
        return False
    if int(row.get("price_intervals_missing") or 0) != 0:
        return False
    if period in {"month", "year"}:
        days_with = row.get("days_with_data")
        days_expected = row.get("days_expected")
        if days_with is None or days_expected is None:
            return False
        return int(days_with) == int(days_expected)
    return True


def days_with_interval_or_daily(store: Store, today: date) -> list[date]:
    """Days that have interval facts and/or a daily row, always including today."""
    days = store.fact_days(date(2000, 1, 1), today)
    if today not in days:
        days.append(today)
    return days


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _due_interval_count(day: date, now: datetime) -> int:
    expected = T.expected_intervals(day)
    local_now = now.astimezone(T.TZ)
    if day < local_now.date():
        return expected
    if day > local_now.date():
        return 0
    day_start = datetime(day.year, day.month, day.day, tzinfo=T.TZ).astimezone(timezone.utc)
    open_start, _ = T.interval_floor(local_now)
    elapsed = int((open_start.astimezone(timezone.utc) - day_start).total_seconds() // SLOT_SECONDS)
    return max(0, min(expected, elapsed))


def _latest_data_through(rows: list[dict]) -> str | None:
    ends = [
        str(row["interval_end"])
        for row in rows
        if row.get("grid_import_kwh") is not None and row.get("interval_end")
    ]
    return max(ends) if ends else None


def _variable_network_gross_eur(cfg: dict, tariff_id: str, rows: list[dict]) -> float:
    tariff = T.tariff_by_id(cfg, tariff_id)
    if not tariff.get("uses_paragraph_14a"):
        return 0.0
    total = 0.0
    for row in rows:
        kwh = parse_float(row.get("grid_import_kwh"))
        if kwh is None:
            continue
        local = _local_from_row(row)
        if tariff.get("uses_modul3"):
            slot = T.modul3_slot(cfg, local)
            net_ct = float(cfg["westnetz_2026"]["modul3"][slot]["net_ct_per_kwh"])
        else:
            net_ct = float(cfg["westnetz_2026"]["working_net_ct_per_kwh"])
        total += kwh * net_ct / 100.0 * (1.0 + float(cfg["vat_rate"]))
    return total


def _local_from_row(row: dict) -> datetime:
    local = datetime.fromisoformat(row["local_start"])
    if local.tzinfo is None:
        local = local.replace(tzinfo=T.TZ)
    return local


def tesla_energy_cost_eur(cfg: dict, tariff_id: str, rows: list[dict]) -> float | None:
    """Arbeitspreis × Wallbox-kWh; keine Grundgebühr. None, wenn ein Slot mit kWh keinen Preis hat."""
    total = 0.0
    saw = False
    for row in rows:
        tesla_kwh = parse_float(row.get("tesla_kwh"))
        if tesla_kwh is None:
            continue
        saw = True
        if tesla_kwh == 0.0:
            continue
        price = T.energy_price_gross_eur_per_kwh(
            cfg,
            tariff_id,
            _local_from_row(row),
            parse_float(row.get("nordpool_eur_kwh")),
        )
        if price is None:
            return None
        total += tesla_kwh * price
    if not saw:
        return 0.0
    return round(total, 6)


def perfect_total_eur(
    cfg: dict, rows: list[dict], tariff_id: str, standing_eur: float | None
) -> float | None:
    """Permute measured kWh onto the cheapest slots of this tariff. Standing stays."""
    if standing_eur is None:
        return None
    kwhs: list[float] = []
    prices: list[float] = []
    for row in rows:
        if row["grid_import_kwh"] is None or row["nordpool_eur_kwh"] is None:
            return None
        price = T.energy_price_gross_eur_per_kwh(
            cfg, tariff_id, _local_from_row(row), float(row["nordpool_eur_kwh"])
        )
        if price is None:
            return None
        kwhs.append(float(row["grid_import_kwh"]))
        prices.append(price)
    energy_perfect = T.perfect_energy_cost(kwhs, prices)
    if energy_perfect is None:
        return None
    return round(energy_perfect + standing_eur, 4)


def rebuild_day(store: Store, cfg: dict, day: date, complete: bool = False, cascade: bool = True) -> dict:
    rows = store.intervals_for_day(day)
    expected = T.expected_intervals(day)
    now_local = datetime.now(T.TZ)
    due = expected if complete else _due_interval_count(day, now_local)
    due_end = datetime(day.year, day.month, day.day, tzinfo=T.TZ).astimezone(timezone.utc) + timedelta(
        seconds=due * SLOT_SECONDS
    )
    due_rows = [
        row
        for row in rows
        if _as_datetime(row.get("interval_start")) is not None
        and _as_datetime(row["interval_start"]).astimezone(timezone.utc) < due_end
    ]
    measured_qualities = (
        "ok",
        "delayed",
        "incomplete",
        "backfilled",
        "backfilled_first",
        "repaired",
    )
    ok = sum(
        1
        for row in due_rows
        if row.get("quality") in measured_qualities and row.get("grid_import_kwh") is not None
    )
    missing = max(0, due - ok)
    future = max(0, expected - due)
    dynamic_is_valid = T.tariff_is_valid(T.tariff_by_id(cfg, "dynamic"), day)
    price_missing = sum(
        1
        for row in due_rows
        if dynamic_is_valid
        and row.get("grid_import_kwh") is not None
        and row.get("cost_dynamic") is None
    )
    energy = _sum(due_rows, "grid_import_kwh")
    tesla_energy = _sum(due_rows, "tesla_kwh")
    tesla_costs = {tid: tesla_energy_cost_eur(cfg, tid, due_rows) for tid in COST_KEYS}
    fraction = (due / expected) if expected else 0.0
    standing: dict[str, float | None] = {}
    paragraph_14a: dict[str, float | None] = {}
    energy_costs: dict[str, float | None] = {}
    costs: dict[str, float | None] = {}
    for tid in COST_KEYS:
        standing[tid] = T.standing_fixed_eur_for_period(cfg, tid, day, fraction=fraction)
        paragraph_14a[tid] = T.paragraph_14a_eur_for_period(
            cfg,
            tid,
            day,
            fraction=fraction,
            variable_network_gross_eur=_variable_network_gross_eur(cfg, tid, due_rows),
        )
        if standing[tid] is None or paragraph_14a[tid] is None:
            energy_costs[tid] = None
            costs[tid] = None
        elif tid in {"dynamic", "dynamic_modul3"} and price_missing > 0:
            energy_costs[tid] = None
            costs[tid] = None
        else:
            energy_costs[tid] = _sum(due_rows, f"cost_{tid}")
            costs[tid] = energy_costs[tid] + standing[tid] + paragraph_14a[tid]

    perfect_flat = None
    perfect_m3 = None
    potential = None
    potential_pct = None
    potential_dynamic = None
    day_complete = due == expected and missing == 0 and price_missing == 0
    if complete and day_complete:
        perfect_flat = perfect_total_eur(
            cfg,
            due_rows,
            "dynamic",
            None if standing["dynamic"] is None else standing["dynamic"] + (paragraph_14a["dynamic"] or 0.0),
        )
        perfect_m3 = perfect_total_eur(
            cfg,
            due_rows,
            "dynamic_modul3",
            None
            if standing["dynamic_modul3"] is None
            else standing["dynamic_modul3"] + (paragraph_14a["dynamic_modul3"] or 0.0),
        )
        if perfect_flat is not None:
            potential_dynamic = round(float(costs["dynamic"]) - perfect_flat, 6)
        if perfect_m3 is not None:
            potential = round(float(costs["dynamic_modul3"]) - perfect_m3, 6)
            if costs["dynamic_modul3"]:
                potential_pct = round(100.0 * potential / costs["dynamic_modul3"], 1)

    if day_complete:
        quality = "vollständig"
    else:
        quality = f"{ok} von {due} fälligen Intervallen"
        if missing:
            quality += f" · {missing} fehlend"
        if price_missing:
            quality += f" · {price_missing} ohne Börsenpreis"
        if future:
            quality += f" · {future} künftig"

    payload = {
        "day": day.isoformat(),
        "grid_import_kwh": round(energy, 4),
        "tesla_kwh": round(tesla_energy, 4),
        **{f"tesla_cost_{tid}": _round_or_none(tesla_costs[tid]) for tid in COST_KEYS},
        "intervals_ok": ok,
        "intervals_due": due,
        "intervals_missing": missing,
        "intervals_future": future,
        "price_intervals_missing": price_missing,
        "intervals_expected": expected,
        "perfect_days": 1
        if perfect_flat is not None and perfect_m3 is not None
        else 0,
        "data_through": _latest_data_through(due_rows),
        **{f"energy_cost_{tid}": _round_or_none(energy_costs[tid]) for tid in COST_KEYS},
        **{f"standing_cost_{tid}": _round_or_none(standing[tid], 8) for tid in COST_KEYS},
        "paragraph_14a_eur": _round_or_none(paragraph_14a.get("dynamic_modul3"), 8),
        **{f"cost_{tid}": _round_or_none(costs[tid]) for tid in COST_KEYS},
        "cost_dynamic_perfect": perfect_m3,
        "cost_dynamic_flat_perfect": perfect_flat,
        "potential_eur": potential,
        "potential_pct": potential_pct,
        "potential_dynamic_eur": potential_dynamic,
        "quality": quality,
        "updated_at": datetime.now(T.TZ).isoformat(),
    }
    store.upsert_daily(payload)
    if cascade:
        rebuild_month(store, cfg, day.year, day.month)
        rebuild_year(store, cfg, day)
    return payload


def rebuild_month(store: Store, cfg: dict, year: int, month: int) -> dict:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    today = datetime.now(T.TZ).date()
    if start > today:
        last = start - timedelta(days=1)
        days = []
    else:
        last = min(end, today)
        days = store.sum_daily_range(start, last)
    dim = T.days_in_month(start)
    if start > today:
        days_used = 0
    elif (year, month) == (today.year, today.month):
        days_used = today.day
    else:
        days_used = dim
    energy = _sum(days, "grid_import_kwh")
    tesla_energy = _sum(days, "tesla_kwh")
    tesla_costs = {tid: _sum_if_complete(days, f"tesla_cost_{tid}") for tid in COST_KEYS}
    energy_costs = {tid: _sum_if_complete(days, f"energy_cost_{tid}") for tid in COST_KEYS}
    standing = {tid: _sum_if_complete(days, f"standing_cost_{tid}") for tid in COST_KEYS}
    costs = {tid: _sum_if_complete(days, f"cost_{tid}") for tid in COST_KEYS}
    complete_days = [
        row
        for row in days
        if int(row.get("intervals_due") or 0) == int(row.get("intervals_expected") or -1)
        and int(row.get("intervals_missing") or 0) == 0
        and int(row.get("price_intervals_missing") or 0) == 0
    ]
    paired_m3 = [
        row
        for row in complete_days
        if row.get("cost_dynamic_perfect") is not None and row.get("cost_dynamic_modul3") is not None
    ]
    paired_flat = [
        row
        for row in complete_days
        if row.get("cost_dynamic_flat_perfect") is not None and row.get("cost_dynamic") is not None
    ]
    perfect = _round_or_none(
        sum(float(row["cost_dynamic_perfect"]) for row in paired_m3) if paired_m3 else None
    )
    perfect_flat = _round_or_none(
        sum(float(row["cost_dynamic_flat_perfect"]) for row in paired_flat) if paired_flat else None
    )
    potential = _round_or_none(
        sum(float(row["cost_dynamic_modul3"]) for row in paired_m3) - float(perfect)
        if perfect is not None
        else None
    )
    potential_dynamic = _round_or_none(
        sum(float(row["cost_dynamic"]) for row in paired_flat) - float(perfect_flat)
        if perfect_flat is not None
        else None
    )
    days_with_data = len(days)
    days_complete = len(complete_days)
    days_incomplete = max(0, days_with_data - days_complete)
    payload = {
        "year_month": f"{year:04d}-{month:02d}",
        "grid_import_kwh": round(energy, 4),
        "tesla_kwh": round(tesla_energy, 4),
        **{f"tesla_cost_{tid}": _round_or_none(tesla_costs[tid]) for tid in COST_KEYS},
        "days_ok": days_complete,
        "days_with_data": days_with_data,
        "days_complete": days_complete,
        "days_incomplete": days_incomplete,
        "perfect_days": min(len(paired_m3), len(paired_flat)),
        "days_expected": days_used,
        "intervals_due": sum(int(row.get("intervals_due") or 0) for row in days),
        "intervals_missing": sum(int(row.get("intervals_missing") or 0) for row in days),
        "price_intervals_missing": sum(
            int(row.get("price_intervals_missing") or 0) for row in days
        ),
        "data_through": max((row.get("data_through") for row in days if row.get("data_through")), default=None),
        **{f"energy_cost_{tid}": _round_or_none(energy_costs[tid]) for tid in COST_KEYS},
        **{f"standing_cost_{tid}": _round_or_none(standing[tid], 8) for tid in COST_KEYS},
        "paragraph_14a_eur": _round_or_none(_sum_if_complete(days, "paragraph_14a_eur"), 8),
        **{f"cost_{tid}": _round_or_none(costs[tid]) for tid in COST_KEYS},
        "cost_dynamic_perfect": perfect,
        "cost_dynamic_flat_perfect": perfect_flat,
        "potential_eur": potential,
        "potential_dynamic_eur": potential_dynamic,
        "quality": f"{days_complete} vollständig · {days_incomplete} unvollständig · {days_with_data} mit Daten",
        "updated_at": datetime.now(T.TZ).isoformat(),
    }
    store.upsert_monthly(payload)
    return payload


def rebuild_year(store: Store, cfg: dict, day: date) -> dict:
    start = date(day.year, 1, 1)
    days = store.sum_daily_range(start, day)
    energy = _sum(days, "grid_import_kwh")
    tesla_energy = _sum(days, "tesla_kwh")
    tesla_costs = {tid: _sum_if_complete(days, f"tesla_cost_{tid}") for tid in COST_KEYS}
    energy_costs = {tid: _sum_if_complete(days, f"energy_cost_{tid}") for tid in COST_KEYS}
    standing = {tid: _sum_if_complete(days, f"standing_cost_{tid}") for tid in COST_KEYS}
    costs = {tid: _sum_if_complete(days, f"cost_{tid}") for tid in COST_KEYS}
    complete_days = [
        row
        for row in days
        if int(row.get("intervals_due") or 0) == int(row.get("intervals_expected") or -1)
        and int(row.get("intervals_missing") or 0) == 0
        and int(row.get("price_intervals_missing") or 0) == 0
    ]
    paired_m3 = [
        row
        for row in complete_days
        if row.get("cost_dynamic_perfect") is not None and row.get("cost_dynamic_modul3") is not None
    ]
    paired_flat = [
        row
        for row in complete_days
        if row.get("cost_dynamic_flat_perfect") is not None and row.get("cost_dynamic") is not None
    ]
    perfect = _round_or_none(
        sum(float(row["cost_dynamic_perfect"]) for row in paired_m3) if paired_m3 else None
    )
    perfect_flat = _round_or_none(
        sum(float(row["cost_dynamic_flat_perfect"]) for row in paired_flat) if paired_flat else None
    )
    potential = _round_or_none(
        sum(float(row["cost_dynamic_modul3"]) for row in paired_m3) - float(perfect)
        if perfect is not None
        else None
    )
    potential_dynamic = _round_or_none(
        sum(float(row["cost_dynamic"]) for row in paired_flat) - float(perfect_flat)
        if perfect_flat is not None
        else None
    )
    named = {tid: costs[tid] for tid in COST_KEYS}
    due_complete = len(days) == (day - start).days + 1 and all(
        int(row.get("intervals_missing") or 0) == 0 for row in days
    )
    cheapest = (
        min(named, key=lambda key: float(named[key]))
        if due_complete and energy >= 1.0 and all(value is not None for value in named.values())
        else None
    )
    days_with_data = len(days)
    days_complete = len(complete_days)
    days_incomplete = max(0, days_with_data - days_complete)
    days_expected = (day - start).days + 1
    payload = {
        "year": str(day.year),
        "grid_import_kwh": round(energy, 4),
        "tesla_kwh": round(tesla_energy, 4),
        **{f"tesla_cost_{tid}": _round_or_none(tesla_costs[tid]) for tid in COST_KEYS},
        "days_with_data": days_with_data,
        "days_complete": days_complete,
        "days_incomplete": days_incomplete,
        "days_expected": days_expected,
        "perfect_days": min(len(paired_m3), len(paired_flat)),
        "intervals_due": sum(int(row.get("intervals_due") or 0) for row in days),
        "intervals_missing": sum(int(row.get("intervals_missing") or 0) for row in days),
        "price_intervals_missing": sum(
            int(row.get("price_intervals_missing") or 0) for row in days
        ),
        "data_through": max((row.get("data_through") for row in days if row.get("data_through")), default=None),
        **{f"energy_cost_{tid}": _round_or_none(energy_costs[tid]) for tid in COST_KEYS},
        **{f"standing_cost_{tid}": _round_or_none(standing[tid], 8) for tid in COST_KEYS},
        "paragraph_14a_eur": _round_or_none(_sum_if_complete(days, "paragraph_14a_eur"), 8),
        **{f"cost_{tid}": _round_or_none(costs[tid]) for tid in COST_KEYS},
        "cost_dynamic_perfect": perfect,
        "cost_dynamic_flat_perfect": perfect_flat,
        "potential_eur": potential,
        "potential_dynamic_eur": potential_dynamic,
        "cheapest": cheapest,
        "quality": f"{days_complete} vollständig · {days_incomplete} unvollständig · {days_with_data} mit Daten",
        "updated_at": datetime.now(T.TZ).isoformat(),
    }
    store.upsert_yearly(payload)
    return payload


def expected_slot_keys(day: date) -> list[str]:
    """UTC interval_start keys for every local 15-minute slot of the day."""
    expected = T.expected_intervals(day)
    day_start = datetime(day.year, day.month, day.day, tzinfo=T.TZ).astimezone(timezone.utc)
    return [utc_iso(day_start + timedelta(seconds=index * SLOT_SECONDS)) for index in range(expected)]


def spot_day_complete(store: Store, day: date) -> bool:
    """True when stored Nord Pool slots cover every local 15-minute slot of the day."""
    keys = expected_slot_keys(day)
    if not keys:
        return False
    spots = store.spots_for_day(day)
    return all(key in spots for key in keys)


def rebuild_all_aggregates(store: Store, cfg: dict) -> int:
    """Rebuild aggregate rows from interval facts; intended for config/schema changes."""
    today = datetime.now(T.TZ).date()
    day_values = days_with_interval_or_daily(store, today)
    months: set[tuple[int, int]] = set()
    years: set[int] = set()
    for value in day_values:
        rebuild_day(store, cfg, value, value < today, False)
        months.add((value.year, value.month))
        years.add(value.year)
    for year, month in sorted(months):
        rebuild_month(store, cfg, year, month)
    for year in sorted(years):
        year_end = today if year == today.year else date(year, 12, 31)
        rebuild_year(store, cfg, year_end)
    return len(day_values)


def apply_tariff_config_change(
    store: Store, cfg: dict, previous_hash: str | None, new_hash: str
) -> int:
    """Reprice stored intervals after tariffs.yaml changes, then rebuild aggregates."""
    if previous_hash == new_hash:
        return 0
    today = datetime.now(T.TZ).date()
    day_values = days_with_interval_or_daily(store, today)
    for day in day_values:
        reprice_day(store, cfg, day, cascade=False, rebuild=False)
    rebuilt = rebuild_all_aggregates(store, cfg)
    store.set_meta_many(
        {
            "config_sha256": new_hash,
            "aggregate_schema_version": AGGREGATE_SCHEMA_VERSION,
        }
    )
    return rebuilt


_REPAIRABLE_QUALITIES = frozenset({None, "missing", "unallocated", "unavailable"})
_KEEP_MEASURED_QUALITIES = frozenset(
    {"ok", "delayed", "incomplete", "backfilled", "backfilled_first", "repaired"}
)


def repair_gaps_from_counter_series(
    store: Store,
    cfg: dict,
    samples: list[tuple[datetime, float]],
    *,
    days: list[date] | None = None,
) -> dict[str, int]:
    """Fill missing/unallocated 15-min slots from a Discovergy counter series (HA recorder)."""
    prepared: list[tuple[datetime, float]] = []
    for when, value in samples:
        stamp = _as_datetime(when)
        amount = parse_float(value)
        if stamp is None or amount is None or not math.isfinite(amount):
            continue
        prepared.append((stamp.astimezone(timezone.utc), float(amount)))
    prepared.sort(key=lambda item: item[0])
    if not prepared:
        return {"changed_slots": 0, "changed_days": 0}
    times = [item[0].timestamp() for item in prepared]

    def counter_at(when: datetime) -> float | None:
        index = bisect.bisect_right(times, when.astimezone(timezone.utc).timestamp()) - 1
        if index < 0:
            return None
        return prepared[index][1]

    today = datetime.now(T.TZ).date()
    now_utc = datetime.now(timezone.utc)
    if days is None:
        days = [today - timedelta(days=offset) for offset in range(7, -1, -1)]
    changed_days: set[date] = set()
    changed_slots = 0
    for day in days:
        expected = T.expected_intervals(day)
        day_start = datetime(day.year, day.month, day.day, tzinfo=T.TZ).astimezone(timezone.utc)
        existing_rows = {row["interval_start"]: row for row in store.intervals_for_day(day)}
        spots = store.spots_for_day(day)
        to_write: list[dict] = []
        for index in range(expected):
            start = day_start + timedelta(seconds=index * SLOT_SECONDS)
            end = start + timedelta(seconds=SLOT_SECONDS)
            if end > now_utc + timedelta(seconds=5):
                continue
            key = utc_iso(start)
            existing = existing_rows.get(key)
            quality = None if existing is None else existing.get("quality")
            if existing and quality in store.PROTECTED_INTERVAL_QUALITIES:
                continue
            if (
                existing
                and existing.get("grid_import_kwh") is not None
                and quality in _KEEP_MEASURED_QUALITIES
            ):
                continue
            if quality not in _REPAIRABLE_QUALITIES and existing is not None:
                continue
            start_counter = counter_at(start)
            end_counter = counter_at(end)
            if start_counter is None or end_counter is None:
                continue
            delta = end_counter - start_counter
            if delta < -0.02:
                continue
            kwh = 0.0 if delta < 0 else delta
            local = start.astimezone(T.TZ)
            spot = spots.get(key)
            if spot is None and existing is not None:
                spot = existing.get("nordpool_eur_kwh")
            costs: dict[str, float | None] = {}
            for tid in COST_KEYS:
                price = T.energy_price_gross_eur_per_kwh(cfg, tid, local, spot)
                costs[tid] = None if price is None else round(kwh * price, 6)
            sources = ["recorder_statistics"]
            sources.append("nordpool_stored" if spot is not None else "nordpool_missing")
            to_write.append(
                {
                    "interval_start": key,
                    "interval_end": utc_iso(end),
                    "local_start": local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": kwh,
                    "grid_export_kwh": None if existing is None else existing.get("grid_export_kwh"),
                    "tesla_kwh": None if existing is None else existing.get("tesla_kwh"),
                    "counter_import": end_counter,
                    "counter_export": None if existing is None else existing.get("counter_export"),
                    "nordpool_eur_kwh": spot,
                    "cost_octopus_heat": costs["octopus_heat"],
                    "cost_octopus_heat_loyalty": costs["octopus_heat_loyalty"],
                    "cost_fix_tarif": costs["fix_tarif"],
                    "cost_dynamic": costs["dynamic"],
                    "cost_dynamic_modul3": costs["dynamic_modul3"],
                    "quality": "repaired",
                    "sources": ",".join(sources),
                    "updated_at": datetime.now(T.TZ).isoformat(),
                }
            )
        if to_write:
            store.bulk_upsert_intervals(to_write)
            changed_slots += len(to_write)
            changed_days.add(day)
    for day in sorted(changed_days):
        rebuild_day(store, cfg, day, complete=day < today, cascade=False)
    months = {(day.year, day.month) for day in changed_days}
    years = {day.year for day in changed_days}
    for year, month in sorted(months):
        rebuild_month(store, cfg, year, month)
    for year in sorted(years):
        year_end = today if year == today.year else date(year, 12, 31)
        rebuild_year(store, cfg, year_end)
    return {"changed_slots": changed_slots, "changed_days": len(changed_days)}


def backfill_perfect(store: Store, cfg: dict) -> int:
    """One-time aggregate/schema refresh; keeps startup work negligible afterward."""
    version = AGGREGATE_SCHEMA_VERSION
    if store.get_meta("aggregate_schema_version") == version:
        return 0
    count = rebuild_all_aggregates(store, cfg)
    store.set_meta("aggregate_schema_version", version)
    return count


def reprice_day(
    store: Store, cfg: dict, day: date, *, cascade: bool = True, rebuild: bool = True
) -> int:
    """Apply stored spot prices to existing measured intervals without changing kWh."""
    rows = store.intervals_for_day(day)
    if not rows:
        return 0
    spots = store.spots_for_day(day)
    changed: list[dict] = []
    dynamic_valid = T.tariff_is_valid(T.tariff_by_id(cfg, "dynamic"), day)
    for original in rows:
        row = dict(original)
        spot = spots.get(row["interval_start"])
        kwh = parse_float(row.get("grid_import_kwh"))
        row_changed = row.get("nordpool_eur_kwh") != spot
        row["nordpool_eur_kwh"] = spot
        for tid in COST_KEYS:
            price = T.energy_price_gross_eur_per_kwh(cfg, tid, _local_from_row(row), spot)
            value = None if kwh is None or price is None else round(kwh * price, 6)
            if row.get(f"cost_{tid}") != value:
                row_changed = True
            row[f"cost_{tid}"] = value

        sources = [part for part in str(row.get("sources") or "").split(",") if part]
        sources = [part for part in sources if part not in {"nordpool_missing", "nordpool_stored"}]
        if spot is None:
            sources.append("nordpool_missing")
            if dynamic_valid and kwh is not None and row.get("quality") in {
                "ok",
                "delayed",
                "incomplete",
                "repaired",
            }:
                row["quality"] = "incomplete"
        else:
            sources.append("nordpool_stored")
            if row.get("quality") == "incomplete" and kwh is not None:
                row["quality"] = "ok"
        new_sources = ",".join(dict.fromkeys(sources))
        if row.get("sources") != new_sources:
            row_changed = True
        row["sources"] = new_sources
        if row_changed:
            row["updated_at"] = datetime.now(T.TZ).isoformat()
            changed.append(row)
    store.bulk_upsert_intervals(changed)
    if changed and rebuild:
        rebuild_day(store, cfg, day, complete=day < datetime.now(T.TZ).date(), cascade=cascade)
    return len(changed)


def repair_spot_prices_from_energy_charts(
    store: Store, cfg: dict, path: str | Path
) -> dict[str, int]:
    """One-time repair of service-unit corruption using the checked-in Energy Charts export."""
    version = "energy-charts-eur-mwh-v1"
    if store.get_meta("spot_unit_repair_version") == version:
        return {"changed_slots": 0, "changed_days": 0}
    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    unit = str(payload.get("unit") or "").replace(" ", "").lower()
    if unit not in {"eur/mwh", "€/mwh"}:
        raise ValueError(f"unexpected Energy Charts unit: {payload.get('unit')!r}")
    stamps = payload.get("unix_seconds")
    values = payload.get("price")
    if not isinstance(stamps, list) or not isinstance(values, list) or len(stamps) != len(values):
        raise ValueError("Energy Charts unix_seconds/price arrays are missing or have different lengths")
    existing = store.all_spots()
    fetched_at = datetime.now(T.TZ).isoformat()
    rows: list[tuple] = []
    changed_days: set[date] = set()
    previous_stamp: int | None = None
    for raw_stamp, raw_value in zip(stamps, values):
        stamp = int(raw_stamp)
        if previous_stamp is not None and stamp - previous_stamp != SLOT_SECONDS:
            raise ValueError("Energy Charts timestamps are not a continuous 15-minute UTC series")
        previous_stamp = stamp
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError("Energy Charts contains a non-finite price")
        start = datetime.fromtimestamp(stamp, tz=timezone.utc)
        end = start + timedelta(minutes=15)
        price = value / 1000.0
        key = utc_iso(start)
        rows.append((key, utc_iso(end), price, fetched_at))
        old = existing.get(key)
        if old is None or abs(float(old) - price) > 1e-12:
            changed_days.add(start.astimezone(T.TZ).date())
    store.bulk_upsert_spots(rows)
    changed_slots = sum(
        1
        for start, _end, price, _fetched in rows
        if start not in existing or abs(float(existing[start]) - float(price)) > 1e-12
    )
    for changed_day in sorted(changed_days):
        reprice_day(store, cfg, changed_day, cascade=False, rebuild=False)
    store.set_meta_many(
        {
            "spot_unit_repair_version": version,
            "aggregate_schema_version": None,
        }
    )
    return {"changed_slots": changed_slots, "changed_days": len(changed_days)}


def _asdict(row) -> dict | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {k: row[k] for k in row.keys()}


def snapshot(store: Store, now: datetime) -> dict:
    today = now.astimezone(T.TZ).date()
    yesterday = today - timedelta(days=1)
    rows = store.snapshot_rows(today, yesterday, None, today.year)
    selected = rows["selected_month"]
    y, m = [int(x) for x in selected.split("-")]
    monday = date.fromisoformat(str(rows["week_start"]))
    week_days = rows.get("week_days") or []
    week = {
        "week_start": monday.isoformat(),
        "week_end": today.isoformat(),
        "week_sunday": (monday + timedelta(days=6)).isoformat(),
        "grid_import_kwh": round(_sum(week_days, "grid_import_kwh"), 4),
        "tesla_kwh": round(_sum(week_days, "tesla_kwh"), 4),
        **{
            f"tesla_cost_{tid}": _round_or_none(_sum_if_complete(week_days, f"tesla_cost_{tid}"))
            for tid in COST_KEYS
        },
        "days_with_data": len(week_days),
        "quality": (
            f"Mo–So {monday.isoformat()}–{(monday + timedelta(days=6)).isoformat()}"
            f" · bis {today.isoformat()}"
        ),
    }
    return {
        "today": _asdict(rows["today"]),
        "yesterday": _asdict(rows["yesterday"]),
        "week": week,
        "month": _asdict(rows["month"]),
        "year": _asdict(rows["year"]),
        "latest_interval": _asdict(rows["latest_interval"]),
        "selected_month": selected,
        "selected_label": f"{m:02d}/{y}",
        "tesla_count_started_utc": rows.get("tesla_count_started_utc"),
        "tesla_pending_kwh": parse_float(rows.get("tesla_pending_kwh")),
    }


def cheapest_working_price_ids(prices: dict) -> list[str]:
    """Return tariff ids that share the lowest finite Arbeitspreis. Empty if none numeric."""
    numeric: list[tuple[str, float]] = []
    for tid in COST_KEYS:
        value = prices.get(tid)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        numeric.append((tid, number))
    if not numeric:
        return []
    lowest = min(item[1] for item in numeric)
    return [tid for tid, number in numeric if abs(number - lowest) < 0.005]


def current_prices(cfg: dict, now: datetime, spot: float | None, next_spot: float | None) -> dict:
    local = now.astimezone(T.TZ)
    out = {}
    for tid in COST_KEYS:
        price = T.energy_price_gross_eur_per_kwh(cfg, tid, local, spot)
        out[tid] = None if price is None else round(price * 100.0, 2)
    start, end = T.interval_floor(local)
    next_price = (
        None
        if next_spot is None
        else T.energy_price_gross_eur_per_kwh(cfg, "dynamic", end, next_spot)
    )
    out["dynamic_next"] = None if next_price is None else round(next_price * 100.0, 2)
    out["spot_eur_kwh"] = spot
    out["spot_ct"] = None if spot is None else round(spot * 100.0, 3)
    out["spot_next_ct"] = None if next_spot is None else round(next_spot * 100.0, 3)
    out["slot_end"] = end.isoformat()
    cheapest_ids = cheapest_working_price_ids(out)
    out["cheapest_current_ids"] = cheapest_ids
    out["cheapest_current_id"] = cheapest_ids[0] if len(cheapest_ids) == 1 else None
    out["cheapest_current_value"] = None if not cheapest_ids else out[cheapest_ids[0]]
    out["current_prices_complete"] = all(out.get(tid) is not None for tid in COST_KEYS)
    out["heat_slot"] = T.heat_slot(cfg, local, "octopus_heat")
    slot3 = T.modul3_slot(cfg, local)
    out["modul3_slot"] = slot3
    labels = {
        "nt": "Niedriglast 00–07 Uhr",
        "st": "Standardlast 07–15 und 20–24 Uhr",
        "ht": "Hochlast 15–20 Uhr",
    }
    out["modul3_label"] = labels.get(slot3, slot3)
    grid_flat = cfg["westnetz_2026"]["working_net_ct_per_kwh"]
    grid_m3 = cfg["westnetz_2026"]["modul3"][slot3]["net_ct_per_kwh"]
    out["grid_flat_net_ct"] = grid_flat
    out["grid_modul3_net_ct"] = grid_m3
    out["grid_same_now"] = abs(grid_flat - grid_m3) < 1e-9
    if out.get("dynamic") is not None and out.get("dynamic_modul3") is not None:
        out["modul3_difference_ct"] = round(
            float(out["dynamic_modul3"]) - float(out["dynamic"]), 2
        )
    if slot3 == "st":
        out["modul3_hint"] = (
            "Gerade Standardlast: Netzentgelt 9,53 ct wie ohne Modul 3. "
            "Ab 15:00 Hochlast 15,65 ct netto, nachts 00–07 Niedriglast 0,95 ct."
        )
    elif slot3 == "ht":
        out["modul3_hint"] = (
            "Gerade Hochlast (15–20 Uhr): Netzentgelt 15,65 ct netto statt 9,53 ct. "
            "Ab 20:00 wieder Standardlast."
        )
    else:
        out["modul3_hint"] = (
            "Gerade Niedriglast (00–07 Uhr): Netzentgelt 0,95 ct netto statt 9,53 ct. "
            "Ab 07:00 Standardlast."
        )
    if spot is not None:
        ht_at = local.replace(hour=15, minute=30, second=0, microsecond=0)
        nt_at = local.replace(hour=3, minute=0, second=0, microsecond=0)
        p_ht = T.energy_price_gross_eur_per_kwh(cfg, "dynamic_modul3", ht_at, spot)
        p_nt = T.energy_price_gross_eur_per_kwh(cfg, "dynamic_modul3", nt_at, spot)
        p_dyn = T.energy_price_gross_eur_per_kwh(cfg, "dynamic", ht_at, spot)
        out["modul3_ht_ct"] = None if p_ht is None else round(p_ht * 100.0, 2)
        out["modul3_nt_ct"] = None if p_nt is None else round(p_nt * 100.0, 2)
        out["dynamic_same_spot_ct"] = None if p_dyn is None else round(p_dyn * 100.0, 2)
    return out
