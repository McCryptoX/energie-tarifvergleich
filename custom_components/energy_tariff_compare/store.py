"""SQLite persistence for 15-minute intervals and aggregates."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS intervals (
  interval_start TEXT PRIMARY KEY,
  interval_end TEXT NOT NULL,
  local_start TEXT NOT NULL,
  grid_import_kwh REAL,
  grid_export_kwh REAL,
  tesla_kwh REAL,
  counter_import REAL,
  counter_export REAL,
  nordpool_eur_kwh REAL,
  cost_octopus_heat REAL,
  cost_octopus_heat_loyalty REAL,
  cost_naturwerke_fix REAL,
  cost_dynamic REAL,
  cost_dynamic_modul3 REAL,
  quality TEXT NOT NULL,
  sources TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spot_prices (
  interval_start TEXT PRIMARY KEY,
  interval_end TEXT NOT NULL,
  nordpool_eur_kwh REAL NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily (
  day TEXT PRIMARY KEY,
  grid_import_kwh REAL,
  intervals_ok INTEGER,
  intervals_due INTEGER,
  intervals_missing INTEGER,
  intervals_future INTEGER,
  price_intervals_missing INTEGER,
  intervals_expected INTEGER,
  perfect_days INTEGER,
  data_through TEXT,
  energy_cost_octopus_heat REAL,
  energy_cost_octopus_heat_loyalty REAL,
  energy_cost_naturwerke_fix REAL,
  energy_cost_dynamic REAL,
  energy_cost_dynamic_modul3 REAL,
  standing_cost_octopus_heat REAL,
  standing_cost_octopus_heat_loyalty REAL,
  standing_cost_naturwerke_fix REAL,
  standing_cost_dynamic REAL,
  standing_cost_dynamic_modul3 REAL,
  cost_octopus_heat REAL,
  cost_octopus_heat_loyalty REAL,
  cost_naturwerke_fix REAL,
  cost_dynamic REAL,
  cost_dynamic_modul3 REAL,
  cost_dynamic_perfect REAL,
  cost_dynamic_flat_perfect REAL,
  potential_eur REAL,
  potential_pct REAL,
  potential_dynamic_eur REAL,
  quality TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monthly (
  year_month TEXT PRIMARY KEY,
  grid_import_kwh REAL,
  days_ok INTEGER,
  days_with_data INTEGER,
  days_complete INTEGER,
  days_incomplete INTEGER,
  perfect_days INTEGER,
  days_expected INTEGER,
  intervals_due INTEGER,
  intervals_missing INTEGER,
  price_intervals_missing INTEGER,
  data_through TEXT,
  energy_cost_octopus_heat REAL,
  energy_cost_octopus_heat_loyalty REAL,
  energy_cost_naturwerke_fix REAL,
  energy_cost_dynamic REAL,
  energy_cost_dynamic_modul3 REAL,
  standing_cost_octopus_heat REAL,
  standing_cost_octopus_heat_loyalty REAL,
  standing_cost_naturwerke_fix REAL,
  standing_cost_dynamic REAL,
  standing_cost_dynamic_modul3 REAL,
  cost_octopus_heat REAL,
  cost_octopus_heat_loyalty REAL,
  cost_naturwerke_fix REAL,
  cost_dynamic REAL,
  cost_dynamic_modul3 REAL,
  cost_dynamic_perfect REAL,
  cost_dynamic_flat_perfect REAL,
  potential_eur REAL,
  potential_dynamic_eur REAL,
  quality TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS yearly (
  year TEXT PRIMARY KEY,
  grid_import_kwh REAL,
  days_with_data INTEGER,
  days_complete INTEGER,
  days_incomplete INTEGER,
  days_expected INTEGER,
  perfect_days INTEGER,
  intervals_due INTEGER,
  intervals_missing INTEGER,
  price_intervals_missing INTEGER,
  data_through TEXT,
  energy_cost_octopus_heat REAL,
  energy_cost_octopus_heat_loyalty REAL,
  energy_cost_naturwerke_fix REAL,
  energy_cost_dynamic REAL,
  energy_cost_dynamic_modul3 REAL,
  standing_cost_octopus_heat REAL,
  standing_cost_octopus_heat_loyalty REAL,
  standing_cost_naturwerke_fix REAL,
  standing_cost_dynamic REAL,
  standing_cost_dynamic_modul3 REAL,
  cost_octopus_heat REAL,
  cost_octopus_heat_loyalty REAL,
  cost_naturwerke_fix REAL,
  cost_dynamic REAL,
  cost_dynamic_modul3 REAL,
  cost_dynamic_perfect REAL,
  cost_dynamic_flat_perfect REAL,
  potential_eur REAL,
  potential_dynamic_eur REAL,
  cheapest TEXT,
  quality TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

COST_COLS = (
    "octopus_heat",
    "octopus_heat_loyalty",
    "naturwerke_fix",
    "dynamic",
    "dynamic_modul3",
)

INTERVAL_COLS = (
    "interval_start",
    "interval_end",
    "local_start",
    "grid_import_kwh",
    "grid_export_kwh",
    "tesla_kwh",
    "counter_import",
    "counter_export",
    "nordpool_eur_kwh",
    "cost_octopus_heat",
    "cost_octopus_heat_loyalty",
    "cost_naturwerke_fix",
    "cost_dynamic",
    "cost_dynamic_modul3",
    "quality",
    "sources",
    "updated_at",
)

AGGREGATE_EXTRA_COLUMNS = {
    "daily": {
        "intervals_due": "INTEGER",
        "intervals_missing": "INTEGER",
        "intervals_future": "INTEGER",
        "price_intervals_missing": "INTEGER",
        "perfect_days": "INTEGER",
        "data_through": "TEXT",
        "paragraph_14a_eur": "REAL",
    },
    "monthly": {
        "days_with_data": "INTEGER",
        "days_complete": "INTEGER",
        "days_incomplete": "INTEGER",
        "perfect_days": "INTEGER",
        "data_through": "TEXT",
        "intervals_due": "INTEGER",
        "intervals_missing": "INTEGER",
        "price_intervals_missing": "INTEGER",
        "paragraph_14a_eur": "REAL",
    },
    "yearly": {
        "days_with_data": "INTEGER",
        "days_complete": "INTEGER",
        "days_incomplete": "INTEGER",
        "days_expected": "INTEGER",
        "perfect_days": "INTEGER",
        "data_through": "TEXT",
        "intervals_due": "INTEGER",
        "intervals_missing": "INTEGER",
        "price_intervals_missing": "INTEGER",
        "paragraph_14a_eur": "REAL",
    },
}
for _table in ("daily", "monthly", "yearly"):
    AGGREGATE_EXTRA_COLUMNS[_table]["tesla_kwh"] = "REAL"
    for _tariff in COST_COLS:
        AGGREGATE_EXTRA_COLUMNS[_table][f"energy_cost_{_tariff}"] = "REAL"
        AGGREGATE_EXTRA_COLUMNS[_table][f"standing_cost_{_tariff}"] = "REAL"
        AGGREGATE_EXTRA_COLUMNS[_table][f"tesla_cost_{_tariff}"] = "REAL"


class Store:
    PROTECTED_INTERVAL_QUALITIES = frozenset({"backfilled", "backfilled_first"})
    TESLA_META_KEYS = frozenset(
        {
            "last_tesla",
            "last_tesla_source_updated_utc",
            "tesla_count_started_utc",
        }
    )

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                conn.execute("PRAGMA journal_mode=DELETE")
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        interval_cols = {row[1] for row in conn.execute("PRAGMA table_info(intervals)")}
        if "tesla_kwh" not in interval_cols:
            conn.execute("ALTER TABLE intervals ADD COLUMN tesla_kwh REAL")
        for table in ("daily", "monthly", "yearly"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "cost_dynamic_flat_perfect" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN cost_dynamic_flat_perfect REAL")
            if "potential_dynamic_eur" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN potential_dynamic_eur REAL")
            for column, sql_type in AGGREGATE_EXTRA_COLUMNS[table].items():
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def close(self) -> None:
        return

    def _one(self, sql: str, params=()) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return None if row is None else dict(row)
        finally:
            conn.close()

    def _all(self, sql: str, params=()) -> list[dict]:
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def _exec(self, sql: str, params=()) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(sql, params)
        finally:
            conn.close()

    def get_meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str | None) -> None:
        if value is None:
            self._exec("DELETE FROM meta WHERE key=?", (key,))
        else:
            self._exec(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def set_meta_many(self, values: dict[str, str | None]) -> None:
        if not values:
            return
        conn = self._connect()
        try:
            with conn:
                conn.executemany("DELETE FROM meta WHERE key=?", [(k,) for k, v in values.items() if v is None])
                conn.executemany(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    [(k, v) for k, v in values.items() if v is not None],
                )
        finally:
            conn.close()

    def bulk_upsert_intervals(self, rows: list[dict]) -> None:
        if not rows:
            return
        cols = INTERVAL_COLS
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "interval_start")
        sql = (
            f"INSERT INTO intervals ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(interval_start) DO UPDATE SET {updates}"
        )
        conn = self._connect()
        try:
            with conn:
                conn.executemany(sql, [[row.get(c) for c in cols] for row in rows])
        finally:
            conn.close()

    def bulk_upsert_spots(self, rows: list[tuple]) -> None:
        if not rows:
            return
        conn = self._connect()
        try:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO spot_prices(interval_start, interval_end, nordpool_eur_kwh, fetched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(interval_start) DO UPDATE SET
                      interval_end=excluded.interval_end,
                      nordpool_eur_kwh=excluded.nordpool_eur_kwh,
                      fetched_at=excluded.fetched_at
                    WHERE spot_prices.interval_end IS NOT excluded.interval_end
                       OR abs(spot_prices.nordpool_eur_kwh - excluded.nordpool_eur_kwh) > 0.000000000001
                    """,
                    rows,
                )
        finally:
            conn.close()

    def upsert_interval_live(self, row: dict) -> dict:
        """Write a live interval unless it would clobber backfill or real kWh with 0."""
        existing = self.get_interval(row["interval_start"])
        if existing and existing.get("quality") in self.PROTECTED_INTERVAL_QUALITIES:
            return existing
        if existing:
            old = existing.get("grid_import_kwh")
            new = row.get("grid_import_kwh")
            if old is not None and float(old) > 0 and (new is None or abs(float(new)) < 1e-12):
                return existing
        self.upsert_interval(row)
        return row

    def upsert_interval(self, row: dict) -> None:
        cols = INTERVAL_COLS
        values = [row.get(c) for c in cols]
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "interval_start")
        self._exec(
            f"INSERT INTO intervals ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(interval_start) DO UPDATE SET {updates}",
            values,
        )

    def insert_missing(self, start_utc: datetime, end_utc: datetime, local_start: datetime) -> None:
        self._exec(
            """
            INSERT OR IGNORE INTO intervals(
              interval_start, interval_end, local_start, quality, sources, updated_at
            ) VALUES (?, ?, ?, 'missing', 'gap', ?)
            """,
            (
                utc_iso(start_utc),
                utc_iso(end_utc),
                local_start.replace(microsecond=0).isoformat(),
                datetime.now(TZ).isoformat(),
            ),
        )

    def upsert_spot(self, start: datetime, end: datetime, price: float) -> None:
        self._exec(
            """
            INSERT INTO spot_prices(interval_start, interval_end, nordpool_eur_kwh, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(interval_start) DO UPDATE SET
              interval_end=excluded.interval_end,
              nordpool_eur_kwh=excluded.nordpool_eur_kwh,
              fetched_at=excluded.fetched_at
            """,
            (utc_iso(start), utc_iso(end), price, datetime.now(TZ).isoformat()),
        )

    def get_spot(self, start: datetime) -> float | None:
        row = self._one(
            "SELECT nordpool_eur_kwh FROM spot_prices WHERE interval_start=?",
            (utc_iso(start),),
        )
        return None if row is None else row["nordpool_eur_kwh"]

    def spots_for_day(self, day: date) -> dict[str, float]:
        start, end = self._utc_day_bounds(day)
        return {
            row["interval_start"]: row["nordpool_eur_kwh"]
            for row in self._all(
                "SELECT interval_start, nordpool_eur_kwh FROM spot_prices "
                "WHERE interval_start>=? AND interval_start<? ORDER BY interval_start",
                (start, end),
            )
        }

    def all_spots(self) -> dict[str, float]:
        return {
            row["interval_start"]: float(row["nordpool_eur_kwh"])
            for row in self._all(
                "SELECT interval_start, nordpool_eur_kwh FROM spot_prices ORDER BY interval_start"
            )
        }

    def intervals_for_day(self, day: date) -> list[dict]:
        start, end = self._utc_day_bounds(day)
        return self._all(
            "SELECT * FROM intervals WHERE interval_start>=? AND interval_start<? ORDER BY interval_start",
            (start, end),
        )

    @staticmethod
    def _utc_day_bounds(day: date) -> tuple[str, str]:
        local_start = datetime(day.year, day.month, day.day, tzinfo=TZ)
        local_end = local_start + timedelta(days=1)
        return utc_iso(local_start), utc_iso(local_end)

    def get_interval(self, start_iso: str) -> dict | None:
        return self._one("SELECT * FROM intervals WHERE interval_start=?", (start_iso,))

    def latest_interval(self) -> dict | None:
        return self._one(
            "SELECT * FROM intervals WHERE grid_import_kwh IS NOT NULL "
            "ORDER BY interval_start DESC LIMIT 1"
        )

    def collect_context(self, start_iso: str) -> dict:
        """Read collector metadata, target interval and stored spot over one connection."""
        conn = self._connect()
        try:
            meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
            existing = conn.execute("SELECT * FROM intervals WHERE interval_start=?", (start_iso,)).fetchone()
            spot = conn.execute(
                "SELECT nordpool_eur_kwh FROM spot_prices WHERE interval_start=?", (start_iso,)
            ).fetchone()
            return {
                "meta": meta,
                "existing": None if existing is None else dict(existing),
                "spot": None if spot is None else spot["nordpool_eur_kwh"],
            }
        finally:
            conn.close()

    @staticmethod
    def _append_source(sources: str | None, extra: str) -> str:
        parts = [part for part in str(sources or "").split(",") if part]
        if extra not in parts:
            parts.append(extra)
        return ",".join(parts)

    def _merge_tesla_into_prior(
        self, conn: sqlite3.Connection, prior: dict, row: dict
    ) -> tuple[dict, bool, bool]:
        """Keep protected grid kWh; set or add tesla_kwh on that same row.

        Returns (actual_row, changed, tesla_persisted). Tesla last_* meta may
        only advance when tesla_persisted is True.
        """
        new_tesla = row.get("tesla_kwh")
        if new_tesla is None:
            return prior, False, False
        old_tesla = prior.get("tesla_kwh")
        try:
            old_value = 0.0 if old_tesla is None else float(old_tesla)
        except (TypeError, ValueError):
            old_value = 0.0
        added = old_value + float(new_tesla)
        sources = self._append_source(prior.get("sources"), "tesla_live")
        updated_at = row.get("updated_at") or datetime.now(TZ).isoformat()
        conn.execute(
            "UPDATE intervals SET tesla_kwh=?, updated_at=?, sources=? WHERE interval_start=?",
            (added, updated_at, sources, row["interval_start"]),
        )
        actual = dict(prior)
        actual["tesla_kwh"] = added
        actual["updated_at"] = updated_at
        actual["sources"] = sources
        return actual, True, True

    def commit_live_collect(
        self,
        row: dict | None,
        meta_values: dict[str, str | None],
        missing_rows: list[dict] | None = None,
    ) -> tuple[dict | None, bool]:
        """Atomically persist a live row, gap markers and its collector baseline."""
        missing_rows = missing_rows or []
        conn = self._connect()
        try:
            with conn:
                if missing_rows:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO intervals(
                          interval_start, interval_end, local_start, quality, sources, updated_at
                        ) VALUES (?, ?, ?, 'missing', 'gap', ?)
                        """,
                        [
                            (
                                item["interval_start"],
                                item["interval_end"],
                                item["local_start"],
                                item["updated_at"],
                            )
                            for item in missing_rows
                        ],
                    )

                actual = None
                changed = False
                existing_last = conn.execute(
                    "SELECT value FROM meta WHERE key=?", ("last_tesla",)
                ).fetchone()
                tesla_persisted = existing_last is None
                if row is not None:
                    prior_row = conn.execute(
                        "SELECT * FROM intervals WHERE interval_start=?", (row["interval_start"],)
                    ).fetchone()
                    prior = None if prior_row is None else dict(prior_row)
                    protected = prior and prior.get("quality") in self.PROTECTED_INTERVAL_QUALITIES
                    drops_real = False
                    new_grid = None if row is None else row.get("grid_import_kwh")
                    new_grid_empty = new_grid is None or abs(float(new_grid)) < 1e-12
                    prior_tesla = False
                    if prior:
                        old = prior.get("grid_import_kwh")
                        drops_real = old is not None and float(old) > 0 and new_grid_empty
                        raw_tesla = prior.get("tesla_kwh")
                        try:
                            prior_tesla = raw_tesla is not None and abs(float(raw_tesla)) > 1e-12
                        except (TypeError, ValueError):
                            prior_tesla = False
                    merge_tesla = bool(prior) and (
                        protected or drops_real or (prior_tesla and new_grid_empty)
                    )
                    if merge_tesla:
                        actual, changed, tesla_persisted = self._merge_tesla_into_prior(
                            conn, prior, row
                        )
                        tesla_persisted = tesla_persisted or existing_last is None
                    else:
                        cols = INTERVAL_COLS
                        placeholders = ",".join("?" * len(cols))
                        updates = ",".join(
                            f"{column}=excluded.{column}" for column in cols if column != "interval_start"
                        )
                        conn.execute(
                            f"INSERT INTO intervals ({','.join(cols)}) VALUES ({placeholders}) "
                            f"ON CONFLICT(interval_start) DO UPDATE SET {updates}",
                            [row.get(column) for column in cols],
                        )
                        actual = row
                        changed = True
                        tesla_persisted = row.get("tesla_kwh") is not None or existing_last is None

                if row is None:
                    tesla_persisted = existing_last is None
                if (
                    meta_values
                    and meta_values.get("tesla_pending_extra") == "1"
                    and meta_values.get("last_tesla") is not None
                ):
                    tesla_persisted = True

                meta_to_write = meta_values
                if not tesla_persisted and meta_values:
                    meta_to_write = {
                        key: value
                        for key, value in meta_values.items()
                        if key not in self.TESLA_META_KEYS
                    }
                if meta_to_write:
                    conn.executemany(
                        "DELETE FROM meta WHERE key=?",
                        [(key,) for key, value in meta_to_write.items() if value is None],
                    )
                    conn.executemany(
                        "INSERT INTO meta(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        [(key, value) for key, value in meta_to_write.items() if value is not None],
                    )
            return actual, changed
        finally:
            conn.close()

    def upsert_daily(self, row: dict) -> None:
        cols = list(row.keys())
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "day")
        self._exec(
            f"INSERT INTO daily ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(day) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )

    def get_daily(self, day: date) -> dict | None:
        return self._one("SELECT * FROM daily WHERE day=?", (day.isoformat(),))

    def upsert_monthly(self, row: dict) -> None:
        cols = list(row.keys())
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "year_month")
        self._exec(
            f"INSERT INTO monthly ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(year_month) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )

    def get_monthly(self, year_month: str) -> dict | None:
        return self._one("SELECT * FROM monthly WHERE year_month=?", (year_month,))

    def upsert_yearly(self, row: dict) -> None:
        cols = list(row.keys())
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "year")
        self._exec(
            f"INSERT INTO yearly ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(year) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )

    def get_yearly(self, year: int) -> dict | None:
        return self._one("SELECT * FROM yearly WHERE year=?", (str(year),))

    def sum_daily_range(self, start_day: date, end_day: date) -> list[dict]:
        return self._all(
            "SELECT * FROM daily WHERE day>=? AND day<=? ORDER BY day",
            (start_day.isoformat(), end_day.isoformat()),
        )

    def fact_days(self, start_day: date, end_day: date) -> list[date]:
        """Local days that have a daily row and/or at least one interval."""
        start_s, end_s = start_day.isoformat(), end_day.isoformat()
        found: set[date] = set()
        for row in self._all(
            "SELECT day FROM daily WHERE day>=? AND day<=?",
            (start_s, end_s),
        ):
            try:
                found.add(date.fromisoformat(row["day"]))
            except (TypeError, ValueError):
                continue
        for row in self._all(
            "SELECT DISTINCT substr(local_start, 1, 10) AS local_day FROM intervals "
            "WHERE substr(local_start, 1, 10)>=? AND substr(local_start, 1, 10)<=?",
            (start_s, end_s),
        ):
            try:
                found.add(date.fromisoformat(row["local_day"]))
            except (TypeError, ValueError):
                continue
        return sorted(found)

    def snapshot_rows(
        self, today: date, yesterday: date, selected_month: str | None, year: int
    ) -> dict:
        """Read the complete HA sensor snapshot from one consistent connection."""
        conn = self._connect()
        try:
            def one(sql: str, params: tuple) -> dict | None:
                row = conn.execute(sql, params).fetchone()
                return None if row is None else dict(row)

            if not selected_month:
                selected_row = conn.execute(
                    "SELECT value FROM meta WHERE key='selected_month'"
                ).fetchone()
                selected_month = (
                    today.strftime("%Y-%m") if selected_row is None else str(selected_row["value"])
                )
            tesla_started = conn.execute(
                "SELECT value FROM meta WHERE key='tesla_count_started_utc'"
            ).fetchone()
            tesla_pending = conn.execute(
                "SELECT value FROM meta WHERE key='tesla_pending_kwh'"
            ).fetchone()
            monday = today - timedelta(days=today.weekday())
            week_days = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM daily WHERE day>=? AND day<=? ORDER BY day",
                    (monday.isoformat(), today.isoformat()),
                )
            ]
            return {
                "today": one("SELECT * FROM daily WHERE day=?", (today.isoformat(),)),
                "yesterday": one("SELECT * FROM daily WHERE day=?", (yesterday.isoformat(),)),
                "month": one("SELECT * FROM monthly WHERE year_month=?", (selected_month,)),
                "year": one("SELECT * FROM yearly WHERE year=?", (str(year),)),
                "latest_interval": one(
                    "SELECT * FROM intervals WHERE grid_import_kwh IS NOT NULL "
                    "ORDER BY interval_start DESC LIMIT 1",
                    (),
                ),
                "selected_month": selected_month,
                "week_days": week_days,
                "week_start": monday.isoformat(),
                "tesla_count_started_utc": None if tesla_started is None else tesla_started["value"],
                "tesla_pending_kwh": None if tesla_pending is None else tesla_pending["value"],
            }
        finally:
            conn.close()
