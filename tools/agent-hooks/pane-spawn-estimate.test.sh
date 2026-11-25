#!/usr/bin/env bash
# Startup completion estimates include automatic check time when a check-delegating lead opens an automatic-checked worker.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
is_main

# Checked workers under the main pane (default/full and figures) across runners include automatic check time.
SESSIONLESS=1 ps --kind auto "기본 확인 등급" "할 일" >/dev/null
has 'first response line must be `끝날 예상: HH:MM KST`' "main auto worker gets startup estimate format"
has 'post-report automatic check session finish (add 5 minutes for the automatic check, or 8 minutes' "main default worker includes automatic check time"
has 'report immediately when your own work finishes' "main worker still reports immediately when its own work finishes"

SESSIONLESS=1 ps --kind auto --check-level full "전체 확인" "할 일" >/dev/null
has 'post-report automatic check session finish (add 5 minutes for the automatic check' "explicit full check level includes automatic check time"

SESSIONLESS=1 ps --kind auto --check-level figures "숫자 확인" "할 일" >/dev/null
has 'post-report automatic check session finish (add 5 minutes for the automatic check' "figures check level includes automatic check time"

ps --why skill "클로드 작업" "할 일" >/dev/null
has 'post-report automatic check session finish (add 5 minutes for the automatic check' "main Claude worker includes automatic check time"

mkdir -p "$SANDBOX/home/.gemini/antigravity-cli"
printf '{"useG1Credits":false,"modelProvider":""}\n' > "$SANDBOX/home/.gemini/antigravity-cli/settings.json"
jq -n --argjson now "$(date +%s)" '{captured_at:$now,plan_tier:"Google AI Ultra",quota:{"gemini-5h":{remaining_fraction:0.9,reset_time:($now+1000)},"gemini-weekly":{remaining_fraction:0.8,reset_time:($now+300000)}}}' > "$SANDBOX/home/.local/state/agent-quota/agy.json"
SESSIONLESS=1 ps --kind agy "AGY 작업" "할 일" >/dev/null
has 'post-report automatic check session finish (add 5 minutes for the automatic check' "main AGY worker includes automatic check time"

# Unchecked workers under main (--check-level none and [rc-<hex>] recheck) keep work-only estimate guidance.
SESSIONLESS=1 ps --kind auto --check-level none "확인 없음" "할 일" >/dev/null
has 'to finish the assigned work and report it. Do not add buffer.' "check-level none keeps work-only estimate"
hasnt 'automatic check session' "check-level none omits automatic check time"

SESSIONLESS=1 ps --kind auto "재확인 [rc-0123456789abcdef]" "읽기 전용 재확인" >/dev/null
has 'to finish the assigned work and report it. Do not add buffer.' "read-only recheck keeps work-only estimate"
hasnt 'automatic check session' "read-only recheck omits automatic check time"

# Check sessions themselves receive no startup estimate prompt.
ps --check "확인 작업" "검증" >/dev/null
hasnt 'first response line must be `끝날 예상: HH:MM KST`' "check pane omits startup estimate"
hasnt 'automatic check session' "check pane omits automatic check estimate"

# Ordinary leads and moved --opener panes do not trigger automatic checks.
not_main
CALLER_LABEL="🟢 👑 api#1 로그인" ps "부하 작업" "할 일" >/dev/null
has 'to finish the assigned work and report it. Do not add buffer.' "ordinary lead worker keeps work-only estimate"
hasnt 'automatic check session' "ordinary lead worker omits automatic check time"

rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits" "$SANDBOX/prompted"
env -u HERDR_SOCKET_PATH -u XDG_STATE_HOME -u HERDR_PANE_ID HOME="$SANDBOX/home" PATH="$SANDBOX:$PATH" HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_RETRY_WAIT=0 bash "$SUBJECT" --opener term_abc123 --cwd /work "원격 이어 감" "지시" >/dev/null
has 'to finish the assigned work and report it. Do not add buffer.' "opener worker keeps work-only estimate"
hasnt 'automatic check session' "opener worker omits automatic check time"

echo "ok: pane-spawn startup estimate includes automatic check"
