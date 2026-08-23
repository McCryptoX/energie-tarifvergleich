# Changelog

## 0.1.7 — 2026-08-23

- Tesla baseline while grid import is still missing no longer advances `last_tesla` over an unbooked delta
- Tesla counter reset keeps open pending kWh (`tesla_pending_extra`) and books them with new-counter energy
- Occupied repaired/ok rows with 0 grid kWh add Tesla instead of replacing

## 0.1.6 — 2026-08-23

- Tesla meta (`last_tesla` / source) advances only after a booked interval or first baseline
- Extra Tesla delta on a protected row that already has `tesla_kwh` is added, not dropped
- Multi-slot Tesla is pending (`tesla_pending_kwh`), flushed on the next safe 15-min slot
- Agent docs: `AGENTS.md` + `GEMINI.md` (ChatGPT and Google Antigravity)

## 0.1.5 — 2026-08-23

- Tesla: charging kWh is merged onto backfilled/repaired slots; last_tesla only advances after a booking
- Tariff YAML reload reprices interval-only days (not just daily rows); startup reprice under collect_lock; config hash after success
- Wallbox month/year/week dynamic costs stay unknown when a spot is missing
- Period ranking requires expected day coverage for month/year
- Nord Pool “complete day” checks the 15-minute raster (92/96/100), not just the point count
- Dashboard: month totals no longer labelled as Heat-win; tab titles visible; honest graph ranges

## 0.1.4 — 2026-08-23

- Tesla Wall Connector: 15-minute charging kWh from lifetime energy, from first baseline onward
- Wallbox costs = slot Arbeitspreis only (no standing charge)
- Week aggregation Monday–Sunday
- Gap repair from recorder 5-minute statistics (so Perfect can run after HA downtime)
- Modul 3 price history sensor `sensor.tarifvergleich_modul3_ct`
- Interval kWh sensor: measurement state class, no Energy device class
- Dashboard: Anker live, Tesla live, Wallbox cost table, separate 24 h graphs

## 0.1.3

- Split Arbeitspreis / Grund/Fix / §14a
- Perfect for Dynamisch and Dynamisch + Modul 3 on complete days
- Nord Pool service prices always treated as EUR/MWh and divided by 1000
- Closed UTC 15-minute slot, collect lock, async callbacks
- Tibber-like markup 2.15 ct gross
