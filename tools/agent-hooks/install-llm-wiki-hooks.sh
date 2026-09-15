#!/usr/bin/env bash
set -euo pipefail

ROOT="${AGENT_HOOKS_ROOT:-$HOME/.dotfiles/tools/agent-hooks}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_HOOKS_JSON="$CODEX_HOME/hooks.json"

install_link() {
    local target="$1"
    local destination="$2"
    local backup="${destination}.pre-agent-hooks"

    mkdir -p "$(dirname "$destination")"
    if [[ -e "$destination" && ! -L "$destination" ]]; then
        if [[ -e "$backup" ]]; then
            echo "Refusing to replace $destination because backup already exists: $backup" >&2
            return 1
        fi
        mv "$destination" "$backup"
    fi
    ln -sfn "$target" "$destination"
}

for required_script in \
    "$ROOT/run-llm-wiki-ingest.sh" \
    "$ROOT/codex-stop-ingest.sh" \
    "$ROOT/codex-work-log-guard.sh"; do
    if [[ ! -x "$required_script" ]]; then
        echo "Required hook script is not executable: $required_script" >&2
        exit 1
    fi
done

install_link "$ROOT/run-llm-wiki-ingest.sh" "$CLAUDE_HOME/hooks/stop-ingest.sh"
install_link "$ROOT/codex-stop-ingest.sh" "$CODEX_HOME/hooks/stop-ingest.sh"
install_link "$ROOT/codex-work-log-guard.sh" "$CODEX_HOME/hooks/work-log-guard.sh"

mkdir -p "$CODEX_HOME"
if [[ ! -f "$CODEX_HOOKS_JSON" ]]; then
    printf '{"hooks":{}}\n' > "$CODEX_HOOKS_JSON"
    chmod 600 "$CODEX_HOOKS_JSON"
fi

hooks_mode="$(stat -f %Lp "$CODEX_HOOKS_JSON")"
hooks_tmp="$(mktemp "${CODEX_HOOKS_JSON}.tmp.XXXXXX")"
guard_command="'$CODEX_HOME/hooks/work-log-guard.sh' begin"

if jq --arg command "$guard_command" '
    .hooks //= {} |
    .hooks.UserPromptSubmit //= [] |
    if any(.hooks.UserPromptSubmit[]?;
        any(.hooks[]?; ((.command // "") | contains("work-log-guard.sh"))))
    then .
    else .hooks.UserPromptSubmit += [{
        "hooks": [{
            "type": "command",
            "command": $command,
            "timeout": 10
        }]
    }]
    end
' "$CODEX_HOOKS_JSON" > "$hooks_tmp" && jq empty "$hooks_tmp"; then
    chmod "$hooks_mode" "$hooks_tmp"
    mv "$hooks_tmp" "$CODEX_HOOKS_JSON"
else
    rm -f "$hooks_tmp"
    echo "Failed to update Codex hooks JSON: $CODEX_HOOKS_JSON" >&2
    exit 1
fi

echo "Installed Claude Code and Codex internal wiki ingest hooks."
