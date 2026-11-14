#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/pane-spawn-test-fixture.sh"
workers_up
is_main

# Missing task operands must fail before querying herdr or opening a pane.
# In particular, forwarded configuration must never become the name/instruction.
refuses_task() {
    local rc=0
    ps "$@" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
    [ "$rc" = 2 ] || fail "invalid task should exit 2, got $rc: $*"
    grep -q '^pane-spawn:' "$SANDBOX/err" || fail 'invalid task should explain the error'
    [ ! -s "$SANDBOX/out" ] || fail 'invalid task must return no pane address'
    [ ! -s "$SANDBOX/log" ] || fail "invalid task must not call herdr: $(log)"
}
refuses_task --kind auto --cwd '/repo with spaces' -- -c model_reasoning_effort=xhigh
refuses_task --kind auto
refuses_task --kind auto name
for blank in '' '   ' $'\t\r\n'; do
    refuses_task --kind auto "$blank" instruction
    refuses_task --kind auto name "$blank"
    refuses_task --check --kind codex "$blank" instruction
    refuses_task --check --kind codex name "$blank"
done
for option in -c --config -m --model -s --sandbox -a --ask-for-approval --effort; do
    refuses_task --kind auto -- "$option" value
    refuses_task --check --kind codex -- "$option" value
done
refuses_task --kind auto -- -cmodel_reasoning_effort=xhigh -mexample
refuses_task --kind auto -- --config=model_reasoning_effort=xhigh --model=example

# Normal tasks and check instructions still arrive separately from agent options.
ps --kind auto --cwd '/repo with spaces' name instruction -- -c model_reasoning_effort=xhigh >"$SANDBOX/out"
has 'agent prompt w1:new instruction' 'work instruction delivered'
has '-c model_reasoning_effort=xhigh' 'work agent configuration retained'
ps --check --kind codex name 'Read the external evidence and report the result.' -- -c model_reasoning_effort=xhigh >"$SANDBOX/out"
has 'agent prompt w1:new Read the external evidence and report the result.' 'check instruction delivered'
has '-c model_reasoning_effort=xhigh' 'check agent configuration retained'

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
