# Versionsverlauf (Changelog)

## 0.1.7 — 2026-08-23

- **Tesla Wall Connector Baseline-Sicherheit:** Wenn beim ersten Start noch kein Netzbezug vorliegt, rückt `last_tesla` nicht vor, ohne dass das Delta als ausstehend (`pending`) gebucht wird.
- **Tesla Zähler-Reset:** Offene ausstehende kWh (`tesla_pending_extra`) bleiben bei einem Zähler-Reset erhalten und werden mit dem neuen Zählerstand zusammen gebucht.
- **Null-Netzbezug-Slots:** Reparierte Slots mit 0 kWh Netzbezug addieren das Tesla-Delta, anstatt Werte zu überschreiben.
- **Fix-Tarif:** Hypothetischer Festpreistarif heißt überall **Fixer Tarif** (`fix_tarif`, Sensor `sensor.tarifvergleich_preis_fix`). Alte Spalten- und Registry-IDs werden beim Start umbenannt bzw. entfernt.
- **Ampel günstige Zeiten:** Anteil der heutigen Netz-kWh in Heat-NT (02–06 / 12–16) bzw. im günstigsten Drittel der Slots (Dynamisch, Modul 3), als Attribute an `sensor.tarifvergleich_heute`.
- **Einschaltfenster:** Nächste Heat-NT sowie günstigste zusammenhängende 1-/2-/3-Stunden-Blöcke (Dynamisch / Modul 3) aus gespeicherten Spotpreisen, nur Anzeige.

## 0.1.6 — 2026-08-23

- **A3-Invariante:** `last_tesla` und Metadaten rücken nur vor, wenn das Tesla-Delta auf einer Intervallzeile gebucht wurde oder es die allererste Baseline ist.
- **Mehrfaches Tesla-Delta:** Zusätzliches Delta auf geschützten Zeilen mit vorhandenen `tesla_kwh` wird aufaddiert statt verworfen.
- **Mehrintervall-Sicherheit:** Längere Lücken puffern das Tesla-Delta in `meta.tesla_pending_kwh` und buchen es auf dem nächsten regulären 15-Minuten-Slot (*Flush*).
- **Agenten-Dokumentation:** Vereinheitlichte Leitfäden in `AGENTS.md` und `GEMINI.md`.

## 0.1.5 — 2026-08-23

- **Tesla-Merge:** Lade-kWh werden auf nacherfasste/reparierte Slots gemergt.
- **YAML-Reload-Optimierung:** Neuberechnung von Preisen erfasst alle Intervalltage; Startup-Reprice läuft thread-sicher unter `collect_lock`.
- **Spot-Lücken:** Bei fehlenden Börsenpreisen bleiben dynamische Kosten für Monat/Jahr/Woche sauber als `unknown`/`None`, Heat bleibt exakt berechnet.
- **Ranking-Vollständigkeit:** Rangfolge für Monat/Jahr erfordert die volle Tagesabdeckung.
- **15-Minuten-Rasterprüfung:** Vollständigkeitsprüfung prüft das echte Zeitraster (92/96/100 Slots) statt reiner Slot-Anzahl.
- **Dashboard-Verbesserungen:** Neutrale Monatsauswertung; sichtbare Tab-Titel und optimierte Diagrammbereiche.

## 0.1.4 — 2026-08-23

- **Tesla Wall Connector Integration:** 15-Minuten-Lade-kWh aus Lifetime-Energie ab der ersten Baseline.
- **Reine Ladekosten:** Wallbox-Kosten = Slot-Arbeitspreis × Lade-kWh (ohne Grundpreisumlage).
- **Wochen-Aggregation:** Exakte Wochenauswertung von Montag bis Sonntag.
- **Lückenreparatur:** Schließt Ausfälle nach HA-Downtime automatisch über 5-Minuten-Statistiken des Recorders.
- **Preisverlauf-Sensor:** Neuer Sensor `sensor.tarifvergleich_modul3_ct`.
- **Dashboard:** Live-Karten für Anker Solix und Tesla Wall Connector, detaillierte Kostentabelle und separate 24h-Graphen.

## 0.1.3

- **Kostenaufteilung:** Getrennte Ausweisung von Arbeitspreis, Grundpreis und §14a-Reduzierung.
- **Perfekt-Tarif:** Mathematische Optimierung für Dynamisch und Dynamisch + Modul 3 für vollständige Tage.
- **Nord Pool Normalisierung:** Automatische Umrechnung von `EUR/MWh` in `EUR/kWh`.
- **UTC-Raster & Asynchronität:** Exakte Buchung geschlossener 15-Minuten-UTC-Slots mit Thread-Locks.
- **Tibber-Aufschlag:** Standardmäßiger Aufschlag von 2,15 ct/kWh brutto integriert.

