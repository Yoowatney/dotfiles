#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/pane-spawn-test-fixture.sh"
workers_up
is_main

# The delimiter protects literal names, including help and option spellings.
for name in '--help [ac-example]' --help --unknown -h -- '-job with spaces'; do
    out=$(ps --check --kind codex --cwd '/repo with spaces' -- "$name" instruction -- -m example-model 2>"$SANDBOX/err") || fail "literal name: $name"
    [ "$out" = workers/w1:new ] || fail "no address for $name: $out"
    has "pane rename w1:new 🟢 🫡 확인 $name" 'literal check name'
    has 'agent prompt w1:new' 'instruction delivered'
    has '-m example-model' 'forwarded arguments retained'
done

# Unknown launcher options are still errors; help never opens a pane.
rc=0
ps --unknown name instruction >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
[ "$rc" = 2 ] || fail "unknown option should exit 2, got $rc"
touch "$SANDBOX/log"
hasnt 'agent start' 'unknown option must not launch'
ps --help >"$SANDBOX/out"
touch "$SANDBOX/log"
grep -q '^usage:' "$SANDBOX/out" || fail 'help should print usage'
hasnt 'agent start' 'help must not launch'
echo 'ok: pane-spawn literal names'
