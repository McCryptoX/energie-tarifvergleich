#!/usr/bin/env python3
"""Importer safety tests. Run: python3 tests/test_import_inexogy.py"""

from __future__ import annotations

import fcntl
import importlib.util
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/config") if Path("/config/energy_tariff_compare").exists() else Path("/Volumes/config")
SCRIPT = ROOT / "energy_tariff_compare" / "scripts" / "import_inexogy.py"
SPEC = importlib.util.spec_from_file_location("test_import_inexogy_module", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise SystemExit(f"FAIL: Importer konnte nicht geladen werden: {SCRIPT}")
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print("OK", message)


def expect_raises(error_type, function, message: str) -> Exception:
    try:
        function()
    except error_type as exc:
        print("OK", message)
        return exc
    except Exception as exc:
        raise SystemExit(
            f"FAIL: {message}; falscher Fehlertyp {type(exc).__name__}: {exc}"
        ) from exc
    raise SystemExit(f"FAIL: {message}; kein Fehler ausgelöst")


def create_database(path: Path, value: str, *, wal: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    if wal:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        check(str(mode).lower() == "wal", "temporäre Test-DB ist im WAL-Modus")
    conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    conn.execute("INSERT INTO sample VALUES (?)", (value,))
    conn.commit()
    return conn


def acquire_ha_lock_once(root: Path) -> None:
    with IMPORTER.home_assistant_execution_lock(root):
        pass


def test_unique_csv_selection(temp_root: Path) -> None:
    imports = temp_root / "imports"
    imports.mkdir()
    first = imports / "inexogy_first.csv"
    second = imports / "inexogy_second.csv"
    first.write_text("x\n", encoding="utf-8")
    check(
        IMPORTER.find_csv(candidates=[imports]) == first.resolve(),
        "eine Inexogy-CSV wird eindeutig gewählt",
    )
    second.write_text("x\n", encoding="utf-8")
    expect_raises(
        IMPORTER.ImportSafetyError,
        lambda: IMPORTER.find_csv(candidates=[imports]),
        "mehrere passende Inexogy-CSVs werden abgelehnt",
    )
    check(
        IMPORTER.find_csv(explicit=second) == second.resolve(),
        "expliziter CSV-Pfad löst Mehrdeutigkeit auf",
    )


def test_dst_fold(temp_root: Path) -> None:
    spot = temp_root / "spotmarkt_fold.csv"
    spot.write_text(
        "Datum;von;Zeitzone von;bis;Zeitzone bis;Spotmarktpreis in ct/kWh\n"
        "25.10.2026;02:45;CEST;02:00;CET;10,0\n"
        "25.10.2026;02:00;CET;02:15;CET;20,0\n",
        encoding="utf-8",
    )
    prices = IMPORTER.load_spot_csv(spot)
    first = int(datetime(2026, 10, 25, 0, 45, tzinfo=timezone.utc).timestamp())
    second = int(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc).timestamp())
    check(prices[first] == 0.10, "CEST-Fold-Slot wird 00:45 UTC zugeordnet")
    check(prices[second] == 0.20, "CET-Fold-Slot wird 01:00 UTC zugeordnet")
    check(len(prices) == 2, "wiederholte lokale Stunde erzeugt zwei UTC-Slots")

    midnight = temp_root / "spotmarkt_midnight.csv"
    midnight.write_text(
        "Datum;von;Zeitzone von;bis;Zeitzone bis;Spotmarktpreis in ct/kWh\n"
        "29.03.2026;23:45;CEST;00:00;CEST;12,0\n",
        encoding="utf-8",
    )
    midnight_prices = IMPORTER.load_spot_csv(midnight)
    midnight_start = int(
        datetime(2026, 3, 29, 21, 45, tzinfo=timezone.utc).timestamp()
    )
    check(
        midnight_prices[midnight_start] == 0.12,
        "Intervallende nach Mitternacht erhält den Folgetag",
    )

    invalid = temp_root / "spotmarkt_unknown_zone.csv"
    invalid.write_text(
        "Datum;von;Zeitzone von;bis;Zeitzone bis;Spotmarktpreis in ct/kWh\n"
        "25.10.2026;02:00;Europe/Berlin;02:15;Europe/Berlin;10,0\n",
        encoding="utf-8",
    )
    expect_raises(
        ValueError,
        lambda: IMPORTER.load_spot_csv(invalid),
        "nicht eindeutige Spot-Zeitzone wird sichtbar abgelehnt",
    )

    seasonally_wrong = temp_root / "spotmarkt_wrong_season.csv"
    seasonally_wrong.write_text(
        "Datum;von;Zeitzone von;bis;Zeitzone bis;Spotmarktpreis in ct/kWh\n"
        "15.01.2026;02:00;CEST;02:15;CEST;10,0\n",
        encoding="utf-8",
    )
    expect_raises(
        ValueError,
        lambda: IMPORTER.load_spot_csv(seasonally_wrong),
        "saisonal falsches CEST wird abgelehnt",
    )


def test_energy_charts_unit() -> None:
    timestamp = 1_767_225_600  # 2026-01-01 00:00:00 UTC
    prices = IMPORTER._energy_charts_spots(
        {"unix_seconds": [timestamp], "price": [96.0], "unit": "EUR / MWh"},
        source="test",
    )
    check(prices[timestamp] == 0.096, "Energy-Charts EUR/MWh wird in EUR/kWh umgerechnet")
    expect_raises(
        ValueError,
        lambda: IMPORTER._energy_charts_spots(
            {"unix_seconds": [timestamp], "price": [0.096], "unit": "EUR/kWh"},
            source="test",
        ),
        "unerwartete Energy-Charts-Einheit wird abgelehnt",
    )
    expect_raises(
        ValueError,
        lambda: IMPORTER._energy_charts_spots(
            {"unix_seconds": [timestamp + 1], "price": [96.0], "unit": "EUR/MWh"},
            source="test",
        ),
        "verschobener API-Zeitstempel wird abgelehnt",
    )


def test_backup_includes_wal(temp_root: Path) -> None:
    source = temp_root / "source.sqlite"
    destination = temp_root / "backup.sqlite"
    source_conn = create_database(source, "committed-wal", wal=True)
    try:
        wal = Path(f"{source}-wal")
        check(wal.exists() and wal.stat().st_size > 0, "Testzeile liegt bei offenem Handle im WAL")
        IMPORTER.sqlite_backup(source, destination)
    finally:
        source_conn.close()
    with sqlite3.connect(destination) as conn:
        value = conn.execute("SELECT value FROM sample").fetchone()[0]
    check(value == "committed-wal", "SQLite Backup API übernimmt committed WAL-Inhalt")
    IMPORTER.sqlite_integrity_check(destination)
    print("OK", "Backup besteht PRAGMA integrity_check")


def test_writer_guards_and_atomic_replace(temp_root: Path) -> None:
    root = temp_root / "config"
    root.mkdir()
    live = root / "live.sqlite"
    work = root / "work.sqlite"
    create_database(live, "old").close()
    create_database(work, "new", wal=True).close()

    lock = root / ".ha_run.lock"
    lock.write_text("stale metadata is allowed", encoding="utf-8")
    with IMPORTER.home_assistant_execution_lock(root):
        print("OK", "vorhandene, aber freie Home-Assistant-Lockdatei wird gesperrt")

    with lock.open("a+", encoding="utf-8") as external_lock:
        fcntl.flock(external_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            expect_raises(
                IMPORTER.ImportSafetyError,
                lambda: acquire_ha_lock_once(root),
                "tatsächlich gehaltener Home-Assistant-flock verhindert Import",
            )
        finally:
            fcntl.flock(external_lock.fileno(), fcntl.LOCK_UN)

    writer = sqlite3.connect(live, timeout=0, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        expect_raises(
            IMPORTER.ImportSafetyError,
            lambda: IMPORTER.ensure_no_active_writer(live),
            "aktiver SQLite-Schreiber verhindert Austausch",
        )
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    with IMPORTER.home_assistant_execution_lock(root):
        IMPORTER.atomic_replace_database(
            live_database=live, work_database=work
        )
    with sqlite3.connect(live) as conn:
        value = conn.execute("SELECT value FROM sample").fetchone()[0]
    check(value == "new", "validierte temporäre DB wird atomar eingesetzt")
    check(not work.exists(), "os.replace hinterlässt keine zweite Arbeits-DB")
    check(not Path(f"{live}-wal").exists(), "Austausch hinterlässt kein altes WAL")
    check(not Path(f"{live}-shm").exists(), "Austausch hinterlässt kein altes SHM")


def test_smb_and_static_guards() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        expect_raises(
            IMPORTER.ImportSafetyError,
            lambda: IMPORTER.require_local_config(Path(temp_dir)),
            "nichtlokaler /config-Pfad wird abgelehnt",
        )
    source = SCRIPT.read_text(encoding="utf-8")
    check("_create_unverified_context" not in source, "kein TLS-Unverified-Fallback vorhanden")
    check("read_bytes()" not in source, "SQLite wird nicht als rohe Datei gelesen")
    check("write_bytes(" not in source, "SQLite wird nicht als rohe Datei geschrieben")


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        test_unique_csv_selection(temp_root)
        test_dst_fold(temp_root)
        test_energy_charts_unit()
        test_backup_includes_wal(temp_root)
        test_writer_guards_and_atomic_replace(temp_root)
    test_smb_and_static_guards()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
