#!/usr/bin/env bash
# PostToolUse hook (Claude Code): marks the herdr pane 🔴 the moment this session starts something that stops
# when the session closes, so 요유 does not close it by mistake (요유 2026-10-17): a background Bash command, a
# Monitor, a scheduled wakeup or /loop, a session cron, a background agent or workflow.
# Going back to 🟢 is the agent's `pane-label --free`, reminded on every prompt by pane-label-nudge.sh.
# The split is deliberate: a 🔴 left on only keeps a session open longer; a 🟢 left on lets watched work stop unseen.
# No-op outside herdr. Always exits 0 and prints nothing.
input=$(cat 2>/dev/null)
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
hold=$(jq -r '
  if .tool_name == "Bash" then (.tool_input.run_in_background == true)
  elif .tool_name == "ScheduleWakeup" then (.tool_input.stop != true)
  else (.tool_name as $t | ["Monitor", "CronCreate", "Agent", "Workflow"] | index($t) != null) end' <<<"$input" 2>/dev/null)
[ "$hold" = true ] && "${PANE_LABEL_BIN:-pane-label}" --hold >/dev/null 2>&1
exit 0
