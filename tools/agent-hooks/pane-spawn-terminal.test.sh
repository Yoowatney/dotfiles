#!/usr/bin/env bash
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"
workers_up; is_main
# A timeout can mean that input is still at the shell prompt. Never append a
# second command to that input. Busy-before-input remains retryable elsewhere.
if FAIL_START=1 ps --kind codex "uncertain start" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "an uncertain Codex start must fail"; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail "do not concatenate a retry to an unconfirmed command"
hasnt "agent prompt" "an uncertain launch receives no instruction"
has "pane close w1:new" "close the undelivered failed launch"
echo 'ok: no ambiguous retry'
