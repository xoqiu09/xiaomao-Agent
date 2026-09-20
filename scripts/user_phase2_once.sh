#!/bin/sh
# One-shot Phase 2: install CLI, serve, smoke, sequential eval.
# Invoked in the background by the existing user-level scan LaunchAgent
# (already unsandboxed). Does not replace scan. Does not create a new daemon.
# Stamp + lockdir so later scans stay rules-only.
set -e
ROOT="/Users/xiuqiu/WorkSpace/xiaomao-Agent"
STAMP="/Volumes/LocalDevData/Xiaomao/cache/phase2-eval.stamp"
LOCKDIR="/Volumes/LocalDevData/Xiaomao/cache/phase2-once.lockdir"
CACHE="/Volumes/LocalDevData/Xiaomao/cache"

if [ -f "$STAMP" ]; then
  echo "phase2 stamp present; skip"
  exit 0
fi
if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: external volume unmounted; refuse model ops"
  exit 0
fi

mkdir -p "$CACHE"
if mkdir "$LOCKDIR" 2>/dev/null; then
  echo $$ >"$LOCKDIR/pid"
else
  oldpid=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "phase2 once already running pid=$oldpid"
    exit 0
  fi
  echo "stale phase2 lock; replacing"
  rm -rf "$LOCKDIR"
  mkdir "$LOCKDIR"
  echo $$ >"$LOCKDIR/pid"
fi
trap 'rm -rf "$LOCKDIR"' EXIT INT TERM

echo "=== 1 install ==="
/bin/sh "$ROOT/scripts/user_install_ollama.sh"

echo "=== 2 serve ==="
/bin/sh "$ROOT/scripts/user_serve_ollama.sh"
sleep 2

PIDFILE="/Users/xiuqiu/Library/Application Support/Xiaomao/ollama.pid"
if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE")
  echo "=== process env pid=$pid ==="
  ps eww -p "$pid" -o command= | tr ' ' '\n' | grep '^OLLAMA_' || true
fi

echo "=== 3 smoke gemma4:12b ==="
/bin/sh "$ROOT/scripts/user_smoke_gemma.sh"

echo "=== 4 eval qwen3.6:35b then qwen3-coder:30b ==="
/bin/sh "$ROOT/scripts/user_eval_models.sh"

echo "=== 5 accept ==="
PYTHONPATH="$ROOT/src" /Users/xiuqiu/.local/bin/python3.11 "$ROOT/scripts/accept.py" | tee "$CACHE/accept-phase2.json"

date -u +%Y-%m-%dT%H:%M:%SZ >"$STAMP"
echo PHASE2_ONCE_OK
