# env-repair – improvements / offene Punkte

> Checklist – zum Abhaken.  
> (Dateiname absichtlich: `improvments.md` wie im Chat erwähnt.)

## Aktueller Stand (neu)

- [x] **One-shot Workflow priorisiert:** `env-repair one-shot --env <env> -y` als empfohlener erster Lauf in der Doku ganz oben.
- [x] **Lokale Builds schützen:** `verify-imports --fix` überspringt lokale/manual Installationen aus `direct_url=file://...`, wenn es kein conda-managed Äquivalent gibt (z. B. custom Wheels).
- [x] **Python-Pin Solver-Fallback:** Bei `--force-reinstall` + Python-Pin-Konflikt wird automatisch ein Retry ohne `--force-reinstall` versucht (ermöglicht kompatible Upgrades wie `altair`).

## itest / QA

- [x] **Neues itest-Szenario:** `verify-imports` soll einen echten Import-Fehler finden (z.B. Datei in `site-packages` gelöscht) und der Run soll **erwartet fehlschlagen** (rc=1) bzw. das als „ok“ werten.
- [x] **itest Runner:** Pipeline-Steps sollen erwartete Returncodes erlauben (z.B. `ok_rc: [0,1]` oder `allow_fail: true` für `verify-imports`-Scans, die absichtlich Fehler detektieren).
- [ ] **S05_INCONSISTENT_SAFE umsetzen** (aus `itest/plan.md`): reproduzierbare „inconsistency/solver friction“ provozieren + Diagnose + `fix-inconsistent --level safe` verifizieren.
- [ ] **itest Runner:** `poison_corrupt_conda_meta` optional auch für `--use-names` unterstützen *oder* klar in Szenarien/Docs einschränken.
- [x] **itest Reporting:** `itest/scripts/summarize.py` optional automatisch nach einem Run ausführen (oder als separater `--summarize` Flag im Runner).

- [ ] **Neues itest-Szenario:** DLL-Import-Fehler auf Windows (z.B. `ImportError: DLL load failed ...`) reproduzierbar erzeugen und via `verify-imports` erkennen.
  - Ansatz: absichtlich eine benötigte DLL im env-`Library/bin` „wegbewegen“ (Backup) und Import (z.B. numpy) prüfen.
  - Quelle/Begründung: Conda Troubleshooting „NumPy MKL library load failed“ (PATH/DLL precedence).

- [ ] **Neues itest-Szenario:** `PYTHONPATH`/user-site Shadowing (Conda sagt installiert, Import schlägt fehl) – sicher & deterministisch testen.
  - Ansatz: per `mamba run` einen kontrollierten `PYTHONPATH` setzen, der ein Fake-`requests`/`ssl` in den Vordergrund bringt; `verify-imports` muss knallen.
  - Quelle: Conda Troubleshooting „package installed but appears not to be“ (PYTHONPATH/PYTHONHOME/user-site).

- [ ] **Neues itest-Szenario:** Clobber/overlapping files (Installation schlägt wegen Dateiüberschneidung fehl) + `diagnose-clobber` testen.
  - Ansatz: ein minimales künstliches `clobber.log` Fixture + `env-repair diagnose-clobber --logfile ...` sollte Konfliktpfade ausgeben.
  - Quelle: conda install `--clobber` Dokumentation.

## verify-imports (Feature/UX)

- [ ] Output transparenter machen: „Found N distributions, checking M unique import targets“ (v.a. wegen Dedup/RECORD-Parsing).
- [ ] Optional: Timeout/Blacklist-Mechanik weiter ausbauen (z.B. separate Behandlung für Timeouts vs. echte ImportErrors).
- [ ] **verify-imports:** Bessere Klassifikation/Hinting für häufige Root-Causes:
  - DLL load failed → Hinweis „env aktivieren / PATH / System32 DLL precedence“ (Conda Troubleshooting)
  - ModuleNotFoundError bei Submodulen (wie `requests.api`) → Hinweis „package files missing → reinstall“
