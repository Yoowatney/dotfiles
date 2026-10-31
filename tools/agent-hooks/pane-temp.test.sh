#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-temp"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr that logs each call; `pane run` really runs its command, as a pane's shell would.
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$SANDBOX/log"
case "\$1 \$2" in
  "pane get") jq -n '{result: {pane: {pane_id: "w7:p3", workspace_id: "w7"}}}' ;;
  "tab create") jq -n '{result: {root_pane: {pane_id: "w1:new"}, tab: {tab_id: "w1:t9"}}}' ;;
  "pane rename"|"pane close") ;;
  "pane run") HERDR_PANE_ID=w1:p1 bash -c "\$4" ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
# Stand-ins for the agents: one exits at once with its arguments logged, one waits to be ended like an idle session.
printf '#!/bin/sh\necho "claude-started $*" >> "%s/log"\n' "$SANDBOX" > "$SANDBOX/claude"
printf '#!/bin/sh\nexec sleep 30\n' > "$SANDBOX/codex"
chmod +x "$SANDBOX/claude" "$SANDBOX/codex"

pt() { rm -f "$SANDBOX/log"; env PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" "$@"; }
log() { cat "$SANDBOX/log" 2>/dev/null || true; }
has() { grep -qF -- "$1" "$SANDBOX/log" || fail "$2: no [$1] in: $(log)"; }
hasnt() { if grep -qF -- "$1" "$SANDBOX/log"; then fail "$2: found [$1] in: $(log)"; fi; }

out=$(pt bash "$SUBJECT" --cwd /work --env A=1 "손세션" -- --model "a b")
[ "$out" = "w1:new" ] || fail "prints the new pane id: $out"
has "tab create --workspace w7 --cwd /work --label 임시 손세션 --env A=1 --no-focus" "a tab in the caller's current workspace, labelled 임시"
has "pane rename w1:new 🟢 임시 손세션" "the pane carries the label the guard looks for"
has "claude-started --model a b" "the agent gets its arguments"
has "pane close w7:p3" "an agent that exits closes its pane, by its current id after a move"
hasnt "agent start" "no herdr name: that is the point of this pane"

SECONDS=0
pt env PANE_TEMP_TTL=1 bash "$SUBJECT" --kind codex "대기" >/dev/null
[ "$SECONDS" -lt 10 ] || fail "the time limit must end a waiting agent: ${SECONDS}s"
has "pane close w7:p3" "the time limit closes the pane too"

for bad in "--kind gemini 이름" "--env" ""; do
    code=0
    # shellcheck disable=SC2086
    pt bash "$SUBJECT" $bad 2>/dev/null || code=$?
    [ "$code" = 2 ] || fail "bad usage [$bad] exits 2: $code"
done
echo "pane-temp tests ok"
