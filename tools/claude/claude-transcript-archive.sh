#!/usr/bin/env bash
# Mirror Claude Code transcripts into an append-only archive, and record which
# ones disappeared upstream.
#
# Transcripts under ~/.claude/projects/ vanish for reasons we could not pin down
# (not the 30-day retention — older files survive while ~1-week-old ones are
# gone, and sessions started in sub-directories lose theirs ~always). Losing one
# means `claude --resume <id>` can never work again, so: keep a copy that nothing
# upstream can delete, and log each disappearance with the state of Claude's own
# retention marker at that moment to identify the culprit.
#
# rsync runs without --delete on purpose: this archive only ever grows.
#
# Runs hourly via com.yoyoo.claude-transcript-archive (StartInterval 3600). The
# plist sits next to this script but is untracked — .gitignore excludes **/*.plist
# — so on a fresh machine recreate it and `launchctl load` it from ~/Library/LaunchAgents.
set -euo pipefail

SRC="${CLAUDE_ARCHIVE_SRC:-$HOME/.claude/projects}"
DEST="${CLAUDE_ARCHIVE_DIR:-$HOME/.local/share/claude-transcript-archive}"
CLEANUP_MARKER="${CLAUDE_CLEANUP_MARKER:-$HOME/.claude/.last-cleanup}"

mkdir -p "$DEST/projects"
mirror="$DEST/projects"
log="$DEST/disappeared.log"
reported="$DEST/.reported"
touch "$reported"

rsync -a --prune-empty-dirs \
  --include='*/' --include='*.jsonl' --exclude='*' \
  "$SRC/" "$mirror/"

# archived-but-no-longer-live = disappeared upstream. Report each path once.
(cd "$mirror" && find . -name '*.jsonl' -type f) | sort > "$DEST/.archived"
(cd "$SRC" && find . -name '*.jsonl' -type f) | sort > "$DEST/.live"

now=$(date +%FT%T%z)
marker=$(cat "$CLEANUP_MARKER" 2>/dev/null || echo none)
comm -23 "$DEST/.archived" "$DEST/.live" | while read -r f; do
  grep -qxF "$f" "$reported" && continue
  printf '%s\tgone\t%s\tlast-cleanup=%s\n' "$now" "${f#./}" "$marker" >> "$log"
  printf '%s\n' "$f" >> "$reported"
done
