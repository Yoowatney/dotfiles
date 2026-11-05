#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-spawn"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr that logs each call to $SANDBOX/log. FAIL_START=<n> fails the first n agent starts, SESSIONLESS=1 keeps
# the session id empty as Codex does until its first prompt, FAIL_WAIT=<n> fails the first n waits for work.
# Each line starts with the server the call went to, read from HERDR_SOCKET_PATH: [default] or [workers].
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] \$*" >> "$SANDBOX/log"
[ "\$s" != workers ] || [ -z "\${STUCK_WORKERS:-}" ] || exec /bin/sleep 30 # herdr's CLI waits on a stuck server for ever
case "\$1 \$2" in
  "session list") cat "$SANDBOX/sessions.json" ;;
  "workspace list") cat "$SANDBOX/\$s-workspaces.json" 2>/dev/null || echo '{"result":{"workspaces":[]}}' ;;
  "workspace create") [ -z "\${FAIL_WORKSPACE:-}" ] && jq -n '{result: {workspace: {workspace_id: "w2"}}}' ;;
  "pane get") jq -n --arg label "\${CALLER_LABEL:-}" '{result: {pane: {pane_id: "w7:p3", tab_id: "w7:t1", workspace_id: "w7", terminal_id: "term_65f21913323dc21", label: \$label}}}' ;; # moved: env says w1:p1
  "agent list") cat "$SANDBOX/\$s-agents.json" 2>/dev/null || echo '{"result":{"agents":[]}}' ;;
  "tab rename") ;;
  "tab create") { [ -z "\${FAIL_WORKERS_TAB:-}" ] || [ "\$s" != workers ]; } && jq -n '{result: {root_pane: {pane_id: "w1:new"}, tab: {tab_id: "w1:t9"}}}' ;;
  "pane layout") [ -z "\${FAIL_LAYOUT:-}" ] && cat "$SANDBOX/layout.json" ;;
  "pane split") [ -z "\${FAIL_SPLIT:-}" ] && jq -n '{result: {pane: {pane_id: "w1:new"}}}' ;;
  "pane rename"|"agent prompt"|"pane close") ;;
  "agent wait")
    n=\$(cat "$SANDBOX/waits" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/waits"
    [ "\$n" -ge "\${FAIL_WAIT:-0}" ] ;;
  "agent start")
    [[ "\$3" =~ ^[a-z][a-z0-9_-]{0,31}\$ ]] || { echo invalid_agent_name; exit 1; } # herdr's rule
    n=\$(cat "$SANDBOX/starts" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/starts"
    [ "\$n" -ge "\${FAIL_START:-0}" ] ;;
  "agent get")
    if [ -n "\${SESSIONLESS:-}" ]; then jq -n '{result: {agent: {interactive_ready: true}}}'
    else jq -n --arg n "\${CALLER_AGENT:-}" '{result: {agent: {name: \$n, interactive_ready: true, agent_session: {value: "sid"}}}}'; fi ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
cat > "$SANDBOX/pane-label" <<EOF
#!/usr/bin/env bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] pane-label \$*" >> "$SANDBOX/log"
EOF
chmod +x "$SANDBOX/pane-label"

run_on() { local sock=$1; shift; rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits"; env -u HERDR_SOCKET_PATH -u XDG_STATE_HOME ${sock:+HERDR_SOCKET_PATH="$sock"} HOME="$SANDBOX/home" PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_RETRY_WAIT=0 bash "$SUBJECT" "$@"; }
ps() { run_on "" "$@"; }
log() { cat "$SANDBOX/log" 2>/dev/null || true; }
has() { grep -qF -- "$1" "$SANDBOX/log" || fail "$2: no [$1] in: $(log)"; }
hasnt() { if grep -qF -- "$1" "$SANDBOX/log"; then fail "$2: found [$1] in: $(log)"; fi; }
before() { [ "$(grep -nF -- "$1" "$SANDBOX/log" | head -1 | cut -d: -f1)" -lt "$(grep -nF -- "$2" "$SANDBOX/log" | head -1 | cut -d: -f1)" ] || fail "[$1] should come before [$2]: $(log)"; }

# The caller's tab as herdr reports it, one "<pane id> <x> <y> <height>" per pane.
layout() { printf '%s\n' "$@" | jq -Rn '{result: {layout: {panes: [inputs | split(" ") | {pane_id: .[0], rect: {x: (.[1] | tonumber), y: (.[2] | tonumber), height: (.[3] | tonumber)}}]}}}' > "$SANDBOX/layout.json"; }
layout "w7:p3 0 0 46"

# Default: the caller's tab, split to its right; the caller is marked 👑, the new pane 🫡 before its agent starts.
out=$(ps --cwd /work "api#1 설계" "할 일입니다")
has "pane-label --lead" "caller becomes the lead"
has "pane split --pane w7:p3 --direction right --cwd /work --no-focus" "a lead alone in its tab keeps the left, the pane goes right"
has "agent start spawn-65f21913323dc21-" "the agent's name records the caller's terminal, within herdr's limit"
hasnt "tab create" "no new tab by default"
has "pane rename w1:new 🟢 🫡 api#1 설계" "the first name carries 🫡"
before "pane rename w1:new" "agent start"
before "pane rename w1:new" "pane-label --sync-tabs"
before "pane-label --sync-tabs" "agent start"
has "--kind claude --pane w1:new" "claude by default"

has "agent prompt w1:new 할 일입니다" "instruction delivered"
has "요유는 이 칸을 보지 않으니" "a new pane learns its reports go to the lead"
before "agent get w1:new" "agent prompt"
before "agent prompt" "agent wait w1:new --until working --until blocked --until done"
[[ "$out" == *"w1:new"* ]] || fail "prints the new pane: $out"

# 요유 2026-11-01: a job has one lead. A pane under a lead that opens a review pane stays under it, and that pane reports to it.
CALLER_LABEL="🟢 🫡 구현" ps --same-tab "리뷰" "봐 줘" >/dev/null
hasnt "pane-label --lead" "a pane under a lead does not become one"
has "agent start spawn-65f21913323dc21-" "the new pane still reports to the pane that opened it"
CALLER_LABEL="🟢 👑 리드" ps --same-tab "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a lead stays one"
CALLER_LABEL="🟢 일" ps "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a pane under nobody becomes the lead"

# 요유 2026-11-04: a lead and its panes share one tab, the lead on the whole left, its panes stacked on the right.
layout "w7:p3 0 0 46" "w7:p4 81 0 46"
ps "구현" "지시" >/dev/null
has "pane split --pane w7:p4 --direction down" "the next pane stacks in the right column"
hasnt "tab create" "still no new tab"
layout "w7:p3 0 0 46" "w7:p4 81 0 23" "w7:p5 81 23 11" "w7:p6 81 34 12"
ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p4 --direction down" "the tallest pane takes it, so heights stay close"
layout "w7:p3 0 0 46" "w7:p4 81 0 23" "w7:p5 81 23 23"
ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p5 --direction down" "a tie goes to the lowest"
layout "w7:p1 0 0 46" "w7:p3 81 0 46"
CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p3 --direction down" "a pane under the lead stacks in the column, not beside itself"
layout "w7:p3 0 0 46" "w7:p4 81 0 11" "w7:p5 81 11 11" "w7:p6 81 22 12" "w7:p7 81 34 12"
ps "다섯째" "지시" >/dev/null
has "tab create --workspace w7 --cwd" "a column too short to read: the work gets its own tab"
has "--label 다섯째 --no-focus" "named for the work"
hasnt "pane split" "no pane shorter than can be read"

# A job the lead does not follow asks for its own tab, and a split or layout that fails still opens the pane.
layout "w7:p3 0 0 46"
ps --new-tab --cwd /work "api#1 설계" "지시" >/dev/null
has "tab create --workspace w7 --cwd /work --label api#1 설계 --no-focus" "--new-tab in the caller's current workspace"
hasnt "pane split" "--new-tab splits nothing"
hasnt "pane layout" "--new-tab reads no layout"
FAIL_SPLIT=1 ps "실패" "지시" >/dev/null
has "tab create --workspace w7" "a failed split falls back to a tab"
has "agent prompt w1:new 지시" "and the work still goes out"
FAIL_LAYOUT=1 ps "실패" "지시" >/dev/null
has "tab create --workspace w7" "so does a failed layout read"

# --same-tab was the way to ask for this before it became the default: sessions already running may still pass it.
ps --same-tab "api#1 리뷰" "리뷰" >/dev/null
has "pane split --pane w7:p3 --direction right" "--same-tab still works"
hasnt "tab create" "--same-tab opens no tab"

# Agent arguments pass through, and Codex works without a session id until its first prompt.
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -m gpt-5 >/dev/null
has "--kind codex --pane w1:new --timeout 120000 -- -m gpt-5" "codex with its arguments"
has "agent prompt w1:new 지시" "codex gets the instruction"

# A pane that was not ready for the first start is tried again.
FAIL_START=1 ps "재시도" "지시" >/dev/null
[ "$(cat "$SANDBOX/starts")" = 2 ] || fail "should retry a failed start"
has "agent prompt w1:new 지시" "retry delivers"

# A start that keeps failing removes the empty pane and says so.
if FAIL_START=9 ps "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "pane close w1:new" "the empty pane goes"
hasnt "agent prompt" "nothing to prompt"

# Work that never shows fails loudly, with no resend and the pane left open: an instruction Codex dropped looks the same as a
# wait that failed on an accepted one, and resending that would run the job twice.
if out=$(FAIL_WAIT=1 ps "무응답" "지시" 2>"$SANDBOX/err"); then fail "no work seen must exit non-zero"; fi
[ "$(grep -c "agent prompt w1:new 지시" "$SANDBOX/log")" = 1 ] || fail "sends once: $(log)"
hasnt "pane close" "the pane stays"
[[ "$out" == *"w1:new"* ]] || fail "still names the pane: $out"
grep -q "pane-tell" "$SANDBOX/err" || fail "says what to do: $(cat "$SANDBOX/err")"

# Bad usage.
if ps "이름만" 2>/dev/null; then fail "missing instruction must exit non-zero"; fi
if ps --kind gemini "이름" "지시" 2>/dev/null; then fail "unknown kind must exit non-zero"; fi
if env -u HERDR_PANE_ID HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "이름" "지시" 2>/dev/null; then fail "outside herdr must exit non-zero"; fi

# The main pane does no work itself: what it opens goes to the workers server, and its address names that server.
is_main() { mkdir -p "$SANDBOX/home/.local/state/orchestrator"; echo term_65f21913323dc21 > "$SANDBOX/home/.local/state/orchestrator/main"; }
not_main() { rm -f "$SANDBOX/home/.local/state/orchestrator/main"; }
workers_up() { jq -n --arg w "$SANDBOX/sessions/workers/herdr.sock" --argjson r "${1:-true}" '{sessions: [{name: "default", running: true, socket_path: "/x/herdr.sock"}, {name: "workers", running: $r, socket_path: $w}]}' > "$SANDBOX/sessions.json"; }
workers_up; echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"

is_main; out=$(ps "api#1 설계" "할 일")
has "[workers] tab create --workspace w1 " "a pane the main pane opens goes to workers"
hasnt "[default] tab create" "nothing opens beside the main pane"
hasnt "pane split" "the main pane keeps its tab to itself"
has "[default] pane get w1:p1" "the caller is read on its own server"
has "[default] pane-label --lead" "the caller is marked on its own server"
has "[workers] pane-label --sync-tabs" "tab marks are synced where the new tab is"
has "[workers] agent start spawn-65f21913323dc21-" "the name still records the main pane's terminal"
has "[workers] agent prompt w1:new" "the instruction reaches the workers pane"
has "보고 첫 줄에" "a pane under the main pane learns its first line is its summary"
[ "$out" = "workers/w1:new" ] || fail "prints an address with the server: $out"

rm "$SANDBOX/workers-workspaces.json"; ps "api#2" "할 일" >/dev/null
has "[workers] workspace create --label workers --no-focus" "an empty workers server gets its workspace"
before "workspace create" "tab create --workspace w2"

# 요유 2026-11-05: a tab named `자동 칸을 workers로 이전` read as a dispatch tab, so pane-tell refused its pane. A work name never
# starts a job tab's name; the pane keeps the work name as given.
for word in 자동 알아서; do
  ps "$word 칸을 workers로 이전" "할 일" >/dev/null
  has "[workers] tab create --workspace w2 --cwd $PWD --label 일 $word 칸을 workers로 이전 --no-focus" "the main pane's tab: no job tab's first word ($word)"
  hasnt "--label $word " "no tab starts with $word"
  has "pane rename w1:new 🟢 🫡 $word 칸을 workers로 이전" "the pane keeps the work name"
done
ps "자동화 정리" "할 일" >/dev/null
has "--label 자동화 정리 --no-focus" "자동화 is no job tab's first word"
not_main; echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
CALLER_LABEL="🟢 자동 칸 정리" ps "구현" "할 일" >/dev/null
has "[workers] tab rename w7:t1 일 자동 칸 정리" "a lead's tab named for it: no job tab's first word"
hasnt "--label 자동 " "and none when the tab opens"
FAIL_SPLIT=1 ps --new-tab "자동 칸 고치기" "할 일" >/dev/null
hasnt "--label 자동 " "a --new-tab name goes the same way"
rm -f "$SANDBOX/workers-workspaces.json"; is_main
ps "api#2-2" "할 일" >/dev/null

workers_up false; if ps "api#3" "할 일" 2>"$SANDBOX/err"; then fail "workers down must fail"; fi
grep -q "com.yoyoo.herdr-workers" "$SANDBOX/err" || fail "says how to start it: $(cat "$SANDBOX/err")"
hasnt "tab create" "no pane opens anywhere when workers is down"
workers_up

if ps --same-tab "리뷰" "봐 줘" 2>/dev/null; then fail "the main pane never splits its own tab"; fi
hasnt "pane split" "no split beside the main pane"

if FAIL_START=9 ps "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "[workers] pane close w1:new" "a failed start removes the workers pane"

hasnt "확인 칸입니다" "a pane that does the job is no check pane"

# 요유 2026-11-05 (api#1002): a fresh pane checks a pane's report for the main pane. Its agent name says so; a rename cannot change that.
out=$(ps --check "api#1 로그인" "npm test를 돌려 보세요")
has "[workers] tab create --workspace " "a check pane opens with the other workers"
has "[workers] pane rename w1:new 🟢 🫡 확인 api#1 로그인" "its name says it is a check pane"
has "[workers] agent start spawn-65f21913323dc21-chk" "its agent name tells auto-tab-close, and still records who opened it"
has "npm test를 돌려 보세요" "the instruction arrives"
has "확인 칸입니다" "it hears what a check pane does"
has "고치지 말고" "a check pane changes nothing"
has "다른 칸의 보고를 다시 읽는 것은 확인이 아닙니다" "rereading a report is no check"
has "보고 첫 줄에" "its first line is what the main pane reads"
[ "$out" = "workers/w1:new" ] || fail "a check pane's address names its server: $out"
ps --cwd /tmp --check "로그인" "돌려 보세요" >/dev/null
has "확인 칸입니다" "options in any order"
ps --check --kind codex "로그인" "돌려 보세요" >/dev/null
has "agent start spawn-65f21913323dc21-chk" "any agent kind"
has "--kind codex" "any agent kind"

# Every other lead checks results itself, as does a pane under it, and a main-pane file that is stale or gone is no main pane.
for state in other stale missing; do
  case "$state" in
    other) not_main ;;
    stale) mkdir -p "$SANDBOX/home/.local/state/orchestrator"; echo term_gone > "$SANDBOX/home/.local/state/orchestrator/main" ;;
    missing) not_main ;;
  esac
  if ps --check "로그인" "돌려 보세요" 2>"$SANDBOX/err"; then fail "--check outside the main pane must fail ($state)"; fi
  grep -q "직접 확인" "$SANDBOX/err" || fail "says what to do instead ($state): $(cat "$SANDBOX/err")"
  hasnt "tab create" "no pane opens ($state)"
  hasnt "pane split" "no pane opens ($state)"
  hasnt "agent start" "no agent starts ($state)"
done
is_main

# 요유 2026-11-05: an ordinary lead's tab holds only the lead. Its panes run on workers, one tab per lead there.
not_main; LEAD="🟢 👑 api#1 로그인"
echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
out=$(CALLER_LABEL=$LEAD ps --cwd /work "설계" "할 일")
has "[default] pane-label --lead" "the caller is marked on its own server"
has "[workers] tab create --workspace w1 --cwd /work --label api#1 로그인 --no-focus" "the tab says whose panes these are"
has "[workers] pane rename w1:new 🟢 🫡 설계" "the pane says what it does"
has "[workers] pane-label --sync-tabs" "marks are synced where the tab is"
has "[workers] agent start spawn-65f21913323dc21-" "the name records the lead's terminal"
hasnt "[default] pane split" "nothing opens beside the lead"
hasnt "[default] tab create" "nothing opens on the lead's server"
hasnt "보고 첫 줄에" "only panes under the main pane get the summary line"
hasnt "확인 칸입니다" "no check pane anywhere else"
[ "$out" = "workers/w1:new" ] || fail "an address that names the server: $out"

# Design, build and review of one job share a tab: the lead's newest pane on workers takes the next one. --same-tab is the same.
jq -n '{result: {agents: [{pane_id: "w1:q2", name: "spawn-65f21913323dc21-7"}, {pane_id: "w1:q3", name: "spawn-65f21913323dc21-8"}, {pane_id: "w1:q9", name: "spawn-OTHER-1"}]}}' > "$SANDBOX/workers-agents.json"
layout "w1:q3 0 0 41"
out=$(CALLER_LABEL=$LEAD ps "구현" "지시")
has "[workers] pane layout --pane w1:q3" "the lead's newest pane, not another lead's"
has "[workers] pane split --pane w1:q3 --direction right" "the same layout as ever"
has "[workers] tab rename w7:t1 api#1 로그인" "a lead that moved on renames its tab"
before "tab rename" "pane-label --sync-tabs"
hasnt "tab create" "no second tab"
[ "$out" = "workers/w1:new" ] || fail "an address that names the server: $out"
CALLER_LABEL=$LEAD ps --same-tab "리뷰" "봐 줘" >/dev/null
has "[workers] pane split" "--same-tab joins the lead's tab on workers"
hasnt "tab create" "--same-tab opens no tab"
layout "w1:q2 0 0 41" "w1:q3 52 0 10" "w1:q4 52 10 10" "w1:q5 52 20 10" "w1:q6 52 30 11"
CALLER_LABEL=$LEAD ps "여섯째" "지시" >/dev/null
has "[workers] tab create --workspace w1 --cwd" "a full tab: another tab of the same lead"
has "--label api#1 로그인 --no-focus" "named for the lead as well"
layout "w1:q3 0 0 41"
CALLER_LABEL=$LEAD ps --new-tab "별개 일" "지시" >/dev/null
has "[workers] tab create --workspace w1" "--new-tab: its own tab on workers"
hasnt "pane split" "--new-tab splits nothing"
hasnt "[default] tab create" "--new-tab stays hidden too"
jq -n '{result: {agents: [{pane_id: "w1:q9", name: "spawn-OTHER-1"}]}}' > "$SANDBOX/workers-agents.json"
CALLER_LABEL="🔴 👑 다른 일" ps "조사" "지시" >/dev/null
has "--label 다른 일 --no-focus" "several leads at once: each its own tab"
hasnt "pane split" "another lead's pane takes nothing"
CALLER_LABEL="" ps "이름 없는 리드" "지시" >/dev/null
has "--label 이름 없는 리드 --no-focus" "a lead with no name yet: the work names the tab"
CALLER_LABEL="🟢 새 세션 munice" ps "이름 없는 리드" "지시" >/dev/null
has "--label 이름 없는 리드 --no-focus" "the name a new session starts with is no name"
rm "$SANDBOX/workers-agents.json"

# 요유 2026-11-05: what a Home lead's pane says may reach the main pane once that lead closes, and it stays off company Slack and the
# board. The agent name says so, since a rename cannot change it and the lead's workspace cannot be read after it closes.
hasnt "agent start spawn-65f21913323dc21-h" "a pane of any other lead carries no mark"
echo '{"result":{"workspaces":[{"workspace_id":"w7","label":"Home"}]}}' > "$SANDBOX/default-workspaces.json"
CALLER_LABEL="🟢 👑 집 일" ps "조사" "지시" >/dev/null
has "[workers] agent start spawn-65f21913323dc21-h" "a Home lead's pane is marked"
is_main; ps "메인 일" "지시" >/dev/null; hasnt "agent start spawn-65f21913323dc21-h" "the main pane is no Home lead, wherever it sits"; not_main
rm "$SANDBOX/default-workspaces.json"
CALLER_LABEL="🟢 🫡 구현" CALLER_AGENT="spawn-OTHER-h77" ps "조사" "지시" >/dev/null
has "[default] agent start spawn-65f21913323dc21-h" "a pane under a Home lead passes the mark on"
CALLER_LABEL="🟢 🫡 구현" CALLER_AGENT="spawn-OTHER-77" ps "조사" "지시" >/dev/null
hasnt "agent start spawn-65f21913323dc21-h" "any other pane has none to pass on"

# Who stays where it is: a pane under a lead, a job tab (pane-tell refuses a job pane, so a hidden child could reach nobody),
# and any server but default.
layout "w7:p3 0 0 46"
stays() { has "pane split --pane w7:p3" "$1"; hasnt "[workers] tab create" "$1"; hasnt "[workers] agent start" "$1"; [ "$2" = "w1:new" ] || fail "$1: a bare id: $2"; }
out=$(CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시"); stays "a pane under a lead" "$out"
out=$(CALLER_LABEL=$LEAD MUNICE_AUTO_DISPATCH=1 ps "리뷰" "지시"); stays "a dispatch tab" "$out"
out=$(CALLER_LABEL=$LEAD MUNICE_AMBIENT=1 ps "리뷰" "지시"); stays "an ambient tab" "$out"
out=$(CALLER_LABEL=$LEAD run_on "$SANDBOX/sessions/resume/herdr.sock" "리뷰" "지시"); stays "another named server" "$out"
has "[resume] pane split" "it opens on its own server"
out=$(CALLER_LABEL=$LEAD run_on "$SANDBOX/sessions/workers/herdr.sock" "리뷰" "지시"); [ "$out" = "w1:new" ] || fail "from workers, a bare id: $out"
has "[workers] pane split --pane w7:p3" "a lead on workers opens beside itself"

# workers cannot take it: an ordinary lead opens in its own tab as before and says so, with no workers socket left behind.
falls_back() { # <why> <pane-spawn's stdout>
  has "[default] pane split --pane w7:p3" "$1: its own tab"
  has "[default] pane rename w1:new" "$1: on its own server"
  has "[default] pane-label --sync-tabs" "$1: on its own server"
  has "[default] agent start" "$1: on its own server"
  [ "$2" = "w1:new" ] || fail "$1: a bare id: $2"
  grep -q "workers 서버" "$SANDBOX/err" || fail "$1: says so: $(cat "$SANDBOX/err")"
}
workers_up false; out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "workers down: a lead still gets its pane"; falls_back "workers down" "$out"
jq -n '{sessions: [{name: "default", running: true, socket_path: "/x/herdr.sock"}]}' > "$SANDBOX/sessions.json"
out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "no workers server: a lead still gets its pane"; falls_back "no workers server (집맥)" "$out"
rm "$SANDBOX/sessions.json"
out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "no session list: a lead still gets its pane"; falls_back "no session list" "$out"
workers_up
out=$(CALLER_LABEL=$LEAD FAIL_WORKERS_TAB=1 ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "a workers tab that fails: a lead still gets its pane"; falls_back "a workers tab that fails" "$out"
mv "$SANDBOX/workers-workspaces.json" "$SANDBOX/kept"
out=$(CALLER_LABEL=$LEAD FAIL_WORKSPACE=1 ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "no workspace there: a lead still gets its pane"; falls_back "no workspace on workers" "$out"
mv "$SANDBOX/kept" "$SANDBOX/workers-workspaces.json"
out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err"); [ ! -s "$SANDBOX/err" ] || fail "a pane on workers says nothing: $(cat "$SANDBOX/err")"
# A stuck server still says it runs. The lead falls back in seconds, and a server that may only be slow gets no second workspace.
started=$SECONDS
out=$(CALLER_LABEL=$LEAD STUCK_WORKERS=1 PANE_SPAWN_WORKERS_LIMIT=1 ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "a stuck workers: a lead still gets its pane"; falls_back "a stuck workers" "$out"
[ $((SECONDS - started)) -lt 10 ] || fail "a stuck workers held the lead for $((SECONDS - started)) s"
hasnt "[workers] workspace create" "nothing is created after a list that timed out"
# The main pane never falls back: it does no work, and neither does its tab.
is_main
if STUCK_WORKERS=1 PANE_SPAWN_WORKERS_LIMIT=1 ps "메인 일" "지시" 2>"$SANDBOX/err"; then fail "the main pane fails on a stuck workers"; fi
if FAIL_WORKERS_TAB=1 ps "메인 일" "지시" 2>"$SANDBOX/err"; then fail "the main pane fails when workers opens nothing"; fi
hasnt "[default] pane split" "nothing beside the main pane"
hasnt "[default] tab create" "nothing beside the main pane"
grep -q "com.yoyoo.herdr-workers" "$SANDBOX/err" || fail "says how to start it: $(cat "$SANDBOX/err")"
ps "메인 일" "지시" >/dev/null
has "--label 메인 일 --no-focus" "the main pane's tabs keep the work's name"
hasnt "tab rename" "and nothing renames them"
hasnt "agent list" "the main pane's panes share no tab, so it looks for none"
not_main

echo "ok: pane-spawn"
