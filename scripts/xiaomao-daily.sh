#!/bin/sh
# User-level daily entrypoint for launchd. Local TZ 21:30.
# Calls the depth model only when `daily --scheduled` sees new disk evidence.
set -e
PYTHON="${XIAOMAO_PYTHON:-/Users/xiuqiu/.local/bin/python3.11}"
export PYTHONPATH="$("$PYTHON" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[1] / "src")' "$0")"
export XIAOMAO_HOME="${XIAOMAO_HOME:-$HOME/Library/Application Support/Xiaomao}"
exec "$PYTHON" -m xiaomao --home "$XIAOMAO_HOME" daily --scheduled
