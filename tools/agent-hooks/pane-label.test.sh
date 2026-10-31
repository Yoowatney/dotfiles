#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-label"
unset MUNICE_AUTO_DISPATCH MUNICE_AMBIENT PANE_CLOSE_REPORTED  # a test run inside such a pane must not inherit them
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
case "\$1 \$2" in
  "pane get")
    st=\$(cat "$SANDBOX/status" 2>/dev/null || echo idle)
    if [ -f "$SANDBOX/label" ]; then jq -n --rawfile l "$SANDBOX/label" --arg s "\$st" '{result: {pane: {label: \$l, agent_status: \$s}}}'
    else jq -n --arg s "\$st" '{result: {pane: {agent_status: \$s}}}'; fi ;;
  "pane rename")
    if [ "\$4" = "--clear" ]; then rm -f "$SANDBOX/label"; else printf '%s' "\$4" > "$SANDBOX/label"; fi ;;
  "pane close") touch "$SANDBOX/closed" ;;
  "agent get")
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
( export PANE_CLOSE_REPORTED=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close 2>/dev/null )
sleep 2; [ ! -f "$SANDBOX/closed" ] || fail "a 👑 pane should not close"
pl --unlead; rm -f "$SANDBOX/name"
( export PANE_CLOSE_REPORTED=1; PANE_CLOSE_GRACE=1 PANE_CLOSE_POLL=0.2 pl --close 2>/dev/null )
sleep 2; [ ! -f "$SANDBOX/closed" ] || fail "a pane with no agent name is 요유's, even with the flag"

echo "ok: pane-label"
