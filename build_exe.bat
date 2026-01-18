@echo off
setlocal
cd /d %~dp0
if "%CONDA_DEFAULT_ENV%"=="env-repair" (
  python build_exe.py
) else (
  python build_exe.py --conda-env env-repair
)
