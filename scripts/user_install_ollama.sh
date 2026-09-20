#!/bin/sh
# USER_ACTION_REQUIRED — 只装一种官方 Ollama：darwin CLI tarball。
# 不 sudo、不写 /Applications、不写 /usr/local、不改 launchd 系统服务。
# 二进制落到已授权外盘；模型目录必须是 /Volumes/LocalDevData/Xiaomao/ollama。
# 沙盒拦 github.com/ollama.com，必须在用户终端跑。
# 支持续传：半成品保存在 .partial，校验通过才改名为正式 tgz。
set -e
VER=v0.34.2
URL="https://github.com/ollama/ollama/releases/download/${VER}/ollama-darwin.tgz"
EXPECT_SHA="f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f"
EXPECT_BYTES=158526160
CACHE="/Volumes/LocalDevData/Xiaomao/cache"
OPT="/Volumes/LocalDevData/Xiaomao/opt/ollama-${VER}"
BINDIR="/Volumes/LocalDevData/Xiaomao/bin"
TGZ="${CACHE}/ollama-darwin-${VER}.tgz"
PARTIAL="${TGZ}.partial"

if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: external volume not mounted"
  exit 3
fi

mkdir -p "$CACHE" "$BINDIR"

LOCKDIR="${CACHE}/ollama-install.lockdir"
acquire_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then
    echo $$ >"$LOCKDIR/pid"
    return 0
  fi
  oldpid=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "install already running pid=$oldpid"
    exit 0
  fi
  echo "stale install lock; replacing"
  rm -rf "$LOCKDIR"
  mkdir "$LOCKDIR"
  echo $$ >"$LOCKDIR/pid"
}
acquire_lock
trap 'rm -rf "$LOCKDIR"' EXIT INT TERM

if [ -x "${BINDIR}/ollama" ]; then
  echo "already have ${BINDIR}/ollama"
  "${BINDIR}/ollama" --version || true
  exit 0
fi

sha_of() {
  shasum -a 256 "$1" | awk '{print $1}'
}

if [ -f "$TGZ" ]; then
  GOT=$(sha_of "$TGZ")
  if [ "$GOT" = "$EXPECT_SHA" ]; then
    echo "sha256_ok $GOT (cached tgz)"
  else
    echo "cached tgz incomplete/mismatch; keeping as partial ($(wc -c < "$TGZ") bytes)"
    mv -f "$TGZ" "$PARTIAL"
  fi
fi

if [ ! -f "$TGZ" ]; then
  attempt=1
  while [ "$attempt" -le 8 ]; do
    echo "download attempt $attempt → $PARTIAL"
    # -C - resumes; do not use -f here: a 416 on a complete partial is recoverable.
    set +e
    curl -L --retry 5 --retry-delay 3 --retry-all-errors \
      -C - -o "$PARTIAL" "$URL"
    curl_rc=$?
    set -e
    have=0
    if [ -f "$PARTIAL" ]; then
      have=$(wc -c < "$PARTIAL" | tr -d ' ')
    fi
    echo "curl_rc=$curl_rc bytes=$have expected=$EXPECT_BYTES"
    if [ -f "$PARTIAL" ]; then
      GOT=$(sha_of "$PARTIAL")
      if [ "$GOT" = "$EXPECT_SHA" ]; then
        mv -f "$PARTIAL" "$TGZ"
        echo "sha256_ok $GOT"
        break
      fi
    fi
    if [ "$have" -ge "$EXPECT_BYTES" ] && [ "$GOT" != "$EXPECT_SHA" ]; then
      echo "ERROR: size reached but sha256 mismatch; deleting corrupt partial"
      rm -f "$PARTIAL"
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
fi

if [ ! -f "$TGZ" ]; then
  echo "ERROR: download did not produce a verified tarball"
  exit 2
fi

GOT=$(sha_of "$TGZ")
if [ "$GOT" != "$EXPECT_SHA" ]; then
  echo "ERROR: sha256 mismatch got=$GOT expected=$EXPECT_SHA"
  rm -f "$TGZ"
  exit 2
fi
echo "sha256_ok $GOT"

rm -rf "$OPT"
mkdir -p "$OPT"
tar -xzf "$TGZ" -C "$OPT"
if [ -x "$OPT/ollama" ]; then
  ln -sfn "$OPT/ollama" "$BINDIR/ollama"
elif [ -x "$OPT/bin/ollama" ]; then
  ln -sfn "$OPT/bin/ollama" "$BINDIR/ollama"
else
  echo "ERROR: ollama binary not found in tarball; listing:"
  find "$OPT" -maxdepth 3 -type f | head
  exit 2
fi

echo "OLLAMA_BIN=$BINDIR/ollama"
"$BINDIR/ollama" --version
echo INSTALL_OK
echo
echo "未启动服务。下一步跑 scripts/user_serve_ollama.sh"
echo "未写入 /Applications /usr/local /Library/LaunchDaemons"
