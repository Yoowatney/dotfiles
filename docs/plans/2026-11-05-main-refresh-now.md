# main-refresh --now Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let the main pane be restarted at any hour with no keyboard input on the laptop: ask it for a handoff, wait for the save, then `/clear` it so the next session picks the handoff up.

**Architecture:** `main-refresh` already does this once a day between 05:00 and 06:59 (launchd). A `--now` flag runs the same flow outside that window, with no once-a-day stamp, a shorter quiet wait, and a request text that names the watches to re-arm. What decides *when* (a token threshold) is another pane's job: it calls `main-refresh --now`. The handoff folder follows the same named-server rule as `handoff`, so the flow also works against a pane on the `workers` server (needed for a live test away from the real main pane).

**Tech Stack:** bash 3.2 (macOS), jq, herdr CLI, the existing `pane-tell` and `handoff` tools.

**Spec:** board item nightly_api#1028 and the lead's brief (2026-11-05): the handoff lists the open panes and their jobs, the collected decisions, the watched threads with their re-watch commands, the answers waited on, and the background jobs; the clear goes in only when the input box is empty; the new session restores the main-pane state and the background watches. Notification filtering and the token watch are out of scope (another pane).

## Global Constraints

- dotfiles is public: no local paths, names or secrets in code, comments, tests or this file.
- A queued `/clear` must never happen: the clear goes straight through `herdr agent prompt`, as today.
- The daily run keeps its behaviour and its tests.

## Review Focus

- `--now` while 요유's words sit in the box: no request, no clear (existing `--box-ready` gate; test with `BOX_FULL`).
- `--now` while the main pane works for a long time: gives up after the deadline, logged, no clear.
- `--now` run twice in a day: both run (no stamp), and neither writes the daily stamp, so 05:00 still runs.
- A message relayed between the save and the clear: no clear (existing last-message check; test with `NEW_TURN=yes`).
- Background watches: they may die at `/clear`; the request asks for each one's restart command, and the new session restarts one only if it is not already running.

---

### Task 1: `--now` in main-refresh

**Files:**
- Modify: `tools/agent-hooks/main-refresh`
- Test: `tools/agent-hooks/main-refresh.test.sh`

**Interfaces:**
- Produces: `main-refresh --now` (exit 0 always, like today; outcome in `refresh.log`). Env knobs as today; `MAIN_REFRESH_QUIET` defaults to 60 with `--now`, 600 without; `MAIN_REFRESH_DEADLINE` defaults to 1800 with `--now`.
- The request starts `[main-refresh] ` in both modes; the daily one goes on `새벽 정리입니다.`, the `--now` one `대화가 길어 지금 새로 시작합니다.`. Both name: 밑 칸과 각 칸의 일, 모아 둔 결정, 감시 중인 스레드와 재감시 명령, 기다리는 요유 답, 이 칸의 백그라운드 작업과 재시작 명령, 오늘 남은 일.

- [ ] **Step 1: failing tests** (`main-refresh.test.sh`):
  - `mr --now` at hour 09 → told `--box-ready w5:p3 claude|w5:p3 [main-refresh] 대화가|--box-ready w5:p3 claude`, cleared 1, no daily stamp.
  - `mr --now` again the same day → cleared 1 (no once-a-day stamp).
  - request text names `재감시` and `백그라운드` (grep count 1 each), in both modes.
  - `mr --now BOX_FULL=1` → no request, no clear.
  - `mr --now NEW_TURN=yes` → no clear, logged `새 대화`.
  - `mr --now` with `HERDR_SOCKET_PATH=<sandbox>/sessions/workers/herdr.sock` and the key files under `agent-handoff/workers/` → cleared 1.
- [ ] **Step 2:** run `bash tools/agent-hooks/main-refresh.test.sh` → the new checks FAIL.
- [ ] **Step 3:** implement: parse `--now`; `in_window` true with it; skip the stamp check and write; defaults above; handoff folder gets `/<session>` for `.../sessions/<session>/herdr.sock`; the `mr` helper passes arguments through.
- [ ] **Step 4:** run the test file → `all ok`.
- [ ] **Step 5:** commit `main-refresh` and its test only.

### Task 2: main pane instructions

**Files:**
- Modify: `~/.claude/skills/main-orchestrator/SKILL.md` (local, not in this repo), section Daily restart.

- [ ] Say a `[main-refresh]` message may come at any hour (`--now`); what to save (the Task 1 list, with each watch's exact restart command); that the clear follows by itself.
- [ ] After any fresh start: restart each watch the handoff lists, only when `pgrep -f` shows it is not running.

### Task 3: live test on a temporary pane

- [ ] Open a `pane-temp` Claude pane on `workers`; have it start a stand-in watch in the background.
- [ ] Run `main-refresh --now` with a scratch state folder whose `orchestrator/main` names the test pane and whose `agent-handoff` is the real one.
- [ ] Pass: `refresh.log` says it restarted; the pane's session id changed; the handoff became `.loaded`; the terminal id still matches the main file; the new session restarted the stand-in watch once (no duplicate). Record whether the old background job survived `/clear`.
