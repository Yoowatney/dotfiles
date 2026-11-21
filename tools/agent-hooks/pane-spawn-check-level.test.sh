#!/usr/bin/env bash
# --check-level none|figures|full: how much of a worker's report the automatic check reads. The worker's pane records it in its
# environment for auto-tab-close; no level means full, the behavior before levels existed.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"
workers_up
is_main

for level in none figures; do
  ps --kind auto --check-level "$level" "level $level" "지시" >/dev/null
  has "--env HERDR_CHECK_LEVEL=$level" "the pane records its $level level"
  has "agent prompt w1:new" "the instruction still arrives with a level"
done
ps --kind auto "no level" "지시" >/dev/null
hasnt "HERDR_CHECK_LEVEL" "no level: the pane looks as it did before levels (full)"
ps --kind auto --check-level full "full level" "지시" >/dev/null
hasnt "HERDR_CHECK_LEVEL" "full is the unmarked default, so it writes nothing"

# A level other than the three is a usage error that opens nothing.
for bad in all NONE '' figure; do
  rc=0
  ps --kind auto --check-level "$bad" "bad level" "지시" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
  [ "$rc" = 2 ] || fail "level [$bad] should exit 2, got $rc"
  grep -q '^pane-spawn:' "$SANDBOX/err" || fail "level [$bad] should explain the error"
done
rc=0
ps --check-level >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
[ "$rc" = 2 ] || fail "--check-level needs a value"

# A check pane never takes the level into its environment (its own report ends the round), and figures narrows its brief:
# no question-coverage sentence, which asks for the whole report to be read again.
ps --check "plain check" "지시" >/dev/null
has "Check whether the result answers the original question" "a full check keeps the coverage brief"
ps --check --check-level figures "figure check" "지시" >/dev/null
hasnt "HERDR_CHECK_LEVEL" "a check pane records no level"
hasnt "Check whether the result answers the original question" "a figures check re-runs the figures only"
has "agent prompt w1:new" "and still gets its instruction"
echo 'ok: pane-spawn check level'
