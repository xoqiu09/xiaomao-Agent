#!/bin/sh
# Sequential eval: qwen3.6:35b then qwen3-coder:30b. One model loaded at a time.
# Never falls back to ~/.ollama/models. Never downloads a third candidate.
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
if [ ! -d "$MODELS" ]; then
  echo "ERROR: models dir missing $MODELS"
  exit 3
fi

mkdir -p "$OUT"

pull_if_needed() {
  tag="$1"
  if "$BIN" show "$tag" >/dev/null 2>&1; then
    echo "already have $tag"
    return 0
  fi
  echo "pulling $tag (official tag, one at a time)"
  "$BIN" pull "$tag"
}

eval_one() {
  tag="$1"
  dest="$OUT/eval-$(echo "$tag" | tr ':/' '__').json"
  echo "=== eval $tag ==="
  "$PY" -m xiaomao --home "$HOME_DIR" eval --model "$tag" >"$dest"
  echo "wrote $dest"
  "$BIN" stop "$tag" || true
}

echo "=== process env evidence ==="
PIDFILE="$HOME_DIR/ollama.pid"
if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE")
  echo "pid=$pid"
  ps eww -p "$pid" -o command= | tr ' ' '\n' | grep '^OLLAMA_' || true
fi

pull_if_needed "qwen3.6:35b"
eval_one "qwen3.6:35b"
"$BIN" stop "qwen3.6:35b" || true

pull_if_needed "qwen3-coder:30b"
eval_one "qwen3-coder:30b"
"$BIN" stop "qwen3-coder:30b" || true

echo "=== daily/handoff with model (degrade on failure) ==="
"$PY" -m xiaomao --home "$HOME_DIR" daily --with-model
"$PY" -m xiaomao --home "$HOME_DIR" handoff --project website --with-model

echo EVAL_SEQUENCE_OK
