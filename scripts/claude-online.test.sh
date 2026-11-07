#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
box=$(mktemp -d)
trap 'rm -rf "$box"' EXIT
mkdir -p "$box/bin" "$box/state/orchestrator" "$box/home/.codex" "$box/home/work/munice/sub" "$box/other"
cat >"$box/bin/dscacheutil" <<'SH'
#!/bin/bash
echo 'ip_address: 127.0.0.1'
SH
cat >"$box/bin/claude" <<'SH'
#!/bin/bash
printf '%s\n' "$@"
SH
cat >"$box/bin/herdr" <<'SH'
#!/bin/bash
echo '{"result":{"pane":{"terminal_id":"term_test"}}}'
SH
chmod +x "$box/bin/"*
# Rule files a pane under a lead loads filtered: pane:skip blocks go, `<!-- pane: ... -->` lines become plain text.
cat >"$box/home/.codex/AGENTS.md" <<'MD'
# Global
<!-- an ordinary comment -->
<!-- pane: PANE_NOTE_TEXT -->
- KEEP_GLOBAL
<!-- pane:skip -->
- SKIP_GLOBAL
<!-- /pane:skip -->
- KEEP_GLOBAL_TAIL
MD
cat >"$box/home/work/munice/CLAUDE.local.md" <<'MD'
- KEEP_LOCAL
<!-- pane:skip -->
## SKIP_LOCAL
<!-- /pane:skip -->
MD
run() { env PATH="$box/bin:$PATH" HOME="$box/home" XDG_STATE_HOME="${STATE:-$box/state}" HERDR_PANE_ID=w1:p1 CLAUDE_STARTUP_ROLE="${ROLE:-}" CLAUDE_STARTUP_PROFILE="${PROFILE:-}" CLAUDE_STARTUP_ARTIFACT="${ARTIFACT:-}" CLAUDE_STARTUP_SKILLS="${SKILLS:-}" bash "$ROOT/scripts/claude-online" "$@"; }
fail() { echo "FAIL: $1"; exit 1; }
arg_after() { awk -v f="$1" 'p { print; exit } $0 == f { p = 1 }'; }

out=$(cd "$box/home/work/munice/sub" && ROLE=worker run 'literal argument $()')
[[ "$out" == --settings$'\n'* ]] || fail 'worker has no startup settings'
settings=$(sed -n '2p' <<<"$out")
jq -e '.enableWorkflows == false and .enabledPlugins["context7@claude-plugins-official"] == false and (has("hooks") | not)' <<<"$settings" >/dev/null || fail 'worker keeps the common profile and no safety settings'
# api#1065: the Artifact tool's schema (~9k tokens) is resent on every call, so a pane under a lead starts without it.
jq -e '.enableArtifact == false' <<<"$settings" >/dev/null || fail 'worker starts without the Artifact tool'
# A job that publishes a page opts back in (pane-spawn --artifact): the key goes, every other worker setting stays.
opt_in=$(cd "$box/home/work/munice/sub" && ROLE=worker ARTIFACT=1 run)
jq -e '(has("enableArtifact") | not) and .enableWorkflows == false and .env.ENABLE_CLAUDEAI_MCP_SERVERS == "false" and (.claudeMdExcludes | length) == 2' <<<"$(sed -n '2p' <<<"$opt_in")" >/dev/null || fail 'opt-in keeps Artifact and the rest of the worker profile'
jq -e '(has("enableArtifact") | not)' <<<"$(cd "$box/home/work/munice/sub" && ROLE=main ARTIFACT=1 run | sed -n '2p')" >/dev/null || fail 'opt-in works for the main role too'
# api#1066: a pane under a lead starts without the skills its work never calls (design, office-file and Claude-API skills), so the
# listing sent on every call shrinks. Plugin skills cannot be switched off one by one, so taste-skill goes as a plugin.
jq -e '.skillOverrides as $o | .enabledPlugins["taste-skill@taste-skill"] == false and (["docx","pdf","pptx","xlsx","code-review","claude-api","update-config","run","loop","init"] | all($o[.] == "off")) and (has("dietSkills") | not)' <<<"$settings" >/dev/null || fail 'worker starts without the unrelated skills'
# Skills that the lead's instructions and the rule files call must stay: superpowers, codex-review, agent-board, writing-*, QA and release skills.
jq -e '.skillOverrides as $o | ["codex-review","agent-board","writing-plain-korean","writing-as-yoyoo","html-explainer","ego-browser","agent-reach","work-log","release-diff","search-history","save-session","merge-and-deploy","release-notify","dataviz","simplify","security-review","plugin-authoring"] | all($o[.] == null)' <<<"$settings" >/dev/null || fail 'worker keeps the skills that instructions call'
jq -e '.enabledPlugins as $p | ["superpowers@claude-plugins-official","commit-commands@claude-plugins-official","ponytail@ponytail","fluent-korean@fluent-korean","core@munice","compliance@munice","ux-writing@munice"] | all($p[.] != false)' <<<"$settings" >/dev/null || fail 'worker keeps the plugins whose skills or hooks instructions call'
# pane-spawn --skills opts back in: only the api#1066 cut returns; every earlier cut and the rest of the worker profile stay.
skills_in=$(cd "$box/home/work/munice/sub" && ROLE=worker SKILLS=1 run)
jq -e '(.enabledPlugins | has("taste-skill@taste-skill") | not) and (.skillOverrides | has("docx") or has("claude-api") | not) and .skillOverrides["hiring-panel"] == "off" and .enabledPlugins["core-skills@hyperframes"] == false and .enableArtifact == false and (has("dietSkills") | not) and (.claudeMdExcludes | length) == 2' <<<"$(sed -n '2p' <<<"$skills_in")" >/dev/null || fail '--skills brings back only the api#1066 cut'
jq -e '(has("dietSkills") | not) and (.skillOverrides // {} | has("docx") | not) and (.enabledPlugins // {} | has("taste-skill@taste-skill") | not)' <<<"$(cd "$box/home/work/munice/sub" && ROLE=main run | sed -n '2p')" >/dev/null || fail 'the main pane keeps every skill'
# Deny rules merge with every other source, so they only narrow; nothing here may allow, ask or change the mode.
jq -e '(.permissions | keys) == ["deny"] and (.permissions.deny | index("AskUserQuestion"))' <<<"$settings" >/dev/null || fail 'worker permissions only deny: a pane never asks 요유'
jq -e '.enabledPlugins["core-skills@hyperframes"] == false and (.deniedMcpServers | index({serverName: "rea"})) and .env.ENABLE_CLAUDEAI_MCP_SERVERS == "false" and .skillOverrides["hiring-panel"] == "off" and (has("paneRules") | not)' <<<"$settings" >/dev/null || fail 'worker drops unused plugins, MCP servers, claude.ai connectors and skills'
jq -e --arg g "$box/home/.codex/AGENTS.md" --arg l "$box/home/work/munice/CLAUDE.local.md" '.claudeMdExcludes == [$g, $l]' <<<"$settings" >/dev/null || fail 'worker excludes the full rule files it loads filtered'
rules=$(arg_after --append-system-prompt-file <<<"$out")
[ -f "$rules" ] || fail 'worker gets no filtered rule file'
for want in KEEP_GLOBAL KEEP_GLOBAL_TAIL PANE_NOTE_TEXT KEEP_LOCAL "$box/home/.codex/AGENTS.md"; do grep -qF -- "$want" "$rules" || fail "filtered rules miss $want"; done
for unwanted in SKIP_GLOBAL SKIP_LOCAL 'pane:skip' '<!--'; do ! grep -qF -- "$unwanted" "$rules" || fail "filtered rules keep $unwanted"; done
[[ "$(tail -1 <<<"$out")" == 'literal argument $()' ]] || fail 'arguments pass through'

# Outside munice the munice rules are not loaded at all, so they are neither excluded nor appended.
out=$(cd "$box/other" && ROLE=worker run)
jq -e --arg g "$box/home/.codex/AGENTS.md" '.claudeMdExcludes == [$g]' <<<"$(sed -n '2p' <<<"$out")" >/dev/null || fail 'outside munice only the global rules are filtered'
! grep -qF KEEP_LOCAL "$(arg_after --append-system-prompt-file <<<"$out")" || fail 'munice rules leak outside munice'
# A rule file that is missing is skipped, not excluded.
mv "$box/home/.codex/AGENTS.md" "$box/agents.bak"
out=$(cd "$box/other" && ROLE=worker run)
jq -e '(.claudeMdExcludes // []) == []' <<<"$(sed -n '2p' <<<"$out")" >/dev/null || fail 'a missing rule file is excluded'
! grep -qx -- --append-system-prompt-file <<<"$out" || fail 'no rule file means no appended file'
mv "$box/agents.bak" "$box/home/.codex/AGENTS.md"

# When the filtered copy cannot be written the pane still starts, with every rule file whole.
out=$(cd "$box/home/work/munice/sub" && ROLE=worker STATE=/dev/null/nowhere run 'still runs' 2>/dev/null) || fail 'an unwritable state dir stops the pane'
jq -e '(has("claudeMdExcludes") | not) and (has("paneRules") | not) and .env.ENABLE_CLAUDEAI_MCP_SERVERS == "false"' <<<"$(sed -n '2p' <<<"$out")" >/dev/null || fail 'unwritable state dir: full rules, rest of the profile kept'
! grep -qx -- --append-system-prompt-file <<<"$out" || fail 'unwritable state dir: no appended file'
[[ "$(tail -1 <<<"$out")" == 'still runs' ]] || fail 'unwritable state dir: arguments pass through'

# The main pane keeps every rule file whole.
out=$(cd "$box/home/work/munice/sub" && ROLE=main run)
jq -e '.enableArtifact == false and .enableWorkflows == false and (has("claudeMdExcludes") | not) and (has("deniedMcpServers") | not)' <<<"$(sed -n '2p' <<<"$out")" >/dev/null || fail 'main profile changed'
! grep -qx -- --append-system-prompt-file <<<"$out" || fail 'main pane gets filtered rules'
printf 'term_test\n' >"$box/state/orchestrator/main"
out=$(run)
jq -e '.enableArtifact == false' <<<"$(sed -n '2p' <<<"$out")" >/dev/null || fail 'main detection'
[[ "$(ROLE=worker PROFILE=full run --version)" == --version ]] || fail 'full opt-out'
rm "$box/state/orchestrator/main"
[[ "$(run --version)" == --version ]] || fail 'a session opened by hand gets no profile'
echo 'PASS: startup role, main detection, opt-out, arguments, safety settings, and filtered pane rules'
