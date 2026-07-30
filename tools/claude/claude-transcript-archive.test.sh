#!/usr/bin/env bash
# Self-check for claude-transcript-archive.sh — runs against temp dirs.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

SRC="$TMP/projects"; DEST="$TMP/archive"
mkdir -p "$SRC/-proj-a" "$SRC/-proj-b/sub/subagents"
echo '{"a":1}' > "$SRC/-proj-a/aaa.jsonl"
echo '{"b":1}' > "$SRC/-proj-b/bbb.jsonl"
echo '{"s":1}' > "$SRC/-proj-b/sub/subagents/ccc.jsonl"
echo 'not a transcript' > "$SRC/-proj-a/tool-result.txt"
echo '2026-07-30T05:23:01.419Z' > "$TMP/last-cleanup"

run() { CLAUDE_ARCHIVE_SRC="$SRC" CLAUDE_ARCHIVE_DIR="$DEST" \
        CLAUDE_CLEANUP_MARKER="$TMP/last-cleanup" "$HERE/claude-transcript-archive.sh"; }

fail=0
ok()      { [ -e "$1" ] || { echo "FAIL: expected $1"; fail=1; }; }
absent()  { [ -e "$1" ] && { echo "FAIL: unexpected $1"; fail=1; } || true; }
count()   { local n; n=$(grep -c "$2" "$1" 2>/dev/null || true); [ "$n" = "$3" ] || { echo "FAIL: $2 appears $n times in $(basename "$1"), want $3"; fail=1; }; }

run
ok      "$DEST/projects/-proj-a/aaa.jsonl"
ok      "$DEST/projects/-proj-b/sub/subagents/ccc.jsonl"
absent  "$DEST/projects/-proj-a/tool-result.txt"
absent  "$DEST/disappeared.log"

# a transcript vanishing upstream must survive in the archive and get logged once
rm "$SRC/-proj-b/bbb.jsonl"
run
ok    "$DEST/projects/-proj-b/bbb.jsonl"
count "$DEST/disappeared.log" "bbb.jsonl" 1
grep -q "last-cleanup=2026-07-30T05:23:01.419Z" "$DEST/disappeared.log" ||
  { echo "FAIL: cleanup marker not recorded"; fail=1; }

run  # idempotent: no duplicate report, no resurrection of the deleted source
count "$DEST/disappeared.log" "bbb.jsonl" 1
absent "$SRC/-proj-b/bbb.jsonl"

[ "$fail" -eq 0 ] && echo "OK — all checks passed"
exit "$fail"
