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
  "pane list") cat "$SANDBOX/panes" ;;
  "agent prompt") printf '%s|%s\n' "\$3" "\$4" >> "$SANDBOX/log" ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
panes() { # "<id>|<label>|<session>" ...
  local p; for p in "$@"; do IFS='|' read -r id label sid <<<"$p"; jq -nc --arg i "$id" --arg l "$label" --arg s "$sid" '{pane_id: $i, label: $l, agent_session: {value: $s}, cwd: "/w"}'; done \
    | jq -s '{result: {panes: .}}' > "$SANDBOX/panes"
  rm -f "$SANDBOX/log"
}
pt() { rm -f "$SANDBOX/log"; env HOME="$SANDBOX/home" HERDR_PANE_ID=w1:p9 HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "$@"; }
sent() { cut -d'|' -f1 "$SANDBOX/log" 2>/dev/null | tr '\n' ' '; }

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
pt "only-one-arg" 2>/dev/null; check "usage" "$?" 2
exit $fails
