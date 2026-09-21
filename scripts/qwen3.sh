#!/bin/sh
# Interactive chat with the local default depth model (qwen3-coder:30b).
# Same idea as `hermes` / `claude`: type `qwen3` in any terminal.
# Does not go through the xiaomao CLI. Does not download models.
set -e
BIN="/Volumes/LocalDevData/Xiaomao/bin/ollama"
MODELS="/Volumes/LocalDevData/Xiaomao/ollama"
TAG="${XIAOMAO_CHAT_MODEL:-qwen3-coder:30b}"
SERVE="/Users/xiuqiu/WorkSpace/xiaomao-Agent/scripts/user_serve_ollama.sh"

unset PYTHONPATH
unset PYTHONHOME

if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: /Volumes/LocalDevData not mounted" >&2
  exit 3
fi
if [ ! -x "$BIN" ]; then
  echo "ERROR: missing $BIN" >&2
  exit 2
fi
if [ ! -d "$MODELS" ]; then
  echo "ERROR: models dir missing $MODELS" >&2
  exit 3
fi

export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS="$MODELS"
export OLLAMA_NO_CLOUD=1
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=0

if ! "$BIN" list >/dev/null 2>&1; then
  /bin/sh "$SERVE"
  i=0
  while [ "$i" -lt 30 ]; do
    if "$BIN" list >/dev/null 2>&1; then
      break
    fi
    i=$((i + 1))
    sleep 0.5
  done
  if ! "$BIN" list >/dev/null 2>&1; then
    echo "ERROR: ollama serve not reachable at $OLLAMA_HOST" >&2
    exit 4
  fi
fi

exec "$BIN" run "$TAG" "$@"
