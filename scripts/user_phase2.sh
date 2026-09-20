#!/bin/sh
# 一条命令完成：提交剩余源码 + 官方 darwin CLI tarball（外盘，无 sudo）+ loopback serve。
# 在 Claude Code 终端任意空闲页、或 macOS 终端.app 粘贴：
#   /bin/sh /Users/xiuqiu/WorkSpace/xiaomao-Agent/scripts/user_phase2.sh
set -e
cd /Users/xiuqiu/WorkSpace/xiaomao-Agent

echo "=== git remaining files ==="
git -c user.name='xiaomao' -c user.email='xiaomao@local' add \
  src/xiaomao/migrate.py \
  src/xiaomao/ollama_runtime.py \
  src/xiaomao/doctor.py \
  tests/test_reports.py \
  scripts/user_install_ollama.sh \
  scripts/user_serve_ollama.sh \
  scripts/user_stop_ollama.sh \
  scripts/user_phase2.sh \
  EXECUTION_STATE.md \
  README.md
git -c user.name='xiaomao' -c user.email='xiaomao@local' status --short
if git diff --cached --quiet; then
  echo "nothing to commit"
else
  git -c user.name='xiaomao' -c user.email='xiaomao@local' commit -m "$(cat <<'EOF'
xiaomao-agent v0.1: external models dir and user-level Ollama CLI

Keep gemma4:12b; copy to /Volumes/LocalDevData/Xiaomao/ollama (source retained).
Official ollama-darwin.tgz lives on the external volume; no sudo, no /Applications.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
fi
echo "HEAD=$(git rev-parse HEAD)"

echo "=== install official CLI ==="
/bin/sh /Users/xiuqiu/WorkSpace/xiaomao-Agent/scripts/user_install_ollama.sh

echo "=== serve ==="
/bin/sh /Users/xiuqiu/WorkSpace/xiaomao-Agent/scripts/user_serve_ollama.sh

BIN=/Volumes/LocalDevData/Xiaomao/bin/ollama
PIDFILE="/Users/xiuqiu/Library/Application Support/Xiaomao/ollama.pid"
echo "=== version / list / process env ==="
"$BIN" --version
"$BIN" list || true
if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE")
  echo "pid=$pid"
  ps eww -p "$pid" -o command= | tr ' ' '\n' | grep '^OLLAMA_' || true
fi
echo PHASE2_USER_OPS_DONE
