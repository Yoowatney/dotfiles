#!/usr/bin/env bash
# pane-spawn under memory pressure (load-guard.py): waits, then refuses with exit 3 and opens nothing; a calm Mac opens the pane.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
echo '{"result":{"workspaces":[{"workspace_id":"w2","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
is_main
for mem in 4,3 2,60 1,10; do
  rc=0
  LOAD_GUARD_MEM=$mem LOAD_GUARD_SPAWN_WAIT=0 ps "구현" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
  [ "$rc" = 3 ] || fail "memory $mem: expected exit 3, got $rc"
  grep -q "새 칸을 열지 않습니다" "$SANDBOX/err" || fail "memory $mem: says why"
  [ ! -e "$SANDBOX/log" ] || fail "memory $mem: herdr was called: $(log)" # the launcher removes the log first, so no file means no call
done
rc=0
LOAD_GUARD_MEM=4,3 LOAD_GUARD_SPAWN_WAIT=0 ps --check "확인" "돌려 보세요" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 3 ] || fail "a check pane waits like any other: got $rc"
rc=0
LOAD_GUARD_MEM=1,80 SESSIONLESS=1 ps --kind auto "구현" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 0 ] || fail "a calm Mac opens the pane: got $rc"
has "agent start" "a calm Mac starts the agent"
# A broken check never blocks a launch: the helper is the only thing that can say 3.
rc=0
LOAD_GUARD_MEM=x SESSIONLESS=1 ps --kind auto "구현" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 0 ] || fail "an unreadable pressure value fails open: got $rc"
echo "pane-spawn-memory ok"
