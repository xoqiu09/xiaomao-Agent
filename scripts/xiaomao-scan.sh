#!/bin/sh
# User-level scan entrypoint for launchd. Rules only — never loads a model.
# Legacy LaunchAgent argv may still pass a project name; ignore it and scan
# every authorized project. New trees are never auto-added.
set -e
PYTHON="${XIAOMAO_PYTHON:-/Users/xiuqiu/.local/bin/python3.11}"
export PYTHONPATH="$("$PYTHON" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[1] / "src")' "$0")"
export XIAOMAO_HOME="${XIAOMAO_HOME:-$HOME/Library/Application Support/Xiaomao}"
exec "$PYTHON" -m xiaomao --home "$XIAOMAO_HOME" scan
