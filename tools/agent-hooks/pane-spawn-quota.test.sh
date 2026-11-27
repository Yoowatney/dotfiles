#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
# Quota-aware normal workers; all data and agent starts remain synthetic.
quota_fixture() { # <Codex weekly> <Claude weekly> [extra usage enabled] [Codex age seconds] [Claude age seconds]
  env HOME="$SANDBOX/home" CODEX_HOME="$SANDBOX/home/.codex" CLAUDE_CONFIG_DIR="$SANDBOX/home/.claude" python3 -I - "$SUBJECT" "$1" "$2" "${3:-false}" "${4:-0}" "${5:-0}" <<'PY'
import importlib.machinery, importlib.util, json, os, pathlib, sys, time, datetime
sys.dont_write_bytecode = True
loader = importlib.machinery.SourceFileLoader('quota', str(pathlib.Path(sys.argv[1]).with_name('agent-quota')))
spec = importlib.util.spec_from_loader(loader.name, loader)
q = importlib.util.module_from_spec(spec)
loader.exec_module(q)
h = pathlib.Path.home(); now = time.time(); at = now - int(sys.argv[5])
def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(obj)); return p
auth = write(h / '.codex/auth.json', {'auth_mode':'chatgpt', 'tokens':{'account_id':'test'}})
os.utime(auth, (at - 100, at - 100))
write(h / '.claude.json', {'oauthAccount':{'accountUuid':'test', 'hasExtraUsageEnabled':json.loads(sys.argv[4])}})
stamp = datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat()
week = lambda used: {'used_percent':int(used), 'window_minutes':10080, 'resets_at':now+302400}
write(h / '.codex/sessions/quota.jsonl', {'timestamp':stamp, 'payload':{'rate_limits':{'limit_id':'codex',
    'primary':{'used_percent':20,'window_minutes':300,'resets_at':at+1000}, 'secondary':week(sys.argv[2]),
    'credits':{'has_credits':False,'unlimited':False}}}})
cat = now - int(sys.argv[6])
snap = {'account':q.identity('test'), 'machine':q.machine(), 'observed_at':cat,
    'five_hour':{'used_percentage':20,'resets_at':cat+1000}, 'seven_day':{'used_percentage':int(sys.argv[3]),'resets_at':now+302400}}
q.private_write(q.state_dir() / 'claude.json', snap)
PY
}
echo quota-aware > "$SANDBOX/home/.local/state/agent-quota/policy"
rm -rf "$SANDBOX/home/.codex/sessions"
quota_fixture 65 40
CALLER_LABEL="🟢 🫡 lead worker" ps --kind auto "quota" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "a normal non-main auto worker chooses Claude with weekly relief"
has "--model claude-sonnet-5-5 --effort high" "auto Claude always defaults to Sonnet/high"
hasnt "--sandbox" "Codex arguments never reach Claude"
has "CLAUDE_STARTUP_ROLE=worker" "auto Claude keeps the worker profile"
grep -q 'weekly pace' "$SANDBOX/err" || fail "selection has a reason"
is_main
ps --kind auto "quota" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "the main pane's auto Claude choice needs no explicit --why"
ps --check "quota" "instruction" >/dev/null
has "--kind claude" "checks ignore normal worker routing: always Claude"
not_main
for args in '--model=gpt-example' '--effort=high' '-m gpt-example' '-c model_reasoning_effort=xhigh' '-c model="gpt-example"'; do
  # Intentional splitting of this fixed list simulates the original argument tokens.
  SESSIONLESS=1 ps --kind auto "model" "instruction" -- $args >/dev/null
  has "--kind codex" "explicit model/effort pins the historical auto runner: $args"
done
quota_fixture 99 90
if ps --kind auto "blocked" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "both reserves reached must refuse"; fi
[ ! -e "$SANDBOX/log" ] || fail "blocked selection must not even query herdr"
rm -rf "$SANDBOX/home/.codex/sessions" "$SANDBOX/home/.local/state/agent-quota/claude.json"
if ps --kind auto "unknown" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "unknown usage must refuse"; fi
[ ! -e "$SANDBOX/log" ] || fail "unknown selection opens no pane"
quota_fixture 99 40 true
if ps --kind auto "paid" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "paid Claude must be excluded"; fi
ps --why "explicit choice" --kind claude "explicit" "instruction" >/dev/null
has "--kind claude" "explicit Claude remains unchanged"
quota_fixture 65 40
if FAIL_START=9 ps --kind auto "failure" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "selected Claude start failure must fail"; fi
has "--kind claude" "selected runner is retained on startup failure"
hasnt "--kind codex" "no fallback to Codex after a Claude start failure"
echo codex-only > "$SANDBOX/home/.local/state/agent-quota/policy"
SESSIONLESS=1 ps --kind auto "rollback" "instruction" >/dev/null
has "--kind codex" "one local policy value restores legacy selection"
echo typo > "$SANDBOX/home/.local/state/agent-quota/policy"
if ps --kind auto "bad policy" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "invalid policy must refuse"; fi
[ ! -e "$SANDBOX/log" ] || fail "invalid policy opens no pane"

# claude-first: one policy value makes Claude the first choice for work and check panes (paid-extra account included).
echo claude-first > "$SANDBOX/home/.local/state/agent-quota/policy"
quota_fixture 10 40 true
not_main
CALLER_LABEL="🟢 🫡 lead worker" ps --kind auto "first" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "claude-first: a worker picks Claude although Codex has more room"
has "--model claude-sonnet-5-5 --effort high" "claude-first: auto Claude defaults to Sonnet/high"
grep -q 'claude first' "$SANDBOX/err" || fail "claude-first selection has a reason"
is_main
ps --kind auto "first" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "claude-first: the main pane's auto choice is Claude without --why"
ps --check "first" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "claude-first: a default check is Claude"
has "--env CLAUDE_CHECK_TARGET=$PWD" "claude-first: the Claude check still protects its checked folder"
has "--model claude-sonnet-5-5 --effort high" "claude-first: a Claude check keeps Sonnet/high"
hasnt "--sandbox" "claude-first: no Codex sandbox flags reach a Claude check"
ps --check --kind auto "first" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--env CLAUDE_CHECK_TARGET=$PWD" "claude-first: an explicit --kind auto check is Claude too"
if ps --check --kind codex "first" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "an explicit --kind codex check is refused"; fi
ps --check "first" "instruction" -- --model x >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "forwarded arguments go to a Claude check"
quota_fixture 10 85 true
not_main
ps --kind auto "over" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind codex" "claude-first: Claude past its reserve returns to Codex"
grep -q 'reserve' "$SANDBOX/err" || fail "the fallback names its reason"
quota_fixture 20 85 true 3600
ps --kind auto "stale fallback" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind codex" "claude-first: low stale Codex usage still opens a worker"
grep -q 'stale' "$SANDBOX/err" || fail "the fallback discloses its stale observation"
quota_fixture 81 85 true 3600
if ps --kind auto "stale refused" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "stale Codex needs extra weekly headroom"; fi
[ ! -e "$SANDBOX/log" ] || fail "stale refusal must not query herdr"
grep -q 'Codex:.*reserve' "$SANDBOX/err" || fail "the refusal names the Codex reason"
grep -q 'agent-quota codex-week.*ask 요유' "$SANDBOX/err" || fail "inspect Codex before asking for an account switch"
grep -q 'does not authorize' "$SANDBOX/err" || fail "displaying usage does not bypass admission checks"
# An overnight Claude reading (no session answered for hours) with low usage still opens a worker, like stale Codex.
quota_fixture 99 14 true 0 28800
ps --kind auto "stale claude" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "claude-first: low stale Claude usage still opens a worker"
grep -q 'stale' "$SANDBOX/err" || fail "the stale Claude reading is disclosed"
quota_fixture 99 70 true 0 28800
if ps --kind auto "stale claude high" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "stale Claude needs extra weekly headroom"; fi
[ ! -e "$SANDBOX/log" ] || fail "stale Claude refusal must not query herdr"
# --opus and --analysis are Claude options, not a request for Codex: auto still reads the quota (2026-11-27: both opened a
# Codex pane that had hit its usage limit).
quota_fixture 99 40 true
ps --kind auto --opus "opus exhausted" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "--opus with Codex exhausted must open Claude: $(cat "$SANDBOX/err")"
has "--kind claude" "claude-first: --opus with Codex exhausted picks Claude"
hasnt "--model claude-sonnet-5-5" "claude-first: --opus keeps the global model on auto Claude"
hasnt "--effort high" "claude-first: --opus keeps the global effort on auto Claude"
hasnt "--kind codex" "claude-first: --opus never opens the exhausted Codex"
ps --kind auto --opus --analysis "opus analysis" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "--opus --analysis must open Claude"
has "--kind claude" "claude-first: --opus --analysis picks Claude"
has "--env CLAUDE_STARTUP_ANALYSIS=1" "claude-first: --analysis reaches the auto Claude pane"
mv "$SANDBOX/home/.codex/sessions" "$SANDBOX/home/.codex/sessions.off"
ps --kind auto --opus "opus unreadable" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "--opus with Codex usage unreadable must open Claude"
has "--kind claude" "claude-first: --opus with unreadable Codex usage picks Claude"
quota_fixture 10 85 true
ps --kind auto --opus "opus claude over" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind codex" "claude-first: --opus still reaches Codex when Claude is past its reserve and Codex has room"
quota_fixture 99 90 true
if ps --kind auto --opus "opus both over" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "--opus must not bypass both reserves"; fi
[ ! -e "$SANDBOX/log" ] || fail "--opus refusal must not query herdr"
quota_fixture 99 40 true
quota_fixture 10 85 true
is_main
# Lead decision 2026-11-19: past Claude's measured reserve, kept for the main pane on the same account, no check opens;
# nothing waits for one: the caller looks again or hands a read-only recheck out (요유 2026-11-20). Codex never stands in.
board="$SANDBOX/home/work/munice/.agents/skills/agent-board"
mkdir -p "$board/ambient"
echo 'YOYOO_DM = "D0FIXTURE"' > "$board/ambient/collect.py"
asked_file="$SANDBOX/home/.local/state/pane-spawn/check-usage-asked"
refused() { # <label>: a check past the reserve; its refusal is left in $SANDBOX/err
  if ps --check "$1" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "$1: a check past the Claude reserve must not open"; fi
}
# What auto-tab-close passes to the main pane is the first 1200 characters of the refusal (automatic-check-start.py).
asks() {
  python3 -c 'import sys; sys.stdout.write(open(sys.argv[1], encoding="utf-8").read().strip()[:1200])' "$SANDBOX/err" >"$SANDBOX/head"
  grep -qF "python3 $board/main/main-channel.py post --channel D0FIXTURE --new <file>" "$SANDBOX/head" || fail "$1: the main pane gets the DM command"
}
asks_nothing() {
  grep -q 'do not ask a second time' "$SANDBOX/err" || fail "$1: the refusal says not to ask again"
  ! grep -q 'main-channel.py post' "$SANDBOX/err" || fail "$1: no second request command"
}
if PANE_SPAWN_CHECK_RECEIPT="$SANDBOX/over-receipt.json" ps --check "over" "instruction" >/dev/null 2>"$SANDBOX/err"; then
  fail "a check past the Claude reserve must not open"
fi
grep -q 'past its usage reserve (week 85%, five-hour 20%)' "$SANDBOX/err" || fail "the refusal names the reserve and the reading"
grep -q 'nothing waits for one' "$SANDBOX/err" || fail "the refusal tells the caller nothing waits for a check"
grep -q 'run it yourself' "$SANDBOX/err" || fail "the refusal names the direct look"
grep -q 'pane-spawn --kind auto .*\[rc-' "$SANDBOX/err" || fail "the refusal names the read-only recheck pane"
grep -q 'Team threads, money and operations posts are the same' "$SANDBOX/err" || fail "the way out covers team, money and operations posts"
grep -q 'production write approval' "$SANDBOX/err" || fail "the other gates stay"
# 요유 2026-11-20: a usage refusal asks for an account switch or a reset ticket and never tells the caller to wait for the reset.
grep -q 'switch the account or spend a reset ticket' "$SANDBOX/err" || fail "the check refusal asks 요유 to switch the account or spend a reset ticket"
grep -q "Never put the work off until the usage reset" "$SANDBOX/err" || fail "the check refusal forbids waiting for the reset"
# 요유 2026-11-26: the first usage refusal of a window has the main pane ask once, in their backend-bot DM; later ones ask nothing.
asks "first refusal"
grep -q 'backend-bot DM' "$SANDBOX/err" || fail "the request goes to the backend-bot DM"
! grep -q 'in #0' "$SANDBOX/err" || fail "the check refusal no longer sends the request to a thread or #0"
refused "second"
asks_nothing "second refusal in the window"
quota_fixture 10 85 true # a new weekly reset time: a new window
refused "new window"
asks "new window"
quota_fixture 10 40 true
ps --check "room again" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "a check within the reserve opens"
[ ! -e "$asked_file" ] || fail "a reading within the reserve ends the window"
quota_fixture 10 85 true
refused "blocked again"
asks "blocked again after room"
# The five-hour window is its own window: a weekly reset time that moves meanwhile does not ask again.
snap="$SANDBOX/home/.local/state/agent-quota/claude.json"
five_hour_over() { # <five-hour reset epoch> <weekly reset epoch>
  jq --argjson f "$1" --argjson w "$2" '.five_hour = {used_percentage: 90, resets_at: $f} | .seven_day = {used_percentage: 40, resets_at: $w}' "$snap" >"$snap.new" && mv "$snap.new" "$snap"
}
now=$(date +%s)
five_hour_over $((now + 1000)) $((now + 300000))
refused "five-hour"
asks "five-hour window"
five_hour_over $((now + 1000)) $((now + 300100))
refused "five-hour again"
asks_nothing "same five-hour window"
five_hour_over $((now + 2000)) $((now + 300100))
refused "next five-hour"
asks "next five-hour window"
# --check from any other pane is refused later in the launcher; past the reserve it must not use up the main pane's one request.
rm -f "$asked_file"
not_main
refused "another pane"
[ ! -e "$asked_file" ] || fail "another pane's refusal does not use up the window's request"
! grep -q 'main-channel.py post' "$SANDBOX/err" || fail "another pane is not told to ask 요유"
is_main
refused "main after another pane"
asks "main after another pane"
rm -f "$board/ambient/collect.py" "$asked_file"
refused "no board"
grep -qF -- '--channel <YOYOO_DM in' "$SANDBOX/err" || fail "an unreadable DM id names where to read it"
quota_fixture 10 85 true
! grep -q 'unchecked' "$SANDBOX/err" || fail "the refusal no longer sends the report on unchecked"
! grep -vF ' pane get ' "$SANDBOX/log" || fail "a check past the reserve only asks herdr which pane called it"
rm -f "$SANDBOX/home/.local/state/agent-quota/claude.json"
quota_fixture 99 90 true
if ps --kind auto "none" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "claude-first refuses when neither runner has room"; fi
grep -q 'no session opened for usage' "$SANDBOX/err" || fail "the auto refusal says it was for usage"
grep -q 'switch the account or spend a reset ticket' "$SANDBOX/err" || fail "the auto refusal asks 요유 to switch the account or spend a reset ticket"
grep -q "Never put the work off until the usage reset" "$SANDBOX/err" || fail "the auto refusal forbids waiting for the reset"
[ ! -e "$SANDBOX/log" ] || fail "claude-first refusal opens no work pane"
if ps --check "none" "instruction" >/dev/null 2>"$SANDBOX/err"; then fail "a check past the Claude reserve (week 90%) must not open"; fi
grep -q 'week 90%' "$SANDBOX/err" || fail "the refusal names the reading"
rm -rf "$SANDBOX/home/.codex/sessions" "$SANDBOX/home/.local/state/agent-quota/claude.json"
ps --check "unknown" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "a check with unknown usage still opens"
has "--kind claude" "unknown usage keeps a check on Claude"
# api#1296: an automatic check's receipt names the runner actually opened and why, for each policy.
quota_fixture 10 40 true
receipt="$SANDBOX/runner-receipt.json"
PANE_SPAWN_CHECK_RECEIPT="$receipt" ps --check "first" "instruction" >/dev/null 2>"$SANDBOX/err"
[ "$(jq -r .runner "$receipt")" = claude ] || fail "claude-first: the receipt records Claude"
[ "$(jq -r .runner_reason "$receipt")" = "checks open only on Claude" ] || fail "claude-first: the receipt records why"
echo codex-only > "$SANDBOX/home/.local/state/agent-quota/policy"
PANE_SPAWN_CHECK_RECEIPT="$receipt" ps --check "first" "instruction" >/dev/null 2>"$SANDBOX/err"
[ "$(jq -r .runner "$receipt")" = claude ] || fail "codex policy: the receipt records Claude too"
[ "$(jq -r .runner_reason "$receipt")" = "checks open only on Claude" ] || fail "codex policy: the receipt records why"
echo claude-first > "$SANDBOX/home/.local/state/agent-quota/policy"
not_main
echo quota-aware > "$SANDBOX/home/.local/state/agent-quota/policy"
quota_fixture 10 40 true
if ps --kind auto "paid" "instruction" >/dev/null 2>"$SANDBOX/err"; then :; fi
hasnt "--kind claude" "quota-aware still excludes a paid-extra Claude account"
is_main
ps --check "legacy" "instruction" >/dev/null 2>"$SANDBOX/err"
has "--kind claude" "quota-aware checks are Claude too"
not_main

# Each regression runs independently so one broken contract does not hide the rest.
regression_failures=0
regression() {
  if ( "$@" ); then echo "ok: $1"; else regression_failures=$((regression_failures + 1)); fi
}
regression_codex_arguments() {
  echo quota-aware > "$SANDBOX/home/.local/state/agent-quota/policy"
  quota_fixture 65 40
  for args in '-c foo=bar' '-cfoo=bar' '--config=foo=bar' '-s read-only' '--sandbox=read-only' '-p myprofile' '--profile=myprofile'; do
    ps --kind auto "args" "instruction" -- $args >/dev/null 2>"$SANDBOX/err" || fail "argument launch: $args"
    has "--kind codex" "Codex-only arguments pin auto: $args"
    has "$args" "arguments retained: $args"
  done
}
regression_ambient_auto() {
  echo quota-aware > "$SANDBOX/home/.local/state/agent-quota/policy"
  quota_fixture 65 40
  mkdir -p "$SANDBOX/home/.config/munice"
  echo ambient-runner > "$SANDBOX/home/.config/munice/host-role"
  ps --kind auto "ambient" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "ambient auto launch"
  has "--kind codex" "ambient auto cannot switch to Claude"
  has "--sandbox workspace-write" "ambient auto retains sandbox"
}
regression_shell_rollback() {
  local failure=$1
  rm -f "$SANDBOX/home/.config/munice/host-role"
  echo codex-only > "$SANDBOX/home/.local/state/agent-quota/policy"
  mkdir -p "$SANDBOX/rollback"
  cp "$SUBJECT" "$SANDBOX/rollback/pane-spawn"
  cp "$(dirname "$SUBJECT")/pane-spawn-request.py" "$SANDBOX/rollback/pane-spawn-request.py"
  cp "$(dirname "$SUBJECT")/agent-quota" "$SANDBOX/rollback/agent-quota"
  [ "$failure" != syntax ] || printf 'not valid python !!!\n' > "$SANDBOX/rollback/agent-quota"
  SUBJECT="$SANDBOX/rollback/pane-spawn"
  # A directory at the log path is unwritable even when the test runs as root.
  rm -rf "$SANDBOX/home/.local/state/agent-quota/decisions.jsonl"
  mkdir "$SANDBOX/home/.local/state/agent-quota/decisions.jsonl"
  for interpreter in "$failure"; do
    mkdir -p "$SANDBOX/no-python"
    if [ "$interpreter" = missing ]; then
      printf '#!/bin/bash\ncase "$2" in */agent-quota) echo called >> "%s";; esac\nexit 97\n' "$SANDBOX/python-called" > "$SANDBOX/no-python/python3"
    elif [ "$interpreter" = system ]; then
      printf '#!/bin/bash\ncase "$2" in */agent-quota) echo called >> "%s";; esac\nexec /usr/bin/python3 "$@"\n' "$SANDBOX/python-called" > "$SANDBOX/no-python/python3"
    else
      printf '#!/bin/bash\ncase "$2" in */agent-quota) echo called >> "%s";; esac\nexec "%s" "$@"\n' "$SANDBOX/python-called" "$(command -v python3)" > "$SANDBOX/no-python/python3"
    fi
    chmod +x "$SANDBOX/no-python/python3"
    PATH="$SANDBOX/no-python:$PATH" SESSIONLESS=1 ps --kind auto "rollback" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "rollback must work before Python: $interpreter"
    has "--kind codex" "shell rollback launches Codex: $interpreter"
    [ ! -e "$SANDBOX/python-called" ] || fail "rollback invoked quota Python: $interpreter"
  done
}
regression_quota_timeout() {
  rm -f "$SANDBOX/home/.config/munice/host-role"
  echo quota-aware > "$SANDBOX/home/.local/state/agent-quota/policy"
  mkdir -p "$SANDBOX/slow-python"
  printf '#!/bin/bash\ncase "$2" in */agent-quota) echo $$ > "%s"; exec /bin/sleep 30;; esac\nexec "%s" "$@"\n' "$SANDBOX/quota-pid" "$(command -v python3)" > "$SANDBOX/slow-python/python3"
  chmod +x "$SANDBOX/slow-python/python3"
  SECONDS=0
  PATH="$SANDBOX/slow-python:$PATH" PANE_SPAWN_QUOTA_LIMIT=1 SESSIONLESS=1 ps --kind auto "timeout" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "timed out lookup must retain legacy Codex"
  [ "$SECONDS" -lt 30 ] || fail "quota timeout did not bound lookup"   # limit 1 s; the lookup sleeps 30, so a busy host's slowness cannot look like no bound
  has "--kind codex" "timeout selects legacy Codex"
  [ "$(wc -l < "$SANDBOX/err" | tr -d ' ')" = 1 ] || fail "timeout emits exactly one warning"
  grep -qi 'timed out' "$SANDBOX/err" || fail "timeout warning explains fallback"
  ! kill -0 "$(cat "$SANDBOX/quota-pid")" 2>/dev/null || fail "timed-out lookup still runs"
}
regression_check_usage_timeout() {
  rm -f "$SANDBOX/home/.config/munice/host-role"
  echo claude-first > "$SANDBOX/home/.local/state/agent-quota/policy"
  mkdir -p "$SANDBOX/slow-python"
  printf '#!/bin/bash\ncase "$2" in */agent-quota) exec /bin/sleep 30;; esac\nexec "%s" "$@"\n' "$(command -v python3)" > "$SANDBOX/slow-python/python3"
  chmod +x "$SANDBOX/slow-python/python3"
  is_main
  SECONDS=0
  PATH="$SANDBOX/slow-python:$PATH" PANE_SPAWN_QUOTA_LIMIT=1 ps --check "slow" "instruction" >/dev/null 2>"$SANDBOX/err" || fail "a check whose usage lookup timed out still opens"
  [ "$SECONDS" -lt 30 ] || fail "the check's usage lookup is bounded"
  has "--kind claude" "a timed-out lookup opens the check on Claude"
  grep -q 'usage unknown' "$SANDBOX/err" || fail "the check says its usage reading was unknown"
}
regression regression_codex_arguments
regression regression_ambient_auto
regression regression_shell_rollback missing
regression regression_shell_rollback syntax
regression regression_shell_rollback system
regression regression_shell_rollback unwritable
regression regression_quota_timeout
regression regression_check_usage_timeout
[ "$regression_failures" = 0 ] || fail "$regression_failures quota regressions"

echo "ok: pane-spawn"
