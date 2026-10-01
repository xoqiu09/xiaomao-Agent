#!/bin/sh
set -e
# One-off asset helper: rebuild the 44px menu-bar head from xiaoba-src.jpg.
DIR="$(cd "$(dirname "$0")" && pwd -P)"
SRC="$DIR/xiaoba-src.jpg"
sips -s format png "$SRC" --out "$DIR/xiaoba-512.png"
sips -z 44 44 "$DIR/xiaoba-512.png" --out "$DIR/xiaoba-head.png"
ls -l "$DIR"
sips -g pixelWidth -g pixelHeight -g format "$DIR/xiaoba-head.png"
