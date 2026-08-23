# Energie- & Tarifvergleich für Home Assistant

Echtzeit- und Vergangenheits-Tarifvergleich für den Strom-Netzbezug in Deutschland im **15-Minuten-Raster**. Entwickelt für **Home Assistant OS**, optimiert für ressourcenschonenden Betrieb (z. B. auf einem **Raspberry Pi 3** ohne InfluxDB, Grafana, Node-RED oder MariaDB).

> **Reines Lese- und Analysesystem:** Diese Integration steuert weder die Wärmepumpe, noch die Wallbox, den PV-Speicher oder den Wechselrichter.

---

## 🎯 Was das Projekt tut

Das System erfasst alle 15 Minuten (Minute 00, 15, 30, 45 + 20 s Versatz) den abgeschlossenen UTC-Slot und berechnet parallel die Kosten für verschiedene Stromtarife auf Basis des echten Netzbezugs (z. B. Discovergy / Inexogy Smart Meter):

| Tarif-ID | Name / Typ | Beschreibung & Berechnungsbasis |
|---|---|---|
| `octopus_heat` | **Octopus Heat (Referenz)** | Echter Time-of-Use-Vertrag (NT / ST / HT), All-in brutto inkl. aller Umlagen, Steuern und Grundpreis. |
| `octopus_heat_loyalty` | **Octopus Heat Loyalty** | Hypothetisches Folgeangebot mit angepassten Arbeitspreisen. |
| `naturwerke_fix` | **Naturwerke Fix** | Hypothetischer klassischer Festpreistarif (fester Arbeitspreis + Grundpreis). |
| `dynamic` | **Dynamischer Tarif** | Börsenpreis (Nord Pool Spot GER) + Lieferantenaufschlag + Standard-Netzentgelt (flach) + gesetzliche Umlagen/Steuern 2026 + MwSt. |
| `dynamic_modul3` | **Dynamisch + §14a Modul 3** | Wie dynamisch, jedoch mit **zeitvariablen Netzentgelten der Westnetz** (NT / ST / HT) sowie pauschaler Grundpreisreduzierung nach **§14a EnWG (Modul 1)**. |
| `dynamic_perfect` | **Perfekt optimiert** | Nachträglich berechnetes, theoretisches Optimum für abgeschlossene Tage: Die gemessenen 15-Minuten-kWh-Blöcke werden auf die günstigsten Börsenpreis-Slots *desselben Tages* umsortiert. |

---

## 💡 Besonderheiten & Methodik

- **Abrechnung ausschließlich über Netzbezug:** PV-Erzeugung, Batteriespeicher und Wallbox-Verbrauch dienen der Erklärung des Hausverbrauchs, fließen aber nicht in die Zähler-Schattenrechnung ein.
- **Transparente Trennung:** Arbeitspreise (ct/kWh bzw. EUR) und Grundgebühren (EUR) werden strikt getrennt ausgewiesen. §14a-Gutschriften werden nicht mit dem Fixpreis vermischt.
- **Echte 15-Minuten-Präzision:** Volle Unterstützung von Sommer-/Winterzeit-Umstellungen (DST 92 / 96 / 100 Slots pro Tag).
- **Optionale Wallbox-Auswertung:** Verfolgt Lifetime-kWh des Tesla Wall Connectors und ermittelt die reinen Lade-Arbeitskosten je Tarif.
- **Automatische Lückenreparatur:** Schließt kurze Ausfälle nach HA-Neustarts über 5-Minuten-Statistiken des Recorders, damit Tage für den Tarifvergleich vollständig bleiben.

---

## 🏛 Architektur

```
Discovergy / Inexogy Zähler + Nord Pool Spot GER
               │
               ▼  alle 15 Minuten (Minute 00/15/30/45 + 20s)
    Lokale SQLite-Datenbank (energy_tariff_compare/data/energy.sqlite)
               │
               ▼  Tages-, Monats- und Jahresaggregate
    Home Assistant Sensoren & Lovelace Dashboard (3 Tabs)
```

- **Keine Cloud-Abhängigkeit:** Berechnungen laufen zu 100 % lokal auf deinem Home Assistant.
- **Nord Pool API:** Spotmarkt-Werte (`EUR/MWh`) werden automatisch auf `EUR/kWh` normiert.

---

## 📋 Voraussetzungen

- **Home Assistant OS** (Version 2026.x)
- **Offizielle [Nord Pool Integration](https://www.home-assistant.io/integrations/nordpool/)** (Bereich `GER`, Währung `EUR`)
- **Smart Meter Sensor** mit `state_class: total_increasing` für den Netzbezug (z. B. Discovergy / Inexogy)
- *Optional:* Tesla Wall Connector (Sensor für Lebensdauer-Energie), Anker Solix (Sensoren für die Live-Visualisierung)

---

## 🚀 Installation & Schnelleinstieg

Eine detaillierte Schritt-für-Schritt-Anleitung findest du in [INSTALL.md](INSTALL.md).

1. Ordner `custom_components/energy_tariff_compare/` nach `/config/custom_components/` kopieren.
2. Ordner `energy_tariff_compare/` nach `/config/energy_tariff_compare/` kopieren.
3. Datei `tariffs.example.yaml` als Vorlage nach `tariffs.yaml` kopieren und eigene Entity-IDs sowie Tarifdaten eintragen.
4. Dashboard-YAML aus `dashboards/energie_tarifvergleich.yaml` einbinden.
5. **Home Assistant Core neu starten** (damit die Python-Komponente geladen wird).
6. Unter *Einstellungen → Geräte & Dienste → Integration hinzufügen* die Integration **Energie & Tarifvergleich** aktivieren.

> **Wichtig:** Füge **kein** `energy_tariff_compare:` in die `configuration.yaml` ein. Die Konfiguration erfolgt über die UI-Integration und die `tariffs.yaml`.

---

## 🧪 Tests ausführen

Das Projekt verfügt über eine vollständige Test-Suite zur Verifizierung von Zeitfenstern, DST, Slot-Buchungen, Aggregationen und Reparaturlogik:

```bash
cd energy_tariff_compare/tests
python3 test_windows.py
python3 test_price_units.py
python3 test_collector_slots.py
python3 test_aggregates.py
python3 test_tesla_and_gaps.py
python3 test_async_callbacks.py
python3 test_spot_repair.py
python3 test_import_inexogy.py
```

---

## 📌 Projekt-Status & Forks

Dieses Projekt ist für ein konkretes Setup (Home Assistant OS auf Pi 3, Discovergy/Inexogy, Westnetz §14a Modul 3, Octopus Heat) maßgeschneidert, stabil im Einsatz und **in sich abgeschlossen (*feature-complete*)**.

- **Keine individuellen Feature-Requests:** Es werden keine zusätzlichen Netzbetreiber, andere Wechselrichter-Marken oder Steuerungsfunktionen eingebaut.
- **Forks ausdrücklich erwünscht:** Wenn du das Projekt für deine eigenen Tarife, andere Zählersysteme oder Steuerungslogiken anpassen möchtest, kannst du das Repository sehr gerne **forken** und frei weiterentwickeln!

---

## 📄 Lizenz

Veröffentlicht unter der [MIT Lizenz](LICENSE).  
*Dieses Projekt steht in keiner offiziellen Verbindung zu Octopus Energy, Tibber, Westnetz, Discovergy/Inexogy, Tesla oder Anker.*

