#!/bin/sh
# USER_ACTION_REQUIRED — 仅初始化小猫自己的仓库。
# 在「终端.app」或 iTerm 中运行（不要在 Claude Code 沙盒里跑）。
# 不会改业务仓、不会 sudo、不会关沙盒。
set -e
cd /Users/xiuqiu/WorkSpace/xiaomao-Agent
if [ -d .git ]; then
  echo "already a git repository"
  git rev-parse --short HEAD
  exit 0
fi
git init -b main
git add -A
git status --short
echo
echo "下一步（仍在本目录、仍不要 push）："
echo "  git -C /Users/xiuqiu/WorkSpace/xiaomao-Agent commit -m 'xiaomao-agent v0.1 baseline'"
echo "或回到 Claude Code 会话说「git init 已完成」，由会话继续提交。"
