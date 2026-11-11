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
event=UserPromptSubmit
[ "${1:-}" != session ] || event=SessionStart
emit() {
    printf '%s' "$1" | jq -Rs --arg event "$event" '{hookSpecificOutput: {hookEventName: $event, additionalContext: .}}' 2>/dev/null
    exit 0
}
correction= machine=
if [ "${1:-}" != session ]; then
    correction=$(printf '%s' "$input" | python3 -I "$(dirname "$0")/correction-nudge.py" 2>/dev/null)
    [ $? != 3 ] || machine=1
fi
if [ -z "${HERDR_PANE_ID:-}" ]; then
    [ -z "$correction" ] || emit "$correction"
    exit 0
fi

# A failed query must not authorize a claim, even if it prints an old object.
# The shared helper bounds this lookup and rejects nonzero/malformed responses.
pane=$(python3 -I -c 'import importlib.util,json,os,sys; s=importlib.util.spec_from_file_location("peers",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps({"result":m.herdr(os.environ,"pane","get",os.environ["HERDR_PANE_ID"]),"main_terminal":m.main_terminal(os.environ)}))' "$(dirname "$0")/herdr_peers.py" 2>/dev/null)
label=$(printf '%s' "$pane" | jq -r '.result.pane.label // empty' 2>/dev/null)
term=$(printf '%s' "$pane" | jq -r '.result.pane.terminal_id | select(type == "string") // empty' 2>/dev/null)
main=$(printf '%s' "$pane" | jq -r '.main_terminal | select(type == "string") // empty' 2>/dev/null)
session=$(printf '%s' "$input" | jq -r '.session_id | select(type == "string") // empty' 2>/dev/null)
inbox= priority=
pane_session=$(printf '%s' "$pane" | jq -r '.result.pane.agent_session.value | select(type == "string") // empty' 2>/dev/null)
if [ -n "$term" ] && [ -n "$session" ] && [ "$session" = "$pane_session" ] && [ "$term" = "$main" ]; then
    priority=$(python3 -I "$(dirname "$0")/main-user-relay.py" drain --terminal "$term" --session "$session" --hook-event "$event" 2>/dev/null) || priority=
    if offered=$(python3 -I "$(dirname "$0")/main-alert-inbox.py" drain --terminal "$term" --session "$session" --hook-event "$event" 2>/dev/null); then
        inbox=$(printf '%s' "$offered" | jq -r --arg event "$event" 'select(.hookSpecificOutput.hookEventName == $event) | .hookSpecificOutput.additionalContext | select(type == "string")' 2>/dev/null)
    fi
fi

if [ "${1:-}" = session ]; then
    # a named pane (resume, /clear) keeps its name
    if [ -z "$label" ]; then
        dir=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
        dir=${dir:-$PWD}
        bash "$(dirname "$0")/pane-label" "$PLACEHOLDER ${dir##*/}"
    fi
    context="${priority}${priority:+

}${inbox}"
    [ -z "$context" ] || emit "$context"
    exit 0
fi

# A new message from 요유: the next working tool asks whether this work belongs in a pane (pane-fit-guard.py).
# Never in a pane that hands lookups and checks out (the main pane today; the one gate is in herdr_peers.py): there each refusal was
# one more model call over the whole long session, 240 on 2026-11-05, so 요유 had it taken out. The main-pane line below says it instead.
delegates=
if [ -n "$term" ] && python3 -I "$(dirname "$0")/herdr_peers.py" --delegates-checks "$term" 2>/dev/null; then delegates=1; fi
if [ -n "$session" ]; then
    armed=${PANE_FIT_DIR:-$HOME/.local/state/pane-fit}
    fit="$armed/${session//[^A-Za-z0-9_-]/_}"
    if [ -n "$delegates" ]; then
        rm -f "$fit"
    elif [ -z "$machine" ]; then
        mkdir -p "$armed" && { : >"$fit" && touch "$fit"; } 2>/dev/null
        find "$armed" -type f -mtime +7 -delete 2>/dev/null
    fi
fi

name=${label#🔴}; name=${name#🟢}; name=${name# }; name=${name#👑}; name=${name#🫡}
case "${name# }" in "$PLACEHOLDER"*) label= ;; esac # the session-start name is no name yet

# The mark tells 요유 whether closing this session stops something (요유 2026-10-17): see pane-label.
if [ -n "$term" ] && [ "$term" = "$main" ]; then
    msg="This pane is 요유's main pane. Keep its name; never rename it for a task. Follow ~/.claude/skills/main-orchestrator/SKILL.md: hand work to panes with pane-spawn, lookups beyond a one-line answer too, have a fresh check pane (pane-spawn --check) check a pane's report, triage what comes back three ways, and end each answer with the collected decisions. What stays here: a one-line answer, a step that needs 요유's \`prod 허용\` or \`staging 허용\`, reading a short report, and chores (pane-spawn, pane-tell, the board); decide that before the first tool call of each message."
elif [ -n "$label" ]; then
    msg="herdr pane label: \"$label\". If this message starts a different task or topic, rename the pane before other work: pane-label \"<issue> <2-4 words>\" (e.g. pane-label \"api#736 Dependabot 정리\"; the 🟢/🔴 mark is kept). Keep the label when a task ends."
    case "$label" in
        🔴*) msg="$msg 🔴 tells 요유 that closing this session stops something. Once nothing of yours still runs here (background command, Monitor, loop or wakeup, session cron, background agent), run pane-label --free." ;;
        *) msg="$msg The 🟢 mark tells 요유 this session may be closed. If you start something here that stops when it closes, run pane-label --hold (Claude's background tools set it automatically)." ;;
    esac
else
    msg="herdr pane label: no label yet (the pane shows \"$PLACEHOLDER\" or the session's first-topic title). Name it for the current task before other work: pane-label \"<issue> <2-4 words>\" (e.g. pane-label \"api#736 Dependabot 정리\")."
fi

# Prompt hooks also reach sessions that were open before the handoff update.
if [ -z "$term" ] || [ "$term" != "$main" ]; then
    msg="$msg Before saving an ordinary-session handoff, read ~/.dotfiles/tools/agent-hooks/handoff-format.md; handoff save validates its required fields. Both Claude and Codex load saved handoffs."
fi

# Every turn re-reads the whole session, so a long one eats the 5-hour and 7-day limits; a new task
# belongs in a cleared session (요유 2026-10-17). Size = the last assistant message's usage.
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)
if [ -n "$transcript" ] && [ -r "$transcript" ]; then
    tokens=$(tail -n 200 "$transcript" | jq -R -r 'fromjson? | select(.type == "assistant") | .message.usage // empty
        | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)' 2>/dev/null | tail -n 1)
    if [ "${tokens:-0}" -gt 300000 ] 2>/dev/null; then
        msg="$msg Context is about $((tokens / 1000))k tokens. If this message starts a different task, first leave what the next session needs (board comment, work-log or memory), then save a handoff following ~/.dotfiles/tools/agent-hooks/handoff-format.md with \`handoff save\`. Claude and Codex both load it in the next session in this pane."
    fi
fi

# What a Stop hook left for this message instead of sending the reply back (yoyoo-share-guard.py note_next: the main pane today).
# ponytail: a note whose session ends unread stays, a few hundred bytes; prune here if that folder ever grows.
note="$HOME/.local/state/next-prompt/${session//[^A-Za-z0-9_-]/_}"
if [ -n "$session" ] && [ -s "$note" ]; then
    msg="$msg $(tr '\n' ' ' <"$note")"
    rm -f "$note"
fi

emit "${priority}${priority:+

}$msg${correction:+ $correction}${inbox:+ $inbox}"
