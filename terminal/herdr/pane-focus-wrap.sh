#!/usr/bin/env bash
# Move focus left/right, wrapping at the edge — the one combination herdr does
# not ship. focus_pane_* stops dead at the last pane, cycle_pane_* wraps but
# walks creation order, so in a 2x2 grid it visits 1 → 3 → 2 → 4 instead of
# going sideways. This is directional, and only falls back to wrapping.
#
# The wrap needs no geometry of its own: having failed to move right, walking
# left until that fails too lands on the leftmost pane of the same row, which is
# where wrapping should put you. herdr reports the edge as
# result.focus.changed = false with reason "no_neighbor".
#
# ponytail: one socket call per step (~6ms), so a four-column wrap costs ~25ms.
# Not worth precomputing from the layout rects to save a round trip.
set -euo pipefail

dir="${1:?usage: pane-focus-wrap.sh left|right}"
case "$dir" in
  left) opp=right ;;
  right) opp=left ;;
  *) echo "usage: ${0##*/} left|right" >&2; exit 2 ;;
esac

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin"

moved() { herdr pane focus --direction "$1" 2>/dev/null | jq -r '.result.focus.changed // false'; }

# The ordinary case: there is a pane that way.
[[ "$(moved "$dir")" == "true" ]] && exit 0

# At the edge — run to the far side instead. The bound is a runaway guard, not a
# real limit; each step must change focus or the loop ends on its own.
for _ in $(seq 1 32); do
  [[ "$(moved "$opp")" == "true" ]] || break
done
