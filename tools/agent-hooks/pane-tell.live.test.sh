#!/bin/bash
# Opt-in integration test. Creates and closes ONLY its own pane-temp pane.
# The isolated state registers that temporary terminal as main, never the live main.
set -euo pipefail
if [ "${PANE_TELL_LIVE_TEST:-}" != 1 ]; then
  echo 'SKIP: set PANE_TELL_LIVE_TEST=1 to create temporary agent panes'
  exit 0
fi
HERE=$(cd "$(dirname "$0")" && pwd)
BASE=${XDG_STATE_HOME:-$HOME/.local/state}
mkdir -p "$BASE/pane-tell-tests"
TEST_STATE=$(mktemp -d "$BASE/pane-tell-tests/$(date +%Y-%m-%d_%H-%M-%S).XXXXXX")
TEMP_PANE=
cleanup() { [ -z "$TEMP_PANE" ] || herdr pane close "$TEMP_PANE" >/dev/null; }
trap cleanup EXIT
TEMP_PANE=$(/bin/bash "$HERE/pane-temp" --kind "${TEST_AGENT:-codex}" --cwd "${TEST_CWD:-/tmp}" "메인 초안 보존 시험")
echo "temporary pane: $TEMP_PANE; isolated state: $TEST_STATE"
terminal=$(herdr pane get "$TEMP_PANE" | jq -r .result.pane.terminal_id)
kind=${TEST_AGENT:-codex}
for i in $(seq 1 120); do
  if /bin/bash "$HERE/pane-tell" --box-ready "$TEMP_PANE" "$kind"; then break; fi
  [ "$i" -lt 120 ] || { echo 'FAIL: temporary editor never became ready'; exit 1; }
  sleep 0.5
done
herdr pane send-text "$TEMP_PANE" $'draft 첫째\nsecond line' >/dev/null
sleep 0.5
run() { env XDG_STATE_HOME="$TEST_STATE" PANE_TELL_BATCH=0 PANE_TELL_SETTLE=0.15 PANE_TELL_POLL=0.2 PANE_TELL_WAIT=90 /bin/bash "$HERE/pane-tell" "$@"; }
run --raw "$TEMP_PANE" '[pane-tell: 자동 시험입니다. 요유가 직접 입력한 글이 아닙니다] Reply only DRAFT_RELAY_OK. Do not use tools.'
sleep 1
herdr pane read "$TEMP_PANE" --source recent --lines 60 --format ansi > "$TEST_STATE/non-main.ansi"
grep -q 'draft 첫째' "$TEST_STATE/non-main.ansi"
test -n "$(find "$TEST_STATE/pane-tell/waiting" -type f -maxdepth 1 -print)"
test ! -d "$TEST_STATE/pane-tell/drafts"
echo 'PASS: non-main kept its draft and queued the relay'
mkdir -p "$TEST_STATE/orchestrator"
printf '%s\n' "$terminal" > "$TEST_STATE/orchestrator/main"
for i in $(seq 1 120); do
  [ -n "$(find "$TEST_STATE/pane-tell/waiting" -type f -maxdepth 1 -print)" ] || break
  [ "$i" -lt 120 ] || { echo 'FAIL: main relay never delivered'; exit 1; }
  sleep 0.25
done
python3 - "$TEST_STATE" <<'PY'
import json, pathlib, sys
state = pathlib.Path(sys.argv[1])
archives = list((state / "pane-tell/drafts").glob("*.json"))
assert len(archives) == 1, archives
data = json.loads(archives[0].read_text())
assert data["rendered_text"] == "draft 첫째\nsecond line", data
assert archives[0].stat().st_mode & 0o777 == 0o600
print("PASS: registered main saved the draft (0600):", archives[0])
PY
herdr pane read "$TEMP_PANE" --source recent-unwrapped --lines 100 --format text > "$TEST_STATE/delivered.txt"
python3 - "$TEST_STATE" <<'PY'
import pathlib, sys
state = pathlib.Path(sys.argv[1])
# Terminal output can wrap even with recent-unwrapped; join its continuation indent.
text = (state / "delivered.txt").read_text().replace("\n  ", "")
assert "입력창 초안 보관 안내" in text, text
assert str(next((state / "pane-tell/drafts").glob("*.json"))) in text, text
assert "DRAFT_RELAY_OK" in text, text
assert "draft 첫째" not in text, text
PY
echo 'PASS: draft cleared and relay delivered with archive notice/path'
echo "Evidence: $TEST_STATE"
