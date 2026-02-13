# itest Plan

## Prinzip
Wir testen `env-repair` wie ein Nutzer es tun würde:

1) Env erstellen (mamba)
2) Fehler gezielt provozieren
3) env-repair laufen lassen (Scan + Fix + Feature-Commands)
4) Verifikation (vorher/nachher)
5) Report (per-run) + Summary
6) Cleanup

## Szenarien (v1)

### S01_DUP_DIST_INFO (P0 / Smoke)
- Provoziert: duplicate `.dist-info` Ordner.
- Erwartung: `env-repair --fix` entfernt/normalisiert Duplikate, danach Scan zeigt keine duplicates.

### S02_VERIFY_IMPORTS_FIX (P1)
- Provoziert: Import bricht (Datei im site-packages gelöscht) → `import requests` schlägt fehl.
- Erwartung: `env-repair verify-imports --full --fix` behebt den Import oder reduziert Broken-Imports.

### S03_CONDA_META_CORRUPT (P1)
- Provoziert: beschädigte `conda-meta/*.json` (z.B. python-*.json truncation).
- Erwartung: `env-repair --fix` erkennt Issue-Typen (`conda-meta-invalid-json`) und repariert via Force-Reinstall.

### S04_VERIFY_IMPORTS_BROKEN_IMPORT (P1)
- Provoziert: echter Import-Fehler (z.B. Datei in `site-packages` gelöscht) → `import requests` schlägt fehl.
- Erwartung: `env-repair verify-imports --full` findet den Fehler (Returncode i.d.R. **1** bei verbleibenden Failures).

### S05_INCONSISTENT_SAFE (P2)
- Provoziert: Inconsistency (solver friction).
- Erwartung: `diagnose-inconsistent` liefert Diagnose, `fix-inconsistent --level safe` nimmt minimal-invasive Schritte.

## Reporting
- `itest/runs/<timestamp>_<scenario>/report.json`
- `itest/runs/<timestamp>_<scenario>/report.md`
- `itest/reports/summary.json` + `summary.md`
