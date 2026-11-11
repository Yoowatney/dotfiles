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
out=$(ps --check "api#1 로그인" "npm test를 돌려 보세요")
has 'Current KST time: 2026-11-12 01:09:00 KST' "Codex checks receive the current KST date and time"
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
# 2026-11-05: a check pane runs on Codex's spare weekly allowance, in Codex's sandbox, so a write to the repo fails instead of
# relying on the instruction. Nobody answers its approval prompts, so a blocked command fails at once.
# 요유 2026-11-07: a check pane reads GCP, GitHub, the board and the network itself, so the network is on, and gcloud's own
# folder is the one more place it writes: gcloud rewrites its login files on every call. The checked folder stays read-only.
SANDBOXED="--sandbox workspace-write --ask-for-approval never -c sandbox_workspace_write.network_access=true"
has "--kind codex --pane w1:new --timeout 120000 -- $SANDBOXED" "a check pane is a sandboxed Codex with the network"
[ "$(grep 'agent start' "$SANDBOX/log" | grep -o -- '--sandbox' | wc -l | tr -d ' ')" = 1 ] || fail "a check gets exactly one sandbox option"
has "-c sandbox_workspace_write.writable_roots=[\"$SANDBOX/home/.config/gcloud\"]" "gcloud's folder is the one writable root it adds"
hasnt "network_access=false" "the network is not shut"
hasnt "danger-full-access" "a check pane keeps its sandbox"
has "Codex 샌드박스" "it hears what the sandbox blocks"
hasnt "--model" "a codex check pane keeps its model"
hasnt "--effort" "and its effort"
hasnt "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" "and its environment"
# It may write only in a fixed empty folder outside any repo, besides temp: a test runs, and the repo stays as it was. Codex asks
# whether to trust a repo it does not know after it reports ready, and the instruction pane-spawn types then answers yes and
# is lost (2026-11-05, ~/.dotfiles). That folder is no repo, so nothing asks; the repo goes in the instruction instead.
CHECK_DIR="$SANDBOX/home/.local/state/pane-spawn/check"
has "tab create --workspace w2 --cwd $CHECK_DIR --label 확인 api#1 로그인" "it starts in the fixed folder"
[ -d "$CHECK_DIR" ] || fail "the fixed folder is made"
has "$PWD" "the folder to check, by absolute path, in the instruction"
has "$PWD/AGENTS.md" "Codex reads no rules of a folder it did not start in, so it is told where they are"
# MCP servers and plugins run outside the sandbox, so each one that can write is off: every server the user's Codex config
# names, a new one included, except two that keep only their read tools. Codex's own connectors (apps) and plugins are off.
RULES="$CHECK_DIR/.codex/rules/check.rules"
has "-c projects={\"$CHECK_DIR\"={trust_level=\"trusted\"}}" "the fixed folder is trusted, so Codex loads its rules and asks nothing"
has "--disable apps --disable plugins" "connectors and plugins are off"
# Codex's TUI serves its own MCP tools (create_worktree, in a trusted repo, runs git outside the sandbox) unless a configured
# server holds their name; then it starts without them. Neither `--disable worktrees` (a removed feature, still on) nor
# `-c mcp_servers.codex_worktrees.enabled=false` (no command: Codex does not start) does it (2026-11-05, Codex 0.166).
has '-c mcp_servers.codex_worktrees={command="/usr/bin/false",enabled=false}' "Codex's worktree tools are off"
has '-c mcp_servers.codex_tui={command="/usr/bin/false",enabled=false}' "and its task tools, served under this name with a daemon"
hasnt "mcp_servers.slack" "a config with no MCP server turns none of its own off"
hasnt "worktrees.enabled" "no server that Codex cannot start"
for override in --sandbox=read-only --ask-for-approval=never --full-auto --yolo; do
  if ps --check "policy override" "instruction" -- "$override" >/dev/null 2>&1; then fail "a check cannot override its policy: $override"; fi
  hasnt "agent start" "conflicting check options fail before starting"
  hasnt "tab create" "conflicting check options open no pane"
done
mkdir -p "$SANDBOX/home/.codex"
printf '%s\n' '[mcp_servers.slack]' 'command = "npx"' '[mcp_servers."notion-token"]' 'command = "x"' \
  '[mcp_servers.munice]' 'url = "https://x"' '[mcp_servers.brand_new]' 'command = "x"' > "$SANDBOX/home/.codex/config.toml"
ps --check "로그인" "돌려 보세요" >/dev/null
# Codex splits a -c path at each dot and keeps quotes in the name: `mcp_servers."x".enabled=false` made a new server with no
# command, and Codex refused to start (2026-11-05). So names go bare.
has '-c mcp_servers.notion-token.enabled=false' "a server that can write is off"
has '-c mcp_servers.brand_new.enabled=false' "so is one added later"
has '-c mcp_servers.slack.enabled_tools=["slack_list_channels", "slack_get_channel_history", "slack_get_thread_replies", "slack_get_users", "slack_get_user_profile"]' "Slack keeps only its read tools"
hasnt "slack_post_message" "no Slack post"
hasnt "slack_reply_to_thread" "no Slack reply"
hasnt "slack_add_reaction" "no Slack reaction"
has '-c mcp_servers.munice.enabled_tools=["ask_code", "ask_data"]' "munice keeps only its questions"
# Under --ask-for-approval never, a tool Codex would ask about is refused: the Slack reads failed so (2026-11-05).
has '-c mcp_servers.slack.default_tools_approval_mode="approve"' "Slack's read tools run without asking"
has '-c mcp_servers.munice.default_tools_approval_mode="approve"' "and munice's"
hasnt 'notion-token.default_tools_approval_mode' "a server that is off gets no approval"
hasnt 'mcp_servers."' "no quoted server name"
has "slack_get_thread_replies" "the instruction names the Slack read tools"
hasnt "gh-read" "with the network on, gh reads GitHub itself"
# Large config output must be read concurrently, including through the installed
# launcher symlink and Apple's Bash 3.2. Percent signs remain prompt data.
python3 -I -c 'print("\n".join(f"[mcp_servers.large_{i}]\ncommand = \"x\"" for i in range(80)))' >> "$SANDBOX/home/.codex/config.toml"
large_prompt=$(python3 -I -c 'print("prompt-%s-" * 2048)')
ln -s "$SUBJECT" "$SANDBOX/pane-spawn-link"
real_subject=$SUBJECT; SUBJECT=$SANDBOX/pane-spawn-link
PANE_SPAWN_TEST_SHELL=/bin/bash ps --check "Large input" "$large_prompt" >/dev/null
has '-c mcp_servers.large_79.enabled=false' "large MCP config disables even its last server"
python3 -I -c 'import pathlib,sys; assert "agent prompt w1:new " + sys.argv[2] in pathlib.Path(sys.argv[1]).read_text(), "large prompt was truncated"' "$SANDBOX/log" "$large_prompt"
cmp "$RULES" "$(dirname "$real_subject")/pane-spawn-check.rules" || fail "installed launcher writes the complete external rule template"
python3 -I -c 'print("#" + "x" * 16384 + "\nnot toml [")' > "$SANDBOX/home/.codex/config.toml"
if PANE_SPAWN_TEST_SHELL=/bin/bash ps --check "Bad large config" "$large_prompt" 2>"$SANDBOX/err" >/dev/null; then fail "large invalid config must stop check startup"; fi
hasnt "agent start" "large invalid config opens no agent"
SUBJECT=$real_subject
for bad in 'not toml [' '[mcp_servers."a.b"]'; do
  echo "$bad" > "$SANDBOX/home/.codex/config.toml"
  if ps --check "로그인" "돌려 보세요" 2>"$SANDBOX/err" >/dev/null; then fail "a server it cannot turn off must stop a Codex check pane ($bad)"; fi
  grep -q -- "--kind claude" "$SANDBOX/err" || fail "and say what to do: $(cat "$SANDBOX/err")"
  hasnt "agent start" "no check pane opens with a server it could not turn off ($bad)"
done
rm -rf "$SANDBOX/home/.codex"
# Inside the sandbox ps sees no other process and launchctl lists nothing (2026-11-07), so rules let ps, pgrep and launchctl's
# two read verbs run outside it, each only from its real path: Codex resolves an absolute path by its last part, so without
# host_executable a ps the agent wrote in temp would run outside too. ego-browser, which the user's own rules let out, drives a
# browser that may be signed in to Slack, so here it is forbidden. The gh verbs that write a pull request or an issue are
# forbidden too: with the network on, only the instruction stopped them.
[ -f "$RULES" ] || fail "the check pane's rules are written"
ls -A "$CHECK_DIR/.codex/rules" | grep -qv '^check.rules$' && fail "no temp file is left beside the rules: $(ls -A "$CHECK_DIR/.codex/rules")"
if command -v codex >/dev/null 2>&1; then
  mkdir -p "$SANDBOX/execpolicy-home" "$SANDBOX/execpolicy-tmp"
  decide() { TMPDIR="$SANDBOX/execpolicy-tmp" CODEX_HOME="$SANDBOX/execpolicy-home" codex execpolicy check --resolve-host-executables -r "$RULES" -r "$SANDBOX/user.rules" -- "$@" | jq -r '.decision // "sandbox"'; }
  echo 'prefix_rule(pattern=["ego-browser"], decision="allow")' > "$SANDBOX/user.rules" # as in ~/.codex/rules/default.rules
  for c in "allow ps -A" "allow /bin/ps -A" "allow pgrep -fl codex" "allow launchctl list" "allow launchctl print gui/501" \
    "sandbox /tmp/x/ps -A" "sandbox /tmp/x/pgrep x" "sandbox /tmp/x/launchctl list" "sandbox launchctl kickstart gui/501/x" \
    "forbidden gh pr merge 3" "forbidden gh pr comment 3 -b x" "forbidden gh issue comment 3 -b x" "forbidden gh issue close 3" \
    "sandbox gh pr view 3" "sandbox gh issue view 3" \
    "forbidden ego-browser open x" "forbidden /Users/x/.local/bin/ego-browser open x" "sandbox curl -d x https://slack.com/api/chat.postMessage"; do
    want=${c%% *} cmd=${c#* }
    # shellcheck disable=SC2086
    [ "$(decide $cmd)" = "$want" ] || fail "rules: $cmd should be $want, is $(decide $cmd)"
  done
fi
ps --cwd /work/repo --check "로그인" "돌려 보세요" >/dev/null
has "확인 칸입니다" "options in any order"
has "--cwd $CHECK_DIR " "a --cwd the caller gives is checked from the fixed folder too"
has "/work/repo" "and named in the instruction"
ps --check "로그인" "돌려 보세요" -- -m gpt-5 >/dev/null
grep -qE -- "-- $SANDBOXED .*--disable plugins $NOUPDATE -m gpt-5\$" "$SANDBOX/log" || fail "the caller's Codex arguments follow the sandbox, and a check pane skips the update prompt: $(log)"
ps --check --kind codex "로그인" "돌려 보세요" >/dev/null
has "--kind codex --pane w1:new --timeout 120000 -- $SANDBOXED" "--kind codex is the same"
SESSIONLESS=1 ps --kind codex --cwd /work/repo "조사" "지시" >/dev/null
has "--cwd /work/repo " "a Codex pane that does the job starts where it is told"
# 2026-11-05: Codex runs commands with mise's shims first on PATH. A test that swaps HOME then hid mise's config and installs from
# it: python3 re-ran its own shim for ever, or met mise's trust error. Every Codex pane gets mise's folders by absolute path.
# Values already set win, so this suite run inside a Codex pane (which has them, and needs them to reach python3) expects those.
H=$SANDBOX/home
MISE_ENV="--env MISE_CONFIG_DIR=${MISE_CONFIG_DIR:-${XDG_CONFIG_HOME:-$H/.config}/mise} --env MISE_DATA_DIR=${MISE_DATA_DIR:-${XDG_DATA_HOME:-$H/.local/share}/mise} --env MISE_STATE_DIR=${MISE_STATE_DIR:-$H/.local/state/mise} --env MISE_CACHE_DIR=${MISE_CACHE_DIR:-$H/Library/Caches/mise}"
has "--no-focus $MISE_ENV" "a Codex pane keeps mise's folders when a test swaps HOME"
ps --check "로그인" "돌려 보세요" >/dev/null
has "--no-focus $MISE_ENV" "so does a Codex check pane"
has "[workers] tab create --workspace w2 --cwd $CHECK_DIR --label 확인 로그인 --no-focus $MISE_ENV $NPROC" "and a workers pane, the lower limit"
ps --why skill "조사" "지시" >/dev/null
hasnt "MISE_" "a Claude pane runs commands without the shims first, so it needs none"
ps --why skill "조사" "지시" >/dev/null
has "--kind claude" "a pane that does the job is still Claude"
hasnt "--sandbox" "and has no sandbox"
# An explicit Claude check keeps process/network reads and protects the target.
out=$(ps --check --kind claude "api#1 로그인" "npm test를 돌려 보세요")
has 'Current KST time: 2026-11-12 01:09:00 KST' "Claude checks receive the current KST date and time"
has "--env CLAUDE_CHECK_TARGET=$PWD" "a Claude check protects its checked folder for the whole process tree"
has "[workers] tab create --workspace w2 --cwd $PWD --label 확인 api#1 로그인 --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" "a workers pane has no advisor"
has "--kind claude --pane w1:new --timeout 120000 -- --model claude-sonnet-5-5 --effort high" "a Claude check pane runs Sonnet at high effort"
hasnt "--sandbox" "a Claude check pane gets no Codex flag"
hasnt "Codex 샌드박스" "nor the sandbox note"
has "--cwd $PWD " "and starts where it is told"
has "확인 칸입니다" "it is still a check pane"
ps --check --kind claude "로그인" "돌려 보세요" -- --model opus >/dev/null
has "-- --effort high --model opus" "the caller's model wins"
hasnt "claude-sonnet-5-5" "and no second model"
ps --check --kind claude "로그인" "돌려 보세요" -- --model=opus --effort=xhigh >/dev/null
hasnt "claude-sonnet-5-5" "the caller's --model= wins"
hasnt "--effort high" "so does the caller's --effort="


echo "ok: pane-spawn sandbox and checks"
