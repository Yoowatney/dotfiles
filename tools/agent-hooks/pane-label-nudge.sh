#!/usr/bin/env bash
# UserPromptSubmit hook (Claude Code and Codex): tells the agent what its herdr pane is
# named, so it renames the pane when the user moves the session to a new task.
# Why a hook: the rule alone was not followed, and a cleared or stale pane falls back to
# the session's first-topic title, which is how 요유 tells panes apart (2026-10-08).
# `pane-label-nudge.sh session` is the SessionStart side: a pane with no name gets "새 세션 <folder>",
# so a new session never sits as a bare "Claude Code" title until its first message (요유 2026-10-17).
# Both tools already run this on every message, so what is added here reaches open sessions at once and needs no new Codex
# hook trust: correction-nudge.py (the only part that also runs outside herdr) and the arming of pane-fit-guard.py.
# Otherwise a no-op outside herdr. Always exits 0: a reminder must never block a prompt.
input=$(cat 2>/dev/null)
PLACEHOLDER="새 세션"

command -v jq >/dev/null 2>&1 || exit 0
emit() {
    jq -n --arg ctx "$1" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' 2>/dev/null
    exit 0
}
correction= machine=
if [ "${1:-}" != session ]; then
    correction=$(python3 -I "$(dirname "$0")/correction-nudge.py" <<<"$input" 2>/dev/null)
    [ $? != 3 ] || machine=1
fi
if [ -z "${HERDR_PANE_ID:-}" ]; then
    [ -z "$correction" ] || emit "$correction"
    exit 0
fi

label=$("${HERDR_BIN_PATH:-herdr}" pane get "$HERDR_PANE_ID" 2>/dev/null | jq -r '.result.pane.label // empty' 2>/dev/null)

if [ "${1:-}" = session ]; then
    # a named pane (resume, /clear) keeps its name
    if [ -z "$label" ]; then
        dir=$(jq -r '.cwd // empty' <<<"$input" 2>/dev/null)
        dir=${dir:-$PWD}
        bash "$(dirname "$0")/pane-label" "$PLACEHOLDER ${dir##*/}"
    fi
    exit 0
fi

# A new message from 요유: the next working tool asks whether this work belongs in a pane (pane-fit-guard.py).
session=$(jq -r '.session_id // empty' <<<"$input" 2>/dev/null)
if [ -n "$session" ] && [ -z "$machine" ]; then
    armed=${PANE_FIT_DIR:-$HOME/.local/state/pane-fit}
    mkdir -p "$armed" && touch "$armed/${session//[^A-Za-z0-9_-]/_}" 2>/dev/null
    find "$armed" -type f -mtime +7 -delete 2>/dev/null
fi

name=${label#🔴}; name=${name#🟢}; name=${name# }; name=${name#👑}; name=${name#🫡}
case "${name# }" in "$PLACEHOLDER"*) label= ;; esac # the session-start name is no name yet

# The mark tells 요유 whether closing this session stops something (요유 2026-10-17): see pane-label.
if [ -n "$label" ]; then
    msg="herdr pane label: \"$label\". If this message starts a different task or topic, rename the pane before other work: pane-label \"<issue> <2-4 words>\" (e.g. pane-label \"api#736 Dependabot 정리\"; the 🟢/🔴 mark is kept). Keep the label when a task ends."
    case "$label" in
        🔴*) msg="$msg 🔴 tells 요유 that closing this session stops something. Once nothing of yours still runs here (background command, Monitor, loop or wakeup, session cron, background agent), run pane-label --free." ;;
        *) msg="$msg The 🟢 mark tells 요유 this session may be closed. If you start something here that stops when it closes, run pane-label --hold (Claude's background tools set it automatically)." ;;
    esac
else
    msg="herdr pane label: no label yet (the pane shows \"$PLACEHOLDER\" or the session's first-topic title). Name it for the current task before other work: pane-label \"<issue> <2-4 words>\" (e.g. pane-label \"api#736 Dependabot 정리\")."
fi

# Every turn re-reads the whole session, so a long one eats the 5-hour and 7-day limits; a new task
# belongs in a cleared session (요유 2026-10-17). Size = the last assistant message's usage.
transcript=$(jq -r '.transcript_path // empty' <<<"$input" 2>/dev/null)
if [ -n "$transcript" ] && [ -r "$transcript" ]; then
    tokens=$(tail -n 200 "$transcript" | jq -R -r 'fromjson? | select(.type == "assistant") | .message.usage // empty
        | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)' 2>/dev/null | tail -n 1)
    if [ "${tokens:-0}" -gt 300000 ] 2>/dev/null; then
        msg="$msg Context is about $((tokens / 1000))k tokens. If this message starts a different task, first leave what the next session needs (board comment, work-log or memory), then pipe a short handoff prompt (Korean, absolute paths, board link) into \`handoff save\` and suggest in one line that 요유 run /clear here: the next session in this pane loads it, so 요유 pastes nothing (Codex does not load it yet: pbcopy the prompt and show it instead)."
    fi
fi

emit "$msg${correction:+ $correction}"
