# F02 – Cache-Korruption / CondaVerificationError / SafetyError

## Problem
Conda/Mamba verwenden einen zentralen Package-Cache (`pkgs/`). Korruption (sha256 mismatch, SafetyError, CondaVerificationError) fuehrt dazu, dass Installationen scheitern – oft in mehreren Environments.

Typische Symptome:
- `CondaVerificationError: ...` (Hash/Size mismatch)
- `SafetyError: ...`
- wiederholte Download/Extract Fehler bei denselben Paketen

## Ziel
env-repair soll:
1) diese Fehlerbilder erkennen (aus Tool-Output und/oder conda logs)
2) einen konservativen Repair-Plan ausfuehren:
   - zuerst sichere `conda clean` Varianten
   - dann gezieltes Entfernen defekter Cache-Eintraege
   - optional aggressiver Modus mit grossen Warnhinweisen

## CLI / UX
### Neue Optionen
- `env-repair cache-check [--env <name|path>] [--json]`
- `env-repair cache-fix [--env <name|path>] [--level safe|targeted|aggressive] [--yes]`

Defaults:
- `cache-fix` ohne `--yes` soll **nur planen** (dry-run output)

## Implementierungsskizze
### Wo im Code
- `env_repair/conda_ops.py`: neue Funktionen `conda_clean(...)`, `find_conda_pkgs_dir(...)`
- `env_repair/subprocess_utils.py`: capture + error pattern extraction
- `env_repair/doctor.py`: Action orchestration + report

### Erkennung
- Pattern-Matcher ueber stderr/stdout von conda/mamba Aktionen:
  - `CondaVerificationError`
  - `SafetyError`
  - `sha256` / `checksum` / `mismatch`
- Optional: Nutzer kann einen Log-String uebergeben (spaeter): `--logfile`

### Fix-Levels
#### Level: safe
- `mamba clean --index-cache -y` (Fallback: `conda clean ...`)
- optional `mamba clean --tempfiles -y`

#### Level: targeted
- Extrahiere betroffene Paketnamen/Build-Strings aus Fehltext (best effort)
- Entferne passende Eintraege aus `<pkgs_dir>/<name>-<ver>-<build>` + ggf. `.tar.bz2`/`.conda`
  - dann reinstall (nur wenn `--env` gesetzt):
  - `mamba install -p <env> --force-reinstall <pkgs> -y` (oder `conda install ...`)

#### Level: aggressive
- `mamba clean --packages -y` oder `mamba clean --all -y`
- **Warnung**: kann bei symlink-basierten Envs Nebenwirkungen haben

### Report
- `actions: [{type: cache_fix, level: safe|targeted|aggressive, removed: [...], ok: bool}]`
- falls nur geplant: `planned_actions`

## Safety / Warnings
- `--packages/--all` nur mit `--yes` ausfuehren.
- Immer klar kommunizieren, wenn Cache global wirkt (mehr als ein env).

## Testplan
### Unit-Tests
- Errortext-Parser:
  - extrahiert Paketnamen/Build (wenn vorhanden)
  - erkennt safe/targeted triggers

### Integration (manuell)
- Simuliere Fehlertext (weil echte Korruption schwer reproduzierbar):
  - `env-repair cache-check --debug` mit injected sample logs (falls ihr dafuer einen hidden option einbaut)

### Manuell realistisch
- in einem throwaway conda-install:
  - manipuliere eine Datei in pkgs cache
  - versuche install
  - dann `env-repair cache-fix --level targeted --yes`
