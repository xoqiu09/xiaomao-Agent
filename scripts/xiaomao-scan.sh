#!/bin/sh
# User-level scan entrypoint for launchd. Rules only — never loads a model.
# Legacy LaunchAgent argv may still pass a project name; ignore it and scan
# every authorized project. New trees are never auto-added.
set -e
export PYTHONPATH="/Users/xiuqiu/WorkSpace/xiaomao-Agent/src"
export XIAOMAO_HOME="${XIAOMAO_HOME:-$HOME/Library/Application Support/Xiaomao}"
exec /Users/xiuqiu/.local/bin/python3.11 -m xiaomao --home "$XIAOMAO_HOME" scan
