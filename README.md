# Energie & Tarifvergleich

Home Assistant custom component: **15-minute grid import**, German retail tariff comparison, optional wallbox costing.

Designed for **Home Assistant OS on a Raspberry Pi 3**. No InfluxDB, Grafana, Node-RED, AppDaemon or MariaDB.

It does **not** control any device.

Agent briefing (ChatGPT, Codex, Google Antigravity/Gemini): see **[AGENTS.md](AGENTS.md)**. Version **0.1.6**.

---

Leichtgewichtiges 15-Minuten-Logging und Tarifvergleich auf Home Assistant OS.

## What it compares

| ID | Role |
|---|---|
| `octopus_heat` | Live contract (reference), all-in gross |
| `octopus_heat_loyalty` | Hypothetical |
| `naturwerke_fix` | Hypothetical |
| `dynamic` | Hypothetical Tibber-like (spot + markup + Westnetz flat + levies + VAT) |
| `dynamic_modul3` | Same, with Westnetz Modul 3 NT/ST/HT and §14a standing reduction |
| Perfect | Post-hoc: measured 15-min kWh blocks reassigned to the cheapest slots *of that same day* |

Billing simulation uses **grid import only** (Discovergy / Inexogy). PV, battery and wallbox explain the house; they are not the invoice meter.

**Perfect** does not dump the whole day onto the single cheapest quarter-hour. Each measured 15-minute kWh block stays that size; blocks are sorted onto the cheapest prices of that day (rearrangement). Standing charges stay. Heat pump and 11 kW wallbox constraints are not modelled.

## Architecture

```
Discovergy meter + Nord Pool GER
        ↓  every 15 min (minute 0/15/30/45 + 20 s)
SQLite   energy_tariff_compare/data/energy.sqlite
        ↓  daily / monthly / yearly aggregates
Sensors + Lovelace dashboard
```

Home Assistant long-term statistics are hourly. 15-minute ToU until 2027 needs the project database.

Nord Pool `get_prices_for_date` values are **EUR/MWh** and always divided by 1000.

## Requirements

- Home Assistant OS 2026.8 (or current 2026.x)
- [Nord Pool](https://www.home-assistant.io/integrations/nordpool/) official integration
- A `total_increasing` grid-import energy sensor (e.g. Discovergy)
- Optional: Tesla Wall Connector lifetime energy, Anker Solix power sensors (display only)

## Install (short)

See [INSTALL.md](INSTALL.md).

1. Copy `custom_components/energy_tariff_compare/` to `/config/custom_components/`
2. Copy `energy_tariff_compare/` to `/config/energy_tariff_compare/`
3. Copy the dashboard YAML, merge the recorder/lovelace snippet
4. Use `tariffs.example.yaml` as a template; point `entities:` at your sensors
5. **Core restart** (not YAML-only reload) so Python loads
6. Settings → Devices & services → Add **Energie & Tarifvergleich**

Do **not** add `energy_tariff_compare:` to `configuration.yaml`. Config is a UI entry plus `tariffs.yaml`.

## Tests

From a machine that can see the files (Samba `/Volumes/config` or HA `/config`):

```bash
cd energy_tariff_compare
python3 tests/test_windows.py
python3 tests/test_price_units.py
python3 tests/test_collector_slots.py
python3 tests/test_aggregates.py
python3 tests/test_tesla_and_gaps.py
python3 tests/test_async_callbacks.py
python3 tests/test_spot_repair.py
python3 tests/test_import_inexogy.py
```

## Pi 3 notes

- Recorder: exclude noisy power/voltage sensors; keep `sensor.tarifvergleich_viertelstunde_kwh`, `sensor.tarifvergleich_dynamisch_ct`, `sensor.tarifvergleich_modul3_ct`
- Do not turn recorder `include` into a two-sensor allowlist
- Do not rewrite `energy.sqlite` over SMB while Home Assistant is running
- Historical Inexogy CSV import: stop HA, run `scripts/import_inexogy.py` on the HA filesystem

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Octopus, Tibber, Westnetz, Tesla or Anker.
