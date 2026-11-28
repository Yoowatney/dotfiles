#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
is_main; out=$(ps --why skill "api#1 설계" "할 일")
has "[workers] tab create --workspace w1 " "a pane the main pane opens goes to workers"
hasnt "[default] tab create" "nothing opens beside the main pane"
hasnt "pane split" "the main pane keeps its tab to itself"
has "[default] pane get w1:p1" "the caller is read on its own server"
has "[default] pane-label --lead" "the caller is marked on its own server"
has "[workers] pane-label --sync-tabs" "tab marks are synced where the new tab is"
has "[workers] agent start spawn-65f21913323dc21-" "the name still records the main pane's terminal"
has "[workers] agent prompt w1:new" "the instruction reaches the workers pane"
has "보고 첫 줄에" "a pane under the main pane learns its first line is its summary"
[ "$out" = "workers/w1:new" ] || fail "prints an address with the server: $out"

rm "$SANDBOX/workers-workspaces.json"; ps --why skill "api#2" "할 일" >/dev/null
has "[workers] workspace create --label workers --no-focus" "an empty workers server gets its workspace"
before "workspace create" "tab create --workspace w2"

# 요유 2026-11-05: a tab named `자동 칸을 workers로 이전` read as a dispatch tab, so pane-tell refused its pane. A work name never
# starts a job tab's name; the pane keeps the work name as given.
for word in 자동 알아서; do
  ps --why skill "$word 칸을 workers로 이전" "할 일" >/dev/null
  has "[workers] tab create --workspace w2 --cwd $PWD --label 일 $word 칸을 workers로 이전 --no-focus" "the main pane's tab: no job tab's first word ($word)"
  hasnt "--label $word " "no tab starts with $word"
  has "pane rename w1:new 🟢 🫡 $word 칸을 workers로 이전" "the pane keeps the work name"
done
ps --why skill "자동화 정리" "할 일" >/dev/null
has "--label 자동화 정리 --no-focus" "자동화 is no job tab's first word"
not_main; echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
CALLER_LABEL="🟢 자동 칸 정리" ps "구현" "할 일" >/dev/null
has "[workers] tab rename w7:t1 일 자동 칸 정리" "a lead's tab named for it: no job tab's first word"
hasnt "--label 자동 " "and none when the tab opens"
FAIL_SPLIT=1 ps --new-tab "자동 칸 고치기" "할 일" >/dev/null
hasnt "--label 자동 " "a --new-tab name goes the same way"
rm -f "$SANDBOX/workers-workspaces.json"; is_main
ps --why skill "api#2-2" "할 일" >/dev/null

workers_up false; if ps --why skill "api#3" "할 일" 2>"$SANDBOX/err"; then fail "workers down must fail"; fi
grep -q "com.yoyoo.herdr-workers" "$SANDBOX/err" || fail "says how to start it: $(cat "$SANDBOX/err")"
hasnt "tab create" "no pane opens anywhere when workers is down"
workers_up

if ps --same-tab --why skill "리뷰" "봐 줘" 2>/dev/null; then fail "the main pane never splits its own tab"; fi
hasnt "pane split" "no split beside the main pane"

if FAIL_START=9 ps --why skill "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "[workers] pane close w1:new" "a failed start removes the workers pane"

hasnt "확인 칸입니다" "a pane that does the job is no check pane"

# 요유 2026-11-05 (api#1002): a fresh pane checks a pane's report for the main pane. Its agent name says so; a rename cannot change that.
# Checks have an independent Codex policy and protect the entire process tree.
out=$(ps --check "api#1 로그인" "npm test를 돌려 보세요")
has 'Current KST time: 2026-11-12 01:09:00 KST' "checks receive the current KST date and time"
hasnt 'first response line must be `끝날 예상: HH:MM KST`' "checks retain their command-and-result first line"
has "[workers] tab create --workspace " "a check pane opens with the other workers"
has "[workers] pane rename w1:new 🟢 🫡 확인 api#1 로그인" "its name says it is a check pane"
has "[workers] agent start spawn-65f21913323dc21-chk" "its agent name tells auto-tab-close, and still records who opened it"
has "npm test를 돌려 보세요" "the instruction arrives"
has "확인 칸입니다" "it hears what a check pane does"
has "Check whether the result answers the original question as asked" "a checker checks question coverage, not just claims"
has "recommendation and next action" "a checker checks the recommended follow-up"
has "Do not mark an unanswered material question as passed" "a checker cannot pass an incomplete answer"
has "고치지 말고" "a check pane changes nothing"
has "다른 칸의 보고를 다시 읽는 것은 확인이 아닙니다" "rereading a report is no check"
has "보고 첫 줄에" "its first line is what the main pane reads"
[ "$out" = "workers/w1:new" ] || fail "a check pane's address names its server: $out"
has "--kind codex --pane w1:new --timeout 120000" "a default check runs Codex"
has "--env HERDR_CHECK_TARGET=$PWD" "its checked folder is protected"
has "--env HERDR_CODEX_STARTUP_ROLE=check" "checks activate the startup wrapper in existing shells too"
has "--env HERDR_CODEX_STARTUP_HELPER=" "checks pin the protection helper beside the launcher"
has "--sandbox danger-full-access" "outer process protection applies regardless of native sandbox"
hasnt "Codex 샌드박스" "nor the Codex sandbox note"
[ ! -d "$SANDBOX/home/.local/state/pane-spawn/check" ] || fail "no fixed Codex check folder is made"
ps --cwd /work/repo --check "로그인" "돌려 보세요" >/dev/null
has "확인 칸입니다" "options in any order"
has "--cwd /work/repo " "a check starts in the folder it checks"
has "--env HERDR_CHECK_TARGET=/work/repo" "and protects that folder"
SESSIONLESS=1 ps --kind codex --cwd /work/repo "조사" "지시" >/dev/null
has "--cwd /work/repo " "a Codex pane that does the job starts where it is told"
# 2026-11-05: Codex runs commands with mise's shims first on PATH. A test that swaps HOME then hid mise's config and installs from
# it: python3 re-ran its own shim for ever, or met mise's trust error. Every Codex pane gets mise's folders by absolute path.
# Values already set win, so this suite run inside a Codex pane (which has them, and needs them to reach python3) expects those.
H=$SANDBOX/home
MISE_ENV="--env MISE_CONFIG_DIR=${MISE_CONFIG_DIR:-${XDG_CONFIG_HOME:-$H/.config}/mise} --env MISE_DATA_DIR=${MISE_DATA_DIR:-${XDG_DATA_HOME:-$H/.local/share}/mise} --env MISE_STATE_DIR=${MISE_STATE_DIR:-$H/.local/state/mise} --env MISE_CACHE_DIR=${MISE_CACHE_DIR:-$H/Library/Caches/mise}"
has "--no-focus $MISE_ENV" "a Codex pane keeps mise's folders when a test swaps HOME"
ps --why skill "조사" "지시" >/dev/null
hasnt "MISE_" "a Claude pane runs commands without the shims first, so it needs none"
ps --why skill "조사" "지시" >/dev/null
has "--kind claude" "a pane that does the job is still Claude"
hasnt "--sandbox" "and has no sandbox"
# An explicit Codex check keeps process/network reads and protects the target.
out=$(ps --check --kind codex "api#1 로그인" "npm test를 돌려 보세요")
has 'Current KST time: 2026-11-12 01:09:00 KST' "Codex checks receive the current KST date and time"
has "--env HERDR_CHECK_TARGET=$PWD" "a Codex check protects its checked folder for the whole process tree"
hasnt "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" "Claude-only flags do not enter Codex checks"
has "--kind codex --pane w1:new --timeout 120000" "a Codex check pane runs Codex"
has "--sandbox danger-full-access" "a Codex check uses the protected startup wrapper"
hasnt "Codex 샌드박스" "nor the sandbox note"
has "--cwd $PWD " "and starts where it is told"
has "확인 칸입니다" "it is still a check pane"
ps --check --kind codex "로그인" "돌려 보세요" -- --model example >/dev/null
has "--model example" "the caller model wins"
hasnt "gpt-6.1-sol" "no second model"
ps --check --kind codex "로그인" "돌려 보세요" -- --model=example -c 'model_reasoning_effort="xhigh"' >/dev/null
hasnt "gpt-6.1-sol" "equals-form model wins"
hasnt 'model_reasoning_effort="high"' "explicit effort wins"
for flag in '--sandbox workspace-write' '--sandbox=read-only' '-sread-only' '--full-auto' '-c sandbox_mode="workspace-write"'; do
  if ps --check "읽기 확인" "확인" -- $flag 2>"$SANDBOX/err"; then fail "native check sandbox override must be refused: $flag"; fi
  grep -q 'check protection' "$SANDBOX/err" || fail "explains outer boundary"
done
echo "ok: pane-spawn sandbox and checks"
