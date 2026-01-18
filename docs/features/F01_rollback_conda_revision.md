# F01 – Rollback auf Conda Revision ("undo")

## Problem
Nach einem fehlgeschlagenen `conda/mamba install` oder einem ungluecklichen `update --all` ist das Environment kaputt (Import-Errors, Solver-Zustand, ABI-Brueche), aber die Dateistruktur ist noch konsistent genug, dass Scan-Heuristiken (dist-info/pyd/artefacts) nicht greifen.

Conda fuehrt eine Historie der Environment-Aenderungen (Revisions) und erlaubt ein Zurueckrollen auf eine fruehere Revision.

## Ziel
env-repair soll ein **sicheres Rollback** auf eine fruehere Conda-Revision anbieten, inklusive Snapshot/Rescue und Post-Check.

## CLI / UX
### Neue Optionen
- `env-repair rollback --env <name|path> [--to latest|prev|<N>] [--dry-run]`

Alternativ (wenn ihr keine Subcommands wollt):
- `env-repair --env X --rollback latest|prev|<N> [--dry-run]`

### Exit-Codes
- `0`: Rollback erfolgreich, Post-Scan ohne Errors
- `1`: Rollback erfolgreich, Post-Scan mit Warnings
- `2`: Rollback fehlgeschlagen oder Pre-Checks fehlgeschlagen

## Implementierungsskizze
### Wo im Code
- `env_repair/cli.py`: Command/Args und Routing
- `env_repair/conda_ops.py`: neue Funktionen fuer revisions/rollback
- `env_repair/doctor.py`: Orchestrierung (Snapshot -> Action -> Rescan)

### Schritte
1) **Manager bestimmen**
   - Rollback wird initial nur fuer `conda` und `mamba` angeboten.
   - Wenn `micromamba`: informative Meldung + Anleitung (oder spaeter separate Umsetzung).

2) **Revisions abrufen**
   - `mamba list --revisions -p <env>` (oder `conda list --revisions ...`)

3) **Zielrevision bestimmen**
   - `latest`: hoechste vorhandene Revision (meist "current") -> nicht sinnvoll
   - `prev`: aktuelle - 1
   - `<N>`: exakte Revision

   Tipp: robustes Parsen: letzte Zeile ist meist die aktuelle Revision.

4) **Snapshot / Rescue**
   - vor Rollback: vorhandenen Snapshot-Mechanismus aus `doctor.py` nutzen

5) **Rollback ausfuehren**
   - `mamba install -p <env> --revision <N> -y` (oder `conda install ...`)

6) **Post-Check**
   - `env_repair.scan.scan_env()` erneut
   - Ergebnis in Report aufnehmen: `actions: [{type: rollback, from: X, to: N, ok: bool}]`

## Safety / Warnings
- Rollback kann Pakete entfernen/ersetzen; daher immer Snapshot.
- Wenn Rollback fehlschlaegt: keine weiteren automatischen Fixes ausfuehren, sondern klare Anleitung.

## Testplan
### Unit-Tests
- Parser fuer `conda list --revisions` Output:
  - JSON-Fall
  - Text-Fall
  - Edge: nur 1 Revision

### Integration-Tests (manuell oder CI optional)
1) Env erstellen
   - `mamba create -n t_rev python=3.11 -y`
   - `mamba install -n t_rev requests -y`
2) bewusst brechen
   - z. B. `mamba install -n t_rev "openssl<3" -y` (irgendwas, das Nebenwirkungen hat)
3) Rollback
   - `env-repair rollback --env t_rev --to prev`
4) Validierung
   - `python -c "import requests"` im Env
   - `env-repair --env t_rev --json` zeigt rollback action + weniger issues

### Negative Tests
- micromamba only -> informative Meldung
- Revision out of range -> exit 2 + message
