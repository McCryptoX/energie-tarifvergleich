# Neuinstallation

Diese Anleitung gilt für Home Assistant OS. Pfade relativ zu `/config`.

## 1. Dateien kopieren

| Quelle in diesem Ordner | Ziel auf dem HA |
|---|---|
| `custom_components/energy_tariff_compare/` | `/config/custom_components/energy_tariff_compare/` |
| `energy_tariff_compare/tariffs.yaml` | `/config/energy_tariff_compare/tariffs.yaml` |
| `energy_tariff_compare/scripts/` | `/config/energy_tariff_compare/scripts/` |
| `energy_tariff_compare/tests/` | `/config/energy_tariff_compare/tests/` |
| `dashboards/energie_tarifvergleich.yaml` | `/config/dashboards/energie_tarifvergleich.yaml` |

`energy_tariff_compare/data/` legt die Integration selbst an.

Öffentliches GitHub: `tariffs.example.yaml` nach `tariffs.yaml` kopieren und Entity-IDs anpassen.

## 2. `configuration.yaml`

Den Block aus `snippets/configuration.yaml.fragment` mergen:

- `recorder.exclude` (Pi 3 / SD-Karte)
- `lovelace.dashboards.energie-tarifvergleich` → YAML-Dashboard in der Sidebar

**Kein** Top-Level-Schlüssel `energy_tariff_compare:`.

## 3. Core-Neustart

Python-Custom-Components und neue Sensoren brauchen einen **Core-Neustart**, nicht nur „Tarife neu laden“.

## 4. Integration anlegen

Einstellungen → Geräte & Dienste → Integration hinzufügen → **Energie & Tarifvergleich**.

Dienste danach:

- `energy_tariff_compare.collect_now`
- `energy_tariff_compare.shift_month`
- `energy_tariff_compare.reload_tariffs` (nur YAML-Preise; Entity-Wechsel → Core-Neustart)

## 5. Abhängigkeiten in Home Assistant

Bereits vorhandene Integrationen, die dieses Projekt *liest*:

- Nord Pool (GER, EUR)
- Discovergy / Inexogy Grid-Import `total_increasing`
- Optional Octopus Energy Germany (nur Kontroll-Preis, Rechnung kommt aus YAML)
- Optional Tesla Wall Connector, Anker Solix (Anzeige, keine Steuerung)

## 6. Historische Zählerwerte

Live-Zählung startet bei der ersten Baseline. 15-Minuten-Historie aus Inexogy-CSV:

1. Home Assistant **stoppen**
2. CSV nach `/config/energy_tariff_compare/imports/`
3. `python3 /config/energy_tariff_compare/scripts/import_inexogy.py` **auf dem HA-Dateisystem**, nicht über Samba gegen die laufende DB

## 7. Nach dem ersten Start

- Erste Viertelstunde: Baseline, noch 0 kWh
- Wallbox: Baseline der Lebensdauer-kWh, Kosten ab der nächsten abgeschlossenen Viertelstunde
- Perfekt: erst nach einem **vollständigen** Tag (alle fälligen Slots mit kWh und Spot)
