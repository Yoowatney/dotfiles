#!/usr/bin/env bash
source "$(dirname "$0")/pane-spawn-test-fixture.sh"
workers_up
is_main
ps --check 'browser check' 'Run ego-browser skill and inspect the page' > "$SANDBOX/out"
has '--kind claude' 'browser requires Claude'
has "CLAUDE_CHECK_TARGET=" 'target remains protected'
has 'blocked_reason:' 'check prompt requests permission metadata'
ps --check 'ordinary check' 'Run pytest tests and gcloud read' > "$SANDBOX/out"
has '--kind codex' 'ordinary check remains Codex'
mkdir -p "$SANDBOX/repo"
printf '%s\n' 'import subprocess' "subprocess.run(['ps', '-A'])" > "$SANDBOX/repo/process.test.py"
ps --check --cwd "$SANDBOX/repo" 'process test' 'Run python3 process.test.py' > "$SANDBOX/out"
has '--kind claude' 'nested ps requires Claude'
ps --check --kind codex 'pinned options' 'Run ego-browser skill' -- -c example=true > "$SANDBOX/out" 2> "$SANDBOX/err" && fail 'Codex options must not reach Claude'
[ ! -s "$SANDBOX/log" ] || hasnt 'agent start' 'incompatible options must be refused before opening'
echo 'PASS: permission routing preserves check boundaries'
