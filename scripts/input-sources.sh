#!/bin/bash
# Usage: input-sources.sh <list|check|apply> [enabled-file]
# Compiles macos/input-sources/input-sources.swift on first use or after it changes.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$DIR/macos/input-sources/input-sources.swift"
BIN="$HOME/.cache/dotfiles/input-sources"
if [[ ! -x "$BIN" || "$SRC" -nt "$BIN" ]]; then
    mkdir -p "$(dirname "$BIN")"
    swiftc -O -o "$BIN.$$" "$SRC" >&2  # its own line: set -e skips commands on the left of &&
    mv "$BIN.$$" "$BIN"
fi
CMD="${1:?list|check|apply}" LIST="${2:-$DIR/macos/input-sources/enabled.txt}"
if [[ $CMD == apply ]]; then
    "$BIN" check "$LIST" >/dev/null && exit 0
    "$BIN" apply "$LIST"
    sleep 2
    CMD=check
fi
exec "$BIN" "$CMD" "$LIST"
