# GitHub-Repository & Synchronisation

Hinweise zur Verwaltung und Veröffentlichung dieses Repositories.

---

## 1. Veröffentlichungs- und Synchronisations-Workflow

Das lokale Verzeichnis dient als sauberer Git-Snapshot für das Repository:

```bash
cd energie-tarifvergleich-github-2026-08-23

# Vor dem Commit prüfen:
git status
```

---

## 2. Checkliste vor jedem Push (Datenschutz & Integrität)

Vor jedem Commit / Push sicherstellen, dass keine privaten Daten enthalten sind:

- [x] **Keine SQLite-Datenbanken:** `energy.sqlite`, `*.sqlite-wal`, `*.sqlite-shm`
- [x] **Keine Zähler-CSVs oder Rechnungen:** Keine Inexogy-/Discovergy-CSVs oder Stromrechnungs-PDFs
- [x] **Keine echten Zählernummern:** In `tariffs.example.yaml` nur Platzhalter verwenden (`tariffs.yaml` wird ignoriert)
- [x] **Keine Home-Assistant-Interna:** Keine `secrets.yaml` oder `.storage/`-Inhalte
- [x] **Keine Prompt-/Notiz-Dateien:** Lokale `.txt`-Dateien werden per `.gitignore` ignoriert

---

## 3. Empfohlene GitHub-Repository-Einstellungen

Wenn das Repository auf GitHub auf **Public** gesetzt wird:

1. **Issues deaktivieren:** Unter *Settings → General → Features* das Häkchen bei **Issues** entfernen, um Support-Tickets zu vermeiden.
2. **Lizenz & Forks:** Das Projekt steht unter der [MIT Lizenz](LICENSE). Andere Entwickler können das Repository frei forken und an ihr eigenes Setup anpassen.

