#!/usr/bin/env bash
set -uo pipefail
unset HERDR_SOCKET_PATH HERDR_PANE_ID XDG_STATE_HOME # never the live herdr, pane or state

SUBJECT="$(cd "$(dirname "$0")" && pwd)/main-refresh"
SANDBOX="$(mktemp -d)"
TEST_TERM="term_M_$$"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
failed_checks=()
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else fail "$1: expected [$3] got [$2]"; fi; }
fail() { echo "FAIL $1"; failed_checks+=("$1"); fails=1; }

mkdir -p "$SANDBOX/state/orchestrator" "$SANDBOX/state/agent-handoff" "$SANDBOX/bin"
# Fake herdr: every call is logged; `pane get` answers the pane in $SANDBOX/panes with the status in $SANDBOX/status.
cat > "$SANDBOX/herdr" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$SANDBOX/herdr-log"
if [ "\$1 \$2" = 'pane get' ] && [ -n "\${QUERY_START_DELAY:-}" ] && [ ! -f "$SANDBOX/query-started" ]; then
  : > "$SANDBOX/query-started"
  sleep "\$QUERY_START_DELAY"
fi
case "\$1 \$2" in
  "pane list") cat "$SANDBOX/panes" ;;
  "pane get") [ ! -f "$SANDBOX/late-on-get" ] || echo 09 > "$SANDBOX/hour"
    # The main pane starts answering between the readiness check and the session-id check.
    if [ -n "\${BUSY_BEFORE_REQUEST:-}" ] && [ -f "$SANDBOX/box-n" ] && [ ! -f "$SANDBOX/request-n" ]; then
      n=\$((\$(cat "$SANDBOX/ready-get-n" 2>/dev/null || echo 0) + 1)); echo "\$n" > "$SANDBOX/ready-get-n"
      if [ "\$n" = 2 ]; then
        echo working > "$SANDBOX/status"
        ( sleep 0.3; echo idle > "$SANDBOX/status" ) >/dev/null 2>&1 &
      fi
    fi
    # the session the pane list gives, or the one in cur-sid: a clear that happened meanwhile
    sid=\$(cat "$SANDBOX/cur-sid" 2>/dev/null || jq -r --arg p "\$3" '.result.panes[] | select(.pane_id == \$p) | .agent_session.value' "$SANDBOX/panes")
    # An empty $SANDBOX/agent is a pane whose agent has exited and left its shell: herdr 0.9 then omits the key (seen 11/13).
    jq -n --arg s "\$(cat "$SANDBOX/status")" --arg p "\$3" --arg sid "\$sid" --arg a "\$(cat "$SANDBOX/agent" 2>/dev/null || echo claude)" \
      '{result: {pane: ({pane_id: \$p, agent_status: \$s, agent_session: {value: \$sid}} + (if \$a == "" then {} else {agent: \$a} end))}}' ;;
  "notification show") echo "\$3" >> "$SANDBOX/alerts"; echo legacy >> "$SANDBOX/legacy-alerts" ;;
  "agent prompt") [ ! -f "$SANDBOX/prompt-fails" ] || exit 1
    [ "\$4" != /clear ] || echo sid-refreshed > "$SANDBOX/cur-sid"
    # /exit returns before the process ends (EXIT_DELAY); exit-ignored: it never does.
    # bg-dialog: background tasks make Claude ask first, with the cursor on the line in that file.
    if [ "\$4" = /exit ] && [ -f "$SANDBOX/bg-dialog" ]; then
      printf '   Background work is running\n   The following will stop when you exit:\n%s\n' "\$(cat "$SANDBOX/bg-dialog")" > "$SANDBOX/screen"
    elif [ "\$4" = /exit ] && [ ! -f "$SANDBOX/exit-ignored" ]; then
      ( sleep "\${EXIT_DELAY:-0}"; : > "$SANDBOX/agent"; echo unknown > "$SANDBOX/status"; : > "$SANDBOX/cur-sid" ) >/dev/null 2>&1 &
    fi
    # LATE_ON_EXIT: the 05:00 run's clock passes 07:00 while claude exits.
    [ "\$4" != /exit ] || [ -z "\${LATE_ON_EXIT:-}" ] || echo 09 > "$SANDBOX/hour" ;;
  "pane read") cat "$SANDBOX/screen" 2>/dev/null ;;
  "agent send-keys") # Enter on "Exit and stop tasks" ends the process; Enter elsewhere is logged as unsafe.
    if [ "\$4" = enter ] && grep -q '❯ 1\. Exit and stop tasks' "$SANDBOX/screen" 2>/dev/null; then
      rm -f "$SANDBOX/screen"; : > "$SANDBOX/agent"; echo unknown > "$SANDBOX/status"; : > "$SANDBOX/cur-sid"
    else echo "keys-without-dialog \$4" >> "$SANDBOX/unsafe-run"; fi ;;
  "pane run") [ -z "\$(cat "$SANDBOX/agent" 2>/dev/null || echo claude)" ] || echo typed-into-agent >> "$SANDBOX/unsafe-run"
    [ ! -f "$SANDBOX/run-fails" ] || exit 1
    # run-no-session: claude starts but no new session ever shows idle.
    [ ! -f "$SANDBOX/run-no-session" ] || { echo claude > "$SANDBOX/agent"; exit 0; }
    echo claude > "$SANDBOX/agent"; echo sid-restarted > "$SANDBOX/cur-sid"; echo idle > "$SANDBOX/status" ;;
  *) exit 2 ;;
esac
EOS
cat > "$SANDBOX/error-notify" <<EOS
#!/usr/bin/env bash
printf '%s|%s\n' "\$1" "\$2" >> "$SANDBOX/errors"
[ ! -f "$SANDBOX/notify-fails" ]
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
  if [ "\$n" = 1 ] && [ -n "\${BOX_RECENT_FIRST:-}" ]; then
    echo '{"type":"user","message":{"content":"new work during readiness wait"}}' >> "\$(cat "$SANDBOX/state/agent-handoff/w5:p3.md.transcript")"
    exit 1
  fi
  [ "\$n" != 1 ] || [ -z "\${BOX_LATE_FIRST:-}" ] || echo 09 > "$SANDBOX/hour"
  if [ "\$n" -ge 2 ]; then
    [ -z "\${BOX_DROP_ONCE:-}" ] || [ "\$n" != 2 ] || rm -f "$SANDBOX/state/agent-handoff/w5:p3.md"
    [ -z "\${BOX_TURN:-}" ] || echo '{"type":"user","message":{"content":"typed during the box check"}}' >> "\$(cat "$SANDBOX/state/agent-handoff/w5:p3.md.transcript")"
    [ -z "\${BOX_WORK:-}" ] || echo working > "$SANDBOX/status"
    [ -z "\${BOX_LATE:-}" ] || echo 09 > "$SANDBOX/hour"
    [ -z "\${BOX_FULL_AFTER:-}" ] || exit 1
  fi
  [ -z "\${BOX_FULL:-}" ]; exit
fi
if [ -n "\${QUEUE_THEN_FAIL:-}" ]; then
  mkdir -p "$SANDBOX/state/pane-tell/waiting"
  printf '%s' "\$2" > "$SANDBOX/state/pane-tell/waiting/failed-registration"
  exit 1
fi
[ ! -f "$SANDBOX/tell-fails" ] || exit 1
case "\$2" in *"handoff save"*)
  [ "\$(cat "$SANDBOX/status")" != working ] || echo busy >> "$SANDBOX/unsafe-request"
  n=\$((\$(cat "$SANDBOX/request-n" 2>/dev/null || echo 0) + 1)); echo "\$n" > "$SANDBOX/request-n"
  t=\$(cat "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md.transcript")
  if [ -n "\${DELIVER_AFTER:-}" ]; then
    # A real queued request returns before delivery or the eventual handoff save.
    if [ -n "\${QUEUED_STALE:-}" ]; then
      echo unrelated-handoff > "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md"
      echo working > "$SANDBOX/status"
    fi
    ( sleep "\$DELIVER_AFTER"
      jq -nc --arg c "\$2" '{type: "user", message: {content: \$c}}' >> "\$t"
      if [ -n "\${QUEUED_STALE:-}" ]; then echo idle > "$SANDBOX/status"; sleep 1; fi
      echo working > "$SANDBOX/status"
      sleep 0.1
      printf '%s\\n%s\\n' "\${2##* }" delivered-handoff > "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md"
      echo idle > "$SANDBOX/status"
    ) >/dev/null 2>&1 &
    echo "queued for test"; exit 0
  fi
  jq -nc --arg c "\$2" '{type: "user", message: {content: \$c}}' >> "\$t"
  if [ -n "\${NEW_TURN_ONCE:-}" ] && [ "\$n" = 1 ]; then
    echo '{"type":"attachment","attachment":{"type":"queued_command","prompt":"new work must reach the next handoff"}}' >> "\$t"
  fi
  case "\${NEW_TURN:-}" in
    yes) echo '{"type":"user","message":{"content":"a new message"}}' >> "\$t" ;;
    mention) echo '{"type":"user","message":{"content":"이 [main-refresh] 메시지는 무슨 뜻인가요?"}}' >> "\$t" ;;
    meta) echo '{"type":"user","isMeta":true,"message":{"content":"meta line"}}' >> "\$t"
          echo '{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}' >> "\$t" ;;
    image) echo '{"type":"user","message":{"content":[{"type":"image"},{"type":"text","text":"hi"}]}}' >> "\$t" ;;
    queued) echo '{"type":"attachment","attachment":{"type":"queued_command","prompt":"typed while it saved"}}' >> "\$t" ;;
    compact) echo '{"type":"user","isCompactSummary":true,"message":{"content":"This session is being continued from a previous conversation."}}' >> "\$t" ;;
    cleared) echo sid-other > "$SANDBOX/cur-sid" ;;
  esac
  [ -z "\${GO_LATE:-}" ] || echo 09 > "$SANDBOX/hour"
  [ -z "\${WORK_AFTER:-}" ] || echo working > "$SANDBOX/status"
  if [ "\${HANDOFF_AFTER:-0}" != never ]; then ( sleep "\${HANDOFF_AFTER:-0}"; if [ -n "\${HANDOFF_EMPTY:-}" ]; then : > "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md"; else printf '%s\\n%s\\n' "\${2##* }" "handoff-\$n" > "$SANDBOX/state/agent-handoff/\${HANDOFF_KEY:-w5:p3}.md"; fi ); fi
  if [ -n "\${TELL_UNKNOWN:-}" ]; then echo "unknown"; elif [ -n "\${QUEUED:-}" ]; then echo "queued (box busy)"; else echo "told x"; fi ;;
esac
exit 0
EOS
# date shim: +%H comes from $SANDBOX/hour, +%s from $SANDBOX/frozen-s when it exists, everything else is the real date.
cat > "$SANDBOX/bin/date" <<EOS
#!/bin/bash
if [ "\${1:-}" = +%H ]; then cat "$SANDBOX/hour"
elif [ "\${1:-}" = +%s ] && [ -f "$SANDBOX/frozen-s" ]; then cat "$SANDBOX/frozen-s"
else exec /bin/date "\$@"; fi
EOS
# pgrep shim: a message lands in the main transcript while the last checks run, and nothing waits for the pane.
mkdir -p "$SANDBOX/pg"
cat > "$SANDBOX/pg/pgrep" <<EOS
#!/bin/bash
echo '{"type":"user","message":{"content":"typed during the last checks"}}' >> "$SANDBOX/sid-main.jsonl"
exit 1
EOS
chmod +x "$SANDBOX/herdr" "$SANDBOX/error-notify" "$SANDBOX/pane-tell" "$SANDBOX/bin/date" "$SANDBOX/pg/pgrep"

# A delivery appears during the final checks, then disappears before the retry. It adds no new transcript message.
mkdir -p "$SANDBOX/retry-pg"
cat > "$SANDBOX/retry-pg/pgrep" <<EOS
#!/bin/bash
if [ -f "$SANDBOX/state/agent-handoff/w5:p3.md" ] && [ "\$(cat "$SANDBOX/box-n" 2>/dev/null)" = 2 ] && [ ! -f "$SANDBOX/pending-once" ]; then
  : > "$SANDBOX/pending-once"
  exit 0
fi
exit 1
EOS
chmod +x "$SANDBOX/retry-pg/pgrep"

S="$SANDBOX/state"
# A sandboxed home and dotfiles copy for main-startup: what the main pane's claude loads only when it starts.
SH_=$SANDBOX/home
mkdir -p "$SH_/.claude/output-styles" "$SH_/.claude/skills/demo/references" "$SH_/.claude/plugins" "$SH_/work" \
  "$SANDBOX/repo/tools/agent-hooks" "$SANDBOX/repo/tools/claude" "$SANDBOX/repo/scripts"
echo '{"model":"opus","enabledPlugins":{"a@b":true},"outputStyle":"plain"}' > "$SH_/.claude/settings.json"
echo '{"version":2,"plugins":{}}' > "$SH_/.claude/plugins/installed_plugins.json"
echo '{}' > "$SH_/.claude/plugins/known_marketplaces.json"
echo '# plain' > "$SH_/.claude/output-styles/plain.md"
printf -- '---\nname: demo\ndescription: first\n---\nbody\n' > "$SH_/.claude/skills/demo/SKILL.md"
REAL_STARTUP="$(dirname "$SUBJECT")/main-startup"
cp "$REAL_STARTUP" "$SANDBOX/repo/tools/agent-hooks/main-startup"
cp "$(dirname "$SUBJECT")/../claude/startup-profile.json" "$SANDBOX/repo/tools/claude/"
cp "$(dirname "$SUBJECT")/../../scripts/claude-online" "$SANDBOX/repo/scripts/"
# The copy reads the same file contents as main-refresh's own helper, so both share one record.
ms() { (cd "$SH_/work" && env HOME="$SH_" XDG_STATE_HOME="$S" /bin/bash "$SANDBOX/repo/tools/agent-hooks/main-startup" "$@"); }
ms record # the main pane's claude started with what is there now
mr() { # every call is sandboxed: state dir, herdr, pane-tell
  rm -f "$SANDBOX/query-started"
  rm -f "$SANDBOX/cur-sid"
  rm -f "$SANDBOX/agent" "$SANDBOX/unsafe-run" "$SANDBOX/screen"
  rm -f "$SANDBOX/told" "$SANDBOX/herdr-log" "$SANDBOX/box-n" "$SANDBOX/request-n" "$SANDBOX/ready-get-n" "$SANDBOX/unsafe-request" "$SANDBOX/pending-once" "$S/orchestrator/refresh.log"; : > "$SANDBOX/alerts"; : > "$SANDBOX/errors"
  env HOME="$SH_" XDG_STATE_HOME="$S" HERDR_BIN_PATH="$SANDBOX/herdr" HERDR_ERROR_NOTIFY="$SANDBOX/error-notify" MAIN_REFRESH_PANE_TELL="$SANDBOX/pane-tell" \
    MAIN_REFRESH_POLL=0.05 MAIN_REFRESH_QUIET=0 MAIN_REFRESH_RETRY_QUIET=0 MAIN_REFRESH_DEADLINE="${MAIN_REFRESH_DEADLINE:-60}" MAIN_REFRESH_HANDOFF_WAIT="${MAIN_REFRESH_HANDOFF_WAIT:-60}" \
    "$@" /bin/bash "$SUBJECT" ${MR_ARGS:-} >/dev/null 2>&1
}
told() { [ -f "$SANDBOX/told" ] && awk '{print $1, $2, $3}' "$SANDBOX/told" | paste -sd'|' - || true; }
cleared() { grep -c '^agent prompt w5:p3 /clear$' "$SANDBOX/herdr-log" 2>/dev/null || true; }
status() { echo "$1" > "$SANDBOX/status"; }
panes() { # <pane id> <session id>
  jq -nc --arg p "$1" --arg s "$2" --arg term "$TEST_TERM" '{result: {panes: [{pane_id: "w1:p9", terminal_id: "term_x", agent_session: {value: "other"}}, {pane_id: $p, terminal_id: $term, agent_session: {value: $s}}]}}' > "$SANDBOX/panes"
}
set_main() { # the main pane, and a transcript for its session that the handoff key's file names
  echo "$TEST_TERM" > "$S/orchestrator/main"; panes w5:p3 sid-main
  : > "$SANDBOX/sid-main.jsonl"; echo "$SANDBOX/sid-main.jsonl" > "$S/agent-handoff/w5:p3.md.transcript"
}
rm_main() { rm -f "$S/orchestrator/main"; }
rm_stamp() { rm -f "$S/orchestrator"/refreshed-*; rm -f "$S"/agent-handoff/*.md "$S"/agent-handoff/*.md.skipped "$S"/agent-handoff/*.refresh-request*; }
STAMP="$S/orchestrator/refreshed-$(date +%F)"

set_main; status idle
# A timeout ends a watcher process, not the request already delivered to the main pane.
# A second tick must resume waiting, and only that request's saved nonce acknowledges it.
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
original_request=$(grep 'handoff save' "$SANDBOX/told")
original_token=${original_request##* }
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a later process does not resend an unacknowledged request" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
cp "$SANDBOX/sid-main.jsonl" "$SANDBOX/sid-main-moved.jsonl"
echo "$SANDBOX/sid-main-moved.jsonl" > "$S/agent-handoff/w5:p3.md.transcript"
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a moved transcript for the same session does not cause a resend" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
mv "$S/agent-handoff/w5:p3.md.transcript" "$S/agent-handoff/w8:p2.md.transcript"
MR_ARGS=--now mr HANDOFF_KEY=w8:p2 HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a new handoff key for the same session does not cause a resend" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
mv "$S/agent-handoff/w8:p2.md.transcript" "$S/agent-handoff/w5:p3.md.transcript"
printf '%s\n' '[request:earlier-ack]' > "$S/agent-handoff/main-refresh.refresh-request.ack"
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "an earlier acknowledgment cannot unlock a pending request" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
rm -f "$S/agent-handoff/main-refresh.refresh-request.ack"
printf '%s\nwrong response\n' '[request:unrelated]' > "$S/agent-handoff/w5:p3.md"
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "another request's save does not release the pending request" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
printf '%s\nlate saved response\n' "$original_token" > "$S/agent-handoff/w5:p3.md"
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "the original save acknowledges across processes without another request" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|1"
rm_stamp; set_main
# A save may arrive after timeout while the pane is busy with other work. Moving that memo aside
# must not forget that the pending request was answered; a later fresh request is then allowed.
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
busy_request=$(grep 'handoff save' "$SANDBOX/told")
printf '%s\nlate save before more work\n' "${busy_request##* }" > "$S/agent-handoff/w5:p3.md"
status working
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=0.3
status idle
MR_ARGS=--now mr MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a save observed while busy permits a fresh request after that work" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "1|1"
rm_stamp; set_main
# Unknown delivery could have succeeded. Do not replay it merely because the sender could not confirm it.
MR_ARGS=--now mr TELL_UNKNOWN=1 HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "uncertain delivery remains pending instead of replaying" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
# A new session owns a new request; an unanswered request for the old session must not block it.
panes w5:p3 sid-new
: > "$SANDBOX/sid-new.jsonl"; echo "$SANDBOX/sid-new.jsonl" > "$S/agent-handoff/w5:p3.md.transcript"
MR_ARGS=--now mr MAIN_REFRESH_HOUR=09
check "a changed session may receive its own request" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "1|1"
rm_stamp; set_main
# Two watcher processes can overlap; the process lock still admits only the first sender.
rm -f "$SANDBOX/request-n"
MR_ARGS=--now mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=09 MAIN_REFRESH_HANDOFF_WAIT=1 & watcher=$!
for tick in $(seq 1 100); do [ ! -f "$SANDBOX/request-n" ] || break; sleep 0.02; done
env XDG_STATE_HOME="$S" HERDR_BIN_PATH="$SANDBOX/herdr" HERDR_ERROR_NOTIFY="$SANDBOX/error-notify" \
  MAIN_REFRESH_PANE_TELL="$SANDBOX/pane-tell" MAIN_REFRESH_HOUR=09 /bin/bash "$SUBJECT" --now >/dev/null 2>&1
wait "$watcher"
check "overlapping watcher processes send only one request" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "1|0"
rm_stamp; set_main
mr MAIN_REFRESH_HOUR=09; check "outside 05-06 nothing is sent or alerted" "$(told)|$(cat "$SANDBOX/errors")" "|"
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
check "a working pane is a routine skip, without an error alert" "$(cat "$SANDBOX/errors")" ""
[ ! -f "$STAMP" ] || fail "a skip leaves no stamp"
status idle; mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "no handoff written: no /clear" "$(told)|$(cleared)" "--box-ready w5:p3 claude|w5:p3 [main-refresh] 새벽|0"
check "no handoff: error gateway receives the warning" "$(grep -c '인계가 저장되지 않아' "$SANDBOX/errors")" 1
check "no handoff: logged" "$(grep -c '인계가 저장되지 않아' "$S/orchestrator/refresh.log")" 1
[ ! -f "$STAMP" ] || fail "no handoff leaves no stamp"
rm_stamp; mr BOX_FULL=1 MAIN_REFRESH_HOUR=05   # 요유 left words in the box: --box-ready fails
check "a full box from the start: no request, no /clear" "$(told)|$(cleared)" "--box-ready w5:p3 claude|0"
check "the skip is logged" "$(grep -c '입력창' "$S/orchestrator/refresh.log")" 1
check "typed input is a routine skip, without an error alert" "$(cat "$SANDBOX/errors")" ""
[ ! -f "$STAMP" ] || fail "a full box leaves no stamp"

# The box fills between the request and the clear: no /clear, and the memo is set aside.
rm_stamp; mr BOX_FULL_AFTER=1 MAIN_REFRESH_HOUR=05
check "box full after the request: no /clear" "$(told)|$(cleared)" "--box-ready w5:p3 claude|w5:p3 [main-refresh] 새벽|--box-ready w5:p3 claude|0"
check "typed input after the request sends no error alert" "$(cat "$SANDBOX/errors")" ""
[ ! -f "$S/agent-handoff/w5:p3.md" ] && [ -f "$S/agent-handoff/w5:p3.md.skipped" ] || fail "a skipped run sets its memo aside"
rm_stamp
# A queued result is followed until its request and handoff are visible, in the daily run too.
mr QUEUED=1 MAIN_REFRESH_HOUR=05
check "queued request with delivered transcript and fresh handoff: /clear" "$(cleared)|$(grep -c '대기열' "$S/orchestrator/refresh.log")" "1|1"
rm_stamp; mr HANDOFF_EMPTY=1 MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "an empty handoff does not count" "$(cleared)" 0
rm_stamp; mr NEW_TURN=yes MAIN_REFRESH_HOUR=05
check "a new message after the save: no /clear, announced" "$(cleared)|$(grep -c '새 대화' "$S/orchestrator/refresh.log")" "0|1"
rm_stamp; mr NEW_TURN=meta MAIN_REFRESH_HOUR=05
check "meta and tool-result lines are not new messages" "$(cleared)" 1
rm_stamp; mr NEW_TURN=image MAIN_REFRESH_HOUR=05
check "a message with an image after the save: no /clear, announced" "$(cleared)|$(grep -c '새 대화' "$S/orchestrator/refresh.log")" "0|1"
rm_stamp; mr NEW_TURN=mention MAIN_REFRESH_HOUR=05
check "a later message that mentions the marker: no /clear" "$(cleared)|$(grep -c '새 대화' "$S/orchestrator/refresh.log")" "0|1"
rm_stamp; mr BOX_TURN=1 MAIN_REFRESH_HOUR=05
check "a message typed during the box check: no /clear" "$(cleared)" 0
rm_stamp; echo 05 > "$SANDBOX/hour"; mr PATH="$SANDBOX/bin:$PATH" BOX_LATE_FIRST=1
check "hour passed during the first box check: no request" "$(told)|$(cleared)" "--box-ready w5:p3 claude|0"
rm_stamp; status idle; mr BOX_WORK=1 MAIN_REFRESH_HOUR=05; status idle
check "pane busy again after the box check: no /clear" "$(cleared)" 0
rm_stamp; echo 05 > "$SANDBOX/hour"; mr PATH="$SANDBOX/bin:$PATH" BOX_LATE=1
check "hour passed during the box check: no /clear" "$(cleared)" 0

# A turn that just ended: wait for quiet. An old transcript goes ahead.
rm_stamp; touch "$SANDBOX/sid-main.jsonl"; mr MAIN_REFRESH_QUIET=600 MAIN_REFRESH_HOUR=05 MAIN_REFRESH_DEADLINE=0.3
check "recent turn: not asked or alerted" "$(told)|$(cat "$SANDBOX/errors")" "|"
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
# Saved before the request but in the same second (check pane, 11/05): whole-second times call it new.
rm_stamp; echo 09 > "$SANDBOX/hour"; touch -t 202001010000 "$SANDBOX/sid-main.jsonl"
echo old > "$S/agent-handoff/w5:p3.md"; stat -f %m "$S/agent-handoff/w5:p3.md" > "$SANDBOX/frozen-s"
MR_ARGS=--now mr PATH="$SANDBOX/bin:$PATH" HANDOFF_AFTER=never MAIN_REFRESH_HANDOFF_WAIT=0.3; rm -f "$SANDBOX/frozen-s"
check "a handoff from before the request, same second: no /clear" "$(cleared)|$(grep -c '인계가 저장되지 않아' "$S/orchestrator/refresh.log")" "0|1"
[ -f "$S/agent-handoff/w5:p3.md" ] || fail "the skip leaves the older handoff where it was"

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
check "woke late: no error alert" "$(cat "$SANDBOX/errors")" ""

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
check "handoff saved but pane working: no /clear or error alert" "$(cleared)|$(cat "$SANDBOX/errors")" "0|"
status idle

# launchd has no pane id, but a run from inside the main pane must not be refused as "this pane".
rm_stamp; mr HERDR_PANE_ID=w5:p3 MAIN_REFRESH_HOUR=05
check "HERDR_PANE_ID never reaches pane-tell" "$(cat "$SANDBOX/tell-env")" unset

# pane-tell or herdr failing is reported, never stamped.
rm_stamp; touch "$SANDBOX/tell-fails"; mr MAIN_REFRESH_HOUR=05
check "pane-tell fails: no /clear, error gateway called" "$(cleared)|$(grep -c '인계 요청을 보내지 못해' "$SANDBOX/errors")" "0|1"
rm "$SANDBOX/tell-fails"
# No state cleanup between processes: a definite send failure must not leave a request waiting for an impossible answer.
mr MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a definite send failure recovers on the next run" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "1|1"
rm_stamp; mr QUEUE_THEN_FAIL=1 MAIN_REFRESH_HOUR=05
mr HANDOFF_AFTER=never MAIN_REFRESH_HOUR=05 MAIN_REFRESH_HANDOFF_WAIT=0.3
check "a failed command with a queued nonce does not resend" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
rm -rf "$S/pane-tell"
touch "$SANDBOX/prompt-fails"; rm_stamp; mr MAIN_REFRESH_HOUR=05
check "agent prompt fails: error gateway called, no stamp" "$(grep -c '/clear를 보내지 못해' "$SANDBOX/errors")|$([ -f "$STAMP" ] && echo stamped)" "1|"
rm "$SANDBOX/prompt-fails"
rm_stamp; mr TELL_UNKNOWN=1 MAIN_REFRESH_HOUR=05
check "an unrecognized delivery result uses the error gateway" "$(cleared)|$(grep -c '결과를 확인하지 못해' "$SANDBOX/errors")" "0|1"
rm_stamp; touch "$SANDBOX/notify-fails" "$SANDBOX/tell-fails"; mr MAIN_REFRESH_HOUR=05
check "a failed error gateway is recorded locally" "$(grep -c '오류 알림을 보내지 못함' "$S/orchestrator/refresh.log")" 1
rm "$SANDBOX/notify-fails" "$SANDBOX/tell-fails"

# Both requests must ask for open state only. The emitted pane-tell payload is the agent's contract: adding general
# history or lessons here would make every fresh main session load them again.
handoff_contract() {
  local mode="$1" request
  request="$(grep 'handoff save' "$SANDBOX/told")"
  check "$mode limits the handoff to five open-state categories" "$(printf '%s' "$request" | grep -c '열린 상태 다섯 가지만')" 1
  check "$mode enumerates exactly five categories" "$(printf '%s' "$request" | grep -oE '\([1-5]\)' | tr '\n' ' ')" "(1) (2) (3) (4) (5) "
  case "$request" in *"부하 세션"*"각 세션의 일"*) ;; *) fail "$mode keeps running subordinate sessions and their work" ;; esac
  case "$request" in *"감시 중인 Slack 스레드"*"재감시 명령"*) ;; *) fail "$mode keeps watched threads and restart commands" ;; esac
  case "$request" in *"답을 기다리는 요유 질문"*"회사 결정은 보드 링크"*"개인 결정은 파일 위치"*) ;; *) fail "$mode locates unanswered questions and company/personal decisions" ;; esac
  case "$request" in *"백그라운드로 도는 작업(main-refresh 자신은 빼고)"*"재시작 명령(명령 전문"*"pgrep -f"*) ;; *) fail "$mode keeps background restart commands and skips running copies" ;; esac
  case "$request" in *"오늘 남은 일"*"아직 처리하지 않은 요청은 원문 포함"*) ;; *) fail "$mode keeps the remaining work and unprocessed original requests" ;; esac
  case "$request" in *"배운 요령은 ~/.claude/skills/main-orchestrator/references/에 적고 인계에는 쓰지 마세요"*) ;; *) fail "$mode sends lessons to references instead of the handoff" ;; esac
  case "$request" in *"'앞 인계 그대로' 같은 사슬 참조는 쓰지 말고 필요한 줄은 직접 옮겨 적으세요"*) ;; *) fail "$mode forbids chained handoffs and requires the needed lines directly" ;; esac
  case "$request" in *"모아 둔 결정"*) fail "$mode must not request accumulated decisions" ;; esac
  HANDOFF_CONTRACT="${request#*열린 상태}"
  HANDOFF_CONTRACT="${HANDOFF_CONTRACT% handoff save*}"
}
rm_stamp; touch -t 202001010000 "$SANDBOX/sid-main.jsonl"; mr MAIN_REFRESH_HOUR=05
check "the daily request names the watches and the background jobs" "$(grep -c '재감시' "$SANDBOX/told")|$(grep -c '백그라운드' "$SANDBOX/told")" "1|1"
handoff_contract "the daily request"; daily_contract="$HANDOFF_CONTRACT"

# --now: at any hour, as often as asked, through the same gates (2026-11-05: nobody types /clear in the main pane any more).
now() { MR_ARGS=--now mr "$@"; }
rm_stamp; now MAIN_REFRESH_HOUR=09
check "--now outside 05-06: asks, then clears" "$(told)|$(cleared)" "--box-ready w5:p3 claude|w5:p3 [main-refresh] 대화가|--box-ready w5:p3 claude|1"
[ ! -f "$STAMP" ] || fail "--now leaves no daily stamp, so 05:00 still runs"
check "--now names the watches and the background jobs" "$(grep -c '재감시' "$SANDBOX/told")|$(grep -c '백그라운드' "$SANDBOX/told")" "1|1"
handoff_contract "--now"; check "daily and --now use the same handoff contract" "$HANDOFF_CONTRACT" "$daily_contract"
check "--now announces nothing when it went through" "$(cat "$SANDBOX/alerts")" ""
rm -f "$S"/agent-handoff/*.md; now MAIN_REFRESH_HOUR=09; check "--now runs again the same day, with what the first left" "$(cleared)" 1
# Its own wait for rest is 30 minutes, not the night's count down to 07:00 (0 in the day).
rm_stamp; status working; ( sleep 1; echo idle > "$SANDBOX/status" ) & now MAIN_REFRESH_DEADLINE= MAIN_REFRESH_HOUR=09; wait
check "--now waits for a busy pane to rest" "$(cleared)" 1
# Words that reach the pane while it saves are recorded as a queued_command attachment, not a user line (review, 2026-11-05).
rm_stamp; now NEW_TURN=queued QUERY_START_DELAY=5.2 MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=30
check "--now with words queued during every save: no /clear" "$(cleared)|$(grep -q '새 대화' "$S/orchestrator/refresh.log" && echo 1 || echo 0)" "0|1"
rm_stamp; mr NEW_TURN=queued MAIN_REFRESH_HOUR=05; check "the night run too" "$(cleared)" 0
rm_stamp; now NEW_TURN=compact MAIN_REFRESH_HOUR=09; check "a compaction summary is no new message" "$(cleared)" 1
# Another run cleared the pane meanwhile: this run's checks read the old session, so it stops.
rm_stamp; now NEW_TURN=cleared MAIN_REFRESH_HOUR=09; rm -f "$SANDBOX/cur-sid"
check "a session that changed since the start: no /clear" "$(cleared)|$(grep -c '세션이 바뀌어' "$S/orchestrator/refresh.log")" "0|1"
check "a changed session sends no error alert" "$(cat "$SANDBOX/errors")" ""
# One run at a time: a second call while one waits does nothing; a lock left by a run that died 2 hours ago does not block.
rm_stamp; mkdir "$S/orchestrator/refresh.lock"; now MAIN_REFRESH_HOUR=09
check "a run in progress: herdr and pane-tell untouched" "$(told)|$([ -f "$SANDBOX/herdr-log" ] && echo called)|$(grep -c '진행 중' "$S/orchestrator/refresh.log")" "||1"
[ -d "$S/orchestrator/refresh.lock" ] || fail "the other run's lock stays"
touch -t 202001010000 "$S/orchestrator/refresh.lock"; now MAIN_REFRESH_HOUR=09
check "a stale lock: goes ahead" "$(cleared)" 1
[ ! -d "$S/orchestrator/refresh.lock" ] || fail "a run removes its lock"
# Queued main relays now survive the session change, so they do not delay clear.
rm_stamp; ( exec -a "pane-tell --deliver msg $TEST_TERM sid-main term_x " sleep 30 ) & pend=$!
now QUERY_START_DELAY=0.6 MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=12; kill "$pend" 2>/dev/null; wait "$pend" 2>/dev/null
check "a message waiting for the main pane does not delay /clear" "$(cleared)" "1"
# A message that lands during the last input checks is still caught: the transcript is read last.
rm_stamp; now BOX_TURN=1 QUERY_START_DELAY=5.2 MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=30
check "a message during the last checks: no /clear" "$(cleared)|$(grep -q '새 대화' "$S/orchestrator/refresh.log" && echo 1 || echo 0)" "0|1"
# Started in the main pane's background, main-refresh must not be listed as a job to start again: it would loop.
rm_stamp; now MAIN_REFRESH_HOUR=09; check "the request leaves main-refresh itself out" "$(grep -c 'main-refresh 자신은 빼고' "$SANDBOX/told")" 1
rm_stamp; : > "$STAMP"; now MAIN_REFRESH_HOUR=05; check "--now ignores the daily stamp" "$(cleared)" 1
# These cases exercise retries after readiness, so their whole-flow budget must
# also allow startup. Short budget/quiet-period cases below remain unchanged.
rm_stamp; now BOX_FULL=1 QUERY_START_DELAY=2.2 MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=12
check "--now with words in the box: no request, no /clear" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "0|0"
check "the slow-start full-box case reaches the readiness check" "$([ -s "$SANDBOX/box-n" ] && [ "$(cat "$SANDBOX/box-n")" -gt 0 ] && echo yes)" yes
rm_stamp; now NEW_TURN=yes QUERY_START_DELAY=5.2 MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=30
check "--now with a message after every save: no /clear" "$(cleared)|$(grep -q '새 대화' "$S/orchestrator/refresh.log" && echo 1 || echo 0)" "0|1"
# Context refresh starts at the first idle turn, even with a fresh transcript.
rm_stamp; touch "$SANDBOX/sid-main.jsonl"; now MAIN_REFRESH_QUIET= MAIN_REFRESH_HOUR=09
check "--now right after a turn: clears without quiet wait" "$(cleared)" "1"
rm_stamp; touch -t "$(date -v-2M +%Y%m%d%H%M.%S)" "$SANDBOX/sid-main.jsonl"; now MAIN_REFRESH_QUIET= MAIN_REFRESH_HOUR=09
check "--now 2 minutes after a turn: clears" "$(cleared)" 1
rm_stamp; touch -t "$(date -v-2M +%Y%m%d%H%M.%S)" "$SANDBOX/sid-main.jsonl"; mr MAIN_REFRESH_QUIET= MAIN_REFRESH_HOUR=05 MAIN_REFRESH_DEADLINE=0.3
check "the night run still waits 10 minutes" "$(told)|$(cleared)" "|0"
touch -t 202001010000 "$SANDBOX/sid-main.jsonl"
# Keep the quiet interval longer than the deadline, with room for parallel CI load.
rm_stamp; now BOX_RECENT_FIRST=1 MAIN_REFRESH_QUIET=60 MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=12
check "a turn during readiness wait needs no quiet interval" "$(grep -c 'handoff save' "$SANDBOX/told")|$(cleared)" "1|1"
rm_stamp; touch -t "$(date -v-10S +%Y%m%d%H%M.%S)" "$SANDBOX/sid-main.jsonl"; now MAIN_REFRESH_QUIET= MAIN_REFRESH_HOUR=09
check "--now proceeds with a recent transcript" "$(cleared)" 1

# A pane on a named herdr server keeps its handoff in that server's folder, as `handoff` writes it.
rm_stamp; mkdir -p "$S/agent-handoff/workers"; mv "$S/agent-handoff/w5:p3.md.transcript" "$S/agent-handoff/workers/"
now HERDR_SOCKET_PATH="$SANDBOX/sessions/workers/herdr.sock" HANDOFF_KEY=workers/w5:p3 MAIN_REFRESH_HOUR=09
check "a named server's handoff folder: found, cleared" "$(cleared)" 1
rm -rf "$S/agent-handoff/workers"; set_main

# Regression: the queued request arrives later, then the pane saves and rests. No second caller is needed.
rm_stamp; now DELIVER_AFTER=0.2 MAIN_REFRESH_HOUR=09
check "queued delivery is followed through to /clear" "$(cleared)|$(cat "$SANDBOX/request-n")|$(tail -n 1 "$S/agent-handoff/w5:p3.md" 2>/dev/null)" "1|1|delivered-handoff"
rm_stamp; now DELIVER_AFTER=0.2 QUEUED_STALE=1 MAIN_REFRESH_HOUR=09
check "a save before queued delivery cannot answer that request" "$(cleared)|$(tail -n 1 "$S/agent-handoff/w5:p3.md" 2>/dev/null)" "1|delivered-handoff"
sleep 1.2 # reap the delayed fake delivery even when the assertion above fails
rm_stamp; now PATH="$SANDBOX/retry-pg:$PATH" MAIN_REFRESH_HOUR=09
check "a retry reuses the saved handoff when no new message arrived" "$(cleared)|$(cat "$SANDBOX/request-n")" "1|1"
rm_stamp; now BUSY_BEFORE_REQUEST=1 MAIN_REFRESH_HOUR=09
check "a pane that starts answering during readiness is not interrupted" "$([ -f "$SANDBOX/unsafe-request" ] && cat "$SANDBOX/unsafe-request")|$(cleared)|$(cat "$SANDBOX/request-n")" "|1|1"
rm_stamp; now BOX_DROP_ONCE=1 MAIN_REFRESH_HOUR=09
check "a handoff removed during the final checks must be saved again" "$(cleared)|$(cat "$SANDBOX/request-n")|$(tail -n 1 "$S/agent-handoff/w5:p3.md" 2>/dev/null)" "1|2|handoff-2"
# Regression: words received after the request require a new handoff; clearing the first would lose them.
rm_stamp; now NEW_TURN_ONCE=1 MAIN_REFRESH_HOUR=09
check "new work triggers another handoff and then /clear" "$(cleared)|$(cat "$SANDBOX/request-n")|$(tail -n 1 "$S/agent-handoff/w5:p3.md" 2>/dev/null)" "1|2|handoff-2"
# The next answer must belong to a distinct request, even when the previous text is still last in the transcript.
check "each retry has a distinct request" "$(grep 'handoff save' "$SANDBOX/told" | sort -u | wc -l | tr -d ' ')" 2
# --restart (api#1204): a new process in place of /clear, since CLI settings such as the startup profile load only at start.
restart() { MR_ARGS=--restart mr "$@"; }
RESTART_LINE='_mr=$SECONDS; claude || (( SECONDS - _mr > 60 )) || CLAUDE_STARTUP_PROFILE=full claude'
rm_stamp; set_main; status idle
restart MAIN_REFRESH_HOUR=09 EXIT_DELAY=0.3
check "--restart exits and starts claude again instead of /clear" \
  "$(cleared)|$(grep -c '^agent prompt w5:p3 /exit$' "$SANDBOX/herdr-log")|$(grep -cxF "pane run w5:p3 $RESTART_LINE" "$SANDBOX/herdr-log")" "0|1|1"
check "--restart types claude only after the old process has left the shell" "$([ -f "$SANDBOX/unsafe-run" ] && cat "$SANDBOX/unsafe-run")" ""
check "--restart asks for a handoff that says the process restarts" "$(grep 'handoff save' "$SANDBOX/told" | grep -c '다시 켭니다')" 1
check "--restart releases the relay hold and the request once the new session is ready" \
  "$([ -f "$S/orchestrator/refresh-delivery.json" ] && echo held)|$([ -f "$S/agent-handoff/main-refresh.refresh-request" ] && echo kept)|$(grep -c '다시 켬' "$S/orchestrator/refresh.log")" "||1"
rm_stamp; set_main; status idle; touch "$SANDBOX/exit-ignored"
restart MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=2
rm -f "$SANDBOX/exit-ignored"
check "--restart never types into a claude that did not exit, and alerts" \
  "$(grep -c '^pane run' "$SANDBOX/herdr-log")|$(grep -c '끝나지 않아' "$SANDBOX/errors")" "0|1"
# /exit was accepted and may still end the process later: its handoff stays, and relays reach the pane either way.
check "an exit that outlasts the wait keeps the handoff and releases the relay hold" \
  "$([ -s "$S/agent-handoff/w5:p3.md" ] && echo kept)|$([ -f "$S/orchestrator/refresh-delivery.json" ] && echo held)" "kept|"
rm_stamp; set_main; status idle; touch "$SANDBOX/run-fails"
restart MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=2
rm -f "$SANDBOX/run-fails"
check "a failed start alerts with the way back" "$(grep -c 'CLAUDE_STARTUP_PROFILE=full claude' "$SANDBOX/errors")" 1
check "a failed start keeps the handoff for the session the way back starts" \
  "$([ -s "$S/agent-handoff/w5:p3.md" ] && echo kept)|$([ -f "$S/agent-handoff/w5:p3.md.skipped" ] && echo skipped)" "kept|"
# Background tasks make /exit ask first (seen on the main pane 11/13). The handoff lists them with their restart commands,
# so the default "Exit and stop tasks" is taken; any other selected line is left alone and the wait times out.
rm_stamp; set_main; status idle; echo '   ❯ 1. Exit and stop tasks' > "$SANDBOX/bg-dialog"
restart MAIN_REFRESH_HOUR=09
check "--restart confirms the background-work dialog's default and starts claude again" \
  "$(grep -c '^agent send-keys w5:p3 enter$' "$SANDBOX/herdr-log")|$(grep -cxF "pane run w5:p3 $RESTART_LINE" "$SANDBOX/herdr-log")|$([ -f "$SANDBOX/unsafe-run" ] && cat "$SANDBOX/unsafe-run")" "1|1|"
check "the log says the background tasks were stopped" "$(grep -c 'Exit and stop tasks를 고름 (1번째)' "$S/orchestrator/refresh.log")" 1
rm_stamp; set_main; status idle; echo '   ❯ 3. Stay' > "$SANDBOX/bg-dialog"
restart MAIN_REFRESH_HOUR=09 MAIN_REFRESH_DEADLINE=2
check "a dialog with another line selected gets no key and no restart" \
  "$(grep -c '^agent send-keys' "$SANDBOX/herdr-log")|$(grep -c '^pane run' "$SANDBOX/herdr-log")|$(grep -c '끝나지 않아' "$SANDBOX/errors")" "0|0|1"
rm -f "$SANDBOX/bg-dialog"
set_main; status idle

# main-startup: the inputs a /clear keeps, since claude loads them only when its process starts.
chg() { ms changed >/dev/null && echo changed || echo same; }
started() { ms record; } # a new main-pane process loaded what is there now
exits() { grep -c '^agent prompt w5:p3 /exit$' "$SANDBOX/herdr-log" 2>/dev/null || true; }
runs() { grep -cxF "pane run w5:p3 $RESTART_LINE" "$SANDBOX/herdr-log" 2>/dev/null || true; }
check "--restart by hand leaves the restart mark to the 05:00 run" "$([ -e "$S/orchestrator/main-startup.restarted" ] && echo marked)" ""
check "main-startup: nothing changed since the recorded start" "$(chg)" same
printf -- '---\nname: demo\ndescription: first\n---\nbody changed\n' > "$SH_/.claude/skills/demo/SKILL.md"
echo tip > "$SH_/.claude/skills/demo/references/tips.md"
echo '{"refreshed":1}' > "$SH_/.claude/plugins/known_marketplaces.json"
echo '{"model":"sonnet","enabledPlugins":{"a@b":true},"outputStyle":"plain"}' > "$SH_/.claude/settings.json"
jq '.roles.worker.enableArtifact = true' "$SANDBOX/repo/tools/claude/startup-profile.json" > "$SANDBOX/p.tmp" && mv "$SANDBOX/p.tmp" "$SANDBOX/repo/tools/claude/startup-profile.json"
check "a skill body, its references, a marketplace refresh, the model and the worker profile are no start setting" "$(chg)" same
printf -- '---\nname: demo\ndescription: second\n---\nbody\n' > "$SH_/.claude/skills/demo/SKILL.md"
check "a skill description is in the skill list" "$(chg)" changed; started
mkdir -p "$SH_/.claude/skills/new"; printf -- '---\nname: new\n---\n' > "$SH_/.claude/skills/new/SKILL.md"
check "a new skill" "$(chg)" changed; started
echo '# plain 2' > "$SH_/.claude/output-styles/plain.md"
check "an output style" "$(chg)" changed; started
echo '{"model":"sonnet","enabledPlugins":{"a@b":false},"outputStyle":"plain"}' > "$SH_/.claude/settings.json"
check "a plugin switched off in settings.json" "$(chg)" changed; started
echo '{"version":2,"plugins":{"a@b":[{"version":"2"}]}}' > "$SH_/.claude/plugins/installed_plugins.json"
check "a plugin update" "$(chg)" changed; started
echo '{"mcpServers":{"x":{}}}' > "$SH_/work/.mcp.json"
check "an MCP server in the folder claude started in" "$(chg)" changed; started
jq -n --arg w "$SH_/work" '{projects: {($w): {mcpServers: {y: {}}}}}' > "$SH_/.claude.json"
check "a local-scope MCP server in ~/.claude.json" "$(chg)" changed; started
mkdir -p "$SH_/work/.claude"; echo '{"permissions":{"allow":["Bash(ls)"]}}' > "$SH_/work/.claude/settings.local.json"
check "a permission approved in the folder's settings is no start setting" "$(chg)" same
echo '{"permissions":{"allow":["Bash(ls)"]},"enabledPlugins":{"a@b":true}}' > "$SH_/work/.claude/settings.local.json"
check "a plugin switched on in the folder's settings.local.json" "$(chg)" changed; started
echo '{"env":{"X":"1"}}' > "$SH_/work/.claude/settings.json"
check "an env var in the folder's settings.json" "$(chg)" changed; started
jq '.roles.main.enableArtifact = true' "$SANDBOX/repo/tools/claude/startup-profile.json" > "$SANDBOX/p.tmp" && mv "$SANDBOX/p.tmp" "$SANDBOX/repo/tools/claude/startup-profile.json"
check "the main role's --settings" "$(chg)" changed
ms restarting "$(ms changed)"
check "a restart made for a change is not asked for again" "$(chg)" same
ms restarting; ms restarting ""
check "a restart mark needs the hash that changed printed" "$(cat "$S/orchestrator/main-startup.restarted")" "$(ms changed)"
# Restarted for B, then started by hand with A: going back to B is a change again (Codex review).
b=$(ms changed); cp "$(dirname "$SUBJECT")/../claude/startup-profile.json" "$SANDBOX/repo/tools/claude/"; started
jq '.roles.main.enableArtifact = true' "$SANDBOX/repo/tools/claude/startup-profile.json" > "$SANDBOX/p.tmp" && mv "$SANDBOX/p.tmp" "$SANDBOX/repo/tools/claude/startup-profile.json"
check "a recorded start drops the earlier restart mark" "$(ms changed)|$([ -e "$S/orchestrator/main-startup.restarted" ] && echo marked)|$(chg)" "$b||changed"
cp "$(dirname "$SUBJECT")/../claude/startup-profile.json" "$SANDBOX/repo/tools/claude/"; started; rm -f "$S/orchestrator/main-startup.restarted"
check "back to the dotfiles profile, recorded" "$(chg)" same

# 05:00: restart instead of /clear only when those inputs changed; the conversation-size --now keeps clearing.
rm_stamp; set_main; status idle
mr MAIN_REFRESH_HOUR=05
check "05:00 with the start settings unchanged still clears" "$(cleared)|$(exits)" "1|0"
echo '# plain 3' > "$SH_/.claude/output-styles/plain.md"
rm_stamp; set_main; status idle
MR_ARGS=--now mr MAIN_REFRESH_HOUR=09
check "--now (the conversation-size call) clears even with changed start settings" "$(cleared)|$(exits)|$(chg)" "1|0|changed"
rm_stamp; set_main; status idle
mr MAIN_REFRESH_HOUR=05
check "05:00 with changed start settings restarts claude instead of clearing" "$(cleared)|$(exits)|$(runs)" "0|1|1"
check "its handoff request says the process restarts" "$(grep 'handoff save' "$SANDBOX/told" | grep -c '다시 켭니다')" 1
check "the log says why, and the day counts as done" \
  "$(grep -c '시작 설정이 바뀌어 /clear 대신 다시 켜기로 함' "$S/orchestrator/refresh.log")|$([ -f "$STAMP" ] && echo stamped)" "1|stamped"
# This fake start records nothing, like a claude started outside claude-online: the restart marker still holds.
check "a restart is made once per change" "$(chg)" same
rm_stamp; set_main; status idle
mr MAIN_REFRESH_HOUR=05
check "so the next day's 05:00 clears" "$(cleared)|$(exits)" "1|0"
# No record: a claude started before main-startup existed. Unknown counts as changed, once.
rm -f "$S/orchestrator/main-startup" "$S/orchestrator/main-startup.restarted"
rm_stamp; set_main; status idle
mr MAIN_REFRESH_HOUR=05
check "no record of the start: 05:00 restarts" "$(cleared)|$(exits)|$(runs)" "0|1|1"
rm_stamp; set_main; status idle
mr MAIN_REFRESH_HOUR=05
check "and a start that never records is not restarted again" "$(cleared)|$(exits)" "1|0"
started
# The new session never shows: alert, keep the hold, and do not restart again for the same change.
echo '# plain 4' > "$SH_/.claude/output-styles/plain.md"
rm_stamp; set_main; status idle; touch "$SANDBOX/run-no-session"
mr MAIN_REFRESH_HOUR=05 MAIN_REFRESH_DEADLINE=2
rm -f "$SANDBOX/run-no-session"
check "a restart whose new session is not confirmed alerts and keeps the relay hold" \
  "$(grep -c '다시 켠 뒤 새 세션을 확인하지 못해' "$SANDBOX/errors")|$([ -f "$S/orchestrator/refresh-delivery.json" ] && echo held)" "1|held"
rm -f "$S/orchestrator/refresh-delivery.json"
rm_stamp; set_main; status idle
mr MAIN_REFRESH_HOUR=05
check "and the next 05:00 does not restart for the same change" "$(cleared)|$(exits)" "1|0"
# Once /exit is sent the 07:00 cutoff no longer applies: the pane must get its claude back.
echo '# plain 5' > "$SH_/.claude/output-styles/plain.md"
rm_stamp; set_main; status idle; echo 05 > "$SANDBOX/hour"
mr PATH="$SANDBOX/bin:$PATH" LATE_ON_EXIT=1 EXIT_DELAY=0.3
check "a 05:00 restart whose exit runs past 07:00 still starts claude" "$(exits)|$(runs)|$([ -f "$STAMP" ] && echo stamped)" "1|1|stamped"
started
set_main; status idle

check "routine skips and errors never use the legacy notification API" "$([ -f "$SANDBOX/legacy-alerts" ] && cat "$SANDBOX/legacy-alerts")" ""

# Installed commands are symlinks into the checkout. Exercise the default helper path without an override;
# both adjacent helpers are sandbox fakes, so a failure here can never reach a real terminal notification.
mkdir -p "$SANDBOX/install/hooks" "$SANDBOX/install/bin"
cp "$SUBJECT" "$SANDBOX/install/hooks/main-refresh"
cp "$SANDBOX/pane-tell" "$SANDBOX/install/hooks/pane-tell"
cat > "$SANDBOX/install/hooks/herdr-error-notify.py" <<'PY'
import json
import os
import pathlib
import sys

pathlib.Path(os.environ["MAIN_REFRESH_FAKE_GATEWAY_LOG"]).write_text(json.dumps({
    "helper": str(pathlib.Path(__file__).resolve()),
    "args": sys.argv[1:],
}))
PY
ln -s ../hooks/main-refresh "$SANDBOX/install/bin/main-refresh"
original_subject=$SUBJECT; SUBJECT="$SANDBOX/install/bin/main-refresh"
rm_stamp; touch "$SANDBOX/tell-fails"
mr MAIN_REFRESH_HOUR=05 MAIN_REFRESH_FAKE_GATEWAY_LOG="$SANDBOX/default-gateway.json" \
  env -u HERDR_ERROR_NOTIFY -u MAIN_REFRESH_NOTIFY -u MAIN_REFRESH_PANE_TELL
SUBJECT=$original_subject; rm "$SANDBOX/tell-fails"
check "a symlinked command finds the default helper beside its real script" \
  "$(jq -r '.helper // empty' "$SANDBOX/default-gateway.json" 2>/dev/null)" \
  "$(/usr/bin/python3 -I -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$SANDBOX/install/hooks/herdr-error-notify.py")"
check "the default helper receives the genuine error title and body" \
  "$(jq -r '.args | join("|")' "$SANDBOX/default-gateway.json" 2>/dev/null)" "메인 세션 새로 시작 실패|인계 요청을 보내지 못해 건너뜀"
check "default pane-tell is also resolved beside the real script" "$(grep -c 'handoff save' "$SANDBOX/told")" 1

[ "$fails" = 0 ] && echo "all ok"
if [ "$fails" != 0 ]; then printf 'FAIL SUMMARY: %s\n' "${failed_checks[@]}"; fi
exit "$fails"
