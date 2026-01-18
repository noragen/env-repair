# Contributing

## Constraints
- Use Windows CMD commands in docs/examples (no PowerShell).
- Keep changes focused; avoid destructive git operations unless requested.

## Dev setup
```bat
pip install -e .[dev]
python -m unittest discover -s tests -p "test_*.py"
```

## Notes
- This project intentionally avoids external runtime dependencies (stdlib only).
- Snapshot uses `mamba env export` / `conda env export` and writes YAML to a file.
