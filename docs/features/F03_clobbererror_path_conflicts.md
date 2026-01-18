# F03 – ClobberError / Path-Conflicts Diagnose + Owner-Analyse

## Problem
`ClobberError` tritt auf, wenn Dateien kollidieren (zwei Pakete liefern denselben Pfad) oder Dateien im Prefix liegen, die conda nicht sauber zuordnen kann (z. B. durch pip/manual copy). Das blockiert Install/Update.

Typische Symptome:
- `ClobberError: ... path ...` (Dateipfad wird genannt)
- `The package ... cannot be installed due to a path conflict` (variiert je nach Tool)

## Ziel
env-repair soll:
- Clobber/Path-Conflict Fehler erkennen
- Konfliktpfade extrahieren
- **Owner** der Konfliktdatei bestimmen (wenn moeglich)
- einen konservativen Fix-Plan ausgeben (standardmaessig dry-run)

## CLI / UX
- `env-repair diagnose-clobber --env <name|path> [--json]`
- `env-repair fix-clobber --env <name|path> [--strategy suggest|remove_pip|reinstall_conda|clobber] [--yes]`

## Implementierungsskizze
### Wo im Code
- `env_repair/conda_ops.py`: helper fuer "dry-run install" um Clobber reproduzierbar zu erzeugen
- `env_repair/scan.py` oder neues Modul `env_repair/conflicts_clobber.py`: Parser + Owner lookup

### Diagnose: Pfade extrahieren
- Regex ueber Tool-Output:
  - Pfade mit `<env>` Prefix
  - Windows und POSIX Varianten

### Owner-Analyse (conda)
- In `conda-meta/*.json` nach `files` Liste suchen (nicht jede Meta hat sie, aber oft)
- Wenn vorhanden:
  - map `relative_path -> package` (name-version-build)
  - fuer Konfliktpfad: owner(s) ausgeben

### Owner-Analyse (pip)
- Wenn Datei unter `site-packages`:
  - heuristisch ueber `*.dist-info/RECORD` oder `importlib.metadata` (nur wenn im env python laeuft)

### Fix-Strategien
- `suggest` (default): nur Plan ausgeben
- `remove_pip`: wenn Owner als pip-package identifiziert -> `pip uninstall -y <pkg>`
- `reinstall_conda`: `mamba install -p <env> --force-reinstall <owner_pkg>` (oder `conda install ...`)
- `clobber`: nur als letzter Ausweg -> Hinweis auf `--clobber`/`path_conflict: clobber` (mit Warnung)

## Testplan
### Unit
- Parser: erkennt Pfade in unterschiedlichen Output-Formaten
- Owner map builder: conda-meta JSON ohne `files` => graceful degrade

### Integration (manuell)
- Repro:
  1) mamba env erstellen
  2) pip installiert Datei, die spaeter conda liefert
  3) mamba install triggert ClobberError
  4) env-repair diagnose-clobber zeigt Pfad + Owner
  5) fix-clobber --strategy remove_pip --yes behebt
