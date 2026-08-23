# Installations- und Einrichtungsanleitung

Diese Anleitung führt Schritt für Schritt durch die Einrichtung auf **Home Assistant OS**. Alle Pfadangaben beziehen sich auf das Wurzelverzeichnis `/config`.

---

## 1. Dateien auf das Home-Assistant-System kopieren

Kopiere die Dateien und Ordner aus dem Repository in deine Home-Assistant-Installation:

| Quelle im Repository | Zielverzeichnis auf Home Assistant |
|---|---|
| `custom_components/energy_tariff_compare/` | `/config/custom_components/energy_tariff_compare/` |
| `energy_tariff_compare/tariffs.example.yaml` | `/config/energy_tariff_compare/tariffs.yaml` *(kopieren & anpassen)* |
| `energy_tariff_compare/scripts/` | `/config/energy_tariff_compare/scripts/` |
| `energy_tariff_compare/tests/` | `/config/energy_tariff_compare/tests/` |
| `dashboards/energie_tarifvergleich.yaml` | `/config/dashboards/energie_tarifvergleich.yaml` |

> Der Unterordner `/config/energy_tariff_compare/data/` für die SQLite-Datenbank wird von der Integration beim ersten Start automatisch angelegt.

---

## 2. Tarife und Sensoren konfigurieren (`tariffs.yaml`)

Kopiere `tariffs.example.yaml` zu `tariffs.yaml` und passe die Sensor-Entity-IDs an dein System an:

```yaml
entities:
  grid_import: sensor.grid_import_energy             # Dein Smart-Meter-Sensor (total_increasing)
  grid_export: sensor.grid_export_energy             # Optional: Einspeisung
  nordpool_current: sensor.nord_pool_ger_current_price # Nord Pool GER Spotpreis
  nordpool_next: sensor.nord_pool_ger_next_price
  octopus_price: sensor.octopus_electricity_price    # Optional: Kontrollsensor
  tesla_energy: sensor.tesla_wall_connector_energy   # Optional: Wallbox-Lebensdauer-kWh
```

Passe in der Datei die gesetzlichen Umlagen, Netzbetreiber-Zeitfenster (z. B. Westnetz NT/ST/HT) sowie deine Arbeitspreise und Grundgebühren an.

---

## 3. `configuration.yaml` anpassen

Füge den Inhalt aus `snippets/configuration.yaml.fragment` in deine `/config/configuration.yaml` ein:

1. **Recorder-Optimierung:** Rauschige Leistungs- und Spannungssensoren ausschließen, um SD-Karte und SQLite schlank zu halten.
2. **Dashboard-Registrierung:** Bindet das YAML-Dashboard in die Home-Assistant-Seitenleiste ein:
   ```yaml
   lovelace:
     mode: storage
     dashboards:
       energie-tarifvergleich:
         mode: yaml
         title: Energie & Tarifvergleich
         icon: mdi:flash-auto
         show_in_sidebar: true
         filename: dashboards/energie_tarifvergleich.yaml
   ```

> **Wichtig:** Trage **kein** `energy_tariff_compare:` als Schlüssel in die `configuration.yaml` ein.

---

## 4. Home Assistant Core neu starten

Nach dem Kopieren von Custom Components und der Anpassung der `configuration.yaml` muss ein **vollständiger Neustart des Home Assistant Core** durchgeführt werden (*Entwicklerwerkzeuge → YAML → Neu starten*). Ein reines Neuladen der YAML-Konfiguration reicht nicht aus.

---

## 5. Integration in der Benutzeroberfläche hinzufügen

1. Navigiere zu: **Einstellungen → Geräte & Dienste → Integration hinzufügen**.
2. Suche nach **Energie & Tarifvergleich** und füge die Integration hinzu.
3. Die Sensoren (z. B. `sensor.tarifvergleich_jetzt_heat_ct`, `sensor.tarifvergleich_monat_heat_eur`, etc.) werden automatisch registriert.

### Verfügbare Dienste (*Entwicklerwerkzeuge → Dienste*):
- `energy_tariff_compare.collect_now`: Erzwingt sofortiges Buchen des letzten Slots.
- `energy_tariff_compare.reload_tariffs`: Lädt Preis- und Fensteränderungen aus der `tariffs.yaml` im laufenden Betrieb neu (bei Entity-ID-Änderungen ist ein Core-Neustart erforderlich).
- `energy_tariff_compare.shift_month`: Erlaubt das Durchschalten historischer Monatsansichten.

---

## 6. Historischer Datenimport (Optional)

Die Live-Erfassung startet ab dem Moment der ersten Baseline. Um historische 15-Minuten-Verbrauchsdaten aus einem Inexogy/Discovergy-CSV-Export nachzupflegen:

1. Home Assistant **vollständig stoppen**.
2. CSV-Datei in den Ordner `/config/energy_tariff_compare/imports/` legen.
3. Auf dem Home-Assistant-System (SSH/Terminal) ausführen:
   ```bash
   python3 /config/energy_tariff_compare/scripts/import_inexogy.py --replace-live
   ```
4. Home Assistant wieder starten.

---

## 7. Verhalten nach dem ersten Start

- **Erste Viertelstunde:** Dient als Zählerstand-Baseline (noch 0 kWh).
- **Wallbox:** Erfasst den Startwert der Lebensdauer-kWh; Ladekosten werden ab dem nächsten abgeschlossenen 15-Minuten-Slot berechnet.
- **Perfekt-Tarif:** Wird täglich rückwirkend berechnet, sobald alle 96 (bzw. 92/100 bei DST) Slots eines Kalendertags vollständig vorliegen.

