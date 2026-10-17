#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-hold.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake pane-label that logs its arguments.
printf '#!/usr/bin/env bash\necho "$*" >> "%s/calls"\n' "$SANDBOX" > "$SANDBOX/pane-label"
chmod +x "$SANDBOX/pane-label"

hook() { # tool-call JSON -> run the hook inside herdr; it must exit 0 and print nothing
    local out
    out=$(echo "$1" | env HERDR_PANE_ID=w1:p1 PANE_LABEL_BIN="$SANDBOX/pane-label" bash "$SUBJECT") || fail "hook exited non-zero: $1"
    [ -z "$out" ] || fail "hook printed: $out"
}
calls() { cat "$SANDBOX/calls" 2>/dev/null | wc -l | tr -d ' '; }

# Things that stop when the session closes mark the pane 🔴.
hook '{"tool_name":"Bash","tool_input":{"command":"sleep 9","run_in_background":true}}'
[ "$(calls)" = 1 ] || fail "background Bash should hold"
for t in Monitor CronCreate Agent Workflow; do hook "{\"tool_name\":\"$t\",\"tool_input\":{}}"; done
[ "$(calls)" = 5 ] || fail "Monitor, CronCreate, Agent, Workflow should hold, got $(calls) calls"
hook '{"tool_name":"ScheduleWakeup","tool_input":{"delaySeconds":1200}}'
[ "$(calls)" = 6 ] || fail "a scheduled wakeup should hold"
grep -qvx -- "--hold" "$SANDBOX/calls" && fail "every call should be --hold: $(cat "$SANDBOX/calls")"

# Everything else leaves the mark alone.
hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}'
hook '{"tool_name":"ScheduleWakeup","tool_input":{"stop":true}}'
hook '{"tool_name":"Read","tool_input":{"file_path":"/x"}}'
hook 'not json'
[ "$(calls)" = 6 ] || fail "foreground Bash, a loop stop, Read and bad input should not hold, got $(calls) calls"

# Outside herdr: nothing.
echo '{"tool_name":"Monitor","tool_input":{}}' | env -u HERDR_PANE_ID PANE_LABEL_BIN="$SANDBOX/pane-label" bash "$SUBJECT" || fail "outside herdr should exit 0"
[ "$(calls)" = 6 ] || fail "outside herdr should not hold"

echo "ok: pane-hold"
