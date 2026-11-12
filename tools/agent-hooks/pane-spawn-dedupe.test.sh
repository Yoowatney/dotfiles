#!/usr/bin/env bash
# Real launcher, isolated herdr boundary: repeated instructions must not start work twice.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"
export PANE_SPAWN_TEST_KEEP_REQUESTS=1
requests=$SANDBOX/home/.local/state/pane-spawn/requests
fresh() { rm -rf "$requests"; }
reuse() {
  [ "$1" = "$2" ] || fail "the duplicate must print the existing address: $1 / $2"
  hasnt 'pane split' 'a duplicate opens no split'
  hasnt 'tab create' 'a duplicate opens no tab'
  hasnt 'agent start' 'a duplicate starts no agent'
  hasnt 'agent prompt' 'a duplicate never resends the instruction'
}

first=$(ps 'first name' 'implement the requested change')
second=$(ps 'renamed work' 'implement the requested change')
reuse "$first" "$second"

# The cache stores hashes, never original instructions.
if rg -q 'implement the requested change' "$requests"; then fail 'request contents must not be persisted'; fi

ps 'different' 'implement another change' >/dev/null
has 'agent start' 'a changed instruction starts work'
ps --cwd /another 'different folder' 'implement the requested change' >/dev/null
has 'agent start' 'the same instruction in another folder starts work'
ps --allow-duplicate 'intentional repeat' 'implement the requested change' >/dev/null
has 'agent start' 'an explicit repeat starts work'
run_on "$SANDBOX/sessions/resume/herdr.sock" 'another server' 'implement the requested change' >/dev/null
has 'agent start' 'another caller server gets independent work'

# Closed panes and reused pane ids cannot suppress new work.
rm "$SANDBOX/default-live.json"
ps 'closed' 'implement the requested change' >/dev/null
has 'agent start' 'a closed pane permits a retry'
echo '{"result":{"panes":[{"pane_id":"w1:new","terminal_id":"term_unrelated"}]}}' > "$SANDBOX/default-live.json"
ps 'recycled id' 'implement the requested change' >/dev/null
has 'agent start' 'a recycled pane id permits a retry'

# Stable terminal identity follows a pane moved to another address.
echo '{"result":{"panes":[{"pane_id":"wA:p7","terminal_id":"term_worker"}]}}' > "$SANDBOX/default-live.json"
second=$(ps 'moved' 'implement the requested change')
reuse 'wA:p7' "$second"

# Older than ten minutes allows new work. Do not refresh the window on a hit.
python3 - "$requests" <<'PY'
import json, pathlib, sys, time
for path in pathlib.Path(sys.argv[1]).glob('*.json'):
    record = json.loads(path.read_text())
    record['created_at'] = time.time() - 601
    path.write_text(json.dumps(record))
PY
ps 'expired' 'implement the requested change' >/dev/null
has 'agent start' 'an expired request starts work'

# Verification sessions are always fresh and never populate the worker cache.
fresh; workers_up; is_main
first=$(ps --check 'verify' 'check the requested change')
second=$(ps --check 'verify' 'check the requested change')
has 'agent start' 'repeated checks start independent sessions'
fresh
first=$(ps --why skill 'worker address' 'keep the workers prefix')
second=$(ps --why skill 'same worker' 'keep the workers prefix')
reuse "$first" "$second"
[ "$second" = workers/w1:new ] || fail 'a workers cache hit keeps its server prefix'
not_main; rm "$SANDBOX/sessions.json"

# Failed startup removes its pane and does not block a retry.
fresh
if EXIT_START=1 ps 'failed' 'retry startup' >/dev/null 2>"$SANDBOX/err"; then fail 'startup failure must fail'; fi
ps 'retry' 'retry startup' >/dev/null
has 'agent start' 'a failed startup permits a retry'

# Ambiguous delivery keeps its pane: return that address without a resend.
fresh
if first=$(FAIL_WAIT=1 ps 'uncertain' 'inspect ambiguous delivery' 2>"$SANDBOX/err"); then fail 'unknown work must fail'; fi
if second=$(ps 'repeat' 'inspect ambiguous delivery'); then fail 'an uncertain delivery keeps its failure status'; fi
reuse "$first" "$second"

# A server outage must fail closed instead of opening duplicate work.
if FAIL_PANE_LIST=1 ps 'offline' 'inspect ambiguous delivery' >/dev/null 2>"$SANDBOX/err"; then fail 'unknown liveness must fail'; fi
hasnt 'agent start' 'an unavailable inventory starts no duplicate'

# A damaged record cannot safely prove the previous work was closed.
printf '{broken' > "$(find "$requests" -name '*.json' | head -1)"
if ps 'corrupt' 'inspect ambiguous delivery' >/dev/null 2>"$SANDBOX/err"; then fail 'corrupt request state must fail closed'; fi
hasnt 'agent start' 'damaged state starts no duplicate'
ps --allow-duplicate 'intentional retry' 'inspect ambiguous delivery' >/dev/null
has 'agent start' 'an intentional retry can bypass damaged request state'

# An interrupted launch receipt cannot prove whether work is already running.
fresh
ps 'receipt seed' 'recover an interrupted request' >/dev/null
record=$(find "$requests" -name '*.json' | head -1)
mv "$record" "${record%.json}.receipt"
if ps 'interrupted' 'recover an interrupted request' >/dev/null 2>"$SANDBOX/err"; then fail 'an incomplete receipt must fail closed'; fi
hasnt 'agent start' 'an incomplete receipt never starts duplicate work'

# The request lock serializes simultaneous first calls.
fresh
rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits" "$SANDBOX/prompted"
export PANE_SPAWN_TEST_KEEP_LOG=1
ps 'concurrent one' 'run once under contention' >"$SANDBOX/one" 2>"$SANDBOX/one.err" & one=$!
ps 'concurrent two' 'run once under contention' >"$SANDBOX/two" 2>"$SANDBOX/two.err" & two=$!
wait "$one"; wait "$two"
[ "$(cat "$SANDBOX/one")" = "$(cat "$SANDBOX/two")" ] || fail 'simultaneous callers return one address'
[ "$(grep -c 'agent prompt' "$SANDBOX/log")" = 1 ] || fail 'simultaneous requests deliver once'

echo 'ok: pane-spawn request dedupe and failure recovery'
