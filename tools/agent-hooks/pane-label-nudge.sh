#!/usr/bin/env bash
# UserPromptSubmit hook (Claude Code and Codex): tells the agent what its herdr pane is
# named, so it renames the pane when the user moves the session to a new task.
# Why a hook: the rule alone was not followed, and a cleared or stale pane falls back to
# the session's first-topic title, which is how 요유 tells panes apart (2026-10-08).
# No-op outside herdr. Always exits 0: a reminder must never block a prompt.
cat >/dev/null 2>&1 # drain the hook input; nothing in it is needed

[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

label=$("${HERDR_BIN_PATH:-herdr}" pane get "$HERDR_PANE_ID" 2>/dev/null | jq -r '.result.pane.label // empty' 2>/dev/null)

# The mark tells 요유 whether closing this session stops something (요유 2026-10-17): see pane-label.
if [ -n "$label" ]; then
    msg="herdr pane label: \"$label\". If this message starts a different task or topic, rename the pane before other work: pane-label \"<issue> <2-4 words>\" (e.g. pane-label \"api#736 Dependabot 정리\"; the 🟢/🔴 mark is kept). Keep the label when a task ends."
    case "$label" in
        🔴*) msg="$msg 🔴 tells 요유 that closing this session stops something. Once nothing of yours still runs here (background command, Monitor, loop or wakeup, session cron, background agent), run pane-label --free." ;;
        *) msg="$msg The 🟢 mark tells 요유 this session may be closed. If you start something here that stops when it closes, run pane-label --hold (Claude's background tools set it automatically)." ;;
    esac
else
    msg="herdr pane label: no label (the pane shows the session's first-topic title). Name it for the current task before other work: pane-label \"<issue> <2-4 words>\" (e.g. pane-label \"api#736 Dependabot 정리\")."
fi

jq -n --arg ctx "$msg" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' 2>/dev/null
exit 0
