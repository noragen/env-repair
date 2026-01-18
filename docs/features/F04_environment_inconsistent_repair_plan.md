# F04 – "Environment is inconsistent" Repair-Plan

## Problem
Conda/mamba kann warnen: "The environment is inconsistent". Ursachen:
- abgebrochene Transaktionen
- gemischte Channels / solver edge cases
- teils defekte metadaten

Das Env kann noch starten, aber Updates/Installs werden fragil.

## Ziel
env-repair soll diesen Zustand erkennen und einen mehrstufigen Plan anbieten (von sehr sicher bis "rebuild").

## CLI
- `env-repair diagnose-inconsistent --env <name|path> [--json]`
- `env-repair fix-inconsistent --env <name|path> [--level safe|normal|rebuild] [--yes]`

## Implementierung
### Erkennung
- Suche nach typischen Warntexten in conda/mamba output
- Optional: harmloser Dry-Run, z. B. `mamba install -p <env> --dry-run python` (oder `conda install ...`) und output parsen

### Level safe
- `conda clean --index-cache`
- Rescan

### Level normal
- Pakete aus Warnung extrahieren (best effort)
- `mamba install -p <env> --force-reinstall <pkgs>` (oder `conda install ...`)
- Rescan

### Level rebuild
- Verweist auf F07 (rebuild workflow)

## Testplan
- Unit: Parser fuer inconsistent warnings
- Manual: install abbrechen -> warning -> fix safe/normal
