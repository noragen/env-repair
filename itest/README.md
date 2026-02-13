# EnvRepair Integration Tests (itest)

Ziel: Reproduzierbare End-to-End Integrationstests für `env-repair`.

- Erstellt Test-Environments (Conda/Mamba) unter `itest/envs/` (oder optional per Name).
- Provoziert definierte Fehlerzustände.
- Führt `env-repair` aus (aus dem lokalen Repo `K:\env-repair`).
- Verifiziert, ob die Fehler behoben wurden.
- Schreibt Reports (JSON + Markdown).
- Räumt am Ende **nur** die Test-Envs aus `itest/envs/` ab.

## Quick start (Windows)

```bat
cd /d K:\env-repair\itest
python scripts\run_itest.py --list
python scripts\run_itest.py --scenario S01_DUP_DIST_INFO --keep-env
python scripts\run_itest.py --scenario S04_VERIFY_IMPORTS_BROKEN_IMPORT --keep-env

# optional: update summary
python scripts\run_itest.py --scenario S01_DUP_DIST_INFO --summarize
```

## Safety
- Standardmäßig werden Envs als **pfadbasierte Envs** unter `K:\env-repair\itest\envs\...` angelegt.
- Cleanup löscht nur diese Pfade.
- Alternativ können Szenarien auch `--use-names` nutzen (dann werden nur Names mit Prefix `envrepair_itest_` angelegt/gelöscht).
