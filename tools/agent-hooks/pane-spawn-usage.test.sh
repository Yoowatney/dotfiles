#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
echo '{"result":{"workspaces":[{"workspace_id":"w2","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
is_main
# api#1051: recorded usage never selects Claude; it cannot prove a current quota block after a coupon reset.
usage() { # <used percent> <seconds until reset> [secondary]: a Codex session record as Codex writes it
  mkdir -p "$SANDBOX/home/.codex/sessions/2026/11/05"
  local week; week=$(jq -n --argjson u "$1" --argjson r "$(($(date +%s) + $2))" '{used_percent: $u, window_minutes: 10080, resets_at: $r}')
  local five='{"used_percent": 99, "window_minutes": 300, "resets_at": 1}'
  if [ -n "${3:-}" ]; then local p=$five s=$week; else local p=$week s=null; fi
  { echo '{"type":"session_meta","payload":{}}'
    jq -nc --argjson p "$p" --argjson s "$s" '{type: "event_msg", payload: {type: "token_count", rate_limits: {limit_id: "codex", primary: $p, secondary: $s}}}'; } > "$SANDBOX/home/.codex/sessions/2026/11/05/rollout-a.jsonl"
}
for percent in 0 69 70 71 99 100; do
  for window in primary secondary; do
    usage "$percent" 3600 "$([ "$window" != secondary ] || echo secondary)"
    ps --check "로그인" "돌려 보세요" 2>"$SANDBOX/err" >/dev/null
    has "--kind codex" "$percent ($window): a Codex check pane"
    has "--sandbox workspace-write --ask-for-approval never" "$percent ($window): check restrictions remain"
    hasnt "--kind claude" "$percent ($window): no automatic Claude fallback"
    SESSIONLESS=1 ps --kind auto "구현" "지시" 2>"$SANDBOX/err" >/dev/null
    has "--kind codex" "$percent ($window): an auto work pane runs Codex"
    hasnt "--sandbox workspace-write" "a work pane is no check pane"
    has "--sandbox danger-full-access" "$percent ($window): an auto work pane runs unsandboxed"
    hasnt "확인 칸입니다" "nor told it is one"
  done
done
usage 100 -60
ps --check "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "a reset week still runs Codex"
usage 100 3600; echo '{"type":"event_msg","payload":{"type":"token_count","rate_limits":null}}' >> "$SANDBOX/home/.codex/sessions/2026/11/05/rollout-a.jsonl"
mkdir -p "$SANDBOX/home/.codex/sessions/2026/11/06"; echo '{"type":"session_meta","payload":{}}' > "$SANDBOX/home/.codex/sessions/2026/11/06/rollout-b.jsonl"
touch -t "$(date -v-1H +%Y%m%d%H%M)" "$SANDBOX/home/.codex/sessions/2026/11/05/rollout-a.jsonl"
ps --check "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "stale recorded exhaustion does not prove a current block"
echo 'not JSON' > "$SANDBOX/home/.codex/sessions/2026/11/06/rollout-b.jsonl"
SESSIONLESS=1 ps --kind auto "구현" "지시" >/dev/null
has "--kind codex" "unreadable usage does not select Claude"
rm -rf "$SANDBOX/home/.codex/sessions"
ps --check "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "missing usage does not select Claude"

# Startup failures are not proof of exhausted quota. Do not silently start another agent or send the job twice.
rc=0
FAIL_START=9 ps --kind auto "실패" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "auto startup failure: expected exit 1, got $rc"
has "--kind codex" "auto startup failure stays Codex"
hasnt "--kind claude" "no fallback on an unclassified start error"
has "pane close w1:new" "a failed start removes its empty pane"
hasnt "agent prompt" "a failed start delivers no job"
grep -q "codex start was not confirmed; command was not resent" "$SANDBOX/err" || fail "reports the startup error"
rc=0
BLOCKED_START=1 SCREEN='You have hit your usage limit' ps --check "막힘" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "an unstructured dialog must fail, got $rc"
hasnt "--kind claude" "dialog text alone cannot prove a current quota block"
grep -qF 'You have hit your usage limit' "$SANDBOX/err" || fail "reports the actual dialog"
# Keep macOS Bash coverage even when the test driver finds Homebrew Bash on PATH.
for readiness_shell in "$(command -v bash)" /bin/bash; do
  rc=0
  PANE_SPAWN_TEST_SHELL="$readiness_shell" NOT_READY=1 ps --kind auto "준비 실패" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
  [ "$rc" = 1 ] || fail "readiness timeout ($readiness_shell): expected exit 1, got $rc"
  hasnt "--kind claude" "readiness timeout does not select Claude"
  has "pane close w1:new" "a pane that never became ready is removed"
  hasnt "agent prompt" "an unready pane gets no job"
  grep -q 'codex never became ready' "$SANDBOX/err" || fail "reports readiness timeout"
done
rc=0
FAIL_PROMPT=1 ps --kind auto "전달 실패" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "prompt failure: expected exit 1, got $rc"
hasnt "--kind claude" "prompt failure does not select Claude"
has "pane close w1:new" "a rejected delivery removes its pane"
hasnt "agent wait" "rejected delivery does not wait for work"
grep -q 'the instruction was not delivered' "$SANDBOX/err" || fail "reports prompt failure"
rc=0
FAIL_WAIT=1 ps --kind auto "무응답" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "auto unseen work: expected exit 1, got $rc"
hasnt "--kind claude" "ambiguous delivery never starts a duplicate Claude job"
hasnt "pane close" "an accepted job may still be running"
[ "$(grep -c 'agent prompt w1:new 지시' "$SANDBOX/log")" = 1 ] || fail "auto delivers only once"
usage 71 3600
ps --check --kind codex "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "a kind the caller names wins"
# Automatic checks may be safely restarted only after the failed pane is closed.
receipt="$SANDBOX/check-receipt.json"
PANE_SPAWN_CHECK_RECEIPT="$receipt" ps --check --kind codex "receipt" "instruction" >/dev/null
[ "$(jq -r .status "$receipt")" = started ] || fail "confirmed work records a started receipt"
if PANE_SPAWN_CHECK_RECEIPT="$receipt" EXIT_START=1 ps --check --kind codex "receipt failure" "instruction" >/dev/null 2>&1; then fail "failed check must exit nonzero"; fi
[ "$(jq -r .status "$receipt")" = closed ] || [ "$(jq -r .status "$receipt")" = not-opened ] || fail "failed startup leaves cleanup evidence"
if PANE_SPAWN_CHECK_RECEIPT="$receipt" WAIT_STATUS=blocked ps --check --kind codex "receipt blocked" "instruction" >/dev/null 2>&1; then fail "blocked check must fail"; fi
has "pane close w1:new" "opt-in read-only check cleanup precedes retry after ambiguous delivery"
[ "$(jq -r .status "$receipt")" = closed ] || fail "ambiguous check delivery has a confirmed cancellation receipt"
if PANE_SPAWN_CHECK_RECEIPT="$receipt" FAIL_WORKERS_TAB=1 ps --check --kind codex "missing creation response" "instruction" >/dev/null 2>&1; then fail "missing creation response must fail"; fi
[ "$(jq -r .status "$receipt")" = creating ] || fail "missing creation response must remain uncertain"
hasnt "pane close" "an unknown created address cannot be guessed for cleanup"
workers_up false
rm -f "$receipt"
if PANE_SPAWN_CHECK_RECEIPT="$receipt" ps --check --kind codex "workers down" "instruction" >/dev/null 2>&1; then fail "unavailable workers must fail"; fi
[ ! -e "$receipt" ] || fail "failure before launcher setup creates no receipt"
workers_up
ps --why skill "조사" "지시" >/dev/null
has "--kind claude" "an ordinary pane never reads the usage"
# api#1036: the main pane opens a Claude work pane only for a reason Codex cannot cover. Slack posts belong to the main pane,
# so a Slack reason is refused. Since 요유 2026-11-07 a Codex work pane runs GCP commands too, so a GCP reason is refused as
# well. Check panes and other callers stay as they were.
refused() { # <case> [pane-spawn options]
  local why=$1; shift
  if ps "$@" "구현" "지시" 2>"$SANDBOX/err" >/dev/null; then fail "$why: the main pane's Claude pane needs a reason"; fi
  grep -qF -- "--kind auto" "$SANDBOX/err" || fail "$why: points to --kind auto: $(cat "$SANDBOX/err")"
  grep -qF -- "--why" "$SANDBOX/err" || fail "$why: names --why: $(cat "$SANDBOX/err")"
  hasnt "tab create" "$why: no pane opens"
  hasnt "pane-label --lead" "$why: nothing is marked"
}
usage 50 3600
refused "no kind, no reason"
refused "claude, no reason" --kind claude
refused "an empty reason" --kind claude --why ""
refused "a blank reason" --why "  "
ps --kind claude --why skill "UI 작업" "지시" >/dev/null; has "--kind claude" "the main pane accepts a reason Codex cannot cover"
for reason in gcp GCP "gcloud로 배포 상태 확인" "bq 조회" "BigQuery 집계"; do
  for kind_args in default claude; do
    opts=(); [ "$kind_args" != claude ] || opts=(--kind claude)
    rc=0
    ps ${opts[@]+"${opts[@]}"} --why "$reason" "GCP 확인" "지시" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
    [ "$rc" = 2 ] || fail "the main pane rejects --why [$reason] ($kind_args): expected exit 2, got $rc"
    grep -qF -- "GCP 명령도 Codex 칸이 합니다" "$SANDBOX/err" || fail "GCP refusal says Codex runs it: $(cat "$SANDBOX/err")"
    grep -qF -- "--kind auto" "$SANDBOX/err" || fail "GCP refusal points to --kind auto: $(cat "$SANDBOX/err")"
    hasnt "agent start" "a GCP reason starts no agent"
  done
done
ps --check --kind claude --why gcp "로그인" "돌려 보세요" >/dev/null; has "--kind claude" "a check pane skips the GCP reason check"
# Slack posts belong to the main pane; mentioning Slack cannot justify a Claude work pane there.
for reason in slack "Slack에 답글" "슬랙에 답글" "SLACK에 답글"; do
  for kind_args in default claude; do
    opts=(); [ "$kind_args" != claude ] || opts=(--kind claude)
    rc=0
    ps ${opts[@]+"${opts[@]}"} --why "$reason" "Slack 답글" "지시" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
    [ "$rc" = 2 ] || fail "the main pane rejects --why [$reason] ($kind_args): expected exit 2, got $rc"
    grep -qF -- "Slack 글은 메인 칸이 올립니다: 밑 칸은 결과를 보고하고 --kind auto로 여세요" "$SANDBOX/err" || fail "Slack refusal explains who posts: $(cat "$SANDBOX/err")"
    hasnt "tab create" "a Slack reason opens no pane"
    hasnt "pane-label --lead" "a Slack reason marks nothing"
    hasnt "agent start" "a Slack reason starts no agent"
  done
done
ps --why "Claude 전용 스킬 필요" "배포 확인" "지시" >/dev/null; has "--kind claude" "a reason opens Claude: the default kind"
ps --why "작업 폴더 밖 파일 수정" "설정 수정" "지시" >/dev/null; has "--kind claude" "another reason still opens Claude"
SESSIONLESS=1 ps --kind codex "구현" "지시" >/dev/null; has "--kind codex" "Codex needs no reason"
SESSIONLESS=1 ps --kind auto "구현" "지시" >/dev/null; has "--kind codex" "auto needs no reason"
usage 71 3600
ps --kind auto "구현" "지시" 2>/dev/null >/dev/null; has "--kind codex" "auto at high usage still needs no reason"
ps --check --kind claude "로그인" "돌려 보세요" >/dev/null; has "--kind claude" "a check pane needs no reason"
ps --check --kind claude --why "Slack에 답글" "로그인" "돌려 보세요" >/dev/null; has "--kind claude" "a check pane skips the Slack reason check"
not_main
CALLER_LABEL="🟢 👑 api#1 로그인" ps "설계" "할 일" >/dev/null; has "--kind claude" "a lead other than the main pane needs no reason"
CALLER_LABEL="🟢 👑 api#1 로그인" ps --why slack "설계" "할 일" >/dev/null; has "--kind claude" "another lead's Slack reason is unchanged"
CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시" >/dev/null; has "--kind claude" "nor does a pane under a lead"
is_main
rm -rf "$SANDBOX/home/.codex"


echo "ok: pane-spawn recorded usage and reasons"
