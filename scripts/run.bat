@echo off
REM Run Operator Board with the system Python (installs requirements on first run).
cd /d "%~dp0.."
python -c "import PySide6" 2>NUL || python -m pip install -r requirements.txt
python -m operator_board %*
