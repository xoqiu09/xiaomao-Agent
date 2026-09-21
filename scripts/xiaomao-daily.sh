#!/bin/sh
# User-level daily entrypoint for launchd. Local TZ 21:30.
# Calls the depth model only when `daily --scheduled` sees new disk evidence.
set -e
export PYTHONPATH="/Users/xiuqiu/WorkSpace/xiaomao-Agent/src"
export XIAOMAO_HOME="${XIAOMAO_HOME:-$HOME/Library/Application Support/Xiaomao}"
exec /Users/xiuqiu/.local/bin/python3.11 -m xiaomao --home "$XIAOMAO_HOME" daily --scheduled
