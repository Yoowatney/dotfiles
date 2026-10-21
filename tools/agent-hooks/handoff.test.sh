#!/usr/bin/env bash
set -uo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/handoff"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }

# A fake pbcopy for the no-herdr fallback, and a fake herdr that logs `agent prompt <pane> <text>`.
mkdir -p "$SANDBOX/bin"
printf '#!/usr/bin/env bash\ncat > "%s/clip"\n' "$SANDBOX" > "$SANDBOX/bin/pbcopy"
printf '#!/usr/bin/env bash\nprintf "%%s|%%s\\n" "$3" "$4" >> "%s/prompts"\n' "$SANDBOX" > "$SANDBOX/bin/herdr"
chmod +x "$SANDBOX/bin/pbcopy" "$SANDBOX/bin/herdr"
ho() { local pane="$1"; shift; env XDG_STATE_HOME="$SANDBOX/state" HERDR_PANE_ID="$pane" HERDR_BIN_PATH="$SANDBOX/bin/herdr" \
  HANDOFF_KICK_DELAY=0 PATH="$SANDBOX/bin:$PATH" bash "$SUBJECT" "$@"; }
# The kick runs detached, so give it a moment to land.
prompts() { local i; for i in 1 2 3 4 5 6 7 8 9 10; do [ -s "$SANDBOX/prompts" ] && break; sleep 0.2; done; cut -d'|' -f1 "$SANDBOX/prompts" 2>/dev/null | tr '\n' ' '; }
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
# 요유 typed during the silent wait and the kick merged onto it (10/21); their words come first in that message.
check "load says text before the kick marker is 요유's" "$(grep -c "before the \[handoff:" <<<"$ctx")" 1
# Interactive sessions ignore initialUserMessage (tested 10/21), so load types the first prompt into its own pane.
check "load starts the first turn in its own pane" "$(prompts)" "w1:p1 "
check "the kick says it was not typed by 요유" "$(grep -c '요유가 직접 친 글이 아닙니다' "$SANDBOX/prompts")" 1
rm -f "$SANDBOX/prompts"
check "load consumes it" "$(ho w1:p1 load)" ""
check "nothing to load types nothing" "$(prompts)" ""

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
