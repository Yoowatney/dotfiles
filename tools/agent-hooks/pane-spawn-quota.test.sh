#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
# Quota-aware normal workers; all data and agent starts remain synthetic.
quota_fixture() { # <Codex weekly> <Claude weekly> [extra usage enabled]
  env HOME="$SANDBOX/home" CODEX_HOME="$SANDBOX/home/.codex" CLAUDE_CONFIG_DIR="$SANDBOX/home/.claude" python3 -I - "$SUBJECT" "$1" "$2" "${3:-false}" <<'PY'
import importlib.machinery, importlib.util, json, os, pathlib, sys, time, datetime
sys.dont_write_bytecode = True
loader = importlib.machinery.SourceFileLoader('quota', str(pathlib.Path(sys.argv[1]).with_name('agent-quota')))
spec = importlib.util.spec_from_loader(loader.name, loader)
q = importlib.util.module_from_spec(spec)
loader.exec_module(q)
h = pathlib.Path.home(); now = time.time()
def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(obj)); return p
auth = write(h / '.codex/auth.json', {'auth_mode':'chatgpt', 'tokens':{'account_id':'test'}})
os.utime(auth, (now - 100, now - 100))
write(h / '.claude.json', {'oauthAccount':{'accountUuid':'test', 'hasExtraUsageEnabled':json.loads(sys.argv[4])}})
stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()
week = lambda used: {'used_percent':int(used), 'window_minutes':10080, 'resets_at':now+302400}
write(h / '.codex/sessions/quota.jsonl', {'timestamp':stamp, 'payload':{'rate_limits':{'limit_id':'codex',
    'primary':{'used_percent':20,'window_minutes':300,'resets_at':now+1000}, 'secondary':week(sys.argv[2]),
    'credits':{'has_credits':False,'unlimited':False}}}})
snap = {'account':q.identity('test'), 'machine':q.machine(), 'observed_at':now,
    'five_hour':{'used_percentage':20,'resets_at':now+1000}, 'seven_day':{'used_percentage':int(sys.argv[3]),'resets_at':now+302400}}
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
has "--kind codex" "checks ignore normal worker routing"
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
regression regression_codex_arguments
regression regression_ambient_auto
regression regression_shell_rollback missing
regression regression_shell_rollback syntax
regression regression_shell_rollback system
regression regression_shell_rollback unwritable
regression regression_quota_timeout
[ "$regression_failures" = 0 ] || fail "$regression_failures quota regressions"

echo "ok: pane-spawn"
