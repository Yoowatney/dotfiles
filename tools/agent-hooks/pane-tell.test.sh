#!/usr/bin/env bash
set -uo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-tell"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }

# A fake herdr: `pane list` prints $SANDBOX/panes, `agent prompt <pane> <text>` is logged to $SANDBOX/log.
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "pane list") [ ! -f "$SANDBOX/herdr-down" ] || exit 1; cat "$SANDBOX/panes" ;;
  "tab list") cat "$SANDBOX/tabs" 2>/dev/null || echo '{"result":{"tabs":[]}}' ;;
  "pane get") jq -n --arg i "\$(cat "$SANDBOX/new-id" 2>/dev/null)" --argjson o "\$(cat "$SANDBOX/scrolled" 2>/dev/null || echo 0)" \
    --arg p "\$3" --slurpfile all "$SANDBOX/panes" \
    '{result: {pane: ((\$all[0].result.panes | map(select(.pane_id == \$p)) | .[0] // {}) + {scroll: {offset_from_bottom: \$o}} + (if \$i == "" then {} else {pane_id: \$i} end))}}' ;;
  "pane read") n=\$((\$(cat "$SANDBOX/reads" 2>/dev/null || echo 0) + 1)); echo "\$n" > "$SANDBOX/reads"
    if [ -f "$SANDBOX/screen-\$3" ]; then cat "$SANDBOX/screen-\$3"
    elif [ -f "$SANDBOX/screen.\$n" ]; then cat "$SANDBOX/screen.\$n"; else cat "$SANDBOX/screen"; fi ;;
  "agent prompt") [ ! -f "$SANDBOX/refuse" ] || exit 1; printf '%s|%s\n' "\$3" "\$4" >> "$SANDBOX/log" ;;
  "notification show") printf '%s\n' "\$5" >> "$SANDBOX/alerts" ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
panes() { # "<id>|<label>|<session>[|<tab id>[|<agent>]]" ...
  local p; for p in "$@"; do IFS='|' read -r id label sid tab kind <<<"$p"; jq -nc --arg i "$id" --arg l "$label" --arg s "$sid" --arg t "${tab:-w1:t1}" --arg k "${kind:-claude}" --arg term "term_${id##*:}" '{pane_id: $i, label: $l, agent_session: {value: $s}, cwd: "/w", tab_id: $t, agent: ($k | if . == "-" then null else . end), terminal_id: $term}'; done \
    | jq -s '{result: {panes: .}}' > "$SANDBOX/panes"
  rm -f "$SANDBOX/log"
}
RULE=$(printf '─%.0s' $(seq 1 40))
claude_screen() { printf '%s\n\xe2\x9d\xaf\xc2\xa0%s\n%s\n  footer\n' "$RULE" "${1:-}" "$RULE"; } # ❯ and a no-break space
claude_screen > "$SANDBOX/screen"
export PANE_TELL_POLL=0.05 PANE_TELL_SETTLE=0
pt() { rm -f "$SANDBOX/log" "$SANDBOX/reads"; env HOME="$SANDBOX/home" XDG_STATE_HOME="$SANDBOX/state" HERDR_PANE_ID=w1:p9 HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "$@"; }
sent() { cut -d'|' -f1 "$SANDBOX/log" 2>/dev/null | tr '\n' ' '; }
# Waits for the detached delivery to finish: nothing is left waiting.
settled() { local i; for i in $(seq 1 100); do [ -z "$(ls "$SANDBOX/state/pane-tell/waiting" 2>/dev/null)" ] && return; sleep 0.05; done; echo "FAIL a delivery never finished"; fails=1; }
box() { printf '%b' "$2" | HERDR_BIN_PATH=/nonexistent bash "$SUBJECT" --box-empty "$1"; echo $?; }

panes "w1:p1|🟢 회고 KPI 표|s1" "w1:p2|🔴 #1-marketing 자동화 조사|s2" "w1:p3|🟢 api#7 시험|s3" "w1:p4|🟢 api#70 다른|s4" "w1:p9|🟢 와우포인트 정리|s9"
check "list shows names without the mark" "$(pt --list | cut -f2 | tr '\n' ',')" "회고 KPI 표,#1-marketing 자동화 조사,api#7 시험,api#70 다른,와우포인트 정리,"
check "list marks this pane" "$(pt --list | grep -c '(이 pane)')" 1
pt "KPI 표" "배포 끝남" >/dev/null; check "name text finds the pane" "$?|$(sent)" "0|w1:p1 "
panes "w1:p2|🔴 #1-marketing 자동화 조사|s2" "w1:p9|🟢 와우포인트 정리|s9"
pt "MARKETING" "x" >/dev/null; check "match ignores case" "$(sent)" "w1:p2 "
check "message says who sent it" "$(cut -d'|' -f2 "$SANDBOX/log")" "[pane-tell: '와우포인트 정리' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] x"
panes "w1:p3|🟢 api#7 시험|s3" "w1:p4|🟢 api#70 다른|s4"
pt "api#7 " "x" >/dev/null; check "a board tag with its space skips api#70" "$(sent)" "w1:p3 "
pt "w1:p4" "x" >/dev/null; check "pane id works" "$(sent)" "w1:p4 "
pt "api" "x" 2>/dev/null; check "two matches refuse" "$?|$(sent)" "2|"
pt "없는 세션" "x" 2>/dev/null; check "no match exits 3" "$?|$(sent)" "3|"
panes "w1:p9|🟢 와우포인트 정리|s9"
pt "와우" "x" 2>/dev/null; check "this pane refuses" "$?|$(sent)" "2|"
panes "w1:p5|🟢 api#8 자동 일|s5"; mkdir -p "$SANDBOX/home/.claude/projects/p"
echo '{"type":"user","origin":{"kind":"human"},"message":{"content":"[auto-dispatch] work"}}' > "$SANDBOX/home/.claude/projects/p/s5.jsonl"
pt "api#8" "x" 2>/dev/null; check "auto-dispatch session refuses" "$?|$(sent)" "2|"
# Claude review 10/21: with no session id to read, the 자동 tab itself still refuses (board.sh unwatch tells this pane)
echo '{"result":{"tabs":[{"tab_id":"w1:t1","label":"작업"},{"tab_id":"w1:t7","label":"자동 api#8"},{"tab_id":"w1:t8","label":"자동화"}]}}' > "$SANDBOX/tabs"
panes "w1:p6|🟢 api#8 자동 일||w1:t7"
pt "api#8" "x" 2>/dev/null; check "auto tab without a session id refuses" "$?|$(sent)" "2|"
panes "w1:p7|🟢 마케팅 자동화|s7|w1:t8"
pt "마케팅" "x" >/dev/null; check "a tab named 자동화 is not an auto tab" "$?|$(sent)" "0|w1:p7 "
# Slack ambient tabs: a prompt typed there without the round marker lifts the guard for that round
echo '{"result":{"tabs":[{"tab_id":"w1:t1","label":"작업"},{"tab_id":"w1:t9","label":"알아서 #1-dev 맥미니 전환"},{"tab_id":"w1:t10","label":"알아서하기"}]}}' > "$SANDBOX/tabs"
panes "w1:p10|🟢 tf#928 전환||w1:t9"
pt "tf#928" "x" 2>/dev/null; check "refuses_ambient_tab" "$?|$(sent)" "2|"
panes "w1:p11|🟢 tf#928 전환|s11|w1:t1"
echo '{"type":"user","origin":{"kind":"human"},"message":{"content":"[ambient-round] work.md"}}' > "$SANDBOX/home/.claude/projects/p/s11.jsonl"
echo '{"type":"user","origin":{"kind":"human"},"message":{"content":"해"}}' >> "$SANDBOX/home/.claude/projects/p/s11.jsonl"
pt "tf#928" "x" 2>/dev/null; check "refuses_ambient_session (moved out of its tab)" "$?|$(sent)" "2|"
panes "w1:p12|🟢 tf#929 전환|s12|w1:t1"
printf '{"type":"user","origin":{"kind":"human"},"message":{"content":"\0[ambient-round] work.md"}}\n' > "$SANDBOX/home/.claude/projects/p/s12.jsonl"
pt "tf#929" "x" 2>/dev/null; check "refuses_ambient_session with a NUL byte in the line" "$?|$(sent)" "2|"
panes "w1:p14|🟢 tf#930 전환|s14|w1:t1"
echo '{"type":"user","origin":{"kind":"human"},"message":{"content":"ambient-round] work.md"}}' > "$SANDBOX/home/.claude/projects/p/s14.jsonl"
pt "tf#930" "x" 2>/dev/null; check "refuses_ambient_session with the [ eaten" "$?|$(sent)" "2|"
# only a session the poll started (its first prompt opens with the marker), not one that talks about the marker
panes "w1:p15|🟢 ambient 리뷰|s15|w1:t1"
echo '{"type":"user","origin":{"kind":"human"},"message":{"content":"deliver.py의 [ambient-round] 마커를 리뷰해 줘"}}' > "$SANDBOX/home/.claude/projects/p/s15.jsonl"
pt "ambient 리뷰" "x" >/dev/null; check "a session that only mentions ambient-round is told" "$?|$(sent)" "0|w1:p15 "
panes "w1:p13|🟢 할 일 정리|s13|w1:t10"
echo '{"type":"user","origin":{"kind":"human"},"message":{"content":"ambient 모드 설계"}}' > "$SANDBOX/home/.claude/projects/p/s13.jsonl"
pt "할 일" "x" >/dev/null; check "a tab named 알아서하기 is not an ambient tab" "$?|$(sent)" "0|w1:p13 "
pt "only-one-arg" 2>/dev/null; check "usage" "$?" 2
# herdr's workspace ids go on past 9 as letters: an id is an id, not a piece of a name.
panes "wA:p7|🟢 👑 리드|" "wA:p3|🟢 🫡 일|"
pt wA:p7 "보고" >/dev/null 2>&1; check "workspace wA id" "$(sent)" "wA:p7 "
# A pane moved to another workspace keeps its old id in the env: still its own name, still not told itself.
echo wA:p3 > "$SANDBOX/new-id"
pt wA:p7 "보고" >/dev/null 2>&1; check "moved caller signs with its name" "$(grep -c "'🫡 일' 세션" "$SANDBOX/log")" "1"
pt wA:p3 "나" >/dev/null 2>&1; check "moved caller is not told itself" "$(sent)" ""
rm -f "$SANDBOX/new-id"

# What the input box holds, read from the screen. Hand-built screens: real captures hold paths and addresses.
P='\xe2\x9d\xaf\xc2\xa0' R="$RULE\n" DIM='\033[2m' OFF='\033[0m' BG='\033[48;2;30;30;30m' C='\xe2\x80\xba'
check "claude: empty" "$(box claude "$R$P\n$R  footer\n")" 0
check "claude: typed" "$(box claude "$R${P}야 리드야\n$R  footer\n")" 1
check "claude: the grey prompt of a working agent is empty" "$(box claude "$R\033[38;2;153;153;153m$P$OFF\n$R")" 0
check "claude: a rule that carries the session name" "$(box claude "──────── 세션 이름 ─\n$P\n$R")" 0
check "claude: only a wrapped or pasted line holds text" "$(box claude "$R$P\n  [Pasted text #1 +11 lines]\n$R")" 1
check "claude: an old prompt above the box is not the box" "$(box claude "${P}지난 글\n$R$P\n$R")" 0
check "claude: a task picker below the box is not the box" "$(box claude "$R$P\n$R\n\xe2\x9d\xaf \xe2\x8f\xba main\n")" 0
check "claude: a question dialog in place of the box" "$(box claude "$R\xe2\x9d\xaf 1. Yes\n  2. No\n$R")" 1
check "claude: no box on the screen" "$(box claude "그냥 출력\n")" 1
check "claude: a dim suggestion is no typed text" "$(box claude "$R${P}${DIM}제안 문구$OFF\n$R")" 0
check "claude: dim text below the prompt line counts" "$(box claude "$R$P\n  ${DIM}둘째 줄$OFF\n$R")" 1
check "codex: the dim placeholder is empty" "$(box codex "$BG  $OFF\n$C$OFF$BG $OFF$DIM${BG}Ask Codex to do anything$OFF$BG   $OFF\n$BG  $OFF\n  status\n")" 0
check "codex: typed" "$(box codex "$BG  $OFF\n$C$OFF$BG 야 리드야   $OFF\n$BG  $OFF\n  status\n")" 1
check "codex: only a wrapped line holds text" "$(box codex "$C$OFF$BG $OFF$DIM${BG}x$OFF\n$BG  둘째 줄$OFF\n$BG  $OFF\n  status\n")" 1
check "codex: dim text below the prompt line counts" "$(box codex "$C$OFF$BG $OFF$DIM${BG}x$OFF\n$BG  ${DIM}둘째 줄$OFF\n  status\n")" 1
check "codex: status text under the box is not typed" "$(box codex "$C$OFF$BG $OFF$DIM${BG}x$OFF\n$BG  $OFF\n  status\n")" 0
check "codex: an old prompt above the box is not the box" "$(box codex "\033[2m$C ${OFF}지난 글\n$C$OFF$BG $OFF$DIM${BG}x$OFF\n$BG  $OFF\n")" 0
check "codex: only old prompts on the screen, no box" "$(box codex "\033[2m$C ${OFF}지난 글\n  status\n")" 1
check "an agent whose box cannot be read" "$(box gemini "$R$P\n$R")" 1

# Delivery: 요유's half-typed text decides when, never the sender.
panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9"
msg="[pane-tell: '일' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] 보고"
claude_screen "야 리드야" > "$SANDBOX/screen"
out=$(pt "리드" "보고"); check "text in the box: no merge, exit 0" "$?|$(sent)" "0|"
check "text in the box: says it is queued" "${out%%: *}" "queued for w1:p1 (👑 리드)"
sleep 0.3; check "still typed: still waiting" "$(sent)" ""
claude_screen > "$SANDBOX/screen"; settled
check "box empty: delivered once" "$(sent)|$(cut -d'|' -f2- "$SANDBOX/log")" "w1:p1 |$msg"
claude_screen "야" > "$SANDBOX/screen"
PANE_TELL_WAIT=0 pt "리드" "보고" >/dev/null; settled
check "too long: 요유's text untouched, nothing sent" "$(sent)" ""
check "too long: the message stays in a file" "$(cat "$SANDBOX"/state/pane-tell/undelivered/*)" "$msg"
rm -f "$SANDBOX"/state/pane-tell/undelivered/*
claude_screen "" > "$SANDBOX/screen"; claude_screen "z" > "$SANDBOX/screen.2"
out=$(pt "리드" "보고"); check "typed between the two reads: held back" "${out%%: *}|$(sent)" "queued for w1:p1 (👑 리드)|"
rm -f "$SANDBOX/screen.2"; settled; check "typed between the two reads: goes later" "$(sent)" "w1:p1 "
echo 3 > "$SANDBOX/scrolled"
out=$(pt "리드" "보고"); check "receiver scrolled up: the box is out of sight" "${out%%: *}|$(sent)" "queued for w1:p1 (👑 리드)|"
rm -f "$SANDBOX/scrolled"; settled
touch "$SANDBOX/refuse"
out=$(pt "리드" "보고"); check "herdr refuses (a dialog is open): queued" "${out%%: *}" "queued for w1:p1 (👑 리드)"
rm -f "$SANDBOX/refuse"; settled; check "herdr refuses: goes later" "$(sent)" "w1:p1 "
claude_screen "야" > "$SANDBOX/screen"; pt "리드" "보고" >/dev/null
panes "w1:p9|🟢 일|s9"; settled
check "receiver closed: nothing sent, the message stays in a file" "$(sent)|$(ls "$SANDBOX"/state/pane-tell/undelivered | wc -l | tr -d ' ')" "|1"
rm -f "$SANDBOX"/state/pane-tell/undelivered/*
panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9"; pt "리드" "보고" >/dev/null
panes "w1:p1|🟢 👑 리드|other" "w1:p9|🟢 일|s9"; claude_screen > "$SANDBOX/screen"; settled
check "another session took the pane: not its message" "$(sent | grep -c w1:p1)|$(ls "$SANDBOX"/state/pane-tell/undelivered | wc -l | tr -d ' ')" "0|1"
rm -f "$SANDBOX"/state/pane-tell/undelivered/*
panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9"; claude_screen "야" > "$SANDBOX/screen"; pt "리드" "보고" >/dev/null
jq '(.result.panes[] | select(.pane_id == "w1:p1") | .pane_id) = "w2:p5"' "$SANDBOX/panes" > "$SANDBOX/panes.new"; mv "$SANDBOX/panes.new" "$SANDBOX/panes"
claude_screen > "$SANDBOX/screen"; settled; check "receiver moved: found by its terminal" "$(sent)" "w2:p5 "
panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9"; claude_screen "야" > "$SANDBOX/screen"; pt "리드" "보고" >/dev/null
touch "$SANDBOX/herdr-down"; sleep 0.3
check "herdr away for a moment: the message keeps waiting" "$(ls "$SANDBOX"/state/pane-tell/waiting | wc -l | tr -d ' ')" 1
rm -f "$SANDBOX/herdr-down"; claude_screen > "$SANDBOX/screen"; settled; check "herdr back: delivered" "$(sent)" "w1:p1 "
panes "w1:p1|🟢 빈 칸|s1|w1:t1|-" "w1:p9|🟢 일|s9"
pt "빈 칸" "x" 2>/dev/null; check "no agent in the pane refuses" "$?|$(sent)" "2|"
panes "w1:p1|🟢 다른 도구|s1|w1:t1|gemini" "w1:p9|🟢 일|s9"
pt "다른 도구" "x" 2>/dev/null; check "an agent whose box cannot be read refuses" "$?|$(sent)" "2|"

# A box that stays full: 요유 hears about it once, the sender learns it did not arrive, and the words in the box stay.
alerted() { local i; for i in $(seq 1 60); do [ -s "$SANDBOX/alerts" ] && [ -s "$SANDBOX/log" ] && return; sleep 0.05; done; }
panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9"; rm -f "$SANDBOX/alerts"
claude_screen "E" > "$SANDBOX/screen"; claude_screen > "$SANDBOX/screen-w1:p9"
PANE_TELL_ALERT=0 pt "리드" "보고" >/dev/null; alerted
check "a stuck box reaches 요유" "$(grep -c '리드 칸 입력창' "$SANDBOX/alerts")" 1
check "and the sender" "$(sent)" "w1:p9 "
check "the note to the sender is not 요유's" "$(cut -d'|' -f2 "$SANDBOX/log" | grep -c '^\[pane-tell: 자동 안내입니다')" 1
sleep 0.2; check "announced once, not on every try" "$(wc -l < "$SANDBOX/alerts" | tr -d ' ')" 1
rm -f "$SANDBOX/screen-w1:p9"; claude_screen > "$SANDBOX/screen"; settled
check "the message still goes in once the box is empty" "$(sent)" "w1:p9 w1:p1 "
rm -f "$SANDBOX/alerts"; claude_screen "E" > "$SANDBOX/screen"; claude_screen > "$SANDBOX/screen-w1:p9"
PANE_TELL_WAIT=0 pt "리드" "보고" >/dev/null; alerted; settled
check "kept in a file: announced with where it is" "$(grep -c 'undelivered/' "$SANDBOX/alerts")" 1
rm -f "$SANDBOX"/state/pane-tell/undelivered/* "$SANDBOX/screen-w1:p9" "$SANDBOX/alerts"
panes "w1:p1|🟢 👑 리드|s1"; PANE_TELL_ALERT=0 pt "w1:p1" "보고" >/dev/null; alerted
check "a sender already closed: the notification alone" "$(wc -l < "$SANDBOX/alerts" | tr -d ' ')|$(sent)" "1|"
claude_screen > "$SANDBOX/screen"; settled
# A note typed into an auto pane counts as 요유 speaking and lifts its guard, so an auto sender gets the notification alone.
rm -f "$SANDBOX/alerts"; echo '{"result":{"tabs":[{"tab_id":"w1:t7","label":"자동 api#8"},{"tab_id":"w1:t9","label":"알아서 #1-dev 전환"}]}}' > "$SANDBOX/tabs"
panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9|w1:t7"; claude_screen "E" > "$SANDBOX/screen"; claude_screen > "$SANDBOX/screen-w1:p9"
PANE_TELL_ALERT=0 pt "w1:p1" "보고" >/dev/null; alerted; sleep 0.2
check "an auto-tab sender gets no note" "$(wc -l < "$SANDBOX/alerts" | tr -d ' ')|$(sent)" "1|"
claude_screen > "$SANDBOX/screen"; settled; check "and its message still goes in" "$(sent)" "w1:p1 "
rm -f "$SANDBOX/alerts"; panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s9|w1:t9"; claude_screen "E" > "$SANDBOX/screen"
PANE_TELL_ALERT=0 pt "w1:p1" "보고" >/dev/null; alerted; sleep 0.2
check "an ambient-tab sender gets no note" "$(wc -l < "$SANDBOX/alerts" | tr -d ' ')|$(sent)" "1|"
claude_screen > "$SANDBOX/screen"; settled
rm -f "$SANDBOX/alerts"; panes "w1:p1|🟢 👑 리드|s1" "w1:p9|🟢 일|s5|w1:t1"; claude_screen "E" > "$SANDBOX/screen"
PANE_TELL_ALERT=0 pt "w1:p1" "보고" >/dev/null; alerted; sleep 0.2
check "an auto session moved out of its tab gets no note" "$(wc -l < "$SANDBOX/alerts" | tr -d ' ')|$(sent)" "1|"
claude_screen > "$SANDBOX/screen"; settled; rm -f "$SANDBOX/screen-w1:p9" "$SANDBOX/alerts" "$SANDBOX/tabs"

# --raw is the same door for a machine's own prompt: no sender line, and it may be its own pane.
panes "w1:p1|🟢 일|s1" "w1:p9|🟢 일 둘|s9"
pt --raw w1:p9 "[handoff: 자동] 이어서"; check "raw: an empty box takes it as it is" "$?|$(cut -d'|' -f2 "$SANDBOX/log")" "0|[handoff: 자동] 이어서"
claude_screen "치던 글" > "$SANDBOX/screen"
pt --raw w1:p1 "[handoff: 자동] 이어서"; check "raw: words in the box hold it back" "$?|$(sent)" "0|"
claude_screen > "$SANDBOX/screen"; settled; check "raw: delivered once empty" "$(cut -d'|' -f2 "$SANDBOX/log")" "[handoff: 자동] 이어서"
claude_screen "치던 글" > "$SANDBOX/screen"; pt --raw w1:p1 "[handoff: 자동] 이어서"
panes "w1:p1|🟢 일|later" "w1:p9|🟢 일 둘|s9"; claude_screen > "$SANDBOX/screen"; settled
check "raw: a later session does not get the old session's prompt" "$(sent)|$(ls "$SANDBOX"/state/pane-tell/undelivered | wc -l | tr -d ' ')" "|1"
rm -f "$SANDBOX"/state/pane-tell/undelivered/* "$SANDBOX/alerts"
pt --raw w1:zz "x" 2>/dev/null; check "raw: no such pane" "$?" 3
pt --raw w1:p1 2>/dev/null; check "raw: bad usage" "$?" 2

pt --box-ready w1:p1 claude; check "box-ready: empty" "$?" 0
claude_screen "치던 글" > "$SANDBOX/screen"; pt --box-ready w1:p1 claude; check "box-ready: words in it" "$?" 1

exit $fails
