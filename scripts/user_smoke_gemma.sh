#!/bin/sh
# Smoke gemma4:12b from the identity-checked models dir. Unload after.
# Requires user-level ollama serve (scripts/user_serve_ollama.sh).
set -e
BIN="/Volumes/LocalDevData/Xiaomao/bin/ollama"
MODELS="/Volumes/LocalDevData/Xiaomao/ollama"
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS="$MODELS"
export OLLAMA_NO_CLOUD=1
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_KEEP_ALIVE=0

if [ ! -x "$BIN" ]; then
  echo "ERROR: missing $BIN"
  exit 2
fi
if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: external volume unmounted"
  exit 3
fi

echo "=== tags ==="
"$BIN" list
echo "=== generate (keep_alive=0) ==="
"$BIN" run --keepalive 0s gemma4:12b "用一句话说明你没有 shell。只输出一句中文。"
echo "=== stop ==="
"$BIN" stop gemma4:12b || true
echo "=== ps ==="
"$BIN" ps || true
echo SMOKE_OK
