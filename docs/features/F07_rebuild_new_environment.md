# F07 – Rebuild in neues Environment (Export/Import Workflow)

## Problem
Manche Environments sind so vermischt (pip+conda, historische Artefakte, solver drift), dass In-Place Repair riskant ist.
Ein bewaehrter Ansatz: export -> neues Env -> verify.

## Ziel
env-repair soll einen gefuehrten Rebuild-Workflow anbieten, der moeglichst viel automatisiert, aber sicher bleibt.

## CLI
- `env-repair rebuild --env <name|path> --to <new_name|new_path> [--verify] [--yes]`

## Implementierung
### Wo
- `env_repair/cli.py`: command
- `env_repair/conda_ops.py`: wrappers fuer `conda env export/create`
- `env_repair/doctor.py`: orchestrierung (snapshot, create, verify)

### Schritte
1) Snapshot export (existierendes Feature verwenden)
2) `mamba env create -p <new> -f <snapshot.yaml>` (oder `conda env ...`)
3) Optional `--verify`: `env-repair --env <new> scan` (oder direkt internal scan)
4) Ergebnisreport: alt vs neu

### Optionen
- `--channels` vom aktuellen env uebernehmen (wenn export das nicht sauber enthaelt)
- `--no-builds` (wenn ihr spaeter eine vereinfachte export-variante wollt)

## Testplan
- Manual: kaputtes env -> rebuild -> verify
- Negative: Zielpfad existiert -> abort
