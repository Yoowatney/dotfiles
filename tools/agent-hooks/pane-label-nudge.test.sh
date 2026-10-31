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
    jq -n --arg l "$FAKE_LABEL" '{result: {pane: {pane_id: "w1:p1", label: $l}}}'
else
    echo '{"result":{"pane":{"pane_id":"w1:p1"}}}'
fi
EOF
chmod +x "$SANDBOX/herdr"

run_hook() { # prints the hook's stdout; the hook must always exit 0
    echo '{"hook_event_name":"UserPromptSubmit","prompt":"hi"}' |
        env -u HERDR_PANE_ID -u FAKE_LABEL -u FAKE_HERDR_FAIL HERDR_BIN_PATH="$SANDBOX/herdr" "$@" "$SUBJECT" ||
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
[[ "$ctx" == *"/clear"* ]] || fail "long session should suggest /clear: $ctx"
[[ "$ctx" == *"341k"* ]] || fail "long session should say its size: $ctx"
# A /clear suggestion saves the handoff for the next session, which loads it itself (요유 2026-10-21);
# the clipboard round trip made 요유 the bottleneck.
[[ "$ctx" == *"handoff save"* ]] || fail "long session should ask for handoff save: $ctx"
# Codex has no load hook (its hook trust prompt is 요유's call), so Codex keeps the clipboard.
[[ "$ctx" == *"Codex"*"pbcopy"* ]] || fail "Codex should keep the pbcopy handoff: $ctx"
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
