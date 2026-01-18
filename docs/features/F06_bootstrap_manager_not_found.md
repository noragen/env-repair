# F06 – Bootstrap: Manager nicht auffindbar (conda/mamba/micromamba)

## Problem
env-repair kann viele Fixes nur ausfuehren, wenn mindestens ein Manager verfuegbar ist.
Hauefige Ursachen:
- Tool nicht im PATH (Shell nicht initialisiert)
- falsches Terminal (Windows: nicht ueber Anaconda Prompt)
- mamba ist nicht installiert

## Ziel
- Eindeutige Diagnose: welche Manager sind verfuegbar, welche fehlen.
- Copy/Paste-faehige, OS-spezifische Hinweise zur Behebung.
- Wenn der Nutzer `--fix` nutzt und kein Manager verfuegbar ist: sauberer Abbruch mit klarer Meldung.

## CLI / UX
- Kein neues Kommando noetig. Erweiterung der bestehenden Ausgabe um Abschnitt **Manager**.
- Optional spaeter: `env-repair bootstrap --install-mamba` (explizit, nie automatisch).

## Implementierung (minimal-invasiv)
### Wo im Code
- `env_repair/discovery.py`: erweitertes Detect (`which` + relevante Env-Variablen)
- `env_repair/doctor.py`: Report-Feld `managers` (gefunden/fehlend)
- `env_repair/cli.py`: Ausgabe/JSON

### Report-Schema (Vorschlag)
```json
"managers": {
  "conda": {"found": true, "path": "..."},
  "mamba": {"found": false, "path": null},
  "micromamba": {"found": false, "path": null}
}
```

### Heuristiken fuer Hinweise
- Windows:
  - Hinweis auf Anaconda Prompt
  - `conda init powershell` bzw. `conda init cmd.exe` (nur als Vorschlag)
- Linux/macOS:
  - `conda init bash`/`zsh` bzw. Shell-Hook nutzen

## Testplan
### Unit
- discovery: PATH ohne Manager -> alle found=false
- discovery: PATH enthaelt nur micromamba -> korrektes Mapping

### Manuell
- T1: Terminal ohne conda init -> env-repair zeigt Hinweise
- T2: Anaconda Prompt -> env-repair erkennt conda korrekt
- T3: `--fix` ohne Manager -> Exit 2, keine Fix-Aktionen
