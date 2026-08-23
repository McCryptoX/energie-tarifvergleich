#!/usr/bin/env python3
"""Import Inexogy 15-minute CSV and Energy-Charts prices safely.

The live SQLite database is only replaced from the local Home Assistant
``/config`` filesystem while Home Assistant is stopped. SQLite databases are
copied with SQLite's Backup API, never as raw files.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import importlib.util
import json
import math
import os
import sqlite3
import stat
import sys
import tempfile
import types
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
SPOT_TIMEZONES = {
    "CET": timezone(timedelta(hours=1), "CET"),
    "CEST": timezone(timedelta(hours=2), "CEST"),
}

ROOT = Path("/config") if Path("/config/energy_tariff_compare").exists() else Path("/Volumes/config")
PKG = ROOT / "custom_components" / "energy_tariff_compare"
CSV_CANDIDATES = [
    ROOT / "energy_tariff_compare" / "imports",
]


class ImportSafetyError(RuntimeError):
    """Raised when a safe live-database replacement cannot be guaranteed."""


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"energy_tariff_compare.{name}", PKG / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Modul energy_tariff_compare.{name} konnte nicht geladen werden")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"energy_tariff_compare.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def _select_csv(
    *,
    label: str,
    predicate: Callable[[Path], bool],
    explicit: Path | None = None,
    candidates: Sequence[Path] | None = None,
    optional: bool = False,
) -> Path | None:
    """Return exactly one CSV or reject an ambiguous selection."""
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{label} nicht gefunden: {path}")
        return path

    matches: dict[str, Path] = {}
    for folder in candidates if candidates is not None else CSV_CANDIDATES:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.csv")):
            if predicate(path):
                resolved = path.resolve()
                matches[str(resolved)] = resolved

    paths = sorted(matches.values(), key=lambda item: str(item))
    if not paths:
        if optional:
            return None
        raise FileNotFoundError(f"{label} nicht gefunden")
    if len(paths) > 1:
        choices = "\n  - ".join(str(path) for path in paths)
        raise ImportSafetyError(
            f"Mehrdeutige Auswahl für {label}; expliziten Pfad angeben:\n  - {choices}"
        )
    return paths[0]


def find_csv(
    explicit: Path | None = None, candidates: Sequence[Path] | None = None
) -> Path:
    path = _select_csv(
        label="Inexogy-CSV",
        predicate=lambda item: "1emh" in item.name.lower()
        or "inexogy" in item.name.lower(),
        explicit=explicit,
        candidates=candidates,
    )
    assert path is not None
    return path


def find_spot_csv(
    explicit: Path | None = None, candidates: Sequence[Path] | None = None
) -> Path | None:
    return _select_csv(
        label="Spotmarkt-CSV",
        predicate=lambda item: "spotmarkt" in item.name.lower()
        or "spot" in item.name.lower(),
        explicit=explicit,
        candidates=candidates,
        optional=True,
    )


def _parse_spot_datetime(
    date_text: str,
    clock_text: str,
    zone_text: str,
    *,
    row_number: int,
    field: str,
    day_offset: int = 0,
) -> datetime:
    zone_name = zone_text.strip().upper()
    zone = SPOT_TIMEZONES.get(zone_name)
    if zone is None:
        raise ValueError(
            f"Spotmarkt-CSV Zeile {row_number}: unbekannte {field}-Zeitzone "
            f"{zone_text!r}; erwartet CET oder CEST"
        )
    try:
        naive = datetime.strptime(
            f"{date_text.strip()} {clock_text.strip()}", "%d.%m.%Y %H:%M"
        ) + timedelta(days=day_offset)
    except ValueError as exc:
        raise ValueError(
            f"Spotmarkt-CSV Zeile {row_number}: ungültige {field}-Zeit"
        ) from exc
    utc_value = naive.replace(tzinfo=zone).astimezone(UTC)
    berlin_value = utc_value.astimezone(TZ)
    if (
        berlin_value.replace(tzinfo=None) != naive
        or berlin_value.tzname() != zone_name
    ):
        raise ValueError(
            f"Spotmarkt-CSV Zeile {row_number}: {field}-Zeit "
            f"{naive:%d.%m.%Y %H:%M} {zone_name} existiert in Europe/Berlin nicht"
        )
    return utc_value


def load_spot_csv(path: Path) -> dict[int, float]:
    """Load spot prices using the CSV's explicit CET/CEST columns.

    Fixed CET/CEST offsets make both occurrences of the repeated autumn hour
    unambiguous. Unknown zones, duplicate UTC slots, and non-15-minute rows are
    rejected instead of silently assigning a DST fold.
    """
    out: dict[int, float] = {}
    source_rows: dict[int, int] = {}
    required = {
        "Datum",
        "von",
        "Zeitzone von",
        "bis",
        "Zeitzone bis",
        "Spotmarktpreis in ct/kWh",
    }
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fieldnames = reader.fieldnames or []
        duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"Spotmarkt-CSV hat doppelte Spalten: {', '.join(duplicates)}"
            )
        missing = required.difference(fieldnames)
        if missing:
            raise ValueError(
                f"Spotmarkt-CSV fehlen Spalten: {', '.join(sorted(missing))}"
            )
        for row_number, rec in enumerate(reader, start=2):
            raw = rec["Spotmarktpreis in ct/kWh"].strip().replace(" ", "")
            if not raw:
                continue
            start_utc = _parse_spot_datetime(
                rec["Datum"],
                rec["von"],
                rec["Zeitzone von"],
                row_number=row_number,
                field="Start",
            )
            end_candidates: list[datetime] = []
            end_errors: list[str] = []
            for day_offset in (0, 1):
                try:
                    candidate = _parse_spot_datetime(
                        rec["Datum"],
                        rec["bis"],
                        rec["Zeitzone bis"],
                        row_number=row_number,
                        field="Ende",
                        day_offset=day_offset,
                    )
                except ValueError as exc:
                    end_errors.append(str(exc))
                    continue
                if candidate - start_utc == timedelta(minutes=15):
                    end_candidates.append(candidate)
            if len(end_candidates) != 1:
                details = f"; {'; '.join(end_errors)}" if end_errors else ""
                raise ValueError(
                    f"Spotmarkt-CSV Zeile {row_number}: Intervall ist nicht 15 Minuten "
                    f"oder kalendarisch mehrdeutig ({start_utc.isoformat()}){details}"
                )
            end_utc = end_candidates[0]
            timestamp = int(start_utc.timestamp())
            if timestamp in out:
                raise ValueError(
                    f"Spotmarkt-CSV Zeile {row_number}: UTC-Slot doppelt; "
                    f"bereits in Zeile {source_rows[timestamp]}"
                )
            try:
                price_ct = float(raw.replace(",", "."))
            except ValueError as exc:
                raise ValueError(
                    f"Spotmarkt-CSV Zeile {row_number}: ungültiger Preis {raw!r}"
                ) from exc
            if not math.isfinite(price_ct):
                raise ValueError(
                    f"Spotmarkt-CSV Zeile {row_number}: Preis ist nicht endlich"
                )
            out[timestamp] = price_ct / 100.0
            source_rows[timestamp] = row_number
    print("spot csv", path.name, "slots", len(out))
    return out


def _energy_charts_spots(data: dict, *, source: str) -> dict[int, float]:
    timestamps = data.get("unix_seconds")
    prices = data.get("price")
    if not isinstance(timestamps, list) or not isinstance(prices, list):
        raise ValueError(f"Energy-Charts-Daten aus {source} enthalten keine Preislisten")
    if len(timestamps) != len(prices):
        raise ValueError(
            f"Energy-Charts-Daten aus {source}: Zeit- und Preislisten sind unterschiedlich lang"
        )
    unit = str(data.get("unit", ""))
    normalized_unit = unit.replace(" ", "").replace("€", "EUR").upper()
    if normalized_unit != "EUR/MWH":
        raise ValueError(
            f"Energy-Charts-Daten aus {source}: unerwartete Einheit {unit!r}; "
            "erwartet EUR/MWh"
        )

    out: dict[int, float] = {}
    for timestamp, price in zip(timestamps, prices, strict=True):
        if price is None:
            continue
        try:
            numeric_timestamp = float(timestamp)
            numeric_price = float(price)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Energy-Charts-Daten aus {source}: ungültiger Zeit- oder Preiswert"
            ) from exc
        if (
            not math.isfinite(numeric_timestamp)
            or not numeric_timestamp.is_integer()
            or int(numeric_timestamp) % 900 != 0
        ):
            raise ValueError(
                f"Energy-Charts-Daten aus {source}: Zeitstempel {timestamp!r} "
                "liegt nicht im UTC-15-Minuten-Raster"
            )
        if not math.isfinite(numeric_price):
            raise ValueError(f"Energy-Charts-Daten aus {source}: Preis ist nicht endlich")
        key = int(numeric_timestamp)
        if key in out:
            raise ValueError(f"Energy-Charts-Daten aus {source}: UTC-Slot {key} doppelt")
        # Energy-Charts reports EUR/MWh; the database stores EUR/kWh.
        out[key] = numeric_price / 1000.0
    return out


def _write_cache_atomically(cache: Path, payload: bytes) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{cache.name}.",
            suffix=".tmp",
            dir=cache.parent,
            delete=False,
        ) as fh:
            temp_path = Path(fh.name)
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, cache)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def fetch_spots(start: str, end: str, *, root: Path = ROOT) -> dict[int, float]:
    url = f"https://api.energy-charts.info/price?bzn=DE-LU&start={start}&end={end}"
    cache = root / "energy_tariff_compare" / "imports" / f"energy_charts_DE-LU_{start}_{end}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        out = _energy_charts_spots(data, source=str(cache))
    else:
        print("fetch", url)
        # urllib's default HTTPS context verifies the server certificate. There
        # is deliberately no insecure fallback.
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
        data = json.loads(payload.decode("utf-8"))
        out = _energy_charts_spots(data, source=url)
        _write_cache_atomically(cache, payload)
    print("spot slots", len(out), data.get("unit"))
    return out


def _sqlite_read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def sqlite_integrity_check(path: Path) -> None:
    """Raise if SQLite's full integrity check does not return exactly ``ok``."""
    if not path.is_file():
        raise FileNotFoundError(f"SQLite-Datenbank nicht gefunden: {path}")
    conn = sqlite3.connect(_sqlite_read_only_uri(path), uri=True, timeout=5)
    try:
        result = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    finally:
        conn.close()
    if result != ["ok"]:
        details = "; ".join(result) if result else "keine Antwort"
        raise ImportSafetyError(f"SQLite-Integritätsprüfung fehlgeschlagen: {details}")


def sqlite_backup(source: Path, destination: Path) -> None:
    """Create a consistent SQLite backup, including committed WAL contents."""
    if destination.exists():
        raise FileExistsError(f"Backup-Ziel existiert bereits: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(_sqlite_read_only_uri(source), uri=True, timeout=5)
    destination_conn: sqlite3.Connection | None = None
    try:
        destination_conn = sqlite3.connect(str(destination), timeout=5)
        source_conn.backup(destination_conn)
        destination_conn.commit()
    except Exception:
        if destination_conn is not None:
            destination_conn.close()
            destination_conn = None
        if destination.exists():
            destination.unlink()
        raise
    finally:
        if destination_conn is not None:
            destination_conn.close()
        source_conn.close()
    sqlite_integrity_check(destination)


def require_local_config(root: Path) -> None:
    if root.resolve() != Path("/config").resolve():
        raise ImportSafetyError(
            "Live-Import über SMB/Netzlaufwerk verweigert. Das Skript muss auf dem "
            "Home-Assistant-System mit lokalem /config ausgeführt werden."
        )


@contextmanager
def home_assistant_execution_lock(root: Path) -> Iterator[None]:
    """Hold the same flock that Home Assistant uses for its whole runtime.

    Home Assistant intentionally keeps ``.ha_run.lock`` on disk after exit, so
    existence is not a running-state signal. Holding its non-blocking exclusive
    flock both proves HA is stopped and prevents it from starting mid-import.
    """
    lock_path = root / ".ha_run.lock"
    lock_file = lock_path.open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise ImportSafetyError(
                "Home Assistant hält .ha_run.lock; vollständig stoppen und Import "
                "erneut starten"
            ) from exc
        yield
    finally:
        if acquired:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def ensure_no_active_writer(database: Path) -> None:
    """Prove that no SQLite writer currently holds the database."""
    if not database.exists():
        return

    try:
        conn = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=rw",
            uri=True,
            timeout=0,
            isolation_level=None,
        )
    except sqlite3.OperationalError as exc:
        raise ImportSafetyError(
            f"SQLite-Datenbank konnte nicht exklusiv geprüft werden: {database}"
        ) from exc
    try:
        conn.execute("PRAGMA busy_timeout=0")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise ImportSafetyError(
            "SQLite-Datenbank ist durch einen Schreiber belegt; Live-Austausch abgebrochen"
        ) from exc
    finally:
        conn.close()


def _finalize_sqlite_file(path: Path) -> None:
    """Checkpoint WAL, switch to a self-contained file, and verify integrity."""
    conn = sqlite3.connect(str(path), timeout=0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=0")
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and int(checkpoint[0]) != 0:
            raise ImportSafetyError(f"WAL-Checkpoint ist belegt: {path}")
        journal_mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
        if journal_mode != "delete":
            raise ImportSafetyError(
                f"SQLite-Journal konnte nicht sicher auf DELETE gesetzt werden: {path}"
            )
    except sqlite3.OperationalError as exc:
        raise ImportSafetyError(f"SQLite-Finalisierung fehlgeschlagen: {path}") from exc
    finally:
        conn.close()

    sidecars = [Path(f"{path}-wal"), Path(f"{path}-shm")]
    leftovers = [sidecar for sidecar in sidecars if sidecar.exists()]
    if leftovers:
        raise ImportSafetyError(
            "SQLite-WAL/SHM blieb nach dem Checkpoint bestehen; Austausch abgebrochen: "
            + ", ".join(str(item) for item in leftovers)
        )
    sqlite_integrity_check(path)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_safety_backup(database: Path, backups_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backups_dir / f"energy.sqlite.pre-import-{stamp}.bak"
    sqlite_backup(database, destination)
    _fsync_file(destination)
    _fsync_directory(destination.parent)
    return destination


def atomic_replace_database(
    *, live_database: Path, work_database: Path
) -> None:
    """Replace the live DB atomically after a final writer/WAL check."""
    _finalize_sqlite_file(work_database)
    previous_stat = live_database.stat() if live_database.exists() else None

    ensure_no_active_writer(live_database)
    if live_database.exists():
        # SQLite itself removes the source WAL/SHM by checkpointing and leaving
        # WAL mode for DELETE mode. We never raw-copy or manually discard WAL.
        _finalize_sqlite_file(live_database)
    ensure_no_active_writer(live_database)

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{live_database}{suffix}")
        if sidecar.exists():
            raise ImportSafetyError(
                f"SQLite-Sidecar {sidecar} ist noch vorhanden; Austausch abgebrochen"
            )

    if previous_stat is not None:
        os.chmod(work_database, stat.S_IMODE(previous_stat.st_mode))
        if hasattr(os, "chown"):
            os.chown(work_database, previous_stat.st_uid, previous_stat.st_gid)
    _fsync_file(work_database)
    os.replace(work_database, live_database)
    _fsync_directory(live_database.parent)
    sqlite_integrity_check(live_database)


def load_inexogy_records(path: Path) -> list[tuple[datetime, float, float, float, float]]:
    required = {
        "Zeit (UTC)",
        "Zählerstand Bezug (Wh)",
        "Zählerstand Einspeisung (Wh)",
        "Leistung Bezug (W)",
        "Leistung Einspeisung (W)",
    }
    records: list[tuple[datetime, float, float, float, float]] = []
    source_rows: dict[datetime, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
        if duplicates:
            raise ValueError(f"Inexogy-CSV hat doppelte Spalten: {', '.join(duplicates)}")
        missing = required.difference(fieldnames)
        if missing:
            raise ValueError(f"Inexogy-CSV fehlen Spalten: {', '.join(sorted(missing))}")
        for row_number, rec in enumerate(reader, start=2):
            try:
                end_utc = datetime.strptime(rec["Zeit (UTC)"], "%d.%m.%Y %H:%M").replace(
                    tzinfo=UTC
                )
                values = (
                    float(rec["Zählerstand Bezug (Wh)"]),
                    float(rec["Zählerstand Einspeisung (Wh)"]),
                    float(rec["Leistung Bezug (W)"]),
                    float(rec["Leistung Einspeisung (W)"]),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Inexogy-CSV Zeile {row_number}: ungültiger Wert") from exc
            if int(end_utc.timestamp()) % 900 != 0:
                raise ValueError(
                    f"Inexogy-CSV Zeile {row_number}: UTC-Zeit liegt nicht im "
                    "15-Minuten-Raster"
                )
            if not all(math.isfinite(value) for value in values):
                raise ValueError(
                    f"Inexogy-CSV Zeile {row_number}: Messwert ist nicht endlich"
                )
            if end_utc in source_rows:
                raise ValueError(
                    f"Inexogy-CSV Zeile {row_number}: UTC-Zeit doppelt; "
                    f"bereits in Zeile {source_rows[end_utc]}"
                )
            source_rows[end_utc] = row_number
            records.append((end_utc, *values))

    records.sort(key=lambda item: item[0])
    if not records:
        raise ValueError("Inexogy-CSV enthält keine Datenzeilen")
    for previous, current in zip(records, records[1:]):
        if current[0] - previous[0] != timedelta(minutes=15):
            raise ValueError(
                "Inexogy-CSV ist in UTC nicht lückenlos im 15-Minuten-Raster: "
                f"{previous[0].isoformat()} -> {current[0].isoformat()}"
            )
    return records


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inexogy-csv", type=Path, help="eindeutige Inexogy-CSV")
    parser.add_argument("--spot-csv", type=Path, help="optionale eindeutige Spotmarkt-CSV")
    parser.add_argument(
        "--replace-live",
        action="store_true",
        help="bestätigt den atomaren Austausch der lokalen Live-Datenbank",
    )
    return parser.parse_args(argv)


def run_import(args: argparse.Namespace) -> None:
    if not args.replace_live:
        raise ImportSafetyError(
            "Kein Live-Austausch ohne --replace-live. Vorher Home Assistant vollständig stoppen."
        )
    require_local_config(ROOT)

    # Home Assistant uses this same flock for its complete runtime. Keep it for
    # the complete snapshot/import/replace transaction to close the start race.
    with home_assistant_execution_lock(ROOT):
        _run_import_locked(args)


def _run_import_locked(args: argparse.Namespace) -> None:
    db_live = ROOT / "energy_tariff_compare" / "data" / "energy.sqlite"
    db_live.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_active_writer(db_live)

    pkg = types.ModuleType("energy_tariff_compare")
    pkg.__path__ = [str(PKG)]
    sys.modules["energy_tariff_compare"] = pkg
    tariffs = load("tariffs")
    store_m = load("store")
    coll = load("collector")

    cfg = tariffs.load_config(ROOT / "energy_tariff_compare" / "tariffs.yaml")
    csv_path = find_csv(args.inexogy_csv)
    spot_csv = find_spot_csv(args.spot_csv)
    print("csv", csv_path)

    spots = fetch_spots("2026-01-01", "2026-08-23", root=ROOT)
    if spot_csv is not None:
        user_spots = load_spot_csv(spot_csv)
        spots.update(user_spots)
        print("spots after merge", len(spots))

    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat()
    spot_rows = []
    for timestamp, price in sorted(spots.items()):
        start = datetime.fromtimestamp(timestamp, tz=UTC)
        end = start + timedelta(minutes=15)
        spot_rows.append((coll.utc_iso(start), coll.utc_iso(end), price, now_iso))

    inexogy_records = load_inexogy_records(csv_path)
    safety_backup: Path | None = None
    ensure_no_active_writer(db_live)
    with tempfile.TemporaryDirectory(prefix=".energy-import-", dir=db_live.parent) as temp_dir:
        db_work = Path(temp_dir) / "energy.sqlite"
        if db_live.exists():
            sqlite_integrity_check(db_live)
            safety_backup = create_safety_backup(
                db_live, ROOT / "energy_tariff_compare" / "backups"
            )
            print("safety backup created", safety_backup)
            # Build the work file from the already verified, immutable snapshot
            # so backup and imported baseline represent the same database state.
            sqlite_backup(safety_backup, db_work)

        store = store_m.Store(db_work)
        store.bulk_upsert_spots(spot_rows)

        prev_imp = prev_exp = None
        rows = []
        days = set()
        for end_utc, stand_imp, stand_exp, power_imp, power_exp in inexogy_records:
            start_utc = end_utc - timedelta(minutes=15)
            start_local = start_utc.astimezone(TZ)
            quality = "backfilled"
            sources = "inexogy_csv,spot"
            if prev_imp is None:
                kwh_in = max(power_imp, 0.0) * 0.25 / 1000.0
                kwh_out = max(power_exp, 0.0) * 0.25 / 1000.0
                quality = "backfilled_first"
            else:
                d_in = (stand_imp - prev_imp) / 1000.0
                d_out = (stand_exp - prev_exp) / 1000.0
                if d_in < -0.02:
                    kwh_in = 0.0
                    quality = "counter_reset"
                    sources += ",negative_delta_dropped"
                else:
                    kwh_in = max(d_in, 0.0)
                kwh_out = 0.0 if d_out < 0 else d_out
            prev_imp, prev_exp = stand_imp, stand_exp
            spot = spots.get(int(start_utc.timestamp()))
            if spot is None:
                quality = "incomplete" if quality == "backfilled" else quality
                sources += ",nordpool_missing"
            costs = {}
            for tariff_id in coll.COST_KEYS:
                price = tariffs.energy_price_gross_eur_per_kwh(
                    cfg, tariff_id, start_local, spot
                )
                costs[tariff_id] = None if price is None else round(kwh_in * price, 6)
            rows.append(
                {
                    "interval_start": coll.utc_iso(start_utc),
                    "interval_end": coll.utc_iso(end_utc),
                    "local_start": start_local.replace(microsecond=0).isoformat(),
                    "grid_import_kwh": round(kwh_in, 6),
                    "grid_export_kwh": round(kwh_out, 6),
                    "counter_import": stand_imp / 1000.0,
                    "counter_export": stand_exp / 1000.0,
                    "nordpool_eur_kwh": spot,
                    "cost_octopus_heat": costs["octopus_heat"],
                    "cost_octopus_heat_loyalty": costs["octopus_heat_loyalty"],
                    "cost_fix_tarif": costs["fix_tarif"],
                    "cost_dynamic": costs["dynamic"],
                    "cost_dynamic_modul3": costs["dynamic_modul3"],
                    "quality": quality,
                    "sources": sources,
                    "updated_at": now_iso,
                }
            )
            days.add(start_local.date())

        print("intervals", len(rows), "days", len(days))
        store.bulk_upsert_intervals(rows)

        today = datetime.now(TZ).date()
        for index, day in enumerate(sorted(days)):
            coll.rebuild_day(store, cfg, day, complete=day < today)
            if index % 30 == 0:
                print("aggregated", day)

        # Invoice window 06.07.2026-05.08.2026 inclusive.
        inv_start = datetime(2026, 7, 6, tzinfo=TZ)
        inv_end = datetime(2026, 8, 6, tzinfo=TZ)
        heat = {"niedrig": 0.0, "standard": 0.0, "hoch": 0.0}
        total = 0.0
        count = 0
        missing_spot = 0
        for row in rows:
            local = datetime.fromisoformat(row["local_start"])
            if not (inv_start <= local < inv_end):
                continue
            kwh = float(row["grid_import_kwh"] or 0)
            total += kwh
            count += 1
            slot = tariffs.heat_slot(cfg, local, "octopus_heat")
            heat[slot] = heat.get(slot, 0.0) + kwh
            if row["nordpool_eur_kwh"] is None:
                missing_spot += 1
        current_tariff = next(item for item in cfg["tariffs"] if item["id"] == "octopus_heat")
        prices = current_tariff["prices_gross_ct_per_kwh"]
        cost_energy = sum(heat[slot] * prices[slot] / 100.0 for slot in heat)
        days_inv = (inv_end.date() - inv_start.date()).days
        standing_per_day = current_tariff.get("standing_eur_per_day")
        if standing_per_day is None:
            standing_per_day = current_tariff["standing_eur_per_month"] * 12.0 / 365.0
        standing = float(standing_per_day) * days_inv
        print("\n=== Octopus-Rechnung 06.07.-05.08.2026 ===")
        print(f"Intervalle {count}")
        print(f"kWh gesamt {total:.3f}  (Rechnung 172,114)")
        print(f"NIEDRIG {heat['niedrig']:.3f}  (Rechnung 151,091)")
        print(f"STANDARD {heat['standard']:.3f}  (Rechnung 11,871)")
        print(f"HOCH {heat['hoch']:.3f}  (Rechnung 9,152)")
        print(
            f"Arbeitspreis brutto {cost_energy:.2f} EUR + Grundpreis {standing:.2f} EUR "
            f"= {cost_energy + standing:.2f} EUR  (Rechnung 51,21)"
        )
        print(f"fehlende Spots {missing_spot}")

        atomic_replace_database(
            live_database=db_live, work_database=db_work
        )

    print("wrote", db_live, "bytes", db_live.stat().st_size)
    if safety_backup is not None:
        print("safety backup", safety_backup)
    print("done")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        run_import(_parse_args(argv))
    except (ImportSafetyError, FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"ABBRUCH: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
