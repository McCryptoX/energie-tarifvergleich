# Security and privacy

## This integration is read-only

It does **not** control a heat pump, wallbox, battery or inverter.
Vaillant / Tesla Wall Connector / Anker Solix are used only as sensors, if present.

## Do not publish live data

Keep these out of a public GitHub repository:

- `energy_tariff_compare/data/energy.sqlite` (your 15-minute consumption)
- `energy_tariff_compare/imports/*.csv` (meter exports)
- live `tariffs.yaml` if it contains account or meter numbers
- Home Assistant `.storage/` and `secrets.yaml`

For a public repo, commit `energy_tariff_compare/tariffs.example.yaml` and copy it
to `tariffs.yaml` on the device.

## Secrets

The custom component does not store supplier passwords. Nord Pool, Discovergy,
Octopus, Tesla and Anker credentials stay in Home Assistant.

## Raspberry Pi 3

Do not add InfluxDB, Grafana, Node-RED, AppDaemon or MariaDB for this dashboard.
The SQLite file under `energy_tariff_compare/data/` is the project store.
The Home Assistant recorder is an allow/exclude list — do not turn it into a
whitelist of two sensors.
