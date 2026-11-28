#!/usr/bin/env bash
source "$(dirname "$0")/pane-spawn-test-fixture.sh"
workers_up
is_main
# Check routing is independent of worker policy and permission-classifier hints.
ps --check 'browser check' 'Run ego-browser skill and inspect the page' > "$SANDBOX/out"
has '--kind codex' 'browser check is Codex'
has "HERDR_CHECK_TARGET=" 'target remains protected'
has 'blocked_reason:' 'check prompt requests permission metadata'
for policy in codex-only quota-aware claude-first agy-only; do
  echo "$policy" > "$SANDBOX/home/.local/state/agent-quota/policy"
  ps --check 'ordinary check' 'Run pytest tests and gcloud read' > "$SANDBOX/out" 2> "$SANDBOX/err"
  has '--kind codex' "an ordinary check is Codex ($policy)"
  hasnt '--kind claude' "no Claude check ($policy)"
  ps --check --kind auto 'automatic check' 'Run pytest tests' > "$SANDBOX/out" 2> "$SANDBOX/err"
  has '--kind codex' "an automatic (--kind auto) check is Codex ($policy)"
done
echo codex-only > "$SANDBOX/home/.local/state/agent-quota/policy"
for rejected in claude agy; do
  if ps --check --kind "$rejected" 'wrong runtime' 'Run pytest tests' > "$SANDBOX/out" 2> "$SANDBOX/err"; then fail 'a non-Codex check must be refused'; fi
  [ ! -e "$SANDBOX/log" ] || hasnt 'agent start' 'a refused check opens nothing'
done
ps --check --kind codex 'explicit check' 'Run pytest tests' > "$SANDBOX/out"
has '--model gpt-6.1-sol' 'check model default'
has 'model_reasoning_effort="high"' 'check effort default'
ps --check --analysis 'analysis check' 'Read analytics' > "$SANDBOX/out"
has 'HERDR_CODEX_STARTUP_ANALYSIS=1' 'analysis checks retain configured analytics access'
ps --check 'forwarded' 'Run pytest tests' -- --model example > "$SANDBOX/out"
has '--kind codex' 'forwarded arguments go to the Codex check'
has '--model example' 'and reach it'
cp -R "$(dirname "$SUBJECT")" "$SANDBOX/launcher-no-helper"
rm "$SANDBOX/launcher-no-helper/codex-startup.py"
SUBJECT="$SANDBOX/launcher-no-helper/pane-spawn"
if ps --check 'missing protection' 'Run tests' > "$SANDBOX/out" 2> "$SANDBOX/err"; then fail 'missing protection helper must refuse'; fi
grep -q 'check protection helper' "$SANDBOX/err" || fail 'explains missing check protection helper'
hasnt 'agent start' 'missing protection helper opens nothing'
echo 'PASS: every check opens on Codex'
