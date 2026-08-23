# Sicherheit und Datenschutz

## 1. Reines Lese-System (Read-Only)

Diese Integration führt **keinerlei Steuerungsbefehle** an Geräten aus:
- Keine Steuerung von Wärmepumpen (z. B. Vaillant)
- Keine Steuerung oder Drosselung von Wallboxen (z. B. Tesla Wall Connector)
- Keine Entlade- oder Ladesteuerung von Heimspeichern / Wechselrichtern (z. B. Anker Solix)

Alle angebundenen Entitäten werden ausschließlich passiv als Messwertgeber für die Verbrauchsberechnung und Visualisierung ausgelesen.

---

## 2. Schutz privater und sensibler Daten

Folgende Dateien und Verzeichnisse dürfen **nicht** in ein öffentliches Repository eingecheckt werden (in `.gitignore` hinterlegt):

- `energy_tariff_compare/data/energy.sqlite*` (Enthält dein 15-Minuten-Verbrauchsprofil)
- `energy_tariff_compare/imports/*.csv` (Zählerdaten-Exporte mit Zählernummern)
- `energy_tariff_compare/tariffs.yaml` (Kann Zähler- und Kundennummern enthalten – stattdessen `tariffs.example.yaml` committen)
- Home-Assistant-interne Dateien wie `.storage/` und `secrets.yaml`

---

## 3. Zugangsdaten & Passwörter

Die Integration speichert **keine Passwörter oder Zugangsdaten**. Alle Zugriffe auf Nord Pool, Discovergy, Octopus Energy oder Tesla laufen über die nativen Home-Assistant-Integrationen und verbleiben in der sicheren Schlüsselverwaltung von Home Assistant.

---

## 4. Systemsicherheit & Hardware-Schonung (Raspberry Pi 3)

- **Keine externen Datenbankserver nötig:** Die Datenhaltung erfolgt in einer lokalen SQLite-Datei im WAL-Modus (*Write-Ahead Logging*), um SD-Karten-Schreibzugriffe zu minimieren.
- **Kein SMB-Schreibzugriff bei laufendem HA:** Die Datenbank darf niemals über Netzlaufwerke (Samba/SMB) manipuliert werden, während Home Assistant aktiv ist.
- **Recorder-Exclude:** Empfohlene Filterung hochfrequenter Messwerte in der `configuration.yaml` schützt die Systemressourcen.

