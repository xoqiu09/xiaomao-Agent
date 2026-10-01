#!/bin/sh
# Install SwiftBar and point it at the read-only Xiaomao plugin.
# Idempotent: safe to re-run. Also repairs a missing app or a reset PluginDirectory.
# Does not scan business repos, does not load a model, does not change LaunchAgents.
set -e

# Install from whichever checkout this script lives in (repo or pinned release dir).
ROOT="${XIAOMAO_ROOT:-$(cd "$(dirname "$0")/.." && pwd -P)}"
PLUGIN_SRC="$ROOT/scripts/swiftbar/xiaomao.1m.sh"
PLUGIN_DIR="$HOME/Library/Application Support/Xiaomao/swiftbar-plugins"
PLUGIN_DST="$PLUGIN_DIR/xiaomao.1m.sh"
APP="/Applications/SwiftBar.app"
ZIP_URL="https://github.com/swiftbar/SwiftBar/releases/download/v2.1.1/SwiftBar.v2.1.1.b597.zip"
ZIP_PATH="$HOME/Downloads/SwiftBar.v2.1.1.b597.zip"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

if [ ! -f "$PLUGIN_SRC" ]; then
  echo "missing plugin source: $PLUGIN_SRC" >&2
  exit 2
fi

mkdir -p "$PLUGIN_DIR"
chmod +x "$PLUGIN_SRC"
ln -sfn "$PLUGIN_SRC" "$PLUGIN_DST"

# A stray unpacked bundle makes `open -a SwiftBar` resolve to the wrong copy.
# Always unpack to a temp dir and never leave one in ~/Downloads.
rm -rf "$HOME/Downloads/SwiftBar-unpack"

# Expected sha256 of the official release zip (matches the Homebrew cask).
ZIP_SHA256="fcdec490782d6587046304044951c63de49ac422fc63892a6fab2dd7bc70c0cd"

# Fallback only. Prefer Homebrew: it strips quarantine and keeps the bundle intact.
# Never `cp -R` an app bundle — that drops extended attributes and breaks the
# signature, and Gatekeeper then reports "已损坏 / is damaged". Use ditto.
install_from_zip() {
  if [ ! -f "$ZIP_PATH" ]; then
    echo "downloading SwiftBar 2.1.1…"
    curl -L --fail --retry 5 --retry-delay 2 -o "$ZIP_PATH" "$ZIP_URL"
  fi
  GOT="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
  if [ "$GOT" != "$ZIP_SHA256" ]; then
    echo "zip checksum mismatch — refusing to install" >&2
    echo "  expected $ZIP_SHA256" >&2
    echo "  got      $GOT" >&2
    echo "  delete $ZIP_PATH and re-run to download again" >&2
    exit 1
  fi
  UNPACK="$(mktemp -d "${TMPDIR:-/tmp}/swiftbar-unpack-XXXXXX")"
  # ditto -x -k preserves symlinks, xattrs and resource forks; unzip + cp do not.
  ditto -x -k "$ZIP_PATH" "$UNPACK"
  SRC_APP="$(find "$UNPACK" -maxdepth 3 -name 'SwiftBar.app' -type d | head -n 1)"
  if [ -z "$SRC_APP" ]; then
    echo "SwiftBar.app not found in $ZIP_PATH" >&2
    rm -rf "$UNPACK"
    exit 1
  fi
  rm -rf "$APP"
  ditto "$SRC_APP" "$APP"
  rm -rf "$UNPACK"
}

if [ ! -d "$APP" ]; then
  if command -v brew >/dev/null 2>&1 && brew install --cask swiftbar; then
    :
  else
    echo "Homebrew cask unavailable or failed; installing from zip…"
    install_from_zip
  fi
fi

if [ ! -d "$APP" ]; then
  echo "$APP is missing after install" >&2
  exit 1
fi

xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true

# A running SwiftBar rewrites prefs on quit; stop it first so PluginDirectory sticks.
osascript -e 'tell application "SwiftBar" to quit' >/dev/null 2>&1 || true
killall SwiftBar >/dev/null 2>&1 || true
sleep 1

defaults write com.ameba.SwiftBar PluginDirectory -string "$PLUGIN_DIR"

# Launch by full path, never `open -a SwiftBar` (that can resolve to a stray copy).
open "$APP"
sleep 4

echo "app:              $APP ($(defaults read "$APP/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo '?'))"
echo "plugin folder:    $PLUGIN_DIR"
echo "plugin:           $PLUGIN_DST -> $PLUGIN_SRC"
echo "PluginDirectory:  $(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || echo 'unset')"
if pgrep -f "$APP/Contents/MacOS/SwiftBar" >/dev/null 2>&1; then
  echo "running:          yes — look for 🐱 in the menu bar"
else
  echo "running:          NO — SwiftBar exited right after launch" >&2
  echo "                  check: log show --last 5m --predicate 'process == \"SwiftBar\"'" >&2
  exit 1
fi
echo
echo "Optional: SwiftBar → Settings → Launch at Login"
