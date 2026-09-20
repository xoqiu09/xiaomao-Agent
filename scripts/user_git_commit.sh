#!/bin/sh
# USER_ACTION_REQUIRED — 小猫本仓 v0.1 baseline commit。
# 必须在用户终端跑：Claude Code 沙盒保护 .git 写入（index.lock Operation not permitted）。
# 不改全局 git config；只用本次 -c user.name/email。不 push。不提交密钥/库/模型/日志/.claude。
set -e
cd /Users/xiuqiu/WorkSpace/xiaomao-Agent

if [ ! -d .git ]; then
  echo "ERROR: .git missing; run scripts/user_git_init.sh first"
  exit 1
fi

if git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "already has commit $(git rev-parse HEAD)"
  git log -1 --oneline
  exit 0
fi

git -c user.name='xiaomao' -c user.email='xiaomao@local' add \
  .gitignore \
  EXECUTION_STATE.md \
  PRECHECK.md \
  README.md \
  pyproject.toml \
  scripts \
  src \
  tests

git -c user.name='xiaomao' -c user.email='xiaomao@local' status --short
echo '--- cached stat ---'
git -c user.name='xiaomao' -c user.email='xiaomao@local' diff --cached --stat

# Refuse if ignored/sensitive paths snuck in.
if git diff --cached --name-only | grep -E '(^|/)(\.claude/|\.venv/|.*\.sqlite($|-)|xiaomao\.lock$)' >/dev/null; then
  echo 'ERROR: staged sensitive/ignored path; abort'
  git diff --cached --name-only
  exit 1
fi

git -c user.name='xiaomao' -c user.email='xiaomao@local' commit -m "$(cat <<'EOF'
xiaomao-agent v0.1 baseline

Read-only collector, formal runtime on internal disk, user-level 5-minute scan,
rule-based daily/handoff, model eval harness. Does not include credentials,
sqlite, models, or runtime logs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"

echo "HEAD=$(git rev-parse HEAD)"
git log -1 --format='%H%n%an <%ae>%n%s'
git status --short
echo COMMIT_OK
