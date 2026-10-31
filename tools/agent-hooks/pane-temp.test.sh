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
  "pane get")
    if [ "\$3" = w1:new ]; then jq -n '{result: {pane: {pane_id: "w1:new", terminal_id: "term_new"}}}'
    else jq -n '{result: {pane: {pane_id: "w7:p3", workspace_id: "w7", terminal_id: "term_me"}}}'; fi ;;
  "tab create") jq -n '{result: {root_pane: {pane_id: "w1:new"}, tab: {tab_id: "w1:t9"}}}' ;;
  "pane rename"|"pane close") ;;
  "pane run")
    [ -z "\${FAIL_RUN:-}" ] || exit 1
    HERDR_PANE_ID=w1:p1 bash -c "\$4" >/dev/null 2>&1 ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
# Stand-ins for the agents: one exits at once with its arguments logged, one outlives the time limit.
printf '#!/bin/sh\necho "claude-started $*" >> "%s/log"\n' "$SANDBOX" > "$SANDBOX/claude"
printf '#!/bin/sh\nsleep 3\necho codex-done >> "%s/log"\n' "$SANDBOX" > "$SANDBOX/codex"
chmod +x "$SANDBOX/claude" "$SANDBOX/codex"

pt() { rm -f "$SANDBOX/log"; env PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" PANE_TEMP_DIR="$SANDBOX/state" PANE_TEMP_RETRY_WAIT=0 "$@"; }
log() { cat "$SANDBOX/log" 2>/dev/null || true; }
has() { grep -qF -- "$1" "$SANDBOX/log" || fail "$2: no [$1] in: $(log)"; }
hasnt() { if grep -qF -- "$1" "$SANDBOX/log"; then fail "$2: found [$1] in: $(log)"; fi; }

out=$(pt env PANE_TEMP_TTL=1 bash "$SUBJECT" --cwd /work --env A=1 "손세션" -- --model "a b")
[ "$out" = "w1:new" ] || fail "prints the new pane id: $out"
has "tab create --workspace w7 --cwd /work --label 임시 손세션 --env A=1 --no-focus" "a tab in the caller's current workspace, labelled 임시"
has "pane rename w1:new 🟢 임시 손세션" "the pane carries the label the guard looks for"
has "claude-started --model a b" "the agent gets its arguments"
has "pane close w7:p3" "an agent that exits closes its pane, by its current id after a move"
hasnt "agent start" "no herdr name: that is the point of this pane"
[ "$(cat "$SANDBOX/state/term_new")" = term_me ] || fail "the opener is on record for yoyoo-share-guard"
sleep 1 # the time limit of the run above

cat > "$SANDBOX/echo-herdr" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "pane get") jq -n '{result: {pane: {pane_id: "w1:new", workspace_id: "w7", terminal_id: "term_x"}}}' ;;
  "tab create") jq -n '{result: {root_pane: {pane_id: "w1:new"}}}' ;;
  "pane run") echo "\$4" > "$SANDBOX/typed" ;;
esac
EOF
chmod +x "$SANDBOX/echo-herdr"
env HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/echo-herdr" PANE_TEMP_DIR="$SANDBOX/state" bash "$SUBJECT" "셸" >/dev/null
case "$(cat "$SANDBOX/typed")" in
  *"exec @ARGV"* | perl*) fail "the agent must start through the pane's shell, so its alias or function applies: $(cat "$SANDBOX/typed")" ;;
esac

pt env PANE_TEMP_TTL=1 bash "$SUBJECT" --kind codex "대기" >/dev/null
first=$(grep -E '^(pane close|codex-done)' "$SANDBOX/log" | head -1)
[[ "$first" == "pane close w7:p3" ]] || fail "the time limit closes the pane while the agent still runs: $(log)"

code=0
pt env FAIL_RUN=1 bash "$SUBJECT" "실패" >/dev/null 2>&1 || code=$?
[ "$code" = 1 ] || fail "a pane the agent never reached must not count as opened: exit $code"
has "pane close w1:new" "and it must not stay behind empty"

for bad in "--kind gemini 이름" "--env" "" "이름 --kind codex"; do
    code=0
    # shellcheck disable=SC2086
    pt bash "$SUBJECT" $bad 2>/dev/null || code=$?
    [ "$code" = 2 ] || fail "bad usage [$bad] exits 2: $code"
done
echo "pane-temp tests ok"
