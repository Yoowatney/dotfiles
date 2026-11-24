#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/pane-spawn-test-fixture.sh"
workers_up
is_main

# api#1508: a job that goes through several phases names, per phase, the Macs allowed to work on it. pane-spawn asks the task
# ledger before it delegates or follows up (--job-id), and the carried Mac is a Mac like the others, not a refused one.
ledger() { python3 "$(dirname "$SUBJECT")/task-ledger.py" --state-dir "$SANDBOX/home/.local/state/agent-task-ledger" record "$@" >/dev/null; }
role() { mkdir -p "$SANDBOX/home/.config/munice"; if [ -n "${1:-}" ]; then echo "$1" > "$SANDBOX/home/.config/munice/host-role"; else rm -f "$SANDBOX/home/.config/munice/host-role"; fi; }

refused() { # <label> <pane-spawn args...>
    local label=$1 rc=0; shift
    ps --kind auto "$@" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
    [ "$rc" = 2 ] || fail "$label: should exit 2, got $rc"
    grep -q 'task-ledger: .*record handoff' "$SANDBOX/err" || fail "$label: should say how to hand over"
    [ ! -s "$SANDBOX/out" ] || fail "$label: a refused task returns no pane address"
    [ ! -s "$SANDBOX/log" ] || fail "$label: a refused task must not touch herdr: $(log)"
}
opens() { # <label> <pane-spawn args...>
    local label=$1; shift
    ps --kind auto "$@" >"$SANDBOX/out" 2>"$SANDBOX/err" || fail "$label: should open"
    has 'agent prompt' "$label: instruction delivered"
}

ledger phase --job-id job1 --phase 상주잡 --allowed-hosts 세션맥 --owner lead
ledger phase --job-id job2 --phase 분석 --allowed-hosts 세션맥,휴대맥 --owner lead
ledger phase --job-id job3 --phase 분석 --allowed-hosts 휴대맥 --owner lead

role portable
refused 'portable, phase names 세션맥 only' --job-id job1 name instruction
opens 'portable, phase names both Macs' --job-id job2 name instruction
role ''
opens 'session Mac, phase names both Macs' --job-id job2 name instruction
refused 'session Mac is not pinned: a phase can leave it out' --job-id job3 name instruction

# A job nobody wrote phases for is not restricted, and a damaged ledger line never stops a launch.
role portable
opens 'no phase recorded' --job-id unrecorded name instruction
printf '%s\n' '{broken' >> "$SANDBOX/home/.local/state/agent-task-ledger/events.jsonl"
refused 'damaged line beside a good phase' --job-id job1 name instruction

# A new worker under a lead's job follows that job's phase; --phase picks another phase of it.
HERDR_TASK_ID=job1 refused 'parent job decides' name instruction
ledger phase --job-id job1 --phase 분석 --allowed-hosts 세션맥,휴대맥 --owner lead
HERDR_TASK_ID=job1 opens 'the explicit phase decides' --phase 분석 name instruction

# The way across: a handoff with artifacts and done criteria, then the same call passes.
role portable
ledger phase --job-id job4 --phase 상주잡 --allowed-hosts 세션맥 --owner lead
refused 'before handoff' --job-id job4 name instruction
ledger handoff --job-id job4 --phase 상주잡 --from-host 세션맥 --to-host 휴대맥 --artifacts 'PR 주소' --done-criteria '시험 통과'
opens 'after handoff' --job-id job4 name instruction

echo 'ok: pane-spawn phase hosts'
