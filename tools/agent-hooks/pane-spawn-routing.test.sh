#!/usr/bin/env bash
source "$(dirname "$0")/pane-spawn-test-fixture.sh"
workers_up
is_main
# 요유 2026-11-19: no Codex reviews. Every check opens on Claude, whatever the report asks for, the runner policy or the
# quota (a quota lookup that timed out had sent automatic checks to Codex), and a Codex check is refused.
ps --check 'browser check' 'Run ego-browser skill and inspect the page' > "$SANDBOX/out"
has '--kind claude' 'browser check is Claude'
has "CLAUDE_CHECK_TARGET=" 'target remains protected'
has 'blocked_reason:' 'check prompt requests permission metadata'
for policy in codex-only quota-aware claude-first; do
  echo "$policy" > "$SANDBOX/home/.local/state/agent-quota/policy"
  ps --check 'ordinary check' 'Run pytest tests and gcloud read' > "$SANDBOX/out" 2> "$SANDBOX/err"
  has '--kind claude' "an ordinary check is Claude ($policy)"
  hasnt '--kind codex' "no Codex check ($policy)"
  ps --check --kind auto 'automatic check' 'Run pytest tests' > "$SANDBOX/out" 2> "$SANDBOX/err"
  has '--kind claude' "an automatic (--kind auto) check is Claude ($policy)"
done
echo codex-only > "$SANDBOX/home/.local/state/agent-quota/policy"
if ps --check --kind codex 'codex check' 'Run pytest tests' > "$SANDBOX/out" 2> "$SANDBOX/err"; then fail 'a Codex check must be refused'; fi
grep -q 'Claude' "$SANDBOX/err" || fail "the refusal says checks run on Claude: $(cat "$SANDBOX/err")"
[ ! -s "$SANDBOX/log" ] || hasnt 'agent start' 'a refused Codex check opens nothing'
ps --check 'forwarded' 'Run pytest tests' -- --model opus > "$SANDBOX/out"
has '--kind claude' 'forwarded arguments go to the Claude check'
has '--model opus' 'and reach it'
echo 'PASS: every check opens on Claude'
