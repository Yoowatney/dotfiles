#!/usr/bin/env bash
# Go to the agent whose notification just fired — herdr's prefix+o, rebuilt.
#
# The built-in open_notification_target only resolves against an in-app toast, so
# it dies the moment notifications are delivered outside herdr, and upstream
# closed that as intended (herdrdev/herdr#2684). The destination is recoverable
# anyway: `herdr agent list` exposes state_change_seq, a counter that rises on
# every status change, so the agent that most recently stopped working is simply
# the highest one that is no longer working.
#
# ponytail: recomputed on each press rather than tracked in a daemon. The server
# already keeps the ordering, so there is nothing here to keep in sync.
#
#   --dry   print the target instead of jumping to it
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin"

# Only settled agents: a notification fires when work ENDS, and a pane that went
# back to working since then has a higher counter without having notified.
target=$(herdr agent list | jq -r '
  .result.agents
  | map(select(.agent_status == "idle" or .agent_status == "blocked"
               or .agent_status == "done"))
  | sort_by(-.state_change_seq)
  | .[0] // empty
  | "\(.pane_id)\t\(.agent_status)\t\(.state_change_seq)\t\(.terminal_title_stripped // .agent)"')

if [[ -z "$target" ]]; then
  [[ "${1:-}" == "--dry" ]] && echo "멈춰 있는 에이전트가 없습니다"
  exit 0
fi

IFS=$'\t' read -r pane status seq name <<<"$target"

if [[ "${1:-}" == "--dry" ]]; then
  printf '→ %s  (%s, seq=%s)  %s\n' "$pane" "$status" "$seq" "$name"
  exit 0
fi

herdr agent focus "$pane" >/dev/null 2>&1
