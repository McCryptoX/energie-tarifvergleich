# AGENTS.md — Energie- & Tarifvergleich für Home Assistant

> **Canonical Briefing for AI Assistants**  
> *(ChatGPT / OpenAI Codex, Google Antigravity / Gemini, Anthropic Claude, xAI Grok, Cursor, Copilot)*

Diese Datei dient als **verbindlicher Leitfaden für KI-Assistenten**, die dieses Repository analysieren, erweitern, testen oder forken. Sie beschreibt die Architektur, mathematische Invarianten, Sicherheitsgrenzen und Testprozesse.

---

## 1. Systemübersicht & Kernprinzipien

* **Zweck:** Hochpräziser 15-Minuten-Stromtarifvergleich und Schattenabrechnung in Home Assistant (Dynamische Tarife wie Tibber, §14a EnWG Modul 3, Time-of-Use wie Octopus Heat, Festpreise und theoretisches Optimum *Perfect*).
* **Zielumgebung:** Home Assistant OS (z. B. auf Raspberry Pi 3 oder Mini-PCs).
* **Datenbank:** SQLite (`energy.sqlite`) im ressourcenschonenden WAL-Modus.
* **Architektur:** Reine Lese- und Simulationsintegration — **niemals externe Hardware (Wallboxen, Batteriespeicher, Wechselrichter, Wärmepumpen) aktiv steuern.**

---

## 2. Hard Rules & Architektur-Invarianten

1. **Abrechnungsgrundlage:**  
   Die Tarifberechnung basiert **ausschließlich auf dem gemessenen Netzbezug** (*Grid Import*) des Smart Meters (z. B. Discovergy / Inexogy / Pulse). PV-Erzeugung oder Hausverbrauch fließen nicht in die Tarifkosten ein.
2. **15-Minuten-UTC-Raster:**  
   Verbrauch und Spotpreise werden minutengenau auf geschlossene UTC-Slots gebucht (`00:00`, `00:15`, `00:30`, `00:45`).
3. **Zeitzone & Sommer-/Winterzeit (DST):**  
   Zeitzone ist `Europe/Berlin`. Ein vollständiger Tag besteht aus:
   * **96 Slots** an normalen Tagen
   * **92 Slots** am Frühling-DST-Umschalttag (23 Stunden)
   * **100 Slots** am Herbst-DST-Umschalttag (25 Stunden, inkl. Fold-Behandlung)
4. **Nord Pool Börsenpreise:**  
   Börsenstrompreise werden einheitlich von `EUR/MWh` durch 1000 in **`EUR/kWh`** umgerechnet.
5. **Fehlende Börsenpreise (Spot Outage):**  
   Sind für ein Intervall keine Börsenpreise vorhanden, werden dynamische Tarife für dieses Intervall und den Tag strikt als `None` / `unknown` geführt (keine verfälschten Teilsummen). Feste Tarife (Heat/Fix) bleiben als Zahl berechenbar (`_sum_if_complete`).
6. **Theoretisches Optimum (*Perfect*):**  
   *Perfect* sortiert die gemessenen 15-Minuten-kWh-Blöcke auf die günstigsten Zeitfenster **desselben Tages** um (kein unphysikalisches Verschieben der gesamten Tageslast in eine einzige Viertelstunde).
   * **Invariante C2:** Der *Perfect*-Ist-Vergleichswert muss exakt denselben Satz vollständiger Tage wie der *Perfect*-Potenzialwert verwenden (`paired_m3`, `paired_flat`).
7. **A3-Tesla-Invariante (Wallbox Lifetime-kWh):**  
   * Wallbox-Kosten = Lade-kWh × Slot-Arbeitspreis (reine Obergrenze, keine Rechnungslegung).
   * `last_tesla` und Zähler-Metadaten dürfen nur vorrücken, wenn das Delta auf einer gültigen Intervallzeile gebucht wurde oder es die erste Baseline ist.
   * Bei mehrfachen Intervall-Lücken (> 20 Min.) wird das Delta in `meta.tesla_pending_kwh` zwischengespeichert und auf dem nächsten gültigen 15-Minuten-Slot nachgebucht (*Flush*).
8. **Sensor-Integrität (Invariante B6):**  
   Der Sensor `sensor.tarifvergleich_modul3_ct` muss zwingend erhalten bleiben.
9. **Leichtgewichtige Systemarchitektur:**  
   Keine schweren Drittanbieter-Erweiterungen (kein InfluxDB, kein Grafana, kein MariaDB, kein Node-RED). Nur Home Assistant Core, SQLite und Mushroom / mini-graph-card im Dashboard.

---

## 3. Repository-Struktur

```text
├── custom_components/
│   └── energy_tariff_compare/          # Home Assistant Custom Component
│       ├── const.py                    # Konstanten, IDs, Migrationsschlüssel
│       ├── tariffs.py                  # Tarifberechnungslogik, Zeitfenster & Validierung
│       ├── store.py                    # SQLite Speicher- & Migrationslogik
│       ├── collector.py                # 15-Min-Erfassung, Lückenreparatur & Aggregate
│       └── sensor.py                   # Home Assistant Sensoren & Registry-Migration
├── dashboards/
│   └── energie_tarifvergleich.yaml     # Lovelace UI Dashboard (3 Tabs: Übersicht, Verlauf, Details)
├── energy_tariff_compare/
│   ├── tariffs.example.yaml            # Vorlage für eigene Stromtarife
│   ├── scripts/
│   │   └── import_inexogy.py           # Importskript für historische Smart-Meter-CSVs
│   └── tests/                          # 9 eigenständige Python-Test-Suites
└── README.md, INSTALL.md, ...          # Deutsche Dokumentation & Setup-Anleitungen
```

---

## 4. Unterstützte Tarif-Modelle

| ID | Modell | Typ | Beschreibung |
|---|---|---|---|
| `octopus_heat` | Time-of-Use | `fix_all_in` | Referenztarif (z. B. Octopus Heat mit Standard-, Niedrig- und Hochpreis-Fenstern) |
| `octopus_heat_loyalty` | Time-of-Use | `fix_all_in` | Hypothetischer Folgetarif mit Loyalty-Konditionen |
| `fix_tarif` | Festpreis | `fix_all_in` | Klassischer Festpreistarif (Arbeitspreis + Grundpreis) |
| `dynamic` | Dynamisch Börse | `dynamic_retail` | Spotpreis + Steuern, Umlagen und Versorger-Aufschlag (Tibber-Logik) |
| `dynamic_modul3` | Dynamisch + §14a | `dynamic_retail` | Dynamischer Tarif kombiniert mit Westnetz Modul 3 (NT/ST/HT-Netzentgelte) und §14a-Pauschale |
| `dynamic_perfect` | Optimum | `dynamic_perfect` | Rechnerisches Optimum bei freier Lastverschiebung am selben Tag |

---

## 5. Tests & Verifikation

Vor jedem Commit oder Pull Request **müssen alle 9 Test-Suites fehlerfrei durchlaufen**:

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
python3 test_incomplete_prices.py
```

---

## 6. Verhaltensregeln für KIs

1. **Keine sensiblen Daten committen:** Niemals Zählernummern, Passwörter, Tokens, SQLite-Datenbankdateien (`*.sqlite`) oder private Messwert-CSVs ins Git schreiben.
2. **Idempotente Migrationen:** Änderungen an Datenbanktabellen in `store.py` müssen immer rückwärtskompatibel und idempotent sein.
3. **Dokumentation:** Die offizielle Projektdokumentation wird primär auf **Deutsch** gepflegt.
4. **Code-Qualität:** Änderungen an asynchronen Methoden in Home Assistant müssen benannte Callback-Funktionen nutzen (`async_call_later`, `async_track_time_change`), niemals blockierende Worker-Threads im Event-Loop blockieren.
