#!/bin/sh
# Serve one official Ollama on loopback, models on the identity-checked volume.
# User-level only. Does not install a LaunchDaemon. Does not sudo.
set -e
BIN="/Volumes/LocalDevData/Xiaomao/bin/ollama"
MODELS="/Volumes/LocalDevData/Xiaomao/ollama"
LOGDIR="/Users/xiuqiu/Library/Application Support/Xiaomao/logs"
PIDFILE="/Users/xiuqiu/Library/Application Support/Xiaomao/ollama.pid"

if [ ! -x "$BIN" ]; then
  echo "ERROR: missing $BIN"
  exit 2
fi
if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: external volume not mounted; refuse to start"
  exit 3
fi
if [ ! -d "$MODELS" ]; then
  echo "ERROR: models dir missing $MODELS"
  exit 3
fi

mkdir -p "$LOGDIR"
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS="$MODELS"
export OLLAMA_NO_CLOUD=1
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=0

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "already running pid=$(cat "$PIDFILE")"
  exit 0
fi

nohup "$BIN" serve >>"$LOGDIR/ollama.out.log" 2>>"$LOGDIR/ollama.err.log" &
echo $! > "$PIDFILE"
sleep 1
echo "started pid=$(cat "$PIDFILE")"
echo "OLLAMA_HOST=$OLLAMA_HOST"
echo "OLLAMA_MODELS=$OLLAMA_MODELS"
echo "OLLAMA_NO_CLOUD=$OLLAMA_NO_CLOUD"
echo SERVE_OK
