"""Pure tariff math. No Home Assistant imports."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

TZ = ZoneInfo("Europe/Berlin")

REQUIRED_TARIFF_IDS = frozenset(
    {
        "octopus_heat",
        "octopus_heat_loyalty",
        "fix_tarif",
        "dynamic",
        "dynamic_modul3",
        "dynamic_perfect",
    }
)
REQUIRED_ENTITY_KEYS = frozenset(
    {
        "grid_import",
        "grid_export",
        "nordpool_current",
        "nordpool_next",
        "nordpool_low",
        "nordpool_high",
        "nordpool_tomorrow_ready",
        "octopus_price",
    }
)
SUPPORTED_TARIFF_TYPES = frozenset({"tou_all_in", "fix_all_in", "dynamic_retail", "dynamic_perfect"})


def _require_keys(mapping: dict, keys: set[str] | frozenset[str], label: str) -> None:
    missing = sorted(set(keys) - set(mapping))
    if missing:
        raise ValueError(f"{label} missing required keys: {', '.join(missing)}")


def _config_date(value, label: str, *, allow_none: bool) -> date | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{label} must be an ISO date")
    if isinstance(value, datetime):
        raise ValueError(f"{label} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as err:
        raise ValueError(f"{label} must be an ISO date") from err


def validate_config(cfg: dict) -> dict:
    """Validate the tariff contract needed by the pure calculation functions."""
    if not isinstance(cfg, dict):
        raise ValueError("tariff config must be a mapping")
    _require_keys(
        cfg,
        {
            "timezone",
            "entities",
            "nordpool",
            "levies_2026_net_ct_per_kwh",
            "vat_rate",
            "dynamic_supplier",
            "westnetz_2026",
            "metering",
            "tariffs",
        },
        "config",
    )
    if cfg["timezone"] != TZ.key:
        raise ValueError(f"timezone must be {TZ.key}")
    if not isinstance(cfg["entities"], dict):
        raise ValueError("entities must be a mapping")
    _require_keys(cfg["entities"], REQUIRED_ENTITY_KEYS, "entities")
    _require_keys(cfg["nordpool"], {"domain", "area", "currency"}, "nordpool")
    _require_keys(
        cfg["levies_2026_net_ct_per_kwh"],
        {"stromsteuer", "kwkg", "stromnev_19", "offshore", "konzessionsabgabe"},
        "levies_2026_net_ct_per_kwh",
    )
    _require_keys(cfg["dynamic_supplier"], {"monthly_fee_eur", "markup_net_ct_per_kwh"}, "dynamic_supplier")
    _require_keys(
        cfg["westnetz_2026"],
        {"standing_gross_eur_per_year", "working_net_ct_per_kwh", "modul3", "paragraph_14a_modul1"},
        "westnetz_2026",
    )
    _require_keys(cfg["westnetz_2026"]["modul3"], {"nt", "st", "ht"}, "westnetz_2026.modul3")
    for slot in ("nt", "st", "ht"):
        _require_keys(
            cfg["westnetz_2026"]["modul3"][slot],
            {"net_ct_per_kwh", "windows"},
            f"westnetz_2026.modul3.{slot}",
        )
    _require_keys(
        cfg["westnetz_2026"]["paragraph_14a_modul1"],
        {"standing_reduction_gross_eur_per_year"},
        "westnetz_2026.paragraph_14a_modul1",
    )
    _require_keys(cfg["metering"], {"yearly_eur"}, "metering")

    tariffs = cfg["tariffs"]
    if not isinstance(tariffs, list) or not tariffs:
        raise ValueError("tariffs must be a non-empty list")
    ids: list[str] = []
    references = 0
    for index, tariff in enumerate(tariffs):
        label = f"tariffs[{index}]"
        if not isinstance(tariff, dict):
            raise ValueError(f"{label} must be a mapping")
        _require_keys(
            tariff,
            {"id", "name", "type", "hypothetical", "reference", "valid_from", "valid_to"},
            label,
        )
        tariff_id = str(tariff["id"])
        ids.append(tariff_id)
        kind = tariff["type"]
        if kind not in SUPPORTED_TARIFF_TYPES:
            raise ValueError(f"{label}.type unsupported: {kind}")
        valid_from = _config_date(tariff["valid_from"], f"{label}.valid_from", allow_none=False)
        valid_to = _config_date(tariff["valid_to"], f"{label}.valid_to", allow_none=True)
        if valid_to is not None and valid_to <= valid_from:
            raise ValueError(f"{label}.valid_to must be later than valid_from (exclusive end)")
        scenario_replay = tariff.get("scenario_replay", False)
        if not isinstance(scenario_replay, bool):
            raise ValueError(f"{label}.scenario_replay must be true or false")
        if kind in ("dynamic_retail", "dynamic_perfect") and valid_to is None:
            raise ValueError(f"{label}.valid_to is required for year-specific dynamic prices")
        if (
            tariff_id in {"dynamic", "dynamic_modul3", "dynamic_perfect"}
            and valid_to is not None
            and valid_to > date(2027, 1, 1)
        ):
            raise ValueError(f"{label}.valid_to must not exceed 2027-01-01 for 2026 price components")
        if kind == "tou_all_in":
            _require_keys(tariff, {"prices_gross_ct_per_kwh", "windows"}, label)
            if "standing_eur_per_day" not in tariff and "standing_eur_per_month" not in tariff:
                raise ValueError(f"{label} needs standing_eur_per_day or standing_eur_per_month")
            _require_keys(tariff["prices_gross_ct_per_kwh"], set(tariff["windows"]), f"{label}.prices")
        elif kind == "fix_all_in":
            _require_keys(tariff, {"price_gross_ct_per_kwh"}, label)
            if "standing_eur_per_day" not in tariff and "standing_eur_per_month" not in tariff:
                raise ValueError(f"{label} needs standing_eur_per_day or standing_eur_per_month")
        else:
            _require_keys(tariff, {"uses_modul3", "uses_paragraph_14a"}, label)
        references += int(bool(tariff["reference"]))

    duplicates = sorted({tariff_id for tariff_id in ids if ids.count(tariff_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate tariff ids: {', '.join(duplicates)}")
    missing_ids = sorted(REQUIRED_TARIFF_IDS - set(ids))
    if missing_ids:
        raise ValueError(f"missing required tariff ids: {', '.join(missing_ids)}")
    if references != 1 or not tariff_by_id(cfg, "octopus_heat").get("reference"):
        raise ValueError("exactly octopus_heat must be the single reference tariff")
    return cfg


def load_config(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as fh:
        return validate_config(yaml.safe_load(fh))


def parse_hhmm(value: str) -> time:
    if value in ("24:00", "24:00:00"):
        return time(0, 0)
    parts = str(value).split(":")
    return time(int(parts[0]), int(parts[1]))


def in_range(moment: time, start: time, end: time) -> bool:
    if start == end:
        return start == time(0, 0)
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def slot_from_windows(moment: time, windows: dict) -> str | None:
    for name, ranges in windows.items():
        for item in ranges:
            if in_range(moment, parse_hhmm(item["from"]), parse_hhmm(item["to"])):
                return name
    return None


def heat_slot(cfg: dict, local_dt: datetime, tariff_id: str) -> str | None:
    tariff = tariff_by_id(cfg, tariff_id)
    local = _local_datetime(local_dt)
    if not tariff_is_valid(tariff, local):
        return None
    return slot_from_windows(local.time(), tariff["windows"])


def tariff_by_id(cfg: dict, tariff_id: str) -> dict:
    for item in cfg["tariffs"]:
        if item["id"] == tariff_id:
            return item
    raise KeyError(tariff_id)


def _local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def tariff_is_valid(tariff: dict, when: date | datetime) -> bool:
    """Return validity for real use; scenario replay only relaxes the lower bound."""
    if isinstance(when, datetime):
        day = _local_datetime(when).date()
    else:
        day = when
    valid_from = _config_date(tariff.get("valid_from"), f"{tariff.get('id', 'tariff')}.valid_from", allow_none=False)
    valid_to = _config_date(tariff.get("valid_to"), f"{tariff.get('id', 'tariff')}.valid_to", allow_none=True)
    if valid_to is not None and day >= valid_to:
        return False
    if day < valid_from:
        return bool(tariff.get("scenario_replay", False))
    return True


def modul3_slot(cfg: dict, local_dt: datetime) -> str:
    windows = {
        name: spec["windows"]
        for name, spec in cfg["westnetz_2026"]["modul3"].items()
    }
    slot = slot_from_windows(local_dt.time(), windows)
    if slot is None:
        raise ValueError(f"no modul3 window for {local_dt}")
    return slot


def _ct_to_eur(ct: float) -> float:
    return ct / 100.0


def levies_net_eur_per_kwh(cfg: dict) -> float:
    lev = cfg["levies_2026_net_ct_per_kwh"]
    return _ct_to_eur(
        lev["stromsteuer"]
        + lev["kwkg"]
        + lev["stromnev_19"]
        + lev["offshore"]
        + lev["konzessionsabgabe"]
    )


def dynamic_gross_eur_per_kwh(cfg: dict, local_dt: datetime, spot_eur_kwh: float, *, modul3: bool) -> float:
    markup = _ct_to_eur(cfg["dynamic_supplier"]["markup_net_ct_per_kwh"])
    if modul3:
        slot = modul3_slot(cfg, local_dt)
        grid = _ct_to_eur(cfg["westnetz_2026"]["modul3"][slot]["net_ct_per_kwh"])
    else:
        grid = _ct_to_eur(cfg["westnetz_2026"]["working_net_ct_per_kwh"])
    net = spot_eur_kwh + markup + grid + levies_net_eur_per_kwh(cfg)
    return net * (1.0 + float(cfg["vat_rate"]))


def energy_price_gross_eur_per_kwh(
    cfg: dict,
    tariff_id: str,
    local_dt: datetime,
    spot_eur_kwh: float | None,
) -> float | None:
    tariff = tariff_by_id(cfg, tariff_id)
    local = _local_datetime(local_dt)
    if not tariff_is_valid(tariff, local):
        return None
    kind = tariff["type"]
    if kind == "tou_all_in":
        slot = slot_from_windows(local.time(), tariff["windows"])
        if slot is None:
            return None
        return _ct_to_eur(tariff["prices_gross_ct_per_kwh"][slot])
    if kind == "fix_all_in":
        return _ct_to_eur(tariff["price_gross_ct_per_kwh"])
    if kind in ("dynamic_retail", "dynamic_perfect"):
        if spot_eur_kwh is None:
            return None
        return dynamic_gross_eur_per_kwh(
            cfg, local, spot_eur_kwh, modul3=bool(tariff.get("uses_modul3"))
        )
    raise ValueError(kind)


def days_in_month(day: date) -> int:
    if day.month == 12:
        nxt = date(day.year + 1, 1, 1)
    else:
        nxt = date(day.year, day.month + 1, 1)
    return (nxt - date(day.year, day.month, 1)).days


def days_in_year(day: date) -> int:
    return 366 if (day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0)) else 365


def expected_intervals(day: date) -> int:
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    end = start + timedelta(days=1)
    return int((end.timestamp() - start.timestamp()) // 900)


def base_eur_for_day(cfg: dict, tariff_id: str, day: date) -> float | None:
    """Supplier/all-in base component, excluding separate network and metering items."""
    tariff = tariff_by_id(cfg, tariff_id)
    if not tariff_is_valid(tariff, day):
        return None
    kind = tariff["type"]
    if kind in ("tou_all_in", "fix_all_in"):
        if tariff.get("standing_eur_per_day") is not None:
            return float(tariff["standing_eur_per_day"])
        return float(tariff["standing_eur_per_month"]) / days_in_month(day)
    return float(cfg["dynamic_supplier"]["monthly_fee_eur"]) / days_in_month(day)


def standing_components_eur_for_day(cfg: dict, tariff_id: str, day: date) -> dict[str, float] | None:
    """Return independently inspectable daily base, network, reduction and metering."""
    tariff = tariff_by_id(cfg, tariff_id)
    base = base_eur_for_day(cfg, tariff_id, day)
    if base is None:
        return None
    if tariff["type"] in ("tou_all_in", "fix_all_in"):
        network = reduction = metering = 0.0
    else:
        year_days = days_in_year(day)
        network = float(cfg["westnetz_2026"]["standing_gross_eur_per_year"]) / year_days
        reduction = 0.0
        if tariff.get("uses_paragraph_14a"):
            reduction = (
                float(
                    cfg["westnetz_2026"]["paragraph_14a_modul1"][
                        "standing_reduction_gross_eur_per_year"
                    ]
                )
                / year_days
            )
        metering = float(cfg["metering"]["yearly_eur"]) / year_days
    return {
        "base": base,
        "network": network,
        "paragraph_14a_reduction": reduction,
        "metering": metering,
        "total": base + network + reduction + metering,
    }


def standing_eur_for_day(cfg: dict, tariff_id: str, day: date) -> float | None:
    """Grund/Fix without §14a. Dynamisch and Modul 3 share supplier + network standing."""
    return standing_fixed_eur_for_period(cfg, tariff_id, day, fraction=1.0)


def standing_fixed_eur_for_period(
    cfg: dict, tariff_id: str, day: date, *, fraction: float
) -> float | None:
    """Time-prorated Grund/Fix: supplier, network standing, metering. No §14a."""
    if fraction < 0.0 or fraction > 1.0:
        raise ValueError("fraction must be between 0 and 1")
    components = standing_components_eur_for_day(cfg, tariff_id, day)
    if components is None:
        return None
    return (
        components["base"] * fraction
        + components["network"] * fraction
        + components["metering"] * fraction
    )


def paragraph_14a_eur_for_period(
    cfg: dict,
    tariff_id: str,
    day: date,
    *,
    fraction: float,
    variable_network_gross_eur: float = 0.0,
) -> float | None:
    """Time-prorated §14a credit. May not push the network block below 0."""
    if fraction < 0.0 or fraction > 1.0:
        raise ValueError("fraction must be between 0 and 1")
    components = standing_components_eur_for_day(cfg, tariff_id, day)
    if components is None:
        return None
    reduction = components["paragraph_14a_reduction"] * fraction
    if reduction < 0.0:
        network = components["network"] * fraction
        network_before_reduction = network + max(0.0, float(variable_network_gross_eur))
        reduction = max(reduction, -network_before_reduction)
    return reduction


def capped_standing_eur_for_period(
    cfg: dict,
    tariff_id: str,
    day: date,
    *,
    fraction: float,
    variable_network_gross_eur: float = 0.0,
) -> float | None:
    """Gesamt-Fixanteil: Grund/Fix plus capped §14a."""
    fixed = standing_fixed_eur_for_period(cfg, tariff_id, day, fraction=fraction)
    reduction = paragraph_14a_eur_for_period(
        cfg,
        tariff_id,
        day,
        fraction=fraction,
        variable_network_gross_eur=variable_network_gross_eur,
    )
    if fixed is None or reduction is None:
        return None
    return fixed + reduction


def standing_eur_for_month(
    cfg: dict, tariff_id: str, year: int, month: int, *, days_used: int | None = None
) -> float | None:
    day = date(year, month, 1)
    dim = days_in_month(day)
    used = dim if days_used is None else days_used
    if used < 0 or used > dim:
        raise ValueError("days_used outside month")
    total = 0.0
    for offset in range(used):
        daily = standing_eur_for_day(cfg, tariff_id, day + timedelta(days=offset))
        if daily is None:
            return None
        total += daily
    return total


def standing_eur_for_year_to_date(cfg: dict, tariff_id: str, day: date) -> float | None:
    total = 0.0
    cursor = date(day.year, 1, 1)
    while cursor <= day:
        daily = standing_eur_for_day(cfg, tariff_id, cursor)
        if daily is None:
            return None
        total += daily
        cursor += timedelta(days=1)
    return total


def perfect_energy_cost(kwh_blocks: list[float], prices: list[float]) -> float | None:
    if not kwh_blocks or not prices or len(kwh_blocks) != len(prices):
        return None
    k_sorted = sorted(kwh_blocks, reverse=True)
    p_sorted = sorted(prices)
    return sum(k * p for k, p in zip(k_sorted, p_sorted))


def interval_floor(now: datetime) -> tuple[datetime, datetime]:
    """Current (possibly still open) 15-minute slot in Europe/Berlin, for live prices."""
    utc = now.astimezone(timezone.utc)
    minute = (utc.minute // 15) * 15
    start_utc = utc.replace(minute=minute, second=0, microsecond=0)
    end_utc = start_utc + timedelta(minutes=15)
    return start_utc.astimezone(TZ), end_utc.astimezone(TZ)


def closed_interval_utc(now: datetime) -> tuple[datetime, datetime]:
    """Last fully closed 15-minute slot on the UTC timeline.

    Sample at 14:15:00 or 14:15:20 → [14:00, 14:15). Never the slot that just started.
    """
    utc = now.astimezone(timezone.utc)
    minute = (utc.minute // 15) * 15
    end = utc.replace(minute=minute, second=0, microsecond=0)
    start = end - timedelta(minutes=15)
    return start, end


def nordpool_service_price_to_eur_kwh(value) -> float | None:
    """Convert nordpool.get_prices_for_date prices. That action is always EUR/MWh."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price):
        return None
    return price / 1000.0
