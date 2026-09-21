#!/bin/sh
# One-shot: load daily LaunchAgent, generate 30b daily/handoff, mark PILOT_RUNNING.
# Does not rewrite the scan plist. Does not download models.
set -e
export PYTHONPATH="/Users/xiuqiu/WorkSpace/xiaomao-Agent/src"
HOME_X="${XIAOMAO_HOME:-$HOME/Library/Application Support/Xiaomao}"
UIDN="$(id -u)"
DAILY_PLIST="$HOME/Library/LaunchAgents/ai.xiaomao.daily.plist"

launchctl bootout "gui/$UIDN" "$DAILY_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UIDN" "$DAILY_PLIST"
echo "=== daily print ==="
launchctl print "gui/$UIDN/ai.xiaomao.daily" | awk '/state =|runs =|last exit code =|path = |Hour|Minute|program =/'
echo "=== scan still loaded ==="
launchctl print "gui/$UIDN/ai.xiaomao.scan" | awk '/state =|runs =|last exit code =|run interval/'
echo "=== api/ps before ==="
curl -sS --max-time 3 http://127.0.0.1:11434/api/ps || echo "ps fail"
echo "=== 30b daily --with-model ==="
python3.11 -m xiaomao --home "$HOME_X" daily --date 2026-09-21 --with-model
echo "=== api/ps after daily ==="
curl -sS --max-time 3 http://127.0.0.1:11434/api/ps || echo "ps fail"
echo "=== 30b handoff --with-model ==="
python3.11 -m xiaomao --home "$HOME_X" handoff --project website --with-model
echo "=== api/ps after handoff ==="
curl -sS --max-time 3 http://127.0.0.1:11434/api/ps || echo "ps fail"
echo "=== daily files ==="
ls -l "$HOME_X/reports/daily/"
echo "=== pilot start ==="
python3.11 -m xiaomao --home "$HOME_X" pilot start
echo "=== health ==="
python3.11 -m xiaomao --home "$HOME_X" health
