#!/bin/sh
# Re-eval qwen3.6:35b with think:false JSON extraction. Does not pull.
# Writes a new file; never overwrites the original invalid_json evidence.
set -e
BIN="/Volumes/LocalDevData/Xiaomao/bin/ollama"
MODELS="/Volumes/LocalDevData/Xiaomao/ollama"
HOME_DIR="/Users/xiuqiu/Library/Application Support/Xiaomao"
PY="/Users/xiuqiu/.local/bin/python3.11"
SRC="/Users/xiuqiu/WorkSpace/xiaomao-Agent/src"
OUT="/Volumes/LocalDevData/Xiaomao/cache"

export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS="$MODELS"
export OLLAMA_NO_CLOUD=1
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=0
export PYTHONPATH="$SRC"
export XIAOMAO_HOME="$HOME_DIR"

if [ ! -x "$BIN" ]; then
  echo "ERROR: missing $BIN"
  exit 2
fi
if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: external volume unmounted; refuse model ops"
  exit 3
fi
if ! "$BIN" show "qwen3.6:35b" >/dev/null 2>&1; then
  echo "ERROR: qwen3.6:35b not present; refuse to pull from this script"
  exit 4
fi

# Original 0/22 invalid_json evidence stays at eval-qwen3.6_35b.json
dest="$OUT/eval-qwen3.6_35b.retest.json"
echo "=== retest qwen3.6:35b -> $dest ==="
"$PY" -m xiaomao --home "$HOME_DIR" eval --model "qwen3.6:35b" >"$dest"
echo "wrote $dest"
"$BIN" stop "qwen3.6:35b" || true
echo RETEST_35B_OK
