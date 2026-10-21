#!/usr/bin/env bash
set -uo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/handoff"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }

# A fake pbcopy for the no-herdr fallback.
mkdir -p "$SANDBOX/bin"
printf '#!/usr/bin/env bash\ncat > "%s/clip"\n' "$SANDBOX" > "$SANDBOX/bin/pbcopy"
chmod +x "$SANDBOX/bin/pbcopy"
ho() { local pane="$1"; shift; env XDG_STATE_HOME="$SANDBOX/state" HERDR_PANE_ID="$pane" PATH="$SANDBOX/bin:$PATH" bash "$SUBJECT" "$@"; }
dir="$SANDBOX/state/agent-handoff"

printf '보드 api#1 이어서\n' | ho w1:p1 save >/dev/null
check "save writes the pane's file" "$(cat "$dir/w1:p1.md")" "보드 api#1 이어서"
check "another pane loads nothing" "$(ho w1:p2 load)" ""
out=$(ho w1:p1 load)
# Claude and Codex both read SessionStart context from this JSON shape.
check "load emits SessionStart JSON" "$(jq -r '.hookSpecificOutput.hookEventName' <<<"$out")" "SessionStart"
ctx=$(jq -r '.hookSpecificOutput.additionalContext' <<<"$out")
check "load carries the handoff" "$(grep -c '보드 api#1 이어서' <<<"$ctx")" 1
check "load says where it came from" "$(grep -c 'previous session in this pane' <<<"$ctx")" 1
check "load consumes it" "$(ho w1:p1 load)" ""

echo "file arg" > "$SANDBOX/h.md"
ho w1:p3 save "$SANDBOX/h.md" >/dev/null
check "save takes a file argument" "$(cat "$dir/w1:p3.md")" "file arg"

# A handoff nobody picked up for a day belongs to work that has moved on.
printf 'old\n' | ho w1:p4 save >/dev/null
touch -t "$(date -v-2d +%Y%m%d%H%M)" "$dir/w1:p4.md"
check "a day-old handoff is ignored" "$(ho w1:p4 load)" ""
check "and removed" "$([ -e "$dir/w1:p4.md" ] && echo kept || echo gone)" "gone"

printf 'no pane\n' | ho "" save >/dev/null
check "outside herdr save falls back to the clipboard" "$(cat "$SANDBOX/clip")" "no pane"
check "outside herdr load prints nothing" "$(ho "" load)" ""

ho w1:p1 bogus 2>/dev/null; check "unknown command exits 2" "$?" 2

exit "$fails"
