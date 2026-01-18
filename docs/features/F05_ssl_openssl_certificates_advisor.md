# F05 – SSL/OpenSSL/CA-Zertifikate Diagnose (Advisor)

## Problem
Wenn `ssl`/OpenSSL nicht funktioniert, kann conda keine HTTPS Downloads mehr. Symptome:
- `SSLError: Can't connect to HTTPS URL ...`
- `SSL module is not available`
- Zertifikatsfehler (corporate proxy, missing certifi)

Oft ist das kein "Env package" Problem, sondern Activation/PATH/Cert-Store.

## Ziel
env-repair soll diese Fehlerbilder erkennen und **konkrete** Anleitungen geben (und nur sehr konservativ automatisch aendern).

## CLI
- `env-repair diagnose-ssl [--env <name|path>|--base] [--json]`
- optional: `env-repair fix-ssl --base [--yes]` (nur wenn conda laeuft)

## Implementierung
### Diagnose-Schritte
- `python -c "import ssl; print(ssl.OPENSSL_VERSION)"` im Ziel-env (wenn python vorhanden)
- `conda info --json` und channel/proxy settings ausgeben
- PATH-Check (Windows): fehlen `<env>\\Library\\bin` etc.
- Heuristik: wenn `certifi` fehlt -> Hinweis/optional install

### Fix (konservativ)
- Vorschlag: Shell neu oeffnen / conda init / Anaconda Prompt
- optional (nur mit `--yes`):
  - `mamba install -n base openssl ca-certificates certifi -y` (oder `conda install ...`)

## Testplan
- Unit: Pattern-Matcher fuer typische SSL Error Strings
- Manual: simulate SSL errors via sample logs
