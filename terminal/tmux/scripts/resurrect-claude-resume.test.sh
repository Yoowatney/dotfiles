#!/usr/bin/env bash
# Self-check for resurrect-claude-resume.sh — no tmux needed (CLAUDE_RESUME_PANES seam).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

TAB=$'\t'
LIVE="$TMP/live.jsonl"; : > "$LIVE"        # transcript that exists
DEAD="$TMP/dead.jsonl"                    # transcript that never got written
CLAUDE=/Users/yoyoo/.local/bin/claude

mkdir -p "$TMP/resurrect"
{
  printf 'window\t0-Work\t1\t1\t:*Z\tmain-vertical\n'
  # 1: tagged + live transcript, save file has the wrong cwd
  printf 'pane\t0-Work\t1\t1\t:*Z\t1\ttitle\t:/Users/yoyoo/work/munice\t1\t2.1.220\t:%s --resume 00000000-dead-dead-dead-000000000000\n' "$CLAUDE"
  # 2: tagged but transcript is gone
  printf 'pane\t0-Work\t1\t2\t:-Z\t2\ttitle\t:/Users/yoyoo/work/munice\t0\t2.1.220\t:%s --resume 11111111-1111-1111-1111-111111111111\n' "$CLAUDE"
  # 3: untagged pane carrying a stale --resume
  printf 'pane\t0-Work\t2\t1\t:-Z\t1\ttitle\t:/Users/yoyoo/work/munice\t0\t2.1.220\t:%s --resume 22222222-2222-2222-2222-222222222222\n' "$CLAUDE"
  # 4: not claude — must pass through untouched
  printf 'pane\t0-Work\t2\t2\t:-Z\t2\ttitle\t:/Users/yoyoo/work/munice\t0\tnvim\t:nvim --resume foo\n'
  # 5: empty pane_title — resurrect shifted every field after 6 one to the left,
  #    so cwd sits at 7 and 8 is pane_active. Tagged, cwd contains a space.
  printf 'pane\t0-Work\t3\t1\t:\t1\t:/Users/yoyoo/work/munice\t1\tzsh\t1751\t:%s\n' "$CLAUDE"
} > "$TMP/resurrect/last"

CLAUDE_RESUME_PANES="0-Work${TAB}1${TAB}1${TAB}sess-live${TAB}${LIVE}${TAB}/Users/yoyoo/work/munice/terraform
0-Work${TAB}1${TAB}2${TAB}sess-dead${TAB}${DEAD}${TAB}/Users/yoyoo/work/munice
0-Work${TAB}3${TAB}1${TAB}sess-shift${TAB}${LIVE}${TAB}/Users/yoyoo/my work/repo" \
  RESURRECT_DIR="$TMP/resurrect" "$HERE/resurrect-claude-resume.sh"

out=$(cat "$TMP/resurrect/last")
line() { printf '%s\n' "$out" | awk -F'\t' -v w="$1" -v p="$2" '$1=="pane" && $3==w && $6==p'; }

fail=0
check() { # desc, haystack, needle
  case "$2" in *"$3"*) ;; *) echo "FAIL: $1"; echo "  got: $2"; fail=1 ;; esac
}
absent() {
  case "$2" in *"$3"*) echo "FAIL: $1"; echo "  got: $2"; fail=1 ;; *) ;; esac
}

l=$(line 1 1)
check "live pane resumes its tagged id" "$l" "--resume sess-live"
check "live pane restored in the session cwd" "$l" ":/Users/yoyoo/work/munice/terraform"
absent "live pane drops the stale id" "$l" "00000000-dead"

l=$(line 1 2)
absent "dead-transcript pane gets no --resume" "$l" "--resume"

l=$(line 2 1)
absent "untagged pane gets its stale --resume stripped" "$l" "--resume"

l=$(line 2 2)
check "non-claude pane untouched" "$l" ":nvim --resume foo"

l=$(line 3 1)
check "shifted pane resumes" "$l" "--resume sess-shift"
check "shifted pane cwd rewritten at field 7, space escaped" \
  "$(printf '%s\n' "$l" | cut -f7)" ':/Users/yoyoo/my\ work/repo'
check "shifted pane keeps pane_active in field 8" "$(printf '%s\n' "$l" | cut -f8)" "1"

check "non-pane lines pass through" "$out" "main-vertical"

[ "$fail" -eq 0 ] && echo "OK — all checks passed"
exit "$fail"
