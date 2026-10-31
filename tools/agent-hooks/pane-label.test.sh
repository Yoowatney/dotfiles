#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-label"
unset MUNICE_AUTO_DISPATCH MUNICE_AMBIENT PANE_CLOSE_REPORTED HERDR_SOCKET_PATH  # a test run inside such a pane must not inherit them
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr that keeps the pane's label in $SANDBOX/label:
# `pane get <id>` prints it, `pane rename <id> <text>` sets it, `pane rename <id> --clear` removes it.
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
s=\${HERDR_SOCKET_PATH:-}
case "\$1 \$2" in
  "pane get")
    st=\$(cat "$SANDBOX/status" 2>/dev/null || echo idle)
    id=\$(cat "$SANDBOX/new-id" 2>/dev/null || echo "\$3")
    if [ -f "$SANDBOX/label" ]; then jq -n --rawfile l "$SANDBOX/label" --arg s "\$st" --arg i "\$id" '{result: {pane: {pane_id: \$i, label: \$l, agent_status: \$s}}}'
    else jq -n --arg s "\$st" --arg i "\$id" '{result: {pane: {pane_id: \$i, agent_status: \$s}}}'; fi ;;
  "pane rename")
    echo "\$3 \$4" >> "$SANDBOX/renames"
    if [ "\$4" = "--clear" ]; then rm -f "$SANDBOX/label"; else printf '%s' "\$4" > "$SANDBOX/label"; fi ;;
  "pane list") cat "$SANDBOX/panes\${s##*/}.json" ;;
  "tab list") cat "$SANDBOX/tabs\${s##*/}.json" ;;
  "tab get")
    if [ -f "$SANDBOX/tab-changed" ]; then jq -n '{result: {tab: {label: "요유가 방금 바꾼 이름"}}}'
    else jq --arg t "\$3" '{result: {tab: (.result.tabs[] | select(.tab_id == \$t))}}' "$SANDBOX/tabs\${s##*/}.json"; fi ;;
  "tab rename")
    [ "\$3" != "\$(cat "$SANDBOX/tab-rename-fails" 2>/dev/null)" ] || exit 1
    echo "\${s##*/} \$3 \$4" >> "$SANDBOX/tab-renames" ;;
  "session list") cat "$SANDBOX/sessions.json" ;;
  "agent list") cat "$SANDBOX/agents.json" ;;
  "pane close") touch "$SANDBOX/closed" ;;
  "agent get")
    if [ -f "$SANDBOX/new-id" ] && [ "\$3" != "\$(cat "$SANDBOX/new-id")" ]; then exit 1; fi
    if [ -f "$SANDBOX/name" ]; then jq -n --rawfile n "$SANDBOX/name" '{result: {agent: {name: \$n}}}'
    else jq -n '{result: {agent: {name: null}}}'; fi ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"

pl() { env PANE_CLOSE_WAIT="${PANE_CLOSE_WAIT:-}" PANE_CLOSE_GRACE="${PANE_CLOSE_GRACE:-}" PANE_CLOSE_POLL="${PANE_CLOSE_POLL:-}" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "$@"; }
label() { cat "$SANDBOX/label" 2>/dev/null || true; }
expect() { [ "$(label)" = "$1" ] || fail "$2: expected [$1], got [$(label)]"; }

# Outside herdr: nothing happens, exit 0.
env -u HERDR_PANE_ID HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "x" || fail "outside herdr should exit 0"
[ ! -f "$SANDBOX/label" ] || fail "outside herdr should not rename"

# No argument: usage, exit 2.
if pl 2>/dev/null; then fail "no argument should exit 2"; fi

# A new name starts 🟢: 요유 may close the session.
pl "api#1 첫 일"; expect "🟢 api#1 첫 일" "first name"

# --hold marks 🔴 and keeps the name; a rename keeps 🔴 while something still runs.
pl --hold; expect "🔴 api#1 첫 일" "hold"
pl "api#2 새 일"; expect "🔴 api#2 새 일" "rename keeps 🔴"

# --free goes back to 🟢, keeping the name.
pl --free; expect "🟢 api#2 새 일" "free"

# A caller that writes the mark itself is taken at its word, never doubled.
pl "🔴 api#3 배포 감시"; expect "🔴 api#3 배포 감시" "explicit 🔴"
pl "🟢 api#3 배포 감시"; expect "🟢 api#3 배포 감시" "explicit 🟢"

# --clear-if-prefix compares the name without its mark.
pl --clear-if-prefix "api#9 "; expect "🟢 api#3 배포 감시" "other prefix keeps the label"
pl --clear-if-prefix "api#3 "; expect "" "own prefix clears"

# --hold on an unnamed pane still shows the mark.
pl --hold; expect "🔴" "hold without a name"
pl "api#4 일"; expect "🔴 api#4 일" "naming after hold keeps 🔴"

# --lead puts 👑 right after the mark; renames, --hold and --free keep it, --unlead drops it.
pl --free; pl "api#6 설계"; pl --lead; expect "🟢 👑 api#6 설계" "lead"
pl --lead; expect "🟢 👑 api#6 설계" "lead twice is not doubled"
pl "api#6 구현 지시"; expect "🟢 👑 api#6 구현 지시" "rename keeps 👑"
pl --hold; expect "🔴 👑 api#6 구현 지시" "hold keeps 👑"
pl --free; pl --clear-if-prefix "api#6 "; expect "" "clear-if-prefix looks past 👑"
pl --lead; expect "🟢 👑" "lead without a name"
pl "api#7 리드"; pl --unlead; expect "🟢 api#7 리드" "unlead"

# --close-now (what --close runs detached) closes only a 🟢 pane whose agent stays idle through the grace period.
# grace counts whole $SECONDS, so a grace of 1 can end at once; the cancel case below needs 2 to be sure to see 0.5 s
close() { rm -f "$SANDBOX/closed"; PANE_CLOSE_WAIT=1 PANE_CLOSE_GRACE=${GRACE:-1} PANE_CLOSE_POLL=0.2 pl --close-now; }
pl "🟢 api#5 끝"; echo idle > "$SANDBOX/status"
close; [ -f "$SANDBOX/closed" ] || fail "idle 🟢 pane should close"
echo working > "$SANDBOX/status"
close; [ ! -f "$SANDBOX/closed" ] || fail "a pane still working should not close"
echo idle > "$SANDBOX/status"; pl --hold
close; [ ! -f "$SANDBOX/closed" ] || fail "a 🔴 pane should not close"
pl --free
(sleep 0.5; echo working > "$SANDBOX/status") &
GRACE=2 close; wait; [ ! -f "$SANDBOX/closed" ] || fail "요유 typing during the grace period should cancel the close"

# --close works only in a tab a job opened (MUNICE_AUTO_DISPATCH=1 dispatch, MUNICE_AMBIENT=1 Slack ambient);
# 요유 closes ordinary sessions.
echo idle > "$SANDBOX/status"; rm -f "$SANDBOX/closed"
( unset MUNICE_AUTO_DISPATCH MUNICE_AMBIENT; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close 2>/dev/null )
sleep 2; [ ! -f "$SANDBOX/closed" ] || fail "an ordinary session should not close itself"
( unset MUNICE_AMBIENT; export MUNICE_AUTO_DISPATCH=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close >/dev/null )
sleep 2; [ -f "$SANDBOX/closed" ] || fail "an auto-dispatch tab should close itself"
# close_allowed_in_ambient
rm -f "$SANDBOX/closed"
( unset MUNICE_AUTO_DISPATCH; export MUNICE_AMBIENT=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close >/dev/null 2>&1 )
sleep 2; [ -f "$SANDBOX/closed" ] || fail "close_allowed_in_ambient: an ambient tab should close itself"

# A pane `herdr agent start` opened has a name in herdr, whoever ran it and whether or not that session still lives.
# It closes only after the Stop hook has saved its report (PANE_CLOSE_REPORTED=1); a session that calls --close on its
# own must not lose the report. A 👑 pane stays: 요유 runs other panes from it.
rm -f "$SANDBOX/closed"; printf 'spawned' > "$SANDBOX/name"
( PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close 2>/dev/null )
sleep 2; [ ! -f "$SANDBOX/closed" ] || fail "an agent-started pane should wait for the report"
( export PANE_CLOSE_REPORTED=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close >/dev/null )
sleep 2; [ -f "$SANDBOX/closed" ] || fail "an agent-started pane should close after its report"
rm -f "$SANDBOX/closed"; pl --lead
( export PANE_CLOSE_REPORTED=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close >/dev/null )
sleep 2; [ -f "$SANDBOX/closed" ] || fail "the hook decides for a named 👑: it closes once its panes are gone"
rm -f "$SANDBOX/closed"; pl --unlead; rm -f "$SANDBOX/name"
( export PANE_CLOSE_REPORTED=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close 2>/dev/null )
sleep 2; [ ! -f "$SANDBOX/closed" ] || fail "a pane with no agent name is 요유's, even with the flag"

# 🫡 marks a pane under a lead: herdr has an agent name for it, outside job tabs, and 👑 outranks it.
rm -f "$SANDBOX/label"; printf 'spawned' > "$SANDBOX/name"
pl "새 세션 munice"; expect "🟢 🫡 새 세션 munice" "the first name already carries 🫡"
pl "api#8 구현"; expect "🟢 🫡 api#8 구현" "rename keeps 🫡"
pl --hold; expect "🔴 🫡 api#8 구현" "hold keeps 🫡"
pl "api#8 감시"; expect "🔴 🫡 api#8 감시" "rename under 🔴 keeps 🫡"
pl --free; expect "🟢 🫡 api#8 감시" "free keeps 🫡"
pl "🫡 api#8 끝"; expect "🟢 🫡 api#8 끝" "a caller that writes 🫡 is not doubled"
pl --clear-if-prefix "api#9 "; expect "🟢 🫡 api#8 끝" "clear-if-prefix compares the task"
pl --lead; expect "🟢 👑 api#8 끝" "--lead leaves only 👑"
pl "api#8 리드"; expect "🟢 👑 api#8 리드" "a lead stays a lead on rename"
pl --unlead; expect "🟢 🫡 api#8 리드" "--unlead gives 🫡 back"
pl --clear-if-prefix "api#8 "; expect "" "own prefix clears past 🫡"
( export MUNICE_AUTO_DISPATCH=1; pl "api#8 자동" ); expect "🟢 api#8 자동" "no 🫡 in a dispatch tab"
( export MUNICE_AMBIENT=1; pl "알아서 일" ); expect "🟢 알아서 일" "no 🫡 in an ambient tab"
pl --clear
printf '🟢 🫡 시작' > "$SANDBOX/label"; rm -f "$SANDBOX/name"
pl "일 이름"; expect "🟢 🫡 일 이름" "a pane pane-spawn named keeps 🫡 until herdr has its agent"
pl --clear
pl "요유 칸"; expect "🟢 요유 칸" "no 🫡 without an agent name"

# A pane moved to another workspace keeps its old id in the env; herdr's agent lookup knows only the new one.
printf 'spawned' > "$SANDBOX/name"; printf 'w9:p1' > "$SANDBOX/new-id"; rm -f "$SANDBOX/label"
pl "옮겨진 칸"; expect "🟢 🫡 옮겨진 칸" "a moved pane is still found"
rm -f "$SANDBOX/new-id" "$SANDBOX/label"

# --mark-subs gives already running panes the same mark, once.
printf 'spawned' > "$SANDBOX/name"; rm -f "$SANDBOX/renames" "$SANDBOX/label"
jq -n '{result: {tabs: [{tab_id: "t1", label: "일"}, {tab_id: "t2", label: "자동 api#1"}, {tab_id: "t3", label: "알아서 #1-dev x"}]}}' > "$SANDBOX/tabs.json"
jq -n '{result: {panes: [{pane_id: "p1", label: "🔴 일"}, {pane_id: "p2", label: "🟢 요유"}, {pane_id: "p3", label: "🟢 자동"},
  {pane_id: "p4", label: "🟢 알아서"}, {pane_id: "p5", label: "🟢 👑 리드"}, {pane_id: "p6", label: "🟢 🫡 이미"}]}}' > "$SANDBOX/panes.json"
jq -n '{result: {agents: [{pane_id: "p1", name: "a", tab_id: "t1"}, {pane_id: "p2", name: null, tab_id: "t1"}, {pane_id: "p3", name: "b", tab_id: "t2"},
  {pane_id: "p4", name: "c", tab_id: "t3"}, {pane_id: "p5", name: "d", tab_id: "t1"}, {pane_id: "p6", name: "e", tab_id: "t1"}]}}' > "$SANDBOX/agents.json"
( export MUNICE_AMBIENT=1; pl --mark-subs >/dev/null )
[ "$(cat "$SANDBOX/renames")" = "p1 🔴 🫡 일" ] || fail "mark-subs renamed: $(cat "$SANDBOX/renames")"

# --sync-tabs: a tab name follows its panes. 👑 outranks 🫡, job tabs and 요유's own tabs stay as they are.
rm -f "$SANDBOX/name" "$SANDBOX/label" "$SANDBOX/renames" "$SANDBOX/tabs.json" "$SANDBOX/panes.json"
jq -n '{sessions: [{running: true, socket_path: "/s/a"}, {running: true, socket_path: "/s/b"}, {running: false, socket_path: "/s/c"}]}' > "$SANDBOX/sessions.json"
jq -n '{result: {tabs: [
  {tab_id: "t1", label: "리드 일"}, {tab_id: "t2", label: "부하 일"}, {tab_id: "t3", label: "요유 칸"},
  {tab_id: "t4", label: "자동 api#1"}, {tab_id: "t5", label: "알아서 #1-dev x"},
  {tab_id: "t6", label: "👑 리드 떠남"}, {tab_id: "t7", label: "👑 강등"}, {tab_id: "t8", label: "🫡 승격"},
  {tab_id: "t9", label: "👑 이미 맞음"}, {tab_id: "t10", label: "👑 🫡 👑 쌓임"},
  {tab_id: "t11", label: "👑 자동 표시 묻음"}, {tab_id: "t12", label: "🫡알아서 붙음"},
  {tab_id: "t13", label: "이름 속 표시"}, {tab_id: "t14", label: "👑"}, {tab_id: "t15", label: "칸 없음"},
  {tab_id: "t16", label: "👑 표시 없는 🔴 칸"}, {tab_id: "t17", label: "빈 이름 칸"}]}}' > "$SANDBOX/tabsa.json"
jq -n '{result: {panes: [
  {tab_id: "t1", label: "🟢 🫡 부하"}, {tab_id: "t1", label: "🔴 👑 리드"}, {tab_id: "t2", label: "🔴 🫡 x"},
  {tab_id: "t3", label: "🟢 요유"}, {tab_id: "t4", label: "🟢 👑 x"}, {tab_id: "t5", label: "🟢 🫡 x"},
  {tab_id: "t6", label: "🟢 일"}, {tab_id: "t7", label: "🟢 🫡 x"}, {tab_id: "t8", label: "🟢 👑 x"},
  {tab_id: "t9", label: "🟢 👑 x"}, {tab_id: "t10", label: "👑 표시만"}, {tab_id: "t11", label: "🟢 👑 x"},
  {tab_id: "t13", label: "🟢 왕관 👑 고르기"}, {tab_id: "t14", label: "🟢 일"}, {tab_id: "t16", label: "🔴 일"},
  {tab_id: "t17"}]}}' > "$SANDBOX/panesa.json"
jq -n '{result: {tabs: [{tab_id: "t1", label: "다른 세션"}]}}' > "$SANDBOX/tabsb.json"
jq -n '{result: {panes: [{tab_id: "t1", label: "🟢 👑 x"}]}}' > "$SANDBOX/panesb.json"
want_renames='a t1 👑 리드 일
a t2 🫡 부하 일
a t6 리드 떠남
a t7 🫡 강등
a t8 👑 승격
a t10 👑 쌓임
a t11 자동 표시 묻음
a t12 알아서 붙음
a t16 표시 없는 🔴 칸
b t1 👑 다른 세션'
renamed() { cat "$SANDBOX/tab-renames" 2>/dev/null || true; }
sync_ok() { rm -f "$SANDBOX/tab-renames"; "$@" || fail "should exit 0: $*"; }

# Launchd runs --sync-all-tabs outside any pane, so it needs no pane of its own, and it covers every running herdr session.
all() { env -u HERDR_PANE_ID -u HERDR_SOCKET_PATH HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" --sync-all-tabs; }
sync_ok all
[ "$(renamed)" = "$want_renames" ] || fail "sync-all-tabs renamed: $(renamed)"

# A pane works in its own session: every pane rename reaches its tab at once and touches no other session, so a herdr
# session that is stuck elsewhere cannot stall pane-label, its hook callers or pane-spawn.
export HERDR_SOCKET_PATH=/s/a
here_renames=$(grep '^a ' <<<"$want_renames")
sync_ok pl --sync-tabs; [ "$(renamed)" = "$here_renames" ] || fail "--sync-tabs renamed: $(renamed)"
sync_ok pl --lead; [ "$(renamed)" = "$here_renames" ] || fail "--lead synced: $(renamed)"
sync_ok pl "일 이름"; [ "$(renamed)" = "$here_renames" ] || fail "a rename synced: $(renamed)"

# A name changed after the list was read is 요유's: it stays, and the next run looks again.
touch "$SANDBOX/tab-changed"; sync_ok pl --sync-tabs; rm "$SANDBOX/tab-changed"
[ "$(renamed)" = "" ] || fail "a name changed since the list was read was overwritten: $(renamed)"

# Failure flow: herdr refusing any of it leaves the pane work whole, and one bad tab does not stop the others.
echo t1 > "$SANDBOX/tab-rename-fails"
sync_ok pl "탭 하나 실패"; expect "🟢 👑 탭 하나 실패" "a failed tab rename leaves the pane rename"
[ "$(renamed)" = "$(grep -v '^a t1 ' <<<"$here_renames")" ] || fail "a failed tab stopped the others: $(renamed)"
rm "$SANDBOX/tab-rename-fails" "$SANDBOX/tabsa.json"
sync_ok pl "탭 목록 실패"; expect "🟢 👑 탭 목록 실패" "a failed tab list leaves the pane rename"
[ "$(renamed)" = "" ] || fail "nothing to sync without a tab list: $(renamed)"
unset HERDR_SOCKET_PATH
sync_ok all; [ "$(renamed)" = "b t1 👑 다른 세션" ] || fail "one broken session stopped the next: $(renamed)"
rm "$SANDBOX/sessions.json"
sync_ok all; [ "$(renamed)" = "" ] || fail "nothing to sync without a session list: $(renamed)"
sync_ok env HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH=/nonexistent/herdr bash "$SUBJECT" --sync-tabs

echo "ok: pane-label"
