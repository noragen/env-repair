@echo off
call python -m pip install -U build twine
call python -m build
call python -m twine upload dist\*