#!/usr/bin/env bash
# Desktop notification when any herdr agent stops working.
#
# Replaces herdr's own background notification, which cannot reach this machine:
# delivery="herdr" only paints inside the app, so it is invisible behind a
# browser; delivery="terminal" produces nothing through iTerm at all; and
# delivery="system" does fire but opens the wrong target on click, which is why
# notifications got switched off here in the first place. All three measured.
#
# terminal-notifier's -execute runs a command on click, so this one lands on the
# pane that finished instead of merely raising the app.
#
# ponytail: polls, rather than subscribing to the socket API's
# pane_agent_status_changed events. A tick is one `herdr pane list`, and polling
# has no daemon to supervise or reconnect. If latency ever matters, the event
# subscription is the upgrade path.
set -euo pipefail

STATE="${HERDR_NOTIFY_STATE:-/tmp/herdr-agent-notify.state}"
LOG="${HERDR_NOTIFY_LOG:-/tmp/herdr-agent-notify.log}"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

herdr pane list >/dev/null 2>&1 || exit 0   # herdr not running: nothing to watch

# pane list carries everything needed in one call: the agent, its status, the
# human-set pane label, and whether the user is already looking at it.
now=$(herdr pane list | jq -r '
  .result.panes[]
  | select(.agent != null)
  | [.pane_id, .agent_status, (.focused|tostring),
     (.label // .terminal_title_stripped // .agent)]
  | @tsv')

# First run records the world without shouting about it.
if [[ ! -f "$STATE" ]]; then
  printf '%s\n' "$now" | cut -f1,2 >"$STATE"
  log "first run — baseline recorded ($(wc -l <"$STATE" | tr -d ' ') agents)"
  exit 0
fi

while IFS=$'\t' read -r pane status focused name; do
  [[ -n "$pane" ]] || continue
  was=$(awk -F'\t' -v p="$pane" '$1 == p { print $2 }' "$STATE")

  # Only the moment work ends. "blocked" counts: that is an agent asking for a
  # human, which is the case most worth interrupting for.
  case "$was:$status" in
    working:idle | working:blocked | working:done) ;;
    *) continue ;;
  esac

  # Skip the pane already on screen — herdr's own sound setting draws the same
  # line ("background workspaces"), and notifying about what you are watching is
  # noise.
  # Braces around ${was}/${status} below are load-bearing: macOS bash folds the
  # following multibyte arrow into the variable name, so "$was→" dies as an
  # unbound variable under set -u.
  if [[ "$focused" == "true" ]]; then
    log "$pane ($name) ${was}→${status} — focused, no notification"
    continue
  fi

  log "$pane ($name) ${was}→${status} — notifying"
  terminal-notifier \
    -title "$name" \
    -message "$([[ "$status" == "blocked" ]] && echo "입력을 기다립니다" || echo "작업이 끝났습니다")" \
    -execute "$(command -v herdr) agent focus $pane; open -a iTerm" \
    -sound Glass >/dev/null 2>&1 || log "  terminal-notifier 실패"
done <<<"$now"

printf '%s\n' "$now" | cut -f1,2 >"$STATE"
