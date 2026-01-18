#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# If already inside the target conda env, don't force conda/mamba run.
if [[ "${CONDA_DEFAULT_ENV:-}" == "env-repair" ]]; then
  python build_exe.py
else
  python build_exe.py --conda-env env-repair
fi

