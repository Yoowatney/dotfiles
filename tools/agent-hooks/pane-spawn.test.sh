#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

# Default: the caller's tab, split to its right; the caller is marked 👑, the new pane 🫡 before its agent starts.
out=$(ps --cwd /work "api#1 설계" "할 일입니다")
has "pane-label --lead" "caller becomes the lead"
has "pane split --pane w7:p3 --direction right --cwd /work --no-focus" "a lead alone in its tab keeps the left, the pane goes right"
has "agent start spawn-65f21913323dc21-" "the agent's name records the caller's terminal, within herdr's limit"
hasnt "tab create" "no new tab by default"
has "pane rename w1:new 🟢 🫡 api#1 설계" "the first name carries 🫡"
before "pane rename w1:new" "agent start"
before "pane rename w1:new" "pane-label --sync-tabs"
before "pane-label --sync-tabs" "agent start"
has "--kind claude --pane w1:new" "claude by default"
has "--env CLAUDE_STARTUP_ROLE=worker" "Claude work panes keep their startup profile through clear"
hasnt "CLAUDE_STARTUP_ARTIFACT" "a Claude pane starts without the Artifact tool unless it asks (api#1065)"

has "agent prompt w1:new 할 일입니다" "instruction delivered"
has 'Current KST time: 2026-11-12 01:09:00 KST' "Claude workers receive the current KST date and time"
has 'first response line must be `끝날 예상: HH:MM KST`' "workers estimate their own completion at startup"
has 'pane-tell default/w7:p3' "the startup estimate goes to the resolved caller"
has 'PANE_TELL_BATCH=0 pane-tell' "the initial estimate does not wait for idle relay batching"
has 'upper bounds, never reasons to wait' "an assigned deadline cannot delay completed work"
has "For investigations and comparisons" "investigations get a default outcome contract"
has "original question" "the user's question survives delegation"
has "추천:" "the investigator must recommend an option"
has "다음 행동:" "the investigator must name the next action"
has "execute available read-only checks" "unknowns require available verification"
has "For implementation-only or routine status tasks" "routine tasks do not need invented recommendations"
has "요유는 이 칸을 보지 않으니" "a new pane learns its reports go to the lead"
has "Before reporting that the user should check something, first determine whether ego Lite or an API can inspect it; if available, check it yourself." "a new pane checks accessible evidence before asking the user"
before "agent get w1:new" "agent prompt"
before "agent prompt" "agent wait w1:new --until working --until blocked --until done"
[[ "$out" == *"w1:new"* ]] || fail "prints the new pane: $out"

# 요유 2026-11-01: a job has one lead. A pane under a lead that opens a review pane stays under it, and that pane reports to it.
CALLER_LABEL="🟢 🫡 구현" ps --same-tab "리뷰" "봐 줘" >/dev/null
hasnt "pane-label --lead" "a pane under a lead does not become one"
has "agent start spawn-65f21913323dc21-" "the new pane still reports to the pane that opened it"
CALLER_LABEL="🟢 👑 리드" ps --same-tab "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a lead stays one"
CALLER_LABEL="🟢 일" ps "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a pane under nobody becomes the lead"

# 요유 2026-11-04: a lead and its panes share one tab, the lead on the whole left, its panes stacked on the right.
layout "w7:p3 0 0 46" "w7:p4 81 0 46"
ps "구현" "지시" >/dev/null
has "pane split --pane w7:p4 --direction down" "the next pane stacks in the right column"
hasnt "tab create" "still no new tab"
layout "w7:p3 0 0 46" "w7:p4 81 0 23" "w7:p5 81 23 11" "w7:p6 81 34 12"
ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p4 --direction down" "the tallest pane takes it, so heights stay close"
layout "w7:p3 0 0 46" "w7:p4 81 0 23" "w7:p5 81 23 23"
ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p5 --direction down" "a tie goes to the lowest"
layout "w7:p1 0 0 46" "w7:p3 81 0 46"
CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p3 --direction down" "a pane under the lead stacks in the column, not beside itself"
layout "w7:p3 0 0 46" "w7:p4 81 0 11" "w7:p5 81 11 11" "w7:p6 81 22 12" "w7:p7 81 34 12"
ps "다섯째" "지시" >/dev/null
has "tab create --workspace w7 --cwd" "a column too short to read: the work gets its own tab"
has "--label 다섯째 --no-focus" "named for the work"
hasnt "pane split" "no pane shorter than can be read"

# A job the lead does not follow asks for its own tab, and a split or layout that fails still opens the pane.
layout "w7:p3 0 0 46"
ps --new-tab --cwd /work "api#1 설계" "지시" >/dev/null
has "tab create --workspace w7 --cwd /work --label api#1 설계 --no-focus" "--new-tab in the caller's current workspace"
hasnt "pane split" "--new-tab splits nothing"
hasnt "pane layout" "--new-tab reads no layout"
FAIL_SPLIT=1 ps "실패" "지시" >/dev/null
has "tab create --workspace w7" "a failed split falls back to a tab"
has "agent prompt w1:new 지시" "and the work still goes out"
FAIL_LAYOUT=1 ps "실패" "지시" >/dev/null
has "tab create --workspace w7" "so does a failed layout read"

# --same-tab was the way to ask for this before it became the default: sessions already running may still pass it.
ps --same-tab "api#1 리뷰" "리뷰" >/dev/null
has "pane split --pane w7:p3 --direction right" "--same-tab still works"
hasnt "tab create" "--same-tab opens no tab"

# Agent arguments pass through, and Codex works without a session id until its first prompt.
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -m gpt-5 >/dev/null
has 'first response line must be `끝날 예상: HH:MM KST`' "Codex estimates at startup too"
has 'Current KST time: 2026-11-12 01:09:00 KST' "Codex workers receive the current KST date and time"
has 'pane-tell default/w7:p3' "Codex relays to its caller too"
has "Before reporting that the user should check something, first determine whether ego Lite or an API can inspect it; if available, check it yourself." "Codex receives the same self-verification instruction"
# 2026-11-06: once Codex sees a newer release than it runs, its TUI opens on an update prompt that nobody here answers, so
# every Codex pane failed to start until 0.167.0 was installed (mise installs a release a day after it comes out).
NOUPDATE="-c check_for_update_on_startup=false"
# 요유 2026-11-07: a Codex work pane runs gcloud, bq, network and process commands as a Claude pane does. Its workspace-write
# sandbox let it write only its folder, so gcloud failed as it rewrote its login files and ps saw no other process. Like a
# Claude pane it runs with no sandbox, guarded by the same PreToolUse hooks, and nobody answers an approval prompt.
UNBOXED="--sandbox danger-full-access --ask-for-approval never"
has "--kind codex --pane w1:new --timeout 120000 -- $UNBOXED $NOUPDATE -m gpt-5" "a codex work pane runs unsandboxed, skips its update prompt, then its arguments"
# Codex refuses an option given twice (`cannot be used multiple times`, Codex 0.167), so the caller's own choice wins.
for own in "--sandbox read-only" "--sandbox=read-only" "-s read-only" "-sread-only" "--full-auto" "--yolo" "--dangerously-bypass-approvals-and-sandbox"; do
  # shellcheck disable=SC2086
  SESSIONLESS=1 ps --kind codex "조사" "지시" -- $own >/dev/null
  hasnt "danger-full-access" "the caller's sandbox wins ($own)"
done
for own in "--ask-for-approval on-request" "--ask-for-approval=untrusted" "-a on-request" "-aon-request" "--full-auto" "--yolo" "--dangerously-bypass-approvals-and-sandbox"; do
  # shellcheck disable=SC2086
  SESSIONLESS=1 ps --kind codex "조사" "지시" -- $own >/dev/null
  hasnt "--ask-for-approval never" "the caller's approval policy wins ($own)"
done
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -s read-only >/dev/null
has "--ask-for-approval never -c check_for_update_on_startup=false -s read-only" "an approval the caller left out is still never"
# On the 집맥 (host-role ambient-runner) the global config is the 회사맥's copy, and its guard (host_guard.py) refuses only
# the Write and Edit tools: a Codex shell write or apply_patch there went through once the sandbox was gone (check pane,
# 2026-11-08). So a Codex work pane there keeps workspace-write, with gcloud's folder added and the network on.
mkdir -p "$SANDBOX/home/.config/munice"; echo ambient-runner > "$SANDBOX/home/.config/munice/host-role"
SESSIONLESS=1 ps --kind codex --cwd "$SANDBOX" "조사" "지시" >/dev/null # no repo: no git folder to add
has "-- --sandbox workspace-write --add-dir $SANDBOX/home/.config/gcloud -c sandbox_workspace_write.network_access=true --ask-for-approval never $NOUPDATE" "a 집맥 Codex work pane keeps its sandbox, with gcloud and the network"
hasnt "danger-full-access" "no 집맥 Codex work pane runs unsandboxed"
# Codex keeps .git read-only inside the folder, so a commit needs the repo's git folders (a worktree's own one too).
git init -q "$SANDBOX/repo" && git -C "$SANDBOX/repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m i
git -C "$SANDBOX/repo" worktree add -q -b feat/x "$SANDBOX/wt"
REAL=$(cd "$SANDBOX/repo" && pwd -P)
SESSIONLESS=1 ps --kind codex --cwd "$SANDBOX/wt" "조사" "지시" >/dev/null
has "--add-dir $SANDBOX/home/.config/gcloud --add-dir $REAL/.git --add-dir $REAL/.git/worktrees/wt -c sandbox_workspace_write.network_access=true" "a 집맥 worktree pane may write its git folders"
SESSIONLESS=1 ps --kind codex --cwd "$SANDBOX/repo" "조사" "지시" >/dev/null
has "--add-dir $SANDBOX/home/.config/gcloud --add-dir $REAL/.git -c sandbox_workspace_write.network_access=true" "a 집맥 checkout pane gets its .git once"
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -s read-only >/dev/null
hasnt "--add-dir" "the caller's sandbox still wins on the 집맥"
echo other > "$SANDBOX/home/.config/munice/host-role"
SESSIONLESS=1 ps --kind codex "조사" "지시" >/dev/null
has "-- $UNBOXED $NOUPDATE" "another role is no 집맥"
rm "$SANDBOX/home/.config/munice/host-role"
# Only an option counts, not its name inside another argument's value (Codex review).
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -c 'developer_instructions="Explain how --full-auto and -s work."' >/dev/null
has "-- $UNBOXED $NOUPDATE -c developer_instructions=" "an option named inside a value leaves the defaults in"
has "agent prompt w1:new 지시" "codex gets the instruction"
has "For investigations and comparisons" "Codex investigators get the same outcome contract"
has "execute available read-only checks" "Codex must investigate material unknowns within scope"
hasnt "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" "a codex pane keeps its environment"

# A Claude pane opened here runs without the advisor tool: the pane's shell gets the variable, so the agent and its subagents do.
ps "조사" "지시" >/dev/null
has "pane split --pane w7:p3 --direction right --cwd $PWD --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" "a split pane has no advisor"
hasnt "--model" "an ordinary pane keeps the caller's model"
ps --new-tab "조사" "지시" >/dev/null
has "tab create --workspace w7 --cwd $PWD --label 조사 --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" "a pane in a new tab has none either"

# 2026-11-05: a runaway in one pane filled the user's process limit (8000) and froze every pane and launchd job. Every pane
# opened here, Claude or Codex, starts its shell with a lower limit, which the agent and all it runs inherit.
NPROC="--env PANE_SPAWN_NPROC=5000"
has "--label 조사 --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 --env CLAUDE_STARTUP_ROLE=worker $NPROC" "a Claude pane in a new tab: the lower limit"
ps "조사" "지시" >/dev/null
has "--direction right --cwd $PWD --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 --env CLAUDE_STARTUP_ROLE=worker $NPROC" "a split Claude pane: the lower limit"
SESSIONLESS=1 ps --kind codex "조사" "지시" >/dev/null
has "pane split --pane w7:p3 --direction right --cwd $PWD --no-focus --env MISE_" "a Codex pane"
has "$NPROC" "a Codex pane: the lower limit"
# The pane's shell is what lowers it: zshrc reads the variable. Checked on a real zsh with this repo's zshrc.
mkdir -p "$SANDBOX/zdot" && ln -s "$(cd "$(dirname "$SUBJECT")/../../shell/zsh" && pwd)/zshrc" "$SANDBOX/zdot/.zshrc"
# Hosted runners can have a hard limit below 4321. Stay strictly below it so
# the assertion still fails when zshrc does not apply the requested soft cap.
nproc=4321
hard_nproc=$(zsh -fc 'ulimit -Hu')
if [[ "$hard_nproc" =~ ^[0-9]+$ ]] && (( hard_nproc <= nproc )); then
  nproc=$((hard_nproc - 1))
fi
(( nproc > 0 )) || fail "the process hard limit is too small for the shell probe"
got=$(PANE_SPAWN_NPROC="$nproc" ZDOTDIR="$SANDBOX/zdot" zsh -i -c 'ulimit -Su' 2>/dev/null </dev/null | tail -1)
[ "$got" = "$nproc" ] || fail "a pane's shell lowers its process limit to PANE_SPAWN_NPROC: expected [$nproc], got [$got]"

# A pane that was not ready for the first start is tried again.
FAIL_START=1 ps "재시도" "지시" >/dev/null
[ "$(cat "$SANDBOX/starts")" = 2 ] || fail "should retry a failed start"
has "agent prompt w1:new 지시" "retry delivers"

# A start that keeps failing removes the empty pane and says so.
if FAIL_START=9 ps "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "pane close w1:new" "the empty pane goes"
hasnt "agent prompt" "nothing to prompt"

# An agent held by a dialog as it starts keeps the name it was given, so a retry could only fail on that name and hide why.
# It is given up at once, quoting what the dialog says: on 2026-11-06 the message guessed a trust question, and the dialog
# was Codex's update prompt.
if BLOCKED_START=1 SCREEN=$'  Update available · 0.166.0 → 0.167.0\n\n› 1. Update now\n  2. Skip' ps --kind codex "막힘" "지시" 2>"$SANDBOX/err"; then fail "a start held by a dialog must exit non-zero"; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail "no retry after a dialog: $(cat "$SANDBOX/starts") starts"
has "pane close w1:new" "the held pane goes"
before "agent read w1:new" "pane close w1:new"
grep -qF "Update available · 0.166.0 → 0.167.0 › 1. Update now 2. Skip" "$SANDBOX/err" || fail "quotes the dialog on one line: $(cat "$SANDBOX/err")"
grep -q "trust" "$SANDBOX/err" || fail "still says how to answer a trust dialog: $(cat "$SANDBOX/err")"
grep -q -- "--kind claude" "$SANDBOX/err" || fail "names the way around it: $(cat "$SANDBOX/err")"

# Work that never shows fails loudly, with no resend and the pane left open: an instruction Codex dropped looks the same as a
# wait that failed on an accepted one, and resending that would run the job twice.
if out=$(FAIL_WAIT=1 ps "무응답" "지시" 2>"$SANDBOX/err"); then fail "no work seen must exit non-zero"; fi
[ "$(grep -c "agent prompt w1:new 지시" "$SANDBOX/log")" = 1 ] || fail "sends once: $(log)"
hasnt "pane close" "the pane stays"
[[ "$out" == *"w1:new"* ]] || fail "still names the pane: $out"
grep -q "pane-tell" "$SANDBOX/err" || fail "says what to do: $(cat "$SANDBOX/err")"

# Bad usage.
if ps "이름만" 2>/dev/null; then fail "missing instruction must exit non-zero"; fi
if ps --kind gemini "이름" "지시" 2>/dev/null; then fail "unknown kind must exit non-zero"; fi
if env -u HERDR_PANE_ID -u XDG_STATE_HOME HOME="$SANDBOX/home" CODEX_HOME="$SANDBOX/home/.codex" HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "이름" "지시" 2>/dev/null; then fail "outside herdr must exit non-zero"; fi


echo "ok: pane-spawn routing and arguments"
