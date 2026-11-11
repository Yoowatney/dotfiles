#!/bin/bash
# Live check for fork-watch.py on a real, small runaway:
#   fork-watch.test.sh
# The runaway is a bash function that starts three copies of itself, in its own session (as a Claude Bash tool command
# runs), under ulimit -u = twice the user's process count now, so it stops there by itself, near half of all (the watch
# stops only a group of a quarter or more). A test-only launcher verifies the owned group is the pick, then uses that
# same initial snapshot with a limit one below its count; later stop-group reads stay live. This exercises group cleanup,
# independently of background host-count changes, rather than testing a fixed host-total threshold. Fakes for pane-tell and the
# notification, but the real main-pane file, so the real main agent is found and protected and the fake pane-tell gets the
# real main pane's address.
#   case 1: the runaway is alive; the watch stops its top and every member.
#   case 2: its top is killed first, as TaskStop did on 2026-11-05; the rest, adopted by launchd, is still found and stopped.
# Refuses to start when its cap (twice the count) would pass three quarters of kern.maxprocperuid; stops the runaway itself if the
# count passes its cap + 100. A fixed 1300 refused on a busy host, where a dozen agent sessions alone hold 1400 processes.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
bomb=0 fails=0
users() { ps -U "$(id -u)" -o pid= | wc -l | tr -d ' '; }
left() { if [ "$bomb" -gt 0 ]; then pgrep -g "$bomb" | wc -l | tr -d ' '; else echo 0; fi; }
cleanup() {
  rc=$?
  if [ "$rc" != 0 ] || [ "$fails" != 0 ]; then
    for f in "$T/readiness" "$T/state/fork-watch/live-snapshot.json" "$T/state/fork-watch/heartbeat"; do
      [ ! -f "$f" ] || { echo "diagnostic $f:"; cat "$f"; }
    done
  fi
  [ "$bomb" -gt 0 ] && { pkill -KILL -g "$bomb" 2>/dev/null; sleep 0.5; pkill -KILL -g "$bomb" 2>/dev/null; }
  rm -rf "$T"
  return "$rc"
}
trap cleanup EXIT
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

base=$(users)
[ $((base * 2)) -lt $(($(sysctl -n kern.maxprocperuid) * 3 / 4)) ] || { echo "skip: $base user processes already"; exit 1; }
cap=$((base * 2)) stop=$((base * 2 + 100))
mkdir -p "$T/state/orchestrator"
cp "$HOME/.local/state/orchestrator/main" "$T/state/orchestrator/main" 2>/dev/null || echo "note: no main pane file"
printf '#!/bin/sh\nprintf "%%s|%%s\\n" "$1" "$2" >> %s\n' "$T/told" > "$T/pt"
printf '#!/bin/sh\nprintf "%%s|%%s\\n" "$1" "$2" >> %s\n' "$T/notified" > "$T/nt"
chmod +x "$T/pt" "$T/nt"
echo "user processes $base, runaway cap $cap, watch limit comes from each owned snapshot"

start() {
  local ready=0
  /usr/bin/python3 -I -c 'import os, sys; os.setsid(); os.execv("/bin/bash", ["bash", "-c", sys.argv[1]])' \
    "ulimit -Su $cap && ulimit -Hu $cap || exit 9; b() { for i in 1 2 3; do ( b ) & done; sleep 97; }; b" >/dev/null 2>&1 &
  bomb=$!
  for _ in $(seq 60); do
    n=$(users)
    [ "$n" -gt "$stop" ] && { pkill -KILL -g "$bomb"; echo "FAIL over $stop ($n), runaway stopped"; exit 1; }
    if XDG_STATE_HOME="$T/state" /usr/bin/python3 -I "$HERE/fork-watch-live.py" ready "$bomb" alive >"$T/readiness" 2>&1; then
      ready=1
      break
    fi
    sleep 0.25
  done
  [ "$ready" = 1 ] || { echo "FAIL owned runaway not ready"; return 1; }
  echo "runaway pgid $bomb: $(left) processes, user processes $(users)"
}

watch() {
  XDG_STATE_HOME="$T/state" FORK_WATCH_PANE_TELL="$T/pt" FORK_WATCH_NOTIFY="$T/nt" \
    /usr/bin/python3 -I "$HERE/fork-watch-live.py" tick "$bomb" "$1"
}

settled() { for _ in $(seq 30); do [ "$(left)" = 0 ] && return 0; sleep 0.1; done; return 1; }

# case 1
start || exit 1
out=$(watch alive); rc=$?; echo "$out"; [ "$rc" = 0 ] || exit "$rc"
check "case 1: the watch stopped the runaway from its top (pid $bomb)" '[[ $out == *"끔 "* && $out == *"맨 위 pid $bomb:"* ]]'
check "case 1: none of its processes is left" 'settled'
check "case 1: the count is back near $base ($(users))" '[ "$(users)" -le $((base + 40)) ]'
pkill -KILL -g "$bomb" 2>/dev/null
wait 2>/dev/null

# case 2
start || exit 1
kill -KILL "$bomb"; wait "$bomb" 2>/dev/null   # only the top, reaped: the rest now hangs under launchd
sleep 0.5
echo "after killing the top alone: $(left) processes left in its group, user processes $(users)"
out=$(watch orphan); rc=$?; echo "$out"; [ "$rc" = 0 ] || exit "$rc"
check "case 2: the watch found the group whose top is gone ($bomb)" '[[ $out == *"끔 "* && $out == *"프로세스 그룹 $bomb("* ]]'
check "case 2: none of its processes is left" 'settled'
check "case 2: the count is back near $base ($(users))" '[ "$(users)" -le $((base + 40)) ]'

log=$T/state/fork-watch/kills.log
check "two lines in the log" '[ "$(grep -c " 끔 " "$log" 2>/dev/null)" = 2 ]'
main=$(cat "$HOME/.local/state/orchestrator/main" 2>/dev/null)
if [ -n "$main" ]; then
  check "the main pane was told twice" '[ "$(grep -c "|fork-watch: " "$T/told" 2>/dev/null)" = 2 ]'
  head -1 "$T/told" 2>/dev/null | cut -c1-160
else
  check "no main pane, nobody told" '[ ! -s "$T/told" ]'
fi
check "two notifications" '[ "$(grep -c "^프로세스 폭주를 껐습니다|" "$T/notified" 2>/dev/null)" = 2 ]'
check "the heartbeat file holds one line of the last round" '[ "$(wc -l < "$T/state/fork-watch/heartbeat" 2>/dev/null | tr -d " ")" = 1 ]'
[ "$fails" = 0 ] && echo "fork-watch live test: ok" || { echo "fork-watch live test: $fails failed"; exit 1; }
