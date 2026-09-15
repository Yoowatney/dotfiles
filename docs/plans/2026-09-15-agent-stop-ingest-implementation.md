# Claude Code/Codex Wiki Ingest Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code와 Codex의 내부 훅만으로 공유 인제스트를 실행하고, Codex가 직접 work-log를 남기지 않은 비 trivial 턴을 자동 복구한다.

**Architecture:** Claude와 Codex의 기존 `stop-ingest.sh` 경로는 유지하되 dotfiles의 서로 다른 진입점으로 심볼릭 링크한다. Claude 진입점은 공유 쿨다운/잠금 인제스터를 바로 실행하고, Codex 진입점은 `UserPromptSubmit` 기준점과 `Stop` payload를 이용해 work-log 누락을 복구한 다음 같은 공유 인제스터를 실행한다.

**Tech Stack:** Bash 3.2, jq, macOS `stat`, coreutils `timeout`, Claude Code CLI, JSON hook configuration

**Spec:** `docs/plans/2026-09-15-agent-stop-ingest-design.md`

## Global Constraints

- launchd 서비스나 plist를 설치·로드하지 않는다.
- Codex transcript의 JSONL 스키마를 파싱하지 않고 바이트 구간을 복구 모델 입력으로만 사용한다.
- Codex Stop 훅은 stdout을 비운다.
- 기존 Claude/Codex 알림 및 Superset 훅을 보존한다.
- 라이브 JSON 설정은 공개 저장소에 복사하지 않는다.
- 모든 설치 작업은 재실행 가능하고 멱등적이어야 한다.

## 파일 구조

- `tools/agent-hooks/run-llm-wiki-ingest.sh`: 두 에이전트가 공유하는 쿨다운·동시 실행 잠금·인제스트 실행
- `tools/agent-hooks/run-llm-wiki-ingest.test.sh`: 공유 실행기의 셸 회귀 테스트
- `tools/agent-hooks/codex-work-log-guard.sh`: Codex 턴 기준점 저장과 누락 work-log 복구
- `tools/agent-hooks/codex-work-log-guard.test.sh`: 기준점·복구·trivial·오류 격리 테스트
- `tools/agent-hooks/codex-stop-ingest.sh`: Stop payload를 복구기와 공유 인제스터에 순서대로 전달
- `tools/agent-hooks/install-llm-wiki-hooks.sh`: 심볼릭 링크와 Codex `UserPromptSubmit` 훅을 멱등 설치
- `tools/agent-hooks/install-llm-wiki-hooks.test.sh`: 임시 HOME을 사용한 설치 회귀 테스트
- `tools/claude/llm-wiki-ingest.sh`: launchd 설명을 내부 Stop 훅 설명으로 교체
- `tools/claude/com.yoyoo.wiki-ingest.plist`: 더 이상 지원하지 않는 launchd 경로이므로 삭제

---

### Task 1: 공유 인제스트 실행기

**Files:**
- Create: `tools/agent-hooks/run-llm-wiki-ingest.sh`
- Create: `tools/agent-hooks/run-llm-wiki-ingest.test.sh`

**Interfaces:**
- Consumes: `LLM_WIKI_STATE_DIR`, `LLM_WIKI_INGEST_SCRIPT`, `LLM_WIKI_COOLDOWN_SEC`, `LLM_WIKI_LOCK_STALE_SEC` 선택 환경 변수
- Produces: 인자와 stdin 없이 호출 가능한 `run-llm-wiki-ingest.sh`, 항상 빈 stdout과 종료 코드 0

- [ ] **Step 1: 첫 실행·쿨다운·잠금·실패 격리 테스트 작성**

```bash
run_hook() {
  HOME="$sandbox" \
  LLM_WIKI_STATE_DIR="$sandbox/state" \
  LLM_WIKI_INGEST_SCRIPT="$sandbox/fake-ingest.sh" \
  LLM_WIKI_COOLDOWN_SEC=3600 \
  "$subject"
}

run_hook
run_hook
assert_eq 1 "$(wc -l < "$sandbox/runs")" "cooldown suppresses duplicate"

mkdir -p "$sandbox/state/.stop-ingest.lock"
touch -t 209901010000 "$sandbox/state/.stop-ingest.lock"
run_hook
assert_eq 1 "$(wc -l < "$sandbox/runs")" "active lock suppresses run"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `bash tools/agent-hooks/run-llm-wiki-ingest.test.sh`

Expected: FAIL because `run-llm-wiki-ingest.sh` does not exist.

- [ ] **Step 3: 최소 공유 실행기 구현**

```bash
#!/usr/bin/env bash
set -uo pipefail

state_dir="${LLM_WIKI_STATE_DIR:-$HOME/.local/share/llm-wiki}"
ingest_script="${LLM_WIKI_INGEST_SCRIPT:-$state_dir/ingest.sh}"
cooldown_sec="${LLM_WIKI_COOLDOWN_SEC:-3600}"
stale_sec="${LLM_WIKI_LOCK_STALE_SEC:-7200}"
cooldown_file="$state_dir/.last-stop-ingest"
lock_dir="$state_dir/.stop-ingest.lock"
log_file="$state_dir/ingest.log"

mkdir -p "$state_dir"
now="$(date +%s)"
if [[ -d "$lock_dir" ]]; then
  lock_mtime="$(stat -f %m "$lock_dir" 2>/dev/null || printf '0')"
  if (( now - lock_mtime >= stale_sec )); then
    rmdir "$lock_dir" 2>/dev/null || exit 0
  fi
fi
mkdir "$lock_dir" 2>/dev/null || exit 0
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT INT TERM

if [[ -f "$cooldown_file" ]]; then
  last_run="$(stat -f %m "$cooldown_file" 2>/dev/null || printf '0')"
  (( now - last_run < cooldown_sec )) && exit 0
fi
touch "$cooldown_file"
[[ -x "$ingest_script" ]] && "$ingest_script" >>"$log_file" 2>&1 || true
exit 0
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `bash tools/agent-hooks/run-llm-wiki-ingest.test.sh`

Expected: PASS for first execution, cooldown, active/stale lock, missing script, failing script, and empty stdout.

- [ ] **Step 5: 커밋**

```bash
git add tools/agent-hooks/run-llm-wiki-ingest.sh tools/agent-hooks/run-llm-wiki-ingest.test.sh
git commit -m "feat: share wiki ingest trigger across agents"
```

### Task 2: Codex work-log 누락 감지 및 복구

**Files:**
- Create: `tools/agent-hooks/codex-work-log-guard.sh`
- Create: `tools/agent-hooks/codex-work-log-guard.test.sh`
- Create: `tools/agent-hooks/codex-stop-ingest.sh`

**Interfaces:**
- Consumes: subcommand `begin|finish` and Codex hook JSON on stdin; private config의 `VAULT`; optional `CLAUDE_BIN`, `CLAUDE_TIMEOUT`
- Produces: `begin`이 `codex-turns/<session>.json` 기준점 생성; `finish`가 필요할 때만 오늘 work-log에 검증된 Markdown 항목 추가; 항상 빈 stdout

- [ ] **Step 1: 기준점·기록 존재·복구·trivial 테스트 작성**

```bash
begin_payload='{"session_id":"session-1","transcript_path":"TRANSCRIPT","cwd":"/Users/test/work/munice","hook_event_name":"UserPromptSubmit"}'
stop_payload='{"session_id":"session-1","turn_id":"turn-1","transcript_path":"TRANSCRIPT","cwd":"/Users/test/work/munice","hook_event_name":"Stop","last_assistant_message":"done"}'

printf '%s' "$begin_payload" | run_guard begin
printf '\n## Existing agent entry\n' >> "$worklog"
printf '%s' "$stop_payload" | run_guard finish
assert_file_not_contains "$worklog" "Recovered entry"

printf '%s' "$begin_payload" | run_guard begin
FAKE_CLAUDE_RESULT=entry printf '%s' "$stop_payload" | run_guard finish
assert_file_contains "$worklog" "## Recovered entry"
```

Tests also cover malformed JSON, absent transcript, `NO_WORK`, invalid model output, and a 2 MiB transcript-segment cap.

- [ ] **Step 2: 테스트 실패 확인**

Run: `bash tools/agent-hooks/codex-work-log-guard.test.sh`

Expected: FAIL because the guard scripts do not exist.

- [ ] **Step 3: `begin` 기준점 저장 구현**

```bash
payload="$(cat)"
session_id="$(jq -r '.session_id // empty' <<<"$payload")"
transcript_path="$(jq -r '.transcript_path // empty' <<<"$payload")"
cwd="$(jq -r '.cwd // empty' <<<"$payload")"
safe_session="$(printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-')"
today="$(date '+%Y-%m-%d')"
worklog="$VAULT/work-logs/$today.md"
transcript_size="$(stat -f %z "$transcript_path" 2>/dev/null || printf '0')"
worklog_size="$(stat -f %z "$worklog" 2>/dev/null || printf '0')"
jq -n --arg session_id "$session_id" --arg transcript_path "$transcript_path" \
  --arg cwd "$cwd" --arg worklog "$worklog" \
  --argjson transcript_size "$transcript_size" --argjson worklog_size "$worklog_size" \
  '{session_id:$session_id,transcript_path:$transcript_path,cwd:$cwd,worklog:$worklog,transcript_size:$transcript_size,worklog_size:$worklog_size}' \
  >"$state_file.tmp"
mv "$state_file.tmp" "$state_file"
```

- [ ] **Step 4: `finish` 누락 판정 및 복구 구현**

```bash
baseline_worklog_size="$(jq -r '.worklog_size' "$state_file")"
current_worklog_size="$(stat -f %z "$worklog" 2>/dev/null || printf '0')"
if (( current_worklog_size > baseline_worklog_size )); then
  rm -f "$state_file"
  exit 0
fi

start_byte="$(( $(jq -r '.transcript_size' "$state_file") + 1 ))"
tail -c "+$start_byte" "$transcript_path" 2>/dev/null | tail -c 2097152 >"$turn_file"
prompt="$(build_recovery_prompt "$turn_file" "$worklog" "$project" "$last_assistant_message")"
if timeout "$claude_timeout" "$claude_bin" -p "$prompt" \
  --permission-mode bypassPermissions --no-session-persistence </dev/null >"$result_file" 2>>"$log_file"; then
  if ! grep -q 'NO_WORK' "$result_file" &&
     grep -q '^## ' "$result_file" &&
     grep -q '^- \*\*프로젝트:\*\*' "$result_file" &&
     grep -q '^- \*\*상황:\*\*' "$result_file" &&
     grep -q '^- \*\*판단:\*\*' "$result_file" &&
     grep -q '^- \*\*결과:\*\*' "$result_file" &&
     grep -q '^- \*\*PR/참고:\*\*' "$result_file"; then
    mkdir -p "$(dirname "$worklog")"
    { printf '\n'; cat "$result_file"; printf '\n'; } >>"$worklog"
  fi
fi
rm -f "$state_file" "$turn_file" "$result_file"
```

복구 프롬프트는 기존 skip 기준을 그대로 명시한다: typo/lint/formatting, 버전 범프만 수행, config 한 줄 변경, 결론 없는 조사 작업은 `NO_WORK`다. 출력은 한국어이고 프로젝트는 `cwd`의 `/work/<name>`에서 추론한다.

- [ ] **Step 5: Codex Stop 조정자 구현**

```bash
#!/usr/bin/env bash
set -uo pipefail
payload="$(cat)"
printf '%s' "$payload" | "$(dirname "$0")/codex-work-log-guard.sh" finish >/dev/null 2>&1 || true
"$(dirname "$0")/run-llm-wiki-ingest.sh" >/dev/null 2>&1 || true
exit 0
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `bash tools/agent-hooks/codex-work-log-guard.test.sh`

Expected: PASS and stdout remains empty for all cases.

- [ ] **Step 7: 커밋**

```bash
git add tools/agent-hooks/codex-work-log-guard.sh tools/agent-hooks/codex-work-log-guard.test.sh tools/agent-hooks/codex-stop-ingest.sh
git commit -m "feat: recover missing Codex work logs"
```

### Task 3: 내부 훅 멱등 설치

**Files:**
- Create: `tools/agent-hooks/install-llm-wiki-hooks.sh`
- Create: `tools/agent-hooks/install-llm-wiki-hooks.test.sh`
- Modify: `tools/claude/llm-wiki-ingest.sh:1-8`
- Delete: `tools/claude/com.yoyoo.wiki-ingest.plist`

**Interfaces:**
- Consumes: optional `AGENT_HOOKS_ROOT`, `CLAUDE_HOME`, `CODEX_HOME`
- Produces: 세 개의 라이브 심볼릭 링크와 Codex `UserPromptSubmit` hook entry; repeated invocation produces identical JSON

- [ ] **Step 1: 설치 보존·멱등성 테스트 작성**

```bash
run_installer
first_hash="$(md5 -q "$sandbox/.codex/hooks.json")"
run_installer
second_hash="$(md5 -q "$sandbox/.codex/hooks.json")"
assert_eq "$first_hash" "$second_hash" "installer is idempotent"

jq -e '.hooks.Stop[] | select(.hooks[].command | contains("stop-notification.sh"))' "$codex_hooks"
jq -e '.hooks.UserPromptSubmit[] | select(.hooks[].command | contains("work-log-guard.sh"))' "$codex_hooks"
test "$(readlink "$sandbox/.claude/hooks/stop-ingest.sh")" = "$root/run-llm-wiki-ingest.sh"
test "$(readlink "$sandbox/.codex/hooks/stop-ingest.sh")" = "$root/codex-stop-ingest.sh"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `bash tools/agent-hooks/install-llm-wiki-hooks.test.sh`

Expected: FAIL because installer does not exist.

- [ ] **Step 3: 멱등 설치기 구현**

```bash
mkdir -p "$claude_home/hooks" "$codex_home/hooks"
ln -sfn "$root/run-llm-wiki-ingest.sh" "$claude_home/hooks/stop-ingest.sh"
ln -sfn "$root/codex-stop-ingest.sh" "$codex_home/hooks/stop-ingest.sh"
ln -sfn "$root/codex-work-log-guard.sh" "$codex_home/hooks/work-log-guard.sh"
guard_command="'$codex_home/hooks/work-log-guard.sh' begin"
jq --arg command "$guard_command" '
  .hooks //= {} |
  .hooks.UserPromptSubmit //= [] |
  if any(.hooks.UserPromptSubmit[]?; any(.hooks[]?; .command == $command))
  then .
  else .hooks.UserPromptSubmit += [{"hooks":[{"type":"command","command":$command,"timeout":10}]}]
  end
' "$codex_home/hooks.json" >"$codex_home/hooks.json.tmp"
jq empty "$codex_home/hooks.json.tmp"
chmod "$(stat -f %Lp "$codex_home/hooks.json")" "$codex_home/hooks.json.tmp"
mv "$codex_home/hooks.json.tmp" "$codex_home/hooks.json"
```

- [ ] **Step 4: launchd 설명 및 유물 제거**

Change the ingest header to:

```bash
# ingest.sh — LLM Wiki auto-ingest script
# Triggered by Claude Code and Codex Stop hooks. Three phases:
```

Delete `tools/claude/com.yoyoo.wiki-ingest.plist` so a future setup cannot accidentally reinstall the unsupported timer.

- [ ] **Step 5: 테스트 통과 확인**

Run: `bash tools/agent-hooks/install-llm-wiki-hooks.test.sh`

Expected: PASS, existing hooks preserved, exactly one guard hook, stable second-run hash, correct symlinks.

- [ ] **Step 6: 전체 단위 테스트 확인**

Run: `for test_file in tools/agent-hooks/*.test.sh; do bash "$test_file"; done`

Expected: All tests PASS.

- [ ] **Step 7: 커밋**

```bash
git add tools/agent-hooks tools/claude/llm-wiki-ingest.sh tools/claude/com.yoyoo.wiki-ingest.plist
git commit -m "feat: install hook-only wiki ingest workflow"
```

### Task 4: 라이브 설치 및 검증

**Files:**
- Modify: `~/.codex/hooks.json` through installer
- Replace with symlink: `~/.claude/hooks/stop-ingest.sh`
- Replace with symlink: `~/.codex/hooks/stop-ingest.sh`
- Create symlink: `~/.codex/hooks/work-log-guard.sh`

**Interfaces:**
- Consumes: completed installer and live settings
- Produces: new Codex sessions and Claude Code sessions use only internal hooks

- [ ] **Step 1: 라이브 설치 실행**

Run: `bash tools/agent-hooks/install-llm-wiki-hooks.sh`

Expected: three link status messages and one Codex configuration status message; no launchctl command.

- [ ] **Step 2: JSON 및 심볼릭 링크 검증**

Run: `jq empty ~/.codex/hooks.json && jq '[.hooks.UserPromptSubmit[] | select(any(.hooks[]; .command | contains("work-log-guard.sh")))] | length' ~/.codex/hooks.json`

Expected: valid JSON and count `1`.

Run: `readlink ~/.claude/hooks/stop-ingest.sh; readlink ~/.codex/hooks/stop-ingest.sh; readlink ~/.codex/hooks/work-log-guard.sh`

Expected: all paths point into `tools/agent-hooks/` in the worktree-independent dotfiles root.

- [ ] **Step 3: fixture hook 실행 검증**

Run the installed `UserPromptSubmit` and `Stop` commands with an isolated temporary HOME, fake config, fake transcript, fake Claude CLI, and fake ingest script.

Expected: exit code 0, empty stdout, a recovered work-log entry for a missing non-trivial turn, and exactly one ingest run.

- [ ] **Step 4: launchd 미사용 검증**

Run: `launchctl print gui/$(id -u)/com.yoyoo.wiki-ingest`

Expected: service not found.

- [ ] **Step 5: 코드 리뷰 실행**

Review `origin/main..HEAD` for correctness, shell portability, destructive behavior, and preservation of existing hook configuration. Apply any required fixes and rerun all tests.

- [ ] **Step 6: 최종 검증**

Run: `bash -n tools/agent-hooks/*.sh tools/claude/llm-wiki-ingest.sh`

Run: `for test_file in tools/agent-hooks/*.test.sh; do bash "$test_file"; done`

Run: `git diff --check origin/main..HEAD`

Expected: all commands exit 0.

- [ ] **Step 7: 작업 로그 기록**

오늘 work-log에 hook-only 구조, Codex 누락 복구 판단, launchd 제거 상태, 검증 결과를 한 항목으로 추가한다.

- [ ] **Step 8: 최종 상태 커밋**

```bash
git add -A
git commit -m "test: verify agent wiki ingest hooks"
```
