# Auf GitHub legen

Lokales Snapshot-Verzeichnis, bereit zum `git init`.

```bash
cd energie-tarifvergleich-github-2026-08-23
git init
# Für ein öffentliches Repo die Live-Konfiguration ersetzen:
cp energy_tariff_compare/tariffs.example.yaml energy_tariff_compare/tariffs.yaml
# Dashboard-Entity-IDs (Anker/Tesla/Zähler) in dashboards/ prüfen
git add .
git status   # keine .sqlite, keine CSV, kein .storage
```

Nicht committen:

- `energy.sqlite`
- Inexogy-/Spot-CSV unter `imports/`
- Account- und Zählernummern in `tariffs.yaml` (Beispiel verwenden)
- Home-Assistant-`secrets.yaml` / `.storage`
