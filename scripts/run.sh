#!/usr/bin/env bash
# Run Operator Board with the system Python (installs requirements on first run).
cd "$(dirname "$0")/.."
python3 -c "import PySide6" 2>/dev/null || python3 -m pip install -r requirements.txt
exec python3 -m operator_board "$@"
