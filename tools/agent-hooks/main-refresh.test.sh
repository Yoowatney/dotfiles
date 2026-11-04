#!/usr/bin/env bash
set -uo pipefail
unset HERDR_SOCKET_PATH HERDR_PANE_ID XDG_STATE_HOME # never the live herdr, pane or state

SUBJECT="$(cd "$(dirname "$0")" && pwd)/main-refresh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }
fail() { echo "FAIL $1"; fails=1; }

mkdir -p "$SANDBOX/state/orchestrator" "$SANDBOX/state/agent-handoff" "$SANDBOX/bin"
# Fake herdr: every call is logged; `pane get` answers the pane in $SANDBOX/panes with the status in $SANDBOX/status.
cat > "$SANDBOX/herdr" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$SANDBOX/herdr-log"
case "\$1 \$2" in
  "pane list") cat "$SANDBOX/panes" ;;
  "pane get") [ ! -f "$SANDBOX/late-on-get" ] || echo 09 > "$SANDBOX/hour"
    jq -n --arg s "\$(cat "$SANDBOX/status")" --arg p "\$3" '{result: {pane: {pane_id: \$p, agent_status: \$s}}}' ;;
  "notification show") echo "\$3" >> "$SANDBOX/alerts" ;;
  "agent prompt") [ ! -f "$SANDBOX/prompt-fails" ] || exit 1 ;;
  *) exit 2 ;;
esac
EOS
# Fake pane-tell: logs its args. --box-ready: BOX_FULL always fails; BOX_FULL_AFTER fails from the second call;
# BOX_WORK / BOX_LATE make the second call set the pane working / the hour to 09. A handoff request prints `told x`
# (QUEUED: `queued`), appends itself to the transcript the key's .md.transcript names, and writes the handoff (key
# $HANDOFF_KEY) after HANDOFF_AFTER seconds. NEW_TURN adds a later user line (meta: lines that must not count).
cat > "$SANDBOX/pane-tell" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$SANDBOX/told"
echo "\${HERDR_PANE_ID:-unset}" > "$SANDBOX/tell-env"
if [ "\$1" = --box-ready ]; then
  n=\$((\$(cat "$SANDBOX/box-n" 2>/dev/null || echo 0) + 1)); echo "\$n" > "$SANDBOX/box-n"
  [ "\$n" != 1 ] || [ -z "\${BOX_LATE_FIRST:-}" ] || echo 09 > "$SANDBOX/hour"
  if [ "\$n" -ge 2 ]; then
    [ -z "\${BOX_WORK:-}" ] || echo working > "$SANDBOX/status"
    [ -z "\${BOX_LATE:-}" ] || echo 09 > "$SANDBOX/hour"
    [ -z "\${BOX_FULL_AFTER:-}" ] || exit 1
  fi
  [ -z "\${BOX_FULL:-}" ]; exit
fi
[ ! -f "$SANDBOX/tell-fails" ] || exit 1
case "\$2" in *"handoff save"*)
  t=\$(cat "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md.transcript")
  jq -nc --arg c "\$2" '{type: "user", message: {content: \$c}}' >> "\$t"
  case "\${NEW_TURN:-}" in
    yes) echo '{"type":"user","message":{"content":"a new message"}}' >> "\$t" ;;
    meta) echo '{"type":"user","isMeta":true,"message":{"content":"meta line"}}' >> "\$t"
          echo '{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}' >> "\$t" ;;
    image) echo '{"type":"user","message":{"content":[{"type":"image"},{"type":"text","text":"hi"}]}}' >> "\$t" ;;
  esac
  [ -z "\${GO_LATE:-}" ] || echo 09 > "$SANDBOX/hour"
  [ -z "\${WORK_AFTER:-}" ] || echo working > "$SANDBOX/status"
  if [ "\${HANDOFF_AFTER:-0}" != never ]; then ( sleep "\${HANDOFF_AFTER:-0}"; if [ -n "\${HANDOFF_EMPTY:-}" ]; then : > "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md"; else echo handoff > "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md"; fi ); fi
  if [ -n "\${QUEUED:-}" ]; then echo "queued (box busy)"; else echo "told x"; fi ;;
esac
exit 0
EOS
# date shim: +%H comes from $SANDBOX/hour, everything else is the real date.
cat > "$SANDBOX/bin/date" <<EOS
#!/bin/bash
if [ "\${1:-}" = +%H ]; then cat "$SANDBOX/hour"; else exec /bin/date "\$@"; fi
EOS
chmod +x "$SANDBOX/herdr" "$SANDBOX/pane-tell" "$SANDBOX/bin/date"

S="$SANDBOX/state"
mr() { # every call is sandboxed: state dir, herdr, pane-tell
  rm -f "$SANDBOX/told" "$SANDBOX/herdr-log" "$SANDBOX/box-n" "$S/orchestrator/refresh.log"; : > "$SANDBOX/alerts"
  env XDG_STATE_HOME="$S" HERDR_BIN_PATH="$SANDBOX/herdr" MAIN_REFRESH_PANE_TELL="$SANDBOX/pane-tell" \
    MAIN_REFRESH_POLL=0 MAIN_REFRESH_QUIET=0 MAIN_REFRESH_DEADLINE="${MAIN_REFRESH_DEADLINE:-5}" MAIN_REFRESH_HANDOFF_WAIT="${MAIN_REFRESH_HANDOFF_WAIT:-5}" \
    "$@" /bin/bash "$SUBJECT" >/dev/null 2>&1
}
told() { [ -f "$SANDBOX/told" ] && awk '{print $1, $2, $3}' "$SANDBOX/told" | paste -sd'|' - || true; }
cleared() { grep -c '^agent prompt w5:p3 /clear$' "$SANDBOX/herdr-log" 2>/dev/null || true; }
status() { echo "$1" > "$SANDBOX/status"; }
panes() { # <pane id> <session id>
  jq -nc --arg p "$1" --arg s "$2" '{result: {panes: [{pane_id: "w1:p9", terminal_id: "term_x", agent_session: {value: "other"}}, {pane_id: $p, terminal_id: "term_M", agent_session: {value: $s}}]}}' > "$SANDBOX/panes"
}
set_main() { # the main pane, and a transcript for its session that the handoff key's file names
  echo term_M > "$S/orchestrator/main"; panes w5:p3 sid-main
  : > "$SANDBOX/sid-main.jsonl"; echo "$SANDBOX/sid-main.jsonl" > "$S/agent-handoff/w5:p3.md.transcript"
}
rm_main() { rm -f "$S/orchestrator/main"; }
rm_stamp() { rm -f "$S/orchestrator"/refreshed-*; rm -f "$S"/agent-handoff/*.md "$S"/agent-handoff/*.md.skipped; }
STAMP="$S/orchestrator/refreshed-$(date +%F)"

set_main; status idle
mr MAIN_REFRESH_HOUR=09; check "outside 05-06 nothing is sent, and the skip is announced" "$(told)|$(grep -c '건너뜀' "$SANDBOX/alerts")" "|1"
rm_main; mr MAIN_REFRESH_HOUR=09; check "no main file stays silent even outside the window" "$(cat "$SANDBOX/alerts")" ""
set_main
rm_main; mr MAIN_REFRESH_HOUR=05; check "no main file: nothing, no alert" "$(told)|$(cat "$SANDBOX/alerts" 2>/dev/null)" "|"
echo term_gone > "$S/orchestrator/main"; mr MAIN_REFRESH_HOUR=05; check "main pane not found: nothing" "$(told)" ""
set_main; mr MAIN_REFRESH_HOUR=05
check "asks for a handoff, then checks the box" "$(told)" "--box-ready w5:p3 claude|w5:p3 [main-refresh] 새벽|--box-ready w5:p3 claude"
check "then clears, straight through herdr, once" "$(cleared)" 1
check "the request names handoff save" "$(grep -c 'handoff save' "$SANDBOX/told")" 1
[ -f "$STAMP" ] || fail "today is marked done"
mr MAIN_REFRESH_HOUR=06; check "once a day" "$(told)" ""
rm_stamp; status working; mr MAIN_REFRESH_HOUR=05 MAIN_REFRESH_DEADLINE=0.3
check "a working main pane is never interrupted" "$(told)" ""
check "the skip is announced" "$(grep -c '건너뜀' "$SANDBOX/alerts")" 1
[ ! -f "$STAMP" ] || fail "a skip leaves no stamp"
status idle; mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "no handoff written: no /clear" "$(told)|$(cleared)" "--box-ready w5:p3 claude|w5:p3 [main-refresh] 새벽|0"
check "no handoff: announced" "$(grep -c '건너뜀' "$SANDBOX/alerts")" 1
check "no handoff: logged" "$(grep -c '인계가 저장되지 않아' "$S/orchestrator/refresh.log")" 1
[ ! -f "$STAMP" ] || fail "no handoff leaves no stamp"
rm_stamp; mr BOX_FULL=1 MAIN_REFRESH_HOUR=05   # 요유 left words in the box: --box-ready fails
check "a full box from the start: no request, no /clear" "$(told)|$(cleared)" "--box-ready w5:p3 claude|0"
check "the skip is logged" "$(grep -c '입력창' "$S/orchestrator/refresh.log")" 1
check "the skip is announced too" "$(grep -c '건너뜀' "$SANDBOX/alerts")" 1
[ ! -f "$STAMP" ] || fail "a full box leaves no stamp"

# The box fills between the request and the clear: no /clear, and the memo is set aside.
rm_stamp; mr BOX_FULL_AFTER=1 MAIN_REFRESH_HOUR=05
check "box full after the request: no /clear" "$(told)|$(cleared)" "--box-ready w5:p3 claude|w5:p3 [main-refresh] 새벽|--box-ready w5:p3 claude|0"
[ ! -f "$S/agent-handoff/w5:p3.md" ] && [ -f "$S/agent-handoff/w5:p3.md.skipped" ] || fail "a skipped run sets its memo aside"
rm_stamp
# A queued request is not a delivered one, even if a handoff turns up anyway.
mr QUEUED=1 MAIN_REFRESH_HOUR=05
check "queued request: no /clear, announced" "$(cleared)|$(grep -c '대기열' "$S/orchestrator/refresh.log")" "0|1"
rm_stamp; mr HANDOFF_EMPTY=1 MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "an empty handoff does not count" "$(cleared)" 0
rm_stamp; mr NEW_TURN=yes MAIN_REFRESH_HOUR=05
check "a new message after the save: no /clear, announced" "$(cleared)|$(grep -c '새 대화' "$S/orchestrator/refresh.log")" "0|1"
rm_stamp; mr NEW_TURN=meta MAIN_REFRESH_HOUR=05
check "meta and tool-result lines are not new messages" "$(cleared)" 1
rm_stamp; mr NEW_TURN=image MAIN_REFRESH_HOUR=05
check "a message with an image after the save: no /clear, announced" "$(cleared)|$(grep -c '새 대화' "$S/orchestrator/refresh.log")" "0|1"
rm_stamp; echo 05 > "$SANDBOX/hour"; mr PATH="$SANDBOX/bin:$PATH" BOX_LATE_FIRST=1
check "hour passed during the first box check: no request" "$(told)|$(cleared)" "--box-ready w5:p3 claude|0"
rm_stamp; status idle; mr BOX_WORK=1 MAIN_REFRESH_HOUR=05; status idle
check "pane busy again after the box check: no /clear" "$(cleared)" 0
rm_stamp; echo 05 > "$SANDBOX/hour"; mr PATH="$SANDBOX/bin:$PATH" BOX_LATE=1
check "hour passed during the box check: no /clear" "$(cleared)" 0

# A turn that just ended: wait for quiet. An old transcript goes ahead.
rm_stamp; touch "$SANDBOX/sid-main.jsonl"; mr MAIN_REFRESH_QUIET=600 MAIN_REFRESH_HOUR=05 MAIN_REFRESH_DEADLINE=0.3
check "recent turn: not asked, announced" "$(told)|$(grep -c '건너뜀' "$SANDBOX/alerts")" "|1"
rm_stamp; touch -t 202001010000 "$SANDBOX/sid-main.jsonl"; mr MAIN_REFRESH_QUIET=600 MAIN_REFRESH_HOUR=05
check "old transcript: goes ahead" "$(cleared)" 1

# Several transcripts hold one session id: the newest key is the real one.
rm_stamp; panes w9:p1 sid-moved
: > "$SANDBOX/sid-moved.jsonl"
echo "$SANDBOX/sid-moved.jsonl" > "$S/agent-handoff/w5:p3.md.transcript"; echo "$SANDBOX/sid-moved.jsonl" > "$S/agent-handoff/w6:p4.md.transcript"
touch -t 202001010000 "$S/agent-handoff/w5:p3.md.transcript"
mr HANDOFF_KEY=w6:p4 MAIN_REFRESH_HOUR=05
check "newest matching key wins" "$(grep -c '^agent prompt w9:p1 /clear$' "$SANDBOX/herdr-log")" 1
rm -f "$S/agent-handoff/w6:p4.md.transcript"; set_main

# An older pending handoff must not count as the answer to this request.
rm_stamp; echo old > "$S/agent-handoff/w5:p3.md"; touch -t 202001010000 "$S/agent-handoff/w5:p3.md"
mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a stale handoff does not count" "$(cleared)" 0

# A pane moved between workspaces: the handoff file keeps the old key; the session id finds it.
rm_stamp; panes w9:p1 sid-moved
: > "$SANDBOX/sid-moved.jsonl"; echo "$SANDBOX/sid-moved.jsonl" > "$S/agent-handoff/w5:p3.md.transcript"
mr HANDOFF_KEY=w5:p3 MAIN_REFRESH_HOUR=05
check "a moved pane: handoff found by session id, /clear to the listed id" "$(grep -c '^agent prompt w9:p1 /clear$' "$SANDBOX/herdr-log")|$(told)" "1|--box-ready w9:p1 claude|w9:p1 [main-refresh] 새벽|--box-ready w9:p1 claude"
rm -f "$S/agent-handoff/w5:p3.md.transcript"; set_main

# The Mac slept past the window: the hour check repeats while waiting and right before /clear.
rm_stamp; echo 05 > "$SANDBOX/hour"
mr PATH="$SANDBOX/bin:$PATH" GO_LATE=1
check "woke at 09: no /clear" "$(cleared)" 0
[ ! -f "$STAMP" ] || fail "woke late leaves no stamp"
check "woke late: announced" "$(grep -c '건너뜀' "$SANDBOX/alerts")" 1

# Woke at 09:00 during the first wait, pane idle: the request must not land in a pane in daytime use.
rm_stamp; echo 05 > "$SANDBOX/hour"; touch "$SANDBOX/late-on-get"
mr PATH="$SANDBOX/bin:$PATH"
check "woke before the request: no pane-tell at all" "$(told)|$(cleared)" "|0"
[ ! -f "$STAMP" ] || fail "woke before the request leaves no stamp"
rm "$SANDBOX/late-on-get"

# The hour is rechecked in the handoff wait itself (a long wait would otherwise run on into the morning).
rm_stamp; echo 05 > "$SANDBOX/hour"
mr PATH="$SANDBOX/bin:$PATH" GO_LATE=1 HANDOFF_AFTER=never MAIN_REFRESH_HANDOFF_WAIT=5
check "late in the wait: stops on the hour, not the timeout" "$(grep -c '05~06시가 지나' "$S/orchestrator/refresh.log")|$(grep -c '인계가 저장되지 않아' "$S/orchestrator/refresh.log")" "1|0"

# A pane that went back to work after saving is not interrupted.
rm_stamp; status idle; mr WORK_AFTER=1 MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "handoff saved but pane working: no /clear, announced" "$(cleared)|$(grep -c '건너뜀' "$SANDBOX/alerts")" "0|1"
status idle

# launchd has no pane id, but a run from inside the main pane must not be refused as "this pane".
rm_stamp; mr HERDR_PANE_ID=w5:p3 MAIN_REFRESH_HOUR=05
check "HERDR_PANE_ID never reaches pane-tell" "$(cat "$SANDBOX/tell-env")" unset

# pane-tell or herdr failing is reported, never stamped.
rm_stamp; touch "$SANDBOX/tell-fails"; mr MAIN_REFRESH_HOUR=05
check "pane-tell fails: no /clear, announced" "$(cleared)|$(grep -c '건너뜀' "$SANDBOX/alerts")" "0|1"
rm "$SANDBOX/tell-fails"; touch "$SANDBOX/prompt-fails"; rm_stamp; mr MAIN_REFRESH_HOUR=05
check "agent prompt fails: announced, no stamp" "$(grep -c '건너뜀' "$SANDBOX/alerts")|$([ -f "$STAMP" ] && echo stamped)" "1|"
rm "$SANDBOX/prompt-fails"

[ "$fails" = 0 ] && echo "all ok"
exit "$fails"
