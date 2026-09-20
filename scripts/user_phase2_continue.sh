#!/bin/sh
# USER_ACTION_REQUIRED — 在已空闲的用户终端页粘贴本脚本。
# 沙盒拦 github.com；本会话 Claude 终端页已满，无法再开 tab。
# 不 sudo、不写 /Applications、不写 /usr/local、不改全局 git。
set -e
ROOT="/Users/xiuqiu/WorkSpace/xiaomao-Agent"
cd "$ROOT"

echo "=== 1 install ==="
/bin/sh "$ROOT/scripts/user_install_ollama.sh"

echo "=== 2 serve ==="
/bin/sh "$ROOT/scripts/user_serve_ollama.sh"

PIDFILE="/Users/xiuqiu/Library/Application Support/Xiaomao/ollama.pid"
if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE")
  echo "=== process env pid=$pid ==="
  ps eww -p "$pid" -o command= | tr ' ' '\n' | grep '^OLLAMA_' || true
  echo "=== listeners ==="
  lsof -nP -iTCP:11434 -sTCP:LISTEN || true
fi

echo "=== 3 smoke gemma4:12b ==="
/bin/sh "$ROOT/scripts/user_smoke_gemma.sh"

echo "=== 4 eval qwen3.6:35b then qwen3-coder:30b ==="
/bin/sh "$ROOT/scripts/user_eval_models.sh"

echo "=== 5 accept ==="
PYTHONPATH="$ROOT/src" /Users/xiuqiu/.local/bin/python3.11 "$ROOT/scripts/accept.py" | tee /Volumes/LocalDevData/Xiaomao/cache/accept-phase2.json

echo PHASE2_CONTINUE_OK
