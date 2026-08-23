# AGENTS.md — Energie & Tarifvergleich

Canonical briefing for **ChatGPT / Codex**, **Google Antigravity / Gemini**, and Grok.

Live system: Home Assistant OS **2026.8.3** on a **Raspberry Pi 3**.
Integration version: **0.1.6** (`custom_components/energy_tariff_compare/manifest.json`).
Schema: `split-costs-v3-tesla-v2`.

If this file and an older `grok-*.txt` or `chatgpt-*.txt` disagree, **this file plus the live Python** win.


## English (hard rules, one screen)

- Read-only devices: never control Tesla Wall Connector, Anker Solix, or Vaillant.
- Billing simulation uses **grid import only** (Discovergy / Inexogy), not PV watts, not wallbox kWh.
- Pi 3: no Influx, Grafana, Node-RED, AppDaemon, MariaDB, ApexCharts, card-mod, layout-card.
- Do not write `energy.sqlite` over Samba while HA is running. Read-only SQL is OK (`mode=ro`).
- Never print secrets, tokens, meter IDs, or account numbers.
- Perfect = rearrange measured 15-min kWh blocks onto the cheapest slots **of that same day**. Not “dump the whole day into the cheapest quarter”.
- **C2 forbidden:** Perfect “Ist” must use the same set of complete days as Perfect+potential. Never swap in period `cost_dynamic`.
- **B6 forbidden:** keep `sensor.tarifvergleich_modul3_ct`.
- Python changes need a **Core restart**. Dashboard YAML: hard frontend reload.
- Do not add `energy_tariff_compare:` to `configuration.yaml`.

Python tests (from `/config/energy_tariff_compare/tests` or Samba `/Volumes/config/.../tests`):

```bash
python3 test_aggregates.py
python3 test_async_callbacks.py
python3 test_collector_slots.py
python3 test_import_inexogy.py
python3 test_incomplete_prices.py
python3 test_price_units.py
python3 test_spot_repair.py
python3 test_tesla_and_gaps.py
python3 test_windows.py
```


## Deutsch — Umgebung

| Was | Pfad |
|---|---|
| Custom component | `/config/custom_components/energy_tariff_compare/` |
| YAML, Tests, Doku | `/config/energy_tariff_compare/` |
| SQLite | `/config/energy_tariff_compare/data/energy.sqlite` |
| Dashboard | `/config/dashboards/energie_tarifvergleich.yaml` |
| Tarife | `/config/energy_tariff_compare/tariffs.yaml` |
| Samba (Mac) | `/Volumes/config/` = `/config/` |
| GitHub-Snapshot ohne sqlite/CSV | `~/Documents/HomeAssistant/energie-tarifvergleich-github-2026-08-23/` |

ChatGPT-TXT-Schleife liegt im YAML-Ordner. Index: `grok-index-fuer-codex-2026-08-23.txt`.


## Was das Ding tut

Alle 15 Minuten (Minute 0/15/30/45 + 20 s) wird der **geschlossene** UTC-Slot gebucht:

- Netzbezug Discovergy `total_increasing` kWh
- optional Tesla Wall Connector Lifetime-kWh (Kosten = Lade-kWh × Arbeitspreis, Obergrenze, keine Rechnung)
- Nord Pool GER Spot, Service immer **EUR/MWh / 1000 → EUR/kWh**

Verglichene Tarife:

| ID | Rolle |
|---|---|
| `octopus_heat` | Live-Vertrag, all-in brutto, Referenz |
| `octopus_heat_loyalty` | hypothetisch |
| `naturwerke_fix` | hypothetisch |
| `dynamic` | hypothetisch Tibber-ähnlich |
| `dynamic_modul3` | dasselbe + Westnetz Modul 3 NT/ST/HT + §14a |
| Perfect | nachträglich, nur vollständige Tage |

Anzeige Arbeit (ct/kWh bzw. EUR) und Grundgebühr (€) getrennt. §14a nicht in Grund/Fix mischen.

Zeitzone `Europe/Berlin`, DST 92 / 96 / 100 Slots.


## A3-Invariante (0.1.6, MUSS halten)

`last_tesla`, `last_tesla_source_updated_utc`, `tesla_count_started_utc` dürfen nur vorrücken, wenn das Tesla-Delta **auf einer Intervallzeile** liegt oder es die **erste Baseline** ist (`last_tesla` noch leer).

Konkret:

1. Leere `backfilled`/`repaired`-Zeile: `tesla_kwh` mergen, Netz-kWh bleiben, dann Meta.
2. Zeile hat schon `tesla_kwh`: **addieren** (1 + 3 = 4), dann Meta.
3. Mehrintervall / `unallocated` (Span > 20 min): `tesla_kwh` bleibt None auf dieser Zeile, `last_tesla` bleibt, Delta nach `meta.tesla_pending_kwh`. Nächster **einzelner** 15-Min-Slot mit `span_ok` bucht das Delta (`tesla_pending_flushed`) und löscht Pending.

Code: `store._merge_tesla_into_prior`, `store.commit_live_collect` (Tesla-Meta-Filter), `collector.collect_tick` (`write_tesla`, Pending, Flush).

Tests: `test_tesla_and_gaps.py` — `run_protected_tesla`, `run_occupied_tesla`, Unallocated 10→16 plus Flush.


## Weitere Invarianten, die sitzen müssen

- Fehlender Spot: Dynamisch / Modul 3 Arbeit **und** Gesamt `None`, Heat bleibt Zahl. Monat/Jahr `_sum_if_complete`. Wallbox-Dynamikkosten ebenso.
- `ranking_complete` Monat/Jahr: zusätzlich `days_with_data == days_expected`.
- `spot_day_complete`: 15-Min-Raster, nicht Count. 96×5 min ≠ komplett.
- YAML-Reload: Tage = `daily` ∪ Intervall-`local_day` ∪ heute. In-Memory-Config erst nach erfolgreichem Reprice. Startup unter `collect_lock`.
- `parse_float`: NaN/Inf → None.
- Grün: `cheapest_current_ids`, kein `float(999)`.
- Recorder ist Allowlist, wenn `include` gesetzt ist — nicht nur zwei Sensoren whitelisten.


## Dashboard

Drei **Tabs im YAML-Dashboard**, nicht extra in der Sidebar:

1. Übersicht `path: vergleich` — Jetzt-Preise, Haus, 24 h, Heute/Gestern Gesamt, kompakter Monat/Jahr
2. Preise `path: preise` — Woche mini-graph 168 h, Monat statistics-graph hour, Jahr day
3. Details `path: details` — Arbeit / Grund / §14a / Perfect / Wallbox

Alle drei: `show_icon_and_title: true`.

Erlaubt: Mushroom, mini-graph-card. Sonst nichts Neues.

Monatszeile: `Gesamt: Heat … · Dynamisch … · Modul 3 …` — Heat nicht als fest verdrahteter Sieger.


## Was du nicht bauen sollst

- C2, B6 (siehe oben)
- B3 Event-Dedup, B4 Gap-Scan umbauen, B5 Attribute ausdünnen, C5 lovelace.mode — nur bei messbarem Problem
- Lastmanagement, Wallbox-Steuerung, dynamisches Laden
- sqlite über Samba ersetzen, WAL/SHM löschen während HA läuft


## Nach einer Code-Änderung

1. Die neun `test_*.py` ausführen.
2. Keine Secrets in Diffs/TXT.
3. Für ChatGPT: kurze `grok-umsetzung-*.txt` + Index + ggf. neuer Prompt.
4. Snapshot ohne sqlite/CSV nach `energie-tarifvergleich-github-2026-08-23/` kopieren.
5. Marco: **Core neu starten** für Python. Dashboard-YAML: Frontend hart neu laden.

Aktueller ChatGPT-Prüfauftrag nach 0.1.6:

`/config/energy_tariff_compare/prompt-chatgpt-nachpruefung-0.1.6-2026-08-23.txt`
