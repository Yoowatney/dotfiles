#!/usr/bin/env bash
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"
workers_up; is_main
mkdir -p "$SANDBOX/home/.codex"
# Public, synthetic configuration: many restrictions must survive a short launch.
for n in $(seq 1 24); do printf '[mcp_servers.example_%s]\ncommand="false"\n' "$n"; done > "$SANDBOX/home/.codex/config.toml"
CAPTURE_LAUNCH=1 ps --check --kind codex "terminal launch" "instruction" -- --model example >/dev/null
python3 "$(dirname "$SUBJECT")/pane-spawn-terminal-fixture.py" "$SANDBOX/launch.json" "$(dirname "$SUBJECT")/codex-startup.py"

# A timeout can mean that input is still at the shell prompt. Never append a
# second command to that input. Busy-before-input remains retryable elsewhere.
if FAIL_START=1 ps --kind codex "uncertain start" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "an uncertain Codex start must fail"; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail "do not concatenate a retry to an unconfirmed command"
hasnt "agent prompt" "an uncertain launch receives no instruction"
has "pane close w1:new" "close the undelivered failed launch"
echo 'ok: short check launch and no ambiguous retry'
