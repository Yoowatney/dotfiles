#!/usr/bin/env bash
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

assignment="$SANDBOX/home/.local/state/pane-spawn/assignments.json"
url=https://github.com/example/service/issues/17
other=https://github.com/example/infra/issues/23

ps --allow-duplicate "Manual work $other" "Compare $url and $url#issuecomment-7" >/dev/null
python3 - "$assignment" "$url" "$other" <<'PY'
import json, pathlib, sys
rows = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert rows == [dict(session_id='w1:new', terminal_id='term_worker',
                     agent_session_id='sid', issue_urls=sorted(sys.argv[2:]))], rows
assert 'Compare' not in pathlib.Path(sys.argv[1]).read_text()
PY

# A launch without an issue does not erase a previous assignment.
ps "Unrelated work" "No issue URL" >/dev/null
python3 - "$assignment" <<'PY'
import json, pathlib, sys
assert len(json.loads(pathlib.Path(sys.argv[1]).read_text())) == 1
PY

# Persisted addresses are stable across the default and workers servers.
workers_up
is_main
ps --kind codex "Manual work" "$url" >/dev/null
python3 - "$assignment" "$url" <<'PY'
import json, pathlib, sys
rows = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert rows == [dict(session_id='workers/w1:new', terminal_id='term_worker',
                     agent_session_id='sid', issue_urls=[sys.argv[2]])], rows
PY
not_main

CALLER_LABEL='🟢 🫡 Worker' run_on "$SANDBOX/sessions/workers/herdr.sock" --kind codex "Nested work" "$other" >/dev/null
python3 - "$assignment" "$other" <<'PY'
import json, pathlib, sys
rows = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert rows == [dict(session_id='workers/w1:new', terminal_id='term_worker',
                     agent_session_id='sid', issue_urls=[sys.argv[2]])], rows
PY

# Session identity may lag behind confirmed work on the same workers socket.
mv "$SANDBOX/herdr" "$SANDBOX/herdr-base"
cat > "$SANDBOX/herdr" <<EOF
#!/bin/bash
# Herdr can confirm work before its session watcher publishes the identity.
if [ "\$1 \$2 \$3" = 'agent get w1:new' ] && [ -n "\${DELAY_ASSIGNMENT_SESSION:-}" ] && [ -e "$SANDBOX/waits" ]; then
    n=\$(cat "$SANDBOX/session-reads" 2>/dev/null || echo 0)
    echo \$((n + 1)) > "$SANDBOX/session-reads"
    if [ "\$n" -lt 3 ]; then
        echo '{"result":{"agent":{"interactive_ready":true,"agent_status":"working","agent_session":null}}}'
        exit 0
    fi
fi
if [ "\$1 \$2" = 'agent get' ] && [ -n "\${ASSIGNMENT_READ_FAIL:-}" ]; then exit 1; fi
if [ "\$1 \$2" = 'agent get' ] && [ -n "\${ASSIGNMENT_READ_HANG:-}" ]; then exec /bin/sleep 30; fi
exec "$SANDBOX/herdr-base" "\$@"
EOF
chmod +x "$SANDBOX/herdr"
python3 - "$assignment" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
rows = json.loads(p.read_text())
rows.append(dict(session_id='workers/w9:old', terminal_id='term_existing',
                 agent_session_id='existing-session', issue_urls=['https://github.com/example/infra/issues/23']))
p.write_text(json.dumps(rows))
PY
DELAY_ASSIGNMENT_SESSION=1 CALLER_LABEL='🟢 🫡 Worker' run_on "$SANDBOX/sessions/workers/herdr.sock" --kind codex --allow-duplicate "Delayed identity" "$url" >/dev/null 2>"$SANDBOX/err"
python3 - "$assignment" "$url" <<'PY'
import json, pathlib, sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text()) == [dict(
    session_id='workers/w9:old', terminal_id='term_existing',
    agent_session_id='existing-session', issue_urls=['https://github.com/example/infra/issues/23']), dict(
    session_id='workers/w1:new', terminal_id='term_worker',
    agent_session_id='sid', issue_urls=[sys.argv[2]])]
PY
if grep -q 'assignment unavailable' "$SANDBOX/err"; then fail "delayed identity must be recorded"; fi
cp "$assignment" "$SANDBOX/assignments-before-failure.json"

# Missing session identity cannot produce a binding for a reused pane.
started=$SECONDS
SESSIONLESS=1 ps --kind codex --allow-duplicate "Unidentified work" "$other" >/dev/null 2>"$SANDBOX/err"
grep -q 'assignment unavailable' "$SANDBOX/err" || fail "missing identity must be reported"
[ "$((SECONDS - started))" -lt 15 ] || fail "missing identity must stop waiting"
cmp "$assignment" "$SANDBOX/assignments-before-failure.json" || fail "missing identity changed existing records"

# CLI errors and a hung read cannot erase bindings or wait indefinitely.
for failure in ASSIGNMENT_READ_FAIL ASSIGNMENT_READ_HANG; do
    started=$SECONDS
    if env "$failure=1" HERDR_BIN_PATH="$SANDBOX/herdr" HOME="$SANDBOX/home" \
        python3 -I "$(dirname "$SUBJECT")/pane-spawn-request.py" --record-assignment w1:new '' work "$url" 2>"$SANDBOX/err"; then
        fail "an unavailable identity must not be recorded"
    fi
    [ "$((SECONDS - started))" -lt 15 ] || fail "identity lookup exceeded its bound"
    cmp "$assignment" "$SANDBOX/assignments-before-failure.json" || fail "failed read changed existing records"
done

# Failed recording preserves evidence and does not undo a delivered launch.
printf '{broken' > "$assignment"
ps --allow-duplicate "Manual work" "$url" >/dev/null 2>"$SANDBOX/err"
grep -q 'assignment unavailable' "$SANDBOX/err" || fail "damaged inventory must be reported"
[ "$(cat "$assignment")" = '{broken' ] || fail "damaged inventory was overwritten"

echo 'pane-spawn assignments: OK'
