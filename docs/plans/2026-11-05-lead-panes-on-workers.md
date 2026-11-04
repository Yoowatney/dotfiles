# Ordinary leads' panes on the workers server: implementation plan

> For agentic workers: executed natively in the pane that wrote it (the main pane asked for the build). Steps use `- [ ]`.

**Goal:** A pane an ordinary lead opens with `pane-spawn` runs on the hidden `workers` herdr server, so 요유's tab holds only the lead, and nothing about reports, alerts, marks or orphans gets worse for it.

**Architecture:** `pane-spawn` picks the server; the terminal id in the agent name (`spawn-<opener terminal>-…`) stays the one link from a pane to its lead across servers. `workers-watch`, `auto-tab-close`, `yoyoo-share-guard` and the status line read that link on both servers.

**Tech stack:** bash, jq, python3 (macOS 3.9 compatible), herdr CLI. Tests: `pane-spawn.test.sh`, `statusline-command.test.sh`, `--selftest` of each python file.

**Spec:** the main pane's instruction of 2026-11-05 (요유's decision); delegating lookups or checks to panes is out of scope.

## Global constraints

- Ordinary leads keep checking results themselves; 요유 talks only to leads.
- Stay out of pD's files (`stop-notification.sh`, agent-board dispatch and ambient).
- `auto-tab-close.py`, `yoyoo-share-guard.py`, the main-orchestrator skill and the commit that turns this on wait until the main pane says pD and pE landed. `pane-spawn`, `pane-spawn.test.sh`, `herdr_peers.py`, `pane-tell` are also mid-edit there: all work happens in this worktree and is rebased.
- `~/.dotfiles` is public: no secrets, no customer data.
- `git commit` and `git push` as separate commands. Real runs on 회사맥 only, temporary panes closed afterwards.

## Decisions (mine, for the main pane to overrule)

| Case | Result |
|---|---|
| Lead on the default server, not `🫡` | workers, in the tab of its newest live pane there, else a new tab named for the lead's task. `--new-tab`: always a new tab, same name |
| `--same-tab` | Accepted, means the default above. Main pane: still bad usage |
| `🫡` caller on any server | Stays beside itself, as today. A pane with an agent name that 요유 made a lead (👑) does open on workers, so `held()` reads both servers |
| Caller on another named server (`resume`, `workers`) | Stays on its server. Only `default` and `workers` are peers; a personal server is left alone |
| `MUNICE_AUTO_DISPATCH` / `MUNICE_AMBIENT` | Stays in the job tab. `pane-tell` refuses job panes as receivers, so a hidden child could reach nobody; 집맥's ambient has no workers anyway |
| workers absent, down, no workspace, or no pane | Ordinary lead: its own tab as before, one line on stderr, exit 0. Main pane: fails as before |
| Alert for a hidden pane | Its opener, else the main pane, else a herdr notification on default |
| Opener on no server, or its pane left at a shell (orphan) | One alert by the same route; later alerts and the report go to the main pane |
| A list that cannot be read when a report goes up | No sideways send and no `reported` mark: the report stays in the file, the pane stays open, and the watch's 15-minute alert reaches the opener and names the file |
| Report from a workers pane whose opener closed | Same-tab 👑, then the main pane, then the day file only. Never "the only 👑 in the workspace": on workers every lead shares one workspace |

요유's decisions on the two questions, passed on by the main pane (2026-11-05):

| Case | Result |
|---|---|
| Orphan, stopped, report sent | Closed by the watch an hour after the stop or the lead's close, whichever came last, with one alert. A pane at work, marked 🔴, without a report or with a live child stays |
| Orphan of a Home lead | Its alerts and report go to the main pane, each saying to keep it off company Slack and the board. `pane-spawn` marks it in the agent name (`spawn-<terminal>-h…`), passed on by a 🫡 caller; the report's note is part of Task 4 |

## Review focus

1. A running pane whose lead closed, with no main pane: 요유 gets one notification, not one a minute. Pinned in the watch selftest.
2. A list that cannot be read (default server slow) must not read as "lead closed". Pinned in the watch selftest.
3. A failed `tab create` on workers must leave no workers socket for the local fallback. Pinned in `pane-spawn.test.sh`.
4. Two leads at once, same pane id on both servers: each report and alert reaches its own opener. Pinned in the auto-tab-close and watch selftests.
5. The main pane's morning list shows other leads' panes: it must not close them. One sentence in the skill.

---

### Task 1: alerts follow the opener

**Files:** modify `tools/agent-hooks/herdr_peers.py`, `tools/agent-hooks/workers-watch.py`.

**Produces:** `herdr_peers.opener(name) -> str` (`term_<id>` or ""), `herdr_peers.all_panes(env) -> dict | None` (terminal id -> (address, pane); None when default or a running server cannot be read).

- [ ] Selftests first: `opener`, `all_panes`; watch routes blocked, idle, ended and gone alerts to the opener; falls to the main pane when the opener refuses; orphan alert once, surviving a state change; no main and no opener: one notification; failed notification retried; unreadable default list: nothing said, records kept; no main, no records, workers absent: silent.
- [ ] Run `python3 workers-watch.py --selftest`: expect failure.
- [ ] Implement `tell(msg, *terms)` in `tick`: each term, then main, then `notify`. Records gain `opener` and `orphaned`.
- [ ] Selftests pass; commit.

### Task 2: the lead's status line shows its hidden panes

**Files:** modify `tools/claude/statusline-command.sh`, `tools/claude/statusline-command.test.sh`.

- [ ] Test: a lead on default with a pane on workers shows its `↳` line; a workers pane does not read workers twice; workers absent changes nothing.
- [ ] See it fail, implement, see it pass, commit.

### Task 3: pane-spawn moves ordinary leads (commit last)

**Files:** modify `tools/agent-hooks/pane-spawn`, `tools/agent-hooks/pane-spawn.test.sh`.

- [ ] Tests per the decisions table, including the tab name and joining an existing tab.
- [ ] See them fail, implement `open_pane` run in a subshell for workers so the socket cannot leak, see them pass.
- [ ] Rebase on pE's `--check`; do not land before Tasks 4 and 5.

### Task 4: reports and sweeps cross servers (after pE landed)

**Files:** modify `tools/agent-hooks/auto-tab-close.py`, `tools/agent-hooks/yoyoo-share-guard.py`.

- [ ] Selftests: closed lead → main pane with a head that says so; closed lead, no main → file only, no wrong 👑; several leads → each its own; `--sweep` lists and closes a finished workers pane by address; share-guard reads workers for any pane, not only the main pane.
- [ ] See them fail, implement, see them pass.

### Task 5: rules

**Files:** `~/.codex/AGENTS.md` (Pane label), `tools/agent-hooks/pane-spawn` header, main-orchestrator skill (one sentence), memory `user_one_task_per_tab.md`, `user_panel_means_herdr_pane.md`, `MEMORY.md`.

- [ ] Replace the same-tab sentence instead of appending; the file is over 32 KiB already.

These files take effect the moment they are saved and have no branch, so the text waits here and goes in with the landing, in the same step as the push. Each edit is re-read first: other panes change these files.

`~/.codex/AGENTS.md`, Pane label, the `pane-spawn` bullet. From `It marks the calling pane 👑` to `opens a tab by itself.` becomes:

> It marks the calling pane 👑 unless it is already 🫡, opens the pane, names it 🫡, starts the agent (`--kind claude`, the default, or `--kind codex`), waits for its session and hands over the instruction. A lead's panes open on the hidden `workers` herdr server, so 요유's tab holds only the lead (요유 2026-11-05): they share a tab there, named for the lead's work, which `herdr session attach workers` shows, and the lead gets another once it is full or with `--new-tab`, which is for a job this lead does not follow. It prints `workers/<id>`: `pane-tell workers/<id>`, `herdr --session workers agent read <id>`, `herdr --session workers pane close <id>`. With `workers` down, stuck or absent (집맥) the pane opens beside the lead and stderr says so. A 🫡 caller, a job tab and a pane on another named server open beside themselves. `workers-watch` tells the opener when a pane sits in a dialog or stops with no report. A pane whose lead closed, or whose lead's agent ended, keeps running: its alerts and report go to the main pane, saying it stands in (with no main pane, a notification and the report file). Once it has stopped and reported, with no 🔴, 👑 or live child, it closes an hour later. What a Home lead's pane says that way stays off company Slack and the board (요유 2026-11-05).

`~/.codex/AGENTS.md`, Communication, `Only a session a job opened`:

- `else the only 👑 in the workspace; with several` becomes `else the only 👑 in the workspace (on `workers`, shared by every lead, the main pane instead, saying it stands in); with several`.
- `` `auto-tab-close.py --sweep` lists them `` becomes `` `auto-tab-close.py --sweep` lists them on both servers ``.

`~/.claude/skills/main-orchestrator/SKILL.md`, Panes:

- After `List: herdr --session workers agent list.` add: `Every lead's panes run there. Yours have agent names starting spawn-<this pane's terminal id>-; leave the others alone unless a report or a workers-watch alert names them.`
- At the end of the `workers-watch` bullet add: `리드가 닫혔습니다 means its lead closed or that lead's agent ended, and the pane is yours: read it and finish it, or leave it, since a pane that stopped and reported closes an hour later unless it is 🔴 or 👑 or has a live child. A report or alert saying 집 일 칸의 내용입니다 stays off company Slack and the board.`

Memory (`user_one_task_per_tab.md`, `user_panel_means_herdr_pane.md`, `MEMORY.md`), in Korean: 리드의 밑 칸은 `workers` 서버로 가고 요유의 탭에는 리드만 남는다(11/05, 11/04의 "한 탭"을 대체). 진행은 리드의 상태 줄과 보고로 보고, 보고 싶으면 `herdr session attach workers`. `--new-tab`은 별개의 일. 집맥에는 `workers`가 없어 옆 칸으로 연다. 한 일의 설계, 구현, 리뷰는 workers의 한 탭에 함께 있다.

### Task 6: real runs (before landing, on the branch's code)

The live hooks and the watch run from `~/.dotfiles` until landing, so the branch's code is run by hand against the real servers. Nothing here reaches the main pane or another lead:

- `XDG_STATE_HOME` points at a scratch folder whose `main` file names a temporary pane standing in for the main pane, so `reported/`, `watch.json` and every "main pane" route stay in the test.
- The watch gets `WORKERS_WATCH_PANE_TELL`: a wrapper that passes on only messages to the temporary panes and logs the rest.
- `PANE_TEMP_DIR` points at the scratch folder, so no real pane is told a test pane of its own is open.

Cases, on an ordinary lead on default (Claude, then Codex) from `pane-temp` in `Research`: spawn lands on workers and the lead's tab stays alone; report reaches the lead; question dialog alert; closed lead (alert, report through the stand-in, closed an hour later with the clock moved on); two leads at once; `--sweep`; tab names; status line; share-guard's stopped-pane line; fallback with `workers` hidden from `session list`. Close every temporary pane, with its `reported/` mark made first. One smoke run on the live hooks after landing. Record each proving command for the last report.
