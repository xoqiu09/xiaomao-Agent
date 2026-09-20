#!/bin/sh
# USER_ACTION_REQUIRED — 只装一种官方 Ollama：darwin CLI tarball。
# 不 sudo、不写 /Applications、不写 /usr/local、不改 launchd 系统服务。
# 二进制落到已授权外盘；模型目录必须是 /Volumes/LocalDevData/Xiaomao/ollama。
# 沙盒拦 github.com/ollama.com，必须在用户终端跑。
set -e
VER=v0.34.2
URL="https://github.com/ollama/ollama/releases/download/${VER}/ollama-darwin.tgz"
EXPECT_SHA="f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f"
CACHE="/Volumes/LocalDevData/Xiaomao/cache"
OPT="/Volumes/LocalDevData/Xiaomao/opt/ollama-${VER}"
BINDIR="/Volumes/LocalDevData/Xiaomao/bin"
TGZ="${CACHE}/ollama-darwin-${VER}.tgz"

if [ ! -d /Volumes/LocalDevData ]; then
  echo "ERROR: external volume not mounted"
  exit 3
fi

mkdir -p "$CACHE" "$BINDIR"

if [ -x "${BINDIR}/ollama" ]; then
  echo "already have ${BINDIR}/ollama"
  "${BINDIR}/ollama" --version || true
  exit 0
fi

if [ ! -f "$TGZ" ]; then
  echo "downloading $URL"
  curl -fL --retry 3 --retry-delay 2 -o "$TGZ" "$URL"
fi

GOT=$(shasum -a 256 "$TGZ" | awk '{print $1}')
if [ "$GOT" != "$EXPECT_SHA" ]; then
  echo "ERROR: sha256 mismatch got=$GOT expected=$EXPECT_SHA"
  rm -f "$TGZ"
  exit 2
fi
echo "sha256_ok $GOT"

rm -rf "$OPT"
mkdir -p "$OPT"
tar -xzf "$TGZ" -C "$OPT"
# Official tarball layout: ollama binary at top level, or ./bin/ollama.
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
