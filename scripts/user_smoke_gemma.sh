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
echo "=== generate (keep_alive=0 num_ctx=8192) ==="
# Pin 8192: serve log may advertise a vram-based default_num_ctx of 262144.
curl -sS --max-time 180 http://127.0.0.1:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma4:12b","stream":false,"keep_alive":0,"options":{"num_ctx":8192,"num_predict":64},"messages":[{"role":"user","content":"用一句话说明你没有 shell。只输出一句中文。"}]}'
echo
echo "=== stop ==="
"$BIN" stop gemma4:12b || true
echo "=== ps ==="
"$BIN" ps || true
echo SMOKE_OK
