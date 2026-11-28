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

# Missing session identity cannot produce a binding for a reused pane.
SESSIONLESS=1 ps --kind codex --allow-duplicate "Unidentified work" "$other" >/dev/null 2>"$SANDBOX/err"
grep -q 'assignment unavailable' "$SANDBOX/err" || fail "missing identity must be reported"
python3 - "$assignment" "$other" <<'PY'
import json, pathlib, sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text())[0]['issue_urls'] == [sys.argv[2]]
PY

# Failed recording preserves evidence and does not undo a delivered launch.
printf '{broken' > "$assignment"
ps --allow-duplicate "Manual work" "$url" >/dev/null 2>"$SANDBOX/err"
grep -q 'assignment unavailable' "$SANDBOX/err" || fail "damaged inventory must be reported"
[ "$(cat "$assignment")" = '{broken' ] || fail "damaged inventory was overwritten"

echo 'pane-spawn assignments: OK'
