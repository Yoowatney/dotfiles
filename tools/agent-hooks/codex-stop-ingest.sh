#!/usr/bin/env bash
set -uo pipefail

# Opt-in per-session validation guard. This never affects pane startup.
model_free_id="${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}"
if [[ -n "$model_free_id" && "$model_free_id" != *[!a-zA-Z0-9_-]* &&
      -f "$HOME/.local/state/agent-model-free/$model_free_id" ]]; then
    exit 0
fi

HOOKS_ROOT="${AGENT_HOOKS_ROOT:-$HOME/.dotfiles/tools/agent-hooks}"
payload="$(cat)"

printf '%s' "$payload" \
    | "$HOOKS_ROOT/codex-work-log-guard.sh" finish >/dev/null 2>&1 || true
"$HOOKS_ROOT/run-llm-wiki-ingest.sh" >/dev/null 2>&1 || true

exit 0
