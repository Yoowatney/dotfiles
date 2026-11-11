#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-label-nudge.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr: `herdr pane get <id>` prints a pane with the label in $FAKE_LABEL,
# or fails when FAKE_HERDR_FAIL is set; `herdr pane rename <id> <name>` logs the name.
cat > "$SANDBOX/herdr" <<'EOF'
#!/usr/bin/env bash
[ -n "${FAKE_HERDR_FAIL:-}" ] && exit 1
if [ "$1 $2" = "pane rename" ]; then echo "$4" >> "$(dirname "$0")/rename.log"; exit 0; fi
[ "$1 $2" = "pane get" ] || exit 2
if [ -n "${FAKE_LABEL:-}" ]; then
    jq -n --arg l "$FAKE_LABEL" --arg t "${FAKE_TERM:-}" '{result: {pane: {pane_id: "w1:p1", label: $l, terminal_id: $t}}}'
else
    echo '{"result":{"pane":{"pane_id":"w1:p1"}}}'
fi
EOF
chmod +x "$SANDBOX/herdr"

run_hook() { # prints the hook's stdout; the hook must always exit 0
    echo '{"hook_event_name":"UserPromptSubmit","prompt":"hi"}' |
        env -u HERDR_PANE_ID -u FAKE_LABEL -u FAKE_TERM -u FAKE_HERDR_FAIL HERDR_BIN_PATH="$SANDBOX/herdr" "$@" "$SUBJECT" ||
        fail "hook exited non-zero ($*)"
}

context() { jq -r '.hookSpecificOutput.additionalContext'; }

# Outside herdr: say nothing.
out=$(run_hook)
[ -z "$out" ] || fail "outside herdr should print nothing, got: $out"

# Inside herdr with a label: name it and say how to rename.
out=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="cf#543 미국 확장")
[ "$(jq -r '.hookSpecificOutput.hookEventName' <<<"$out")" = "UserPromptSubmit" ] || fail "event name missing: $out"
ctx=$(context <<<"$out")
[[ "$ctx" == *'"cf#543 미국 확장"'* ]] || fail "current label not shown: $ctx"
[[ "$ctx" == *'pane-label "'* ]] || fail "rename command not shown: $ctx"

# 🔴: say what it means and how to turn it back to 🟢 once nothing runs here (요유 2026-10-17).
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="🔴 api#819 배포 감시" | context)
[[ "$ctx" == *'"🔴 api#819 배포 감시"'* ]] || fail "🔴 label not shown: $ctx"
[[ "$ctx" == *"pane-label --free"* ]] || fail "🔴 should say how to free: $ctx"

# 🟢: say how to mark 🔴 when starting something that stops with the session.
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 api#819 배포" | context)
[[ "$ctx" == *"pane-label --hold"* ]] || fail "🟢 should say how to hold: $ctx"
[[ "$ctx" != *"pane-label --free"* ]] || fail "🟢 should not ask to free: $ctx"

# A correction gets its reminder, inside herdr and outside it (Codex or a plain terminal), and a plain request does not.
ask() { # <prompt> <env...>
    local prompt=$1; shift
    jq -n --arg p "$prompt" '{hook_event_name: "UserPromptSubmit", session_id: "s/1", prompt: $p}' |
        env -u HERDR_PANE_ID -u FAKE_LABEL -u FAKE_HERDR_FAIL HERDR_BIN_PATH="$SANDBOX/herdr" PANE_FIT_DIR="$SANDBOX/fit" "$@" "$SUBJECT" ||
        fail "hook exited non-zero ($*)"
}
ctx=$(ask "왜 또 그렇게 했어" | context)
[[ "$ctx" == *"같은 종류"* ]] || fail "outside herdr a correction still reminds: $ctx"
[ -z "$(ask "로그 봐 줘")" ] || fail "outside herdr a plain request prints nothing"
[ ! -e "$SANDBOX/fit" ] || fail "outside herdr nothing is armed"
ctx=$(ask "왜 또 그렇게 했어" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 일" | context)
[[ "$ctx" == *"같은 종류"* && "$ctx" == *"pane-label"* ]] || fail "both reminders in one message: $ctx"
ctx=$(ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 일" | context)
[[ "$ctx" != *"같은 종류"* ]] || fail "a plain request gets no reminder: $ctx"

# Each message arms the pane question, under a name that cannot leave the folder.
[ -f "$SANDBOX/fit/s_1" ] || fail "message did not arm pane-fit"
rm "$SANDBOX/fit/s_1"
ask "[pane-tell: 다른 세션] 보고입니다" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 일" >/dev/null
[ ! -e "$SANDBOX/fit/s_1" ] || fail "another session's message armed pane-fit"
ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 일" >/dev/null
[ -f "$SANDBOX/fit/s_1" ] || fail "a message from 요유 stopped arming"

# The main pane is pointed at its rules file, never asked to rename; other panes are nudged as before.
unset XDG_STATE_HOME
mkdir -p "$SANDBOX/home/.local/state/orchestrator"; echo term_M > "$SANDBOX/home/.local/state/orchestrator/main"
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인 오케스트레이터" FAKE_TERM=term_M HOME="$SANDBOX/home" | context)
[[ "$ctx" == *"main-orchestrator/SKILL.md"* ]] || fail "the main pane is pointed at its rules: $ctx"
[[ "$ctx" != *'pane-label "'* ]] || fail "the main pane is never asked to rename: $ctx"
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="cf#543 미국 확장" FAKE_TERM=term_X HOME="$SANDBOX/home" | context)
[[ "$ctx" == *'pane-label "'* ]] || fail "other panes are nudged as before: $ctx"
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인 오케스트레이터" FAKE_TERM=term_M HOME="$SANDBOX/home" | context)
[[ "$ctx" == *"lookups"* && "$ctx" == *"check"* ]] || fail "the main pane hears that lookups and checks go to panes too: $ctx"

# 요유 2026-11-05 (#0-yoyooyo token thread): pane-fit never refuses the main pane. Each refusal is one more model call over the
# whole long session (240 that day); the main pane's line above carries the same guidance. A file left from before goes.
mkdir -p "$SANDBOX/fit"; printf delegates >"$SANDBOX/fit/s_1"
ask "[pane-tell: '자동 작업' 세션이 보낸 글입니다] [Slack #yoyooyo 요유] 로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_TERM=term_M HOME="$SANDBOX/home" >/dev/null
[ ! -e "$SANDBOX/fit/s_1" ] || fail "a relayed message does not arm the main pane, and an old file goes"
ctx=$(ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_TERM=term_M HOME="$SANDBOX/home" | context)
[ ! -e "$SANDBOX/fit/s_1" ] || fail "요유's own message does not arm it either"
[[ "$ctx" == *"one-line answer"* && "$ctx" == *"prod 허용"* && "$ctx" == *"pane-tell"* ]] || fail "the main pane hears what stays there: $ctx"
# What yoyoo-share-guard.py left for the next message instead of sending the main pane's reply back: told once, then gone.
mkdir -p "$SANDBOX/home/.local/state/next-prompt"
printf 'yoyoo-share-guard: 지난 보고 때 칸이 멈춰 있었습니다: 구현(workers/w1:p3).\n두 번째 줄.\n' >"$SANDBOX/home/.local/state/next-prompt/s_1"
ctx=$(ask "[pane-tell: 다른 세션] 보고입니다" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_TERM=term_M HOME="$SANDBOX/home" | context)
[[ "$ctx" == *"main-orchestrator"*"구현(workers/w1:p3). 두 번째 줄."* ]] || fail "the note rides on the next message: $ctx"
[ ! -e "$SANDBOX/home/.local/state/next-prompt/s_1" ] || fail "and is removed once told"
ctx=$(ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_TERM=term_M HOME="$SANDBOX/home" | context)
[[ "$ctx" != *"구현(workers/w1:p3)"* ]] || fail "told once: $ctx"
# Every other pane: an empty file, as ever, even one the same session wrote as the main pane a message ago.
ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 일" FAKE_TERM=term_X HOME="$SANDBOX/home" >/dev/null
[ -f "$SANDBOX/fit/s_1" ] && [ ! -s "$SANDBOX/fit/s_1" ] || fail "an ordinary pane arms the ordinary question"
rm -f "$SANDBOX/fit/s_1"
ask "[pane-tell: 다른 세션] 보고입니다" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 일" FAKE_TERM=term_X HOME="$SANDBOX/home" >/dev/null
[ ! -e "$SANDBOX/fit/s_1" ] || fail "an ordinary pane is not armed by another session's message"
# A main-pane file that is missing or names another terminal: the ordinary question.
echo term_gone > "$SANDBOX/home/.local/state/orchestrator/main"
ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_TERM=term_M HOME="$SANDBOX/home" >/dev/null
[ -f "$SANDBOX/fit/s_1" ] && [ ! -s "$SANDBOX/fit/s_1" ] || fail "a stale main-pane file falls back"
rm -f "$SANDBOX/home/.local/state/orchestrator/main" "$SANDBOX/fit/s_1"
ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_TERM=term_M HOME="$SANDBOX/home" >/dev/null
[ -f "$SANDBOX/fit/s_1" ] && [ ! -s "$SANDBOX/fit/s_1" ] || fail "a missing main-pane file falls back"
rm -f "$SANDBOX/fit/s_1"
ask "로그 봐 줘" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 👑 메인" FAKE_HERDR_FAIL=1 HOME="$SANDBOX/home" >/dev/null
[ ! -s "$SANDBOX/fit/s_1" ] || fail "herdr down falls back"

# Inside herdr without a label: say there is none.
ctx=$(run_hook HERDR_PANE_ID=w1:p1 | context)
[[ "$ctx" == *"no label"* ]] || fail "missing-label case not stated: $ctx"

# herdr unreachable: still remind, never fail the prompt.
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_HERDR_FAIL=1 | context)
[[ "$ctx" == *"no label"* ]] || fail "herdr failure should read as no label: $ctx"

# The placeholder a new session gets reads as no name yet: name it, not "rename if the task changed".
ctx=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 새 세션 gomgom" | context)
[[ "$ctx" == *"no label yet"* ]] || fail "placeholder should ask for a name: $ctx"

# Long session: past 300k tokens of context, a new task should start in a cleared session (요유 2026-10-17).
# The size is the last assistant message's usage; a compacted session reads small again.
run_with_transcript() { # $1 = transcript path; prints the additionalContext
    echo "{\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"hi\",\"transcript_path\":\"$1\"}" |
        env -u FAKE_HERDR_FAIL HERDR_BIN_PATH="$SANDBOX/herdr" HERDR_PANE_ID=w1:p1 FAKE_LABEL="🟢 api#1 작업" "$SUBJECT" |
        context || fail "hook exited non-zero with transcript $1"
}
usage_line() { echo "{\"type\":\"assistant\",\"message\":{\"usage\":{\"input_tokens\":2,\"cache_read_input_tokens\":$1,\"cache_creation_input_tokens\":1000}}}"; }
T="$SANDBOX/long.jsonl"
{ echo '{"type":"user","message":{"content":"hi"}}'; usage_line 340000; echo 'not json'; } > "$T"
ctx=$(run_with_transcript "$T")
[[ "$ctx" == *"handoff-format.md"* ]] || fail "long session should name the handoff format: $ctx"
[[ "$ctx" == *"341k"* ]] || fail "long session should say its size: $ctx"
# A /clear suggestion saves the handoff for the next session, which loads it itself (요유 2026-10-21);
# the clipboard round trip made 요유 the bottleneck.
[[ "$ctx" == *"handoff save"* ]] || fail "long session should ask for handoff save: $ctx"
[[ "$ctx" == *"Claude and Codex both load"* ]] || fail "both tools should load the handoff: $ctx"
[[ "$ctx" != *"pbcopy"* ]] || fail "Codex must not require clipboard handoffs: $ctx"
{ usage_line 600000; usage_line 40000; } > "$SANDBOX/compacted.jsonl"
ctx=$(run_with_transcript "$SANDBOX/compacted.jsonl")
[[ "$ctx" != *"/clear"* ]] || fail "only the last message counts: $ctx"
ctx=$(run_with_transcript "$SANDBOX/missing.jsonl")
[[ "$ctx" != *"/clear"* ]] || fail "missing transcript should not suggest /clear: $ctx"
[[ "$ctx" == *'pane-label "'* ]] || fail "missing transcript should still remind about the label: $ctx"

# Session start: a pane with no name gets "새 세션 <folder>" at once, so it never shows a bare
# "Claude Code" title while waiting for the first message (요유 2026-10-17).
run_start() { # session-start mode; prints stdout, must exit 0
    echo "{\"hook_event_name\":\"SessionStart\",\"cwd\":\"/Users/yoyoo/work/gomgom\"}" |
        env -u HERDR_PANE_ID -u FAKE_LABEL -u FAKE_HERDR_FAIL HERDR_BIN_PATH="$SANDBOX/herdr" "$@" "$SUBJECT" session ||
        fail "session-start exited non-zero ($*)"
}
rm -f "$SANDBOX/rename.log"
out=$(run_start HERDR_PANE_ID=w1:p1)
[ -z "$out" ] || fail "session-start should print nothing, got: $out"
[ "$(cat "$SANDBOX/rename.log" 2>/dev/null)" = "🟢 새 세션 gomgom" ] || fail "new pane not named: $(cat "$SANDBOX/rename.log" 2>/dev/null)"

# A named pane (resume, /clear) keeps its name.
rm -f "$SANDBOX/rename.log"
run_start HERDR_PANE_ID=w1:p1 FAKE_LABEL="🔴 cf#584 계측" >/dev/null
[ ! -s "$SANDBOX/rename.log" ] || fail "named pane renamed: $(cat "$SANDBOX/rename.log")"

# The placeholder behind 🫡 or 👑 is still no name yet.
for l in "🟢 🫡 새 세션 munice" "🔴 👑 새 세션 munice"; do
    out=$(run_hook HERDR_PANE_ID=w1:p1 FAKE_LABEL="$l")
    [[ "$out" == *"no label yet"* ]] || fail "placeholder behind a role mark: $out"
done

# Outside herdr or herdr down: nothing, exit 0.
rm -f "$SANDBOX/rename.log"
run_start >/dev/null
run_start HERDR_PANE_ID=w1:p1 FAKE_HERDR_FAIL=1 >/dev/null
[ ! -s "$SANDBOX/rename.log" ] || fail "renamed outside herdr: $(cat "$SANDBOX/rename.log")"

echo "ok: pane-label-nudge"
