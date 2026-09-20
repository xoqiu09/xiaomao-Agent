#!/bin/sh
# Stop the user-level ollama started by user_serve_ollama.sh.
set -e
PIDFILE="/Users/xiuqiu/Library/Application Support/Xiaomao/ollama.pid"
if [ ! -f "$PIDFILE" ]; then
  echo "no pidfile"
  pkill -x ollama 2>/dev/null || true
  exit 0
fi
pid=$(cat "$PIDFILE")
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid" || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" || true
  fi
  echo "stopped $pid"
else
  echo "stale pid $pid"
fi
rm -f "$PIDFILE"
echo STOP_OK
