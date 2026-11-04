#!/usr/bin/env python3
"""Stop hook (Claude Code and Codex): a pane an agent opened closes itself when its report says nothing is left there.

The global rule asks a dispatch tab (MUNICE_AUTO_DISPATCH=1) or a Slack ambient tab (MUNICE_AMBIENT=1) to end with
`세션: 닫음` and `pane-label --close` as its last tool call. Sessions kept writing `세션: 닫아도 됨` and stopping, so 요유
closed the tabs by hand (요유 2026-10-28: "자동탭에서 할거없으면 좀 알아서 닫으라니까 죽어도 안닫네 ... 로직을
개선하라고"). This hook reads the report instead of trusting the session to remember: `세션: 닫음`, or `세션: 닫아도 됨`
with `요유 몫: 없음`, runs `pane-label --close`. That command keeps its own guards: only job tabs, never a 🔴 pane, and it
waits for the turn to end and cancels if the agent works again within 20 seconds (요유 typed, a goal judge sent it back).
A `요유 몫` with something in it keeps the tab open even at `닫아도 됨`: 요유 may need to type in it (`결정 필요`, `승격`).
Under a lead nobody but the lead types there: it asks 요유 in its own pane and passes the answer down.
A pane `herdr agent start` opened closes the same way, whichever session started it and whether or not that session still
lives: herdr's agent name tells it from a session 요유 started by hand, which has none (요유 2026-10-31). A closing pane
takes its report with it, so the whole reply is first appended to a day file under ~/.local/state/pane-reports/, and the
closing lines go to the pane that opened it, else a 👑 it can be sure of. A reply that cannot be saved keeps the pane open; a
👑 that cannot hear does not, and job tabs tell no 👑 (their reports go to the board and Slack). A 🔴 pane stays, and so does
any pane while a pane it opened is alive; one whose panes are gone closes like the rest. A report that keeps the pane open
(요유 몫 left, `세션: 닫으면 …`, held as above) goes up the same way and the pane stays: an implementer finished with two
questions for 요유 and its lead never heard (요유 2026-11-01). A reply without `남은 것:` is no report and tells nobody.
What the pane above hears tells it to check the result itself before passing it up, and yoyoo-share-guard holds its next report
to that (요유 2026-11-04). A pane that hands checks out (herdr_peers.delegates_checks: the main pane today, 요유 2026-11-05) hears
to open a fresh check pane instead, and a check pane's report, which pane-spawn --check's agent name tells apart, asks for no other.
A pane pane-spawn opened runs on the workers herdr server, so its opener is looked up on the other server too. The main pane
hears a short form (first line, `남은 것:`, the pane's address and the saved file); the whole reply stays in that file. Every
lead's panes share that server's one workspace: when the opener is gone or cannot hear, the report goes to a 👑 in the same tab or
the main pane, saying it stands in (and, for a Home pane, to keep it off company Slack and the board; 요유 2026-11-05), never to
another lead.
A pane moved to another workspace gets a new id, and only `herdr pane get` still knows the old one from its env, so the
current pane comes from there, and pane-spawn records its caller by terminal id, which a move does not change.
A Stop hook runs beside the other Stop hooks, and any of them can send the reply back to be rewritten, so a Stop is not the
end of a turn: a draft went to the lead, then its rewrite (2026-10-31, share-guard asked for pane names). The hook only leaves
the reply in a per-pane file and detaches. That step waits until the agent has stopped working, and each Stop of the pane
replaces or withdraws the file, so the last reply of the turn is the only one acted on.
Never blocks; prints nothing. Fails open.

Before all that, every Stop of every pane (요유's own too) keeps its 🔴/🟢 mark true: see sync_mark.

--sweep lists the panes that already finished on the default and workers servers, which no Stop hook will visit again: named,
idle, not held as above, and a last reply that passes the same test. --sweep --apply closes them the same way.

    auto-tab-close.py --selftest
    auto-tab-close.py --sweep [--apply]
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import datetime
import glob
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every stop
HERE = os.path.dirname(os.path.realpath(__file__))
_spec = importlib.util.spec_from_file_location("share", os.path.join(HERE, "yoyoo-share-guard.py"))
share = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(share)  # reuse its 요유 몫 reader, code and quote stripping, and transcript fallback
_spec = importlib.util.spec_from_file_location("herdr_peers", os.path.join(HERE, "herdr_peers.py"))
herdr_peers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(herdr_peers)  # the default and workers servers, and pane addresses across them

SESSION = re.compile(r"세션[ \t]*\**[ \t]*[:：][ \t]*\**([^·\n]*)")
LEFT = re.compile(r"남은[ \t]*것[ \t]*\**[ \t]*[:：]")


def plain(text: str) -> str:
    """Code and quotes are not a report."""
    text = unicodedata.normalize("NFC", text)
    for pat in share.STRIP:
        text = pat.sub(" ", text)
    return text


def session_value(text: str) -> str:
    """The last `세션:` value, or ""."""
    values = [m.group(1).strip(" \t*_.,;") for m in SESSION.finditer(plain(text))]
    return values[-1] if values else ""


def should_close(text: str) -> bool:
    """The last `세션:` value says 닫음, or 닫아도 됨 with nothing in 요유 몫."""
    value = session_value(text)
    if value.startswith("닫음"):
        return True
    return value.startswith("닫아도 됨") and not share.leaves_yoyoo_work(plain(text))


STARTS = ("Monitor", "Agent", "Workflow")  # with a background Bash, CronCreate and ScheduleWakeup: what pane-hold.sh marks 🔴
TASK = re.compile(r"(with ID: |\(task |agentId: |stopped task: )(\w+)")
NOTICE = re.compile(r"<tool-use-id>(toolu_\w+)</tool-use-id>")
# Run as a command, not named in one: `rg 'pane-label --free'`, a quoted example or a heredoc body is text (Codex review,
# 2026-11-03). ponytail: a regex reading of shell, not a parser; a miss it makes keeps 🔴 or frees as the agent last asked.
BY_HAND = re.compile(r"(?:^|[;&|(\n])[ \t]*(?:\S*/)?pane-label[ \t]+(--hold|--free)\b")
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def hand_flags(command: str) -> list:
    return BY_HAND.findall(QUOTED.sub("''", HEREDOC.sub("", command)))


def still_running(path: str, claim: bool = True) -> bool:
    """Whether a Claude transcript shows something pane-hold.sh marked 🔴 that may still run: a background command, Monitor,
    agent or workflow without its end notice or a TaskStop, a session cron with no CronDelete, a wakeup not stopped, a
    `pane-label --hold` with no `--free` after it, or (with claim) a last report saying `세션: 닫으면 …`, which holds until
    a later report replaces it. A failed or refused call counts for nothing (a --hold in a failed command still counts), and
    of the ones left the last wakeup and the last hand mark decide. A line it cannot read raises. Shapes it does not know count as running, so a doubt keeps 🔴,
    which only keeps a session open (the old behaviour).
    ponytail: a one-shot cron or a /loop that ends without stop=True, and a Monitor that expires without a notice, stay 🔴."""
    running: set = set()
    task_of: dict = {}
    calls: list = []  # (kind, id, value) of cron, wakeup and hand-mark calls, in the order they were made
    failed: set = set()
    closes = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            content = (d.get("message") or {}).get("content")
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            for b in blocks:
                if b.get("type") == "text" and d.get("type") == "assistant":
                    value = session_value(b.get("text") or "")
                    closes = value.startswith("닫으면") if value else closes
                elif b.get("type") == "tool_use":
                    name, inp, i = b.get("name"), b.get("input") or {}, b.get("id")
                    flags = hand_flags(inp.get("command") or "") if name == "Bash" else []
                    if flags:  # `cleanup && pane-label --free` that failed never freed: see failed
                        calls.append(("hold", i, flags[-1] == "--hold"))
                    if (name == "Bash" and inp.get("run_in_background")) or name in STARTS:
                        running.add(i)
                        if inp.get("name"):  # an agent's name, for SendMessage
                            task_of[inp["name"]] = i
                    if name == "SendMessage" and task_of.get(inp.get("to")):
                        running.add(task_of[inp.get("to")])  # wakes an agent that had finished
                    if name in ("CronCreate", "CronDelete"):
                        calls.append(("cron", i, 1 if name == "CronCreate" else -1))
                    if name == "ScheduleWakeup":
                        calls.append(("wake", i, inp.get("stop") is not True))
                elif b.get("type") == "tool_result":
                    i = b.get("tool_use_id")
                    if b.get("is_error"):
                        running.discard(i)  # refused, so it never started
                        failed.add(i)
                    out = b.get("content")
                    out = out if isinstance(out, str) else " ".join(x.get("text") or "" for x in out or [] if isinstance(x, dict))
                    if out.startswith("Command running in background with ID:"):
                        running.add(i)  # a command moved to the background without run_in_background
                    for kind, task in TASK.findall(json.dumps(b.get("content"), ensure_ascii=False)):
                        if kind == "stopped task: ":
                            running.discard(task_of.get(task))
                        elif i in running:
                            task_of[task] = i
            # An end notice comes as its own user message or queued mid-turn; one printed inside a tool's output is not one.
            if d.get("type") in ("user", "queue-operation", "attachment") and not any(b.get("type") == "tool_result" for b in blocks):
                running -= set(NOTICE.findall(line))
    # A failed Bash call may still have run its --hold (`pane-label --hold; false`), so only its --free is in doubt.
    done = [(kind, value) for kind, i, value in calls if i not in failed or (kind == "hold" and value)]
    crons = sum(value for kind, value in done if kind == "cron")
    wake = next((value for kind, value in reversed(done) if kind == "wake"), False)
    hold = next((value for kind, value in reversed(done) if kind == "hold"), False)
    return bool(running) or crons > 0 or wake or hold or (claim and closes)


def sync_mark(payload: dict, env):
    """Keep the pane mark true at every Stop: a report saying `세션: 닫으면 …` holds 🔴; otherwise a 🔴 goes back to 🟢 once
    the Claude transcript shows nothing still running. pane-hold.sh sets 🔴 at every background start and only the agent's
    own `--free` took it off, so finished sessions stayed 🔴, under a report that said they could close or under no report
    (요유 2026-11-03: "왜 자꾸 빨간색으로 되어있냐이거"; 14 of 21 sessions left 🔴 that week had nothing running). Codex has
    no reader here, so it is held but never freed. An agent's own `--free` over a running watch stays: this never re-holds
    for running work. Any doubt changes nothing."""
    if not env.get("HERDR_PANE_ID") or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return
    try:
        red = (here(env).get("label") or "").startswith("🔴")  # the mark leads; a name may hold 🔴 too
        path = payload.get("transcript_path") or ""
        value = session_value(reply_of(payload) or "")
        if value.startswith("닫으면"):
            flag = "--hold"
            if red:
                return
        # A reply with its own 세션 line is the newest report; one without keeps the last report's 닫으면 (no_new_report).
        elif red and f"{os.sep}.claude{os.sep}projects{os.sep}" in path and not still_running(path, claim=not value):
            flag = "--free"
        else:
            return
        pl = env.get("AUTO_TAB_CLOSE_PANE_LABEL") or os.path.join(HERE, "pane-label")
        subprocess.run([pl, flag], env=dict(env), timeout=3, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except Exception:
        pass


herdr = herdr_peers.herdr  # passes env on, so HERDR_SOCKET_PATH picks the server; {} on any failure, never raises (see finish)
opener = herdr_peers.opener


def here(env) -> dict:
    """This pane as herdr has it now. HERDR_PANE_ID goes stale when the pane moves; `pane get` alone still resolves it."""
    pane = env.get("HERDR_PANE_ID")
    return (herdr(env, "pane", "get", pane).get("pane") or {}) if pane else {}


def agent_name(env) -> str:
    """`herdr agent start` names the agent; a session started by hand, even in that pane afterwards, has no name."""
    pane = here(env).get("pane_id") or env.get("HERDR_PANE_ID")
    return ((herdr(env, "agent", "get", pane).get("agent") or {}).get("name") or "") if pane else ""


def held(label: str, terminal, agents: list) -> bool:
    """🔴 stops something; a pane waits for the panes it opened. 요유's own leads never get here: they have no agent name."""
    return label.startswith("🔴") or any(opener(a.get("name")) == terminal for a in agents)


def hidden_agents(env) -> list | None:
    """The workers server's agents for a pane elsewhere: pane-spawn opens a lead's panes there, a named 👑's included. None when it
    cannot tell, which keeps the pane open. A workers pane opens on workers, so it has nothing to read."""
    if herdr_peers.server_key(env) == "workers":
        return []
    sock = herdr_peers.socket_of(env, "workers")
    if sock is None:
        return None
    return herdr(env, "agent", "list", socket=sock).get("agents") if sock else []


def reply_of(payload: dict):
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = share.final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    return text


def no_new_report(payload: dict) -> bool:
    """yoyoo-share-guard tells a session with nothing to do to end without a word, and Claude Code then asks it for a visible
    reply: one line with no `남은 것`. Such a Stop brings no report, so the reply held from the Stop before is the report, and
    withdrawing it would leave the pane open with no report saved or sent (요유 2026-11-01)."""
    return bool(payload.get("stop_hook_active")) and not LEFT.search(share.prose(reply_of(payload) or ""))


def decide(payload: dict, env) -> str | None:
    """The reply to act on when the turn ends, or None: one that closes this pane, or a report that keeps it open and still has
    to reach the pane above. The nested claude -p inherits this pane's env but is not its session."""
    if env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None
    text = reply_of(payload)
    if not isinstance(text, str):
        return None
    job = env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT")
    if should_close(text):
        return text if job or agent_name(env) else None
    return text if not job and LEFT.search(share.prose(text)) and agent_name(env) else None


def main_pane(env):
    """(address, pane) of the main pane, or ("", {})."""
    return (herdr_peers.all_panes(env) or {}).get(herdr_peers.main_terminal(env)) or ("", {})


def lead_pane(me: dict, panes: list, name: str = "", sideways: bool = True, env=None):
    """(address, pane) of nearest_lead, ("", {}) when there is none, or None when a list it could not read may hide its opener."""
    others = [p for p in panes if p.get("pane_id") != me.get("pane_id")]
    parent = opener(name)
    for p in others:
        if parent and p.get("terminal_id") == parent:
            return p["pane_id"], p
    if parent and env is not None:
        where = herdr_peers.all_panes(env)
        if where is None:
            return None
        if parent in where:  # opened from the other server: every lead's panes run on workers
            return where[parent]
    if not sideways:
        return "", {}
    leads = [p for p in others if "👑" in (p.get("label") or "")]
    tab = [p for p in leads if p.get("tab_id") == me.get("tab_id")]
    if tab:
        return tab[0]["pane_id"], tab[0]
    if env is not None and herdr_peers.server_key(env) == "workers":
        return main_pane(env)  # every lead shares that server's one workspace, so its only 👑 is nobody in particular
    workspace = [p for p in leads if p.get("workspace_id") == me.get("workspace_id")]
    return (workspace[0]["pane_id"], workspace[0]) if len(workspace) == 1 else ("", {})


def nearest_lead(me: dict, panes: list, name: str = "", sideways: bool = True, env=None) -> str:
    """The pane that opened this one (on this server, else the other one), else the 👑 in this tab, else the only 👑 in this
    workspace, or on workers the main pane. With several, none is known to be ours, and a report in the wrong session reads as a
    task: the saved copy is enough. Another workspace is another life (company, home). Without `sideways` only the opener counts:
    two open 👑 would answer each other for ever. A pane on the other server comes back as `<session>/<id>`."""
    return (lead_pane(me, panes, name, sideways, env) or ("",))[0]


def short_report(reply: str, cap: int | None = 200) -> str:
    """The first prose line, up to `cap` characters, and the last `남은 것:` line (with the lines under it when it is a heading). Both
    keep their inline code and quotes (`prod 허용` is what 요유 types, and a check pane's first line names what it ran); only fenced
    code and blockquotes are not the report's."""
    raw = share.STRIP[2].sub(" ", share.STRIP[0].sub(" ", unicodedata.normalize("NFC", reply)))
    lines = [line.strip() for line in raw.splitlines()]
    first = next((line for line in lines if line and not LEFT.search(line)), "")[:cap]
    at = max((i for i, line in enumerate(lines) if LEFT.search(line)), default=-1)
    left = []
    if at >= 0:
        left = [lines[at]]
        if not LEFT.split(lines[at], 1)[-1].strip(" *"):  # `남은 것:` and its items on the lines below
            left += list(itertools.takewhile(bool, lines[at + 1:]))
    return "\n".join(line for line in ([] if first in left else [first]) + left if line)


def save(reply: str, me: dict, pane: str, env, now=None) -> str:
    """Outside any session and repo: reports can name customers, and the dotfiles are public."""
    folder = env.get("PANE_REPORT_DIR") or os.path.expanduser("~/.local/state/pane-reports")
    os.makedirs(folder, exist_ok=True)
    now = now or datetime.datetime.now()
    session = (me.get("agent_session") or {}).get("value", "")
    path = os.path.join(folder, f"{now:%Y-%m-%d}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## {now:%H:%M:%S} {herdr_peers.address(env, pane)} {me.get('label') or ''}\n\n"
                f"세션 {session}, 폴더 {me.get('cwd') or ''}\n\n{reply.strip()}\n")
    return path


def finish(reply: str, env):
    """Keep the report, tell the pane above, then close unless the report or the pane says to stay. Only a failed save stops
    the close (it raises)."""
    me = here(env)
    pane = me.get("pane_id") or env.get("HERDR_PANE_ID", "")
    env = {**env, "HERDR_PANE_ID": pane}
    panes = herdr(env, "pane", "list").get("panes") or []
    agents = herdr(env, "agent", "list").get("agents")
    hidden = hidden_agents(env)
    # herdr fails open ({}): a pane or agent list it could not give hides the panes this one opened, so the pane stays
    close = (should_close(reply) and bool(me) and agents is not None and hidden is not None
             and not held(me.get("label") or "", me.get("terminal_id"), agents + hidden))
    name = agent_name(env) if agents is None else next((a.get("name") for a in agents if a.get("pane_id") == pane), "")
    now = datetime.datetime.now()
    path = save(reply, me, pane, env, now)
    quiet: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    sideways = close or "👑" not in (me.get("label") or "")
    workers = herdr_peers.server_key(env) == "workers"
    if env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT"):
        found = ("", {})
    else:  # with no name on workers, sideways would be the main pane, with no word of a Home pane
        found = None if workers and agents is None and not name else lead_pane(me, panes, name, sideways, env)
    if found is None:  # its lead may be alive: the file, no mark, and the pane stays for the watch to reach that lead
        close, found = False, ("", {})
    lead, to = found
    main = herdr_peers.main_terminal(env)

    def tell(lead: str, to: dict) -> bool:
        pt = env.get("AUTO_TAB_CLOSE_PANE_TELL") or os.path.join(HERE, "pane-tell")
        head = "칸의 마지막 보고입니다(칸을 닫기 전에 보냅니다)" if close else "칸의 보고입니다(칸은 열어 둡니다)"
        who, note = "이", share.RELAY_NOTE
        if herdr_peers.delegates_checks(env, to.get("terminal_id")):  # a check pane's report ends the round: no check of a check
            who, note = ("확인", share.CHECK_NOTE) if share.CHECK_AGENT.match(name or "") else ("이", share.DELEGATE_NOTE)
        if main and to.get("terminal_id") == main:  # the main pane keeps many panes going: it reads the gist and opens the file when it needs more
            # A check pane's first line holds its pass or fail, often at the end, and the main pane writes its `확인:` line from it: never cut.
            gist = short_report(reply, None) if who == "확인" else short_report(reply)
            body = (f"{gist}\n칸: {herdr_peers.address(env, pane)} {me.get('label') or ''}\n"
                    f"전체 보고: {path} ({now:%H:%M:%S} 항목)")
        else:
            body = f"{unicodedata.normalize('NFC', reply).strip()}\n\n전체 보고: {path}"
        # Someone other than the opener reads this: say why, and that a Home pane's words stay off company Slack and the board (요유 2026-11-05).
        stand_in = ""
        if opener(name) and to.get("terminal_id") != opener(name):
            stand_in = "이 칸을 연 리드에게 보낼 수 없어 대신 보냅니다. " + (f"{herdr_peers.HOME_NOTE} " if herdr_peers.home_pane(name) else "")
        try:  # share.RELAYED knows the head: the lead's next report has to show its check
            # 10 s, as the watch gives it: a slow send killed here would still type, then the main pane would get the same report
            return subprocess.run([pt, lead, f"{who} {head}. {stand_in}{note}:\n{body}"], env=dict(env), timeout=10, **quiet).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    sent = bool(lead) and tell(lead, to)
    if lead and not sent and workers and to.get("terminal_id") != main:  # hidden: no tab of 요유's shows this pane stop, so the main pane hears it
        lead, to = main_pane(env)
        sent = bool(lead) and tell(lead, to)
    # the main pane's watch: this pane is waiting, not stuck. Only workers panes: a personal server is left alone
    if sent and me.get("terminal_id") and (workers or to.get("terminal_id") == main):
        try:
            mark = os.path.join(herdr_peers.state_dir(env), "reported", me["terminal_id"])
            os.makedirs(os.path.dirname(mark), exist_ok=True)
            with open(mark, "a"):
                os.utime(mark)
        except OSError:
            pass
    if close:
        pl = env.get("AUTO_TAB_CLOSE_PANE_LABEL") or os.path.join(HERE, "pane-label")
        subprocess.run([pl, "--close"], env={**env, "PANE_CLOSE_REPORTED": "1"}, timeout=3, **quiet)


def pending_file(env) -> str:
    """Per server: the same pane id names another pane on the other server."""
    folder = env.get("PANE_REPORT_DIR") or os.path.expanduser("~/.local/state/pane-reports")
    return os.path.join(folder, herdr_peers.server_key(env), ".pending-" + env["HERDR_PANE_ID"])


def defer(reply, env):
    """Hold this Stop's reply until the turn ends, in place of any earlier Stop's; None withdraws. A claude -p inside the
    pane inherits its id but is not its session, so its Stop leaves the pane's reply alone."""
    if not env.get("HERDR_PANE_ID") or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return
    path = pending_file(env)
    if reply is None:
        if os.path.exists(path):
            os.unlink(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token = str(time.time_ns())
    with open(f"{path}.{token}", "w", encoding="utf-8") as f:
        json.dump({"token": token, "reply": reply}, f, ensure_ascii=False)
    os.replace(f"{path}.{token}", path)  # the step of an earlier Stop may be reading it
    # Own session, as `pane-label --close`: it outlives this hook and the pane it closes.
    subprocess.Popen([sys.executable, "-I", os.path.realpath(__file__), "--after-turn", token], env=dict(env), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def held_reply(token: str, env) -> str | None:
    """The reply left for this step, unless a later Stop replaced or withdrew it."""
    try:
        with open(pending_file(env), encoding="utf-8") as f:
            held = json.load(f)
    except (OSError, ValueError):
        return None
    return held["reply"] if held.get("token") == token else None


def drop(token: str, env):
    """A later Stop's reply is not this step's to remove."""
    if held_reply(token, env) is not None:
        os.unlink(pending_file(env))


def ready_for_input(env) -> bool:
    """Only these two are the end of a turn: `blocked` is a dialog, and herdr not answering says nothing."""
    try:
        return here(env).get("agent_status") in ("idle", "done")
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def after_turn(token: str, env):
    """herdr keeps an agent `working` through a sent-back reply. A short quiet spell after it covers the gap before a turn
    starts again; 요유 typing in that spell restarts it, and that turn's own Stop then decides. Past the wait the pane is
    busy with something else: the report is kept and the pane stays."""
    settle = float(env.get("AUTO_TAB_CLOSE_SETTLE", 3))
    deadline = time.monotonic() + float(env.get("AUTO_TAB_CLOSE_WAIT", 600))
    quiet_from = time.monotonic()
    while time.monotonic() < deadline:
        reply = held_reply(token, env)
        if reply is None:
            return
        if not ready_for_input(env):
            quiet_from = time.monotonic()
        elif time.monotonic() - quiet_from >= settle:
            try:
                finish(reply, env)
            finally:
                drop(token, env)
            return
        time.sleep(float(env.get("AUTO_TAB_CLOSE_POLL", 0.5)))
    reply = held_reply(token, env)
    if reply is not None:
        save(reply, {}, env["HERDR_PANE_ID"], env)
        drop(token, env)


def last_reply(session: str) -> str:
    """The last reply of a finished session, from its Claude transcript or its Codex log."""
    home = os.path.expanduser("~")
    for path in glob.glob(f"{home}/.claude/projects/*/{session}.jsonl"):
        return share.final_text(path)
    for path in glob.glob(f"{home}/.codex/sessions/*/*/*/rollout-*-{session}.jsonl"):
        with open(path, encoding="utf-8") as f:
            for line in reversed(f.read().splitlines()):
                payload = json.loads(line).get("payload") or {}
                if payload.get("type") == "task_complete":
                    return payload.get("last_agent_message") or ""
    return ""


def sweep(env, apply: bool):
    me = here(env).get("pane_id") or env.get("HERDR_PANE_ID")
    own = env.get("HERDR_SOCKET_PATH") or herdr_peers.socket_of(env, "default")
    socks = [herdr_peers.socket_of(env, n) for n in herdr_peers.PEERS]
    for e in [env] + [{**env, "HERDR_SOCKET_PATH": s} for s in socks if s and s != own]:  # the same pane id names another pane on the other server
        panes = {p.get("pane_id"): p for p in herdr(e, "pane", "list").get("panes") or []}
        agents = herdr(e, "agent", "list").get("agents") or []
        hidden = hidden_agents(e)
        for a in agents:
            pane, session = a.get("pane_id"), (a.get("agent_session") or {}).get("value")
            label = panes.get(pane, {}).get("label") or ""
            address = herdr_peers.address(e, pane)
            if (not a.get("name") or not session or a.get("agent_status") not in ("idle", "done") or (e is env and pane == me)
                    or hidden is None or held(label, panes.get(pane, {}).get("terminal_id"), agents + hidden)):
                continue
            try:
                reply = last_reply(session)
                if not should_close(reply):
                    continue
                print(f"{address}\t{a['name']}\t{label}")
                if apply:
                    finish(reply, {**e, "HERDR_PANE_ID": pane})
            except Exception as err:  # one unreadable pane must not stop the others
                print(f"{address}\t건너뜀: {err}", file=sys.stderr)


def selftest():
    import shutil
    import tempfile

    os.environ.pop("HERDR_SOCKET_PATH", None)  # never the live server
    close = [
        "끝났습니다.\n\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",  # this session, 2026-10-28
        "남은 것: 요유 몫: 없음 · 유요 몫: watch 대기 · 세션: 닫음",
        "남은 것: 요유 몫: GitHub에서 draft PR 머지 · 유요 몫: 없음 · 세션: 닫음",  # 요유's part is outside the tab
        "남은 것:\n- 요유 몫: 없음\n- 유요 몫: 없음\n- 세션: 닫아도 됨",
        "**남은 것:** 요유 몫: **없음** · 유요 몫: 없음 · 세션: **닫아도 됨**",
        unicodedata.normalize("NFD", "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"),
    ]
    keep = [
        "남은 것: 요유 몫: 이 탭에 \"반영\" 입력 · 유요 몫: 반영 후 닫기 · 세션: 닫아도 됨",  # 결정 필요, this session
        "남은 것: 요유 몫: 이 탭에서 승격 입력 · 유요 몫: prod 배포 · 세션: 닫으면 승격 못 함",
        "남은 것: 요유 몫: 없음 · 유요 몫: CI 대기 · 세션: 닫으면 Monitor 멈춤",
        "진행 중입니다. CI를 기다립니다.",
        "형식은 `세션: 닫음` 입니다.",  # talking about the format
        "> 남은 것: 요유 몫: 없음 · 세션: 닫음\n이전 보고를 인용했습니다.",
        "",
    ]
    for t in close:
        assert should_close(t), f"should close: {t[:70]!r}"
    for t in keep:
        assert not should_close(t), f"should keep: {t[:70]!r}"

    auto, done = {"MUNICE_AUTO_DISPATCH": "1"}, close[0]
    assert decide({"last_assistant_message": done}, auto)
    assert decide({"last_assistant_message": done}, {"MUNICE_AMBIENT": "1"})
    assert no_new_report({"stop_hook_active": True, "last_assistant_message": " \n"}), "the reply held before it is the report"
    assert no_new_report({"stop_hook_active": True, "last_assistant_message": "위 보고가 최종입니다."}), \
        "Claude Code refuses a silent ending, so a sent-back session writes a line like this one"
    assert not no_new_report({"stop_hook_active": True, "last_assistant_message": done}), "a rewrite replaces it"
    assert not no_new_report({"last_assistant_message": ""}), "a first stop with no reply holds nothing"
    assert not no_new_report({"last_assistant_message": "위 보고가 최종입니다."}), "not sent back: a new reply and no report"
    assert not decide({"last_assistant_message": done}, {}), "a session 요유 opened is never closed"
    assert not decide({"last_assistant_message": keep[0]}, auto)
    assert not decide({"last_assistant_message": None}, auto)

    sandbox = tempfile.mkdtemp()

    def script(name, body):
        path = os.path.join(sandbox, name)
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, 0o755)
        return path

    def herdr_says_on(srv, agent=None, panes=(), agents=()):
        os.makedirs(f"{sandbox}/{srv}", exist_ok=True)
        for name, result in (("agent", {"agent": agent}), ("panes", {"panes": list(panes)}), ("agents", {"agents": list(agents)})):
            with open(f"{sandbox}/{srv}/{name}.json", "w") as f:
                json.dump({"result": result}, f)

    def herdr_says(agent=None, panes=(), agents=()):
        herdr_says_on("", agent, panes, agents)

    def sessions(**name_socket):
        with open(f"{sandbox}/sessions.json", "w") as f:
            json.dump({"sessions": [{"name": n, "socket_path": s, "running": True} for n, s in name_socket.items()]}, f)

    # The workers server reads its own files; `session list` is not wrapped in `result`.
    fake_herdr = script("herdr", f'''case "$HERDR_SOCKET_PATH" in */sessions/workers/*) d={sandbox}/workers ;; *) d={sandbox} ;; esac
case "$1 $2" in
  "session list") cat {sandbox}/sessions.json ;;
  "agent get") [ -f {sandbox}/agent-gone ] || [ "$3" = "$(cat {sandbox}/old-id 2>/dev/null)" ] && exit 1; cat $d/agent.json ;;
  "pane get") [ -f {sandbox}/herdr-bad ] && {{ echo "not json"; exit 0; }}; jq --arg id "$3" --arg new "$(cat {sandbox}/new-id 2>/dev/null)" \
    '{{result: {{pane: ([.result.panes[] | select(.pane_id == $id or .pane_id == $new)][0])}}}}' $d/panes.json ;;
  "agent list") [ -f $d/agents-bad ] && {{ echo "not json"; exit 0; }}; cat $d/agents.json ;;
  "pane list") [ -f $d/panes-bad ] && exit 1; cat $d/panes.json ;;
  *) exit 2 ;;
esac''')
    sessions(default=f"{sandbox}/herdr.sock")
    fake = script("pane-label", f'echo "reported=$PANE_CLOSE_REPORTED pane=$HERDR_PANE_ID $@" >> {sandbox}/calls')
    fake_tell = script("pane-tell", f'echo "tell $1" >> {sandbox}/calls\n[ "$1" = "${{TELL_REFUSE:-}}" ] && exit 2\necho "$2" > {sandbox}/told\nsleep ${{TELL_SLEEP:-0}}\nexit ${{TELL_EXIT:-0}}')

    me = {"pane_id": "w1:p2", "tab_id": "w1:t1", "workspace_id": "w1", "label": "🟢 일", "cwd": "/w",
          "terminal_id": "term_2", "agent_session": {"value": "sid-2"}, "agent_status": "idle"}
    spawned = {"HERDR_BIN_PATH": fake_herdr, "HERDR_PANE_ID": "w1:p2"}
    herdr_says({"name": "spawned"})
    assert decide({"last_assistant_message": done}, spawned), "a pane `herdr agent start` opened closes"
    assert decide({"last_assistant_message": keep[0]}, spawned) == keep[0], "요유 몫 left keeps the pane, and the report goes up"
    assert not decide({"last_assistant_message": keep[3]}, spawned), "a progress line is no report"
    assert not decide({"last_assistant_message": keep[5]}, spawned), "a quoted report is no report"
    assert not decide({"last_assistant_message": done}, {**spawned, "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}), \
        "a headless claude -p inside the pane inherits its id but is not the pane's session"
    herdr_says({"name": None})
    assert not decide({"last_assistant_message": done}, spawned), "a session 요유 started by hand has no name"
    open(f"{sandbox}/agent-gone", "w").close()
    assert not decide({"last_assistant_message": done}, spawned), "herdr without a record: not ours to close"
    os.unlink(f"{sandbox}/agent-gone")

    full ="조사를 마쳤습니다.\n\n무엇을: 닫기 규칙을 바꿨습니다.\n왜: 칸이 남습니다.\n남은 것: 요유 몫: 없음 · 세션: 닫음"

    reports = f"{sandbox}/reports"

    def stop(payload, env, args=()):
        return subprocess.run([sys.executable, "-I", os.path.realpath(__file__), *args], input=payload, capture_output=True,
                              text=True, env={"PATH": os.environ.get("PATH", ""), "HOME": sandbox, "HERDR_BIN_PATH": fake_herdr,
                                              "PANE_REPORT_DIR": reports, "AUTO_TAB_CLOSE_PANE_LABEL": fake,
                                              "AUTO_TAB_CLOSE_PANE_TELL": fake_tell, "AUTO_TAB_CLOSE_SETTLE": "0",
                                              "AUTO_TAB_CLOSE_POLL": "0.02", **env},
                              timeout=10)

    def taken():
        """What ran, once the detached step has finished."""
        for _ in range(250):
            if not glob.glob(f"{reports}/**/.pending*", recursive=True):  # a named server's are in its folder
                break
            time.sleep(0.02)
        else:
            raise AssertionError("the step after the turn never finished")
        calls = open(f"{sandbox}/calls").read() if os.path.exists(f"{sandbox}/calls") else ""
        if os.path.exists(f"{sandbox}/calls"):
            os.unlink(f"{sandbox}/calls")
        return calls

    def run(payload, env, args=()):
        r = stop(payload, env, args)
        return r.returncode, r.stdout, taken()

    def saved():
        text = "".join(open(f).read() for f in glob.glob(f"{reports}/*") if os.path.isfile(f))
        if os.path.isdir(reports):
            shutil.rmtree(reports)
        return text

    closed = "reported=1 pane=w1:p2 --close\n"
    herdr_says({"name": "spawned"}, [me])
    assert run(json.dumps({"last_assistant_message": done}), {**auto, "HERDR_PANE_ID": "w1:p2"}) == (0, "", closed), \
        "done report must close"
    saved()
    assert run(json.dumps({"last_assistant_message": keep[0]}), auto) == (0, "", ""), "decision left must stay"
    assert run(json.dumps({"last_assistant_message": done}), {}) == (0, "", ""), "요유's own session must stay"
    assert run("not json{", auto) == (0, "", ""), "malformed input must exit 0 silently"
    assert saved() == "", "nothing closed, nothing saved"

    reported, pane2 = json.dumps({"last_assistant_message": full}), {"HERDR_PANE_ID": "w1:p2"}
    assert run(reported, pane2) == (0, "", closed), "no 👑 anywhere: the saved copy is enough to close"
    text = saved()
    assert full in text and "w1:p2" in text and "sid-2" in text, f"the whole reply, with where it came from: {text!r}"

    crown = {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "label": "🟢 👑 리드", "terminal_id": "term_1"}
    other_tab = {**crown, "pane_id": "w1:p7", "tab_id": "w1:t2", "terminal_id": "term_7"}
    third_tab = {**crown, "pane_id": "w1:p8", "tab_id": "w1:t3", "terminal_id": "term_8"}
    other_space = {**crown, "pane_id": "w2:p1", "tab_id": "w2:t1", "workspace_id": "w2", "terminal_id": "term_9"}
    mine = [{"pane_id": "w1:p2", "name": "spawn-7-000000-1"}]  # pane-spawn records the caller's terminal in the name
    herdr_says({"name": "spawned"}, [other_tab, crown, me])
    assert run(reported, pane2) == (0, "", "tell w1:p1\n" + closed), "the 👑 in this tab first"
    told = open(f"{sandbox}/told").read()
    # 요유 2026-11-04 ("너희끼리는 그냥 편하게 주고받아"): a pane writes to its lead as it likes, so the lead gets all of it, not the labels.
    assert full in told and reports in told, f"the whole reply and its file must reach the lead: {told!r}"
    # 요유 2026-11-04: a pane's "done" is its claim. The lead hears that it checks before passing it up, and its next report is
    # held to it by yoyoo-share-guard, which must recognise this very message.
    assert "직접 확인" in told and "'확인:'" in told, f"the lead is told to check before passing it up: {told!r}"
    lead_log = os.path.join(sandbox, "lead.jsonl")
    with open(lead_log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": told}}, ensure_ascii=False) + "\n")
    unchecked = {"last_assistant_message": "무엇을: 고침\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨", "transcript_path": lead_log}
    assert "하위 칸" in (share.decide(unchecked, {"HERDR_BIN_PATH": "/nonexistent/herdr", "EXPLAIN_STATE": sandbox}) or ""), \
        "share-guard knows the message auto-tab-close sends"
    os.unlink(lead_log)
    assert full in saved(), "kept even when a 👑 hears it"
    assert run(reported, {**pane2, "TELL_EXIT": "3"}) == (0, "", "tell w1:p1\n" + closed), "a 👑 that cannot hear holds nothing"
    herdr_says({"name": "spawned"}, [other_space, other_tab, me])
    assert run(reported, pane2) == (0, "", "tell w1:p7\n" + closed), "no 👑 in the tab: the only one in the workspace"
    herdr_says({"name": "spawned"}, [third_tab, other_tab, me])
    assert run(reported, pane2) == (0, "", closed), "several 👑 and none is known to be ours: the saved copy only"
    herdr_says({"name": "spawned"}, [third_tab, crown, other_tab, me], mine)
    assert run(reported, pane2) == (0, "", "tell w1:p7\n" + closed), "the pane that opened this one comes first"
    herdr_says({"name": "spawned"}, [third_tab, crown, me], mine)
    assert run(reported, pane2) == (0, "", "tell w1:p1\n" + closed), "its opener is gone: back to the tab's 👑"
    saved()
    open(f"{sandbox}/old-id", "w").write("w1:p2")
    open(f"{sandbox}/new-id", "w").write("w9:p2")
    moved = {**me, "pane_id": "w9:p2", "workspace_id": "w9", "tab_id": "w9:t1"}
    herdr_says({"name": "spawned"}, [moved])
    assert decide({"last_assistant_message": done}, spawned), "a moved pane keeps its old id in its env and is still found"
    assert run(reported, pane2) == (0, "", "reported=1 pane=w9:p2 --close\n"), "a moved pane closes by its current id"
    assert "w9:p2" in saved()
    os.unlink(f"{sandbox}/old-id")
    os.unlink(f"{sandbox}/new-id")
    herdr_says({"name": "spawned"}, [other_tab, me])
    herdr_says({"name": "spawned"}, [other_space, me])
    assert run(reported, pane2) == (0, "", closed), "a 👑 in another workspace is another life: company, home"
    herdr_says({"name": "spawned"}, [crown, me])
    assert run(reported, {**pane2, "MUNICE_AUTO_DISPATCH": "1"}) == (0, "", closed), "job tabs report on the board, not to a 👑"
    saved()

    # 요유 2026-11-01: a pane that stays open told its lead nothing, so a finished implementer sat there unreported.
    stays = json.dumps({"last_assistant_message": keep[0]})
    assert run(stays, pane2) == (0, "", "tell w1:p1\n"), "요유 몫 left: the pane stays, and its lead still hears the report"
    assert "칸은 열어 둡니다" in open(f"{sandbox}/told").read() and keep[0] in saved()
    assert run(stays, {**pane2, "MUNICE_AUTO_DISPATCH": "1"}) == (0, "", ""), "job tabs report on the board, not to a 👑"
    assert saved() == ""
    herdr_says({"name": "spawned"}, [crown, {**other_tab, "label": "🟢 🫡 구현"}, me], mine)
    assert run(stays, pane2) == (0, "", "tell w1:p7\n"), "a 🫡 that opened this pane hears it, not the 👑 above that 🫡"
    # With no pane above, a report goes sideways to another 👑. An open pane can answer, and two would go on for ever.
    second = {**me, "label": "🟢 👑 다른 리드"}
    herdr_says({"name": "spawned"}, [crown, second])
    assert run(stays, pane2) == (0, "", ""), "an open 👑 tells no other 👑"
    assert keep[0] in saved(), "the report is still kept"
    herdr_says({"name": "spawned"}, [third_tab, crown, other_tab, second], mine)
    assert run(stays, pane2) == (0, "", "tell w1:p7\n"), "an open 👑 still tells the pane that opened it"
    saved()
    child = [{"pane_id": "w1:pc", "name": "spawn-2-000000-1"}]
    for label, openers in (("🔴 감시", ()), ("🟢 👑 리드", child), ("🟢 🫡 구현", child)):
        herdr_says({"name": "spawned"}, [crown, {**me, "label": label}], openers)
        expected = "" if "👑" in label else "tell w1:p1\n"
        assert run(reported, pane2) == (0, "", expected), f"{label} stays, and its report goes up unless it would go sideways"
        if expected:
            assert "칸은 열어 둡니다" in open(f"{sandbox}/told").read(), f"{label} is not closing and must not say so"
    saved()
    herdr_says({"name": "spawned"}, [crown, {**me, "label": "🟢 🫡 구현"}])
    assert run(reported, pane2) == (0, "", "tell w1:p1\n" + closed), "a 🫡 whose panes are gone closes like the rest"
    saved()
    herdr_says({"name": "spawned"}, [crown, {**me, "label": "🟢 👑 끝난 리드"}])
    assert run(reported, pane2) == (0, "", "tell w1:p1\n" + closed), "a named 👑 whose panes are gone closes like the rest"
    saved()
    # A Stop hook can send a reply back while this one runs (10/31: share-guard asked for pane names): the draft and the
    # rewrite both went out, the draft first.
    draft, slow = full.replace("닫기 규칙", "초안"), {**pane2, "AUTO_TAB_CLOSE_SETTLE": "0.1"}
    busy = {**me, "agent_status": "working"}
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": draft}), slow)
    stop(json.dumps({"last_assistant_message": full}), slow)
    time.sleep(0.4)
    assert not os.path.exists(f"{sandbox}/calls"), "nothing goes out while the turn runs"
    herdr_says({"name": "spawned"}, [crown, me])
    assert taken() == "tell w1:p1\n" + closed, "one turn, one report"
    told, text = open(f"{sandbox}/told").read(), saved()
    assert "닫기 규칙" in told and "초안" not in told, f"the lead hears the last version: {told!r}"
    assert text.count("칸이 남습니다") == 1 and "초안" not in text, f"one copy, the last version: {text!r}"
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": full}), slow)
    stop(json.dumps({"last_assistant_message": keep[0]}), slow)
    herdr_says({"name": "spawned"}, [crown, me])
    time.sleep(0.4)
    assert taken() == "tell w1:p1\n", "the rewrite left work for 요유: nothing closes, and only the rewrite goes up"
    text = saved()
    assert keep[0] in text and full not in text, f"the last version alone: {text!r}"
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": full}), slow)
    stop(json.dumps({"last_assistant_message": keep[3]}), slow)
    herdr_says({"name": "spawned"}, [crown, me])
    time.sleep(0.4)
    assert taken() == "" and saved() == "", "the rewrite is no report: it withdraws the first"
    # 요유 2026-11-01: share-guard sent an implementer's report back, it answered one line without `남은 것`, and its lead never heard.
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": keep[0]}), slow)
    stop(json.dumps({"stop_hook_active": True, "last_assistant_message": "위 보고가 최종입니다."}), slow)
    herdr_says({"name": "spawned"}, [crown, me])
    time.sleep(0.4)
    assert taken() == "tell w1:p1\n", "a sent-back reply without a report leaves the report held before it"
    assert keep[0] in saved()
    # The session said it was done, and the hook only asked for one more step: the report stands and the pane closes, by choice.
    # A line after it cannot be told from the "확인했습니다." that Claude Code's prompt draws; the transcript keeps it.
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": full}), slow)
    stop(json.dumps({"stop_hook_active": True, "last_assistant_message": "보드 항목을 만들었습니다."}), slow)
    herdr_says({"name": "spawned"}, [crown, me])
    time.sleep(0.4)
    assert taken() == "tell w1:p1\n" + closed, "the closing report held before a sent-back line still closes"
    assert full in saved()
    herdr_says({"name": "spawned"}, [crown, me])
    stop(json.dumps({"last_assistant_message": full}), {**slow, "AUTO_TAB_CLOSE_SETTLE": "0.5"})
    herdr_says({"name": "spawned"}, [crown, busy])
    time.sleep(0.6)
    assert not os.path.exists(f"{sandbox}/calls"), "요유 typed before it settled: the pane is in use again"
    herdr_says({"name": "spawned"}, [crown, me])
    stop(json.dumps({"last_assistant_message": full}), {**pane2, "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    assert taken() == "tell w1:p1\n" + closed, "a claude -p inside the pane withdraws nothing"
    saved()
    for state in ("blocked", "unknown"):  # a dialog opened during the rewrite: this is not the end of the turn
        herdr_says({"name": "spawned"}, [crown, {**me, "agent_status": state}])
        stop(json.dumps({"last_assistant_message": full}), slow)
        time.sleep(0.4)
        assert not os.path.exists(f"{sandbox}/calls"), f"{state} is not quiet"
        herdr_says({"name": "spawned"}, [crown, me])
        assert taken() == "tell w1:p1\n" + closed, f"after {state}, idle closes"
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": full}), slow)
    open(f"{sandbox}/herdr-bad", "w").close()
    time.sleep(0.3)
    assert not os.path.exists(f"{sandbox}/calls"), "herdr cannot say: not quiet"
    os.unlink(f"{sandbox}/herdr-bad")
    herdr_says({"name": "spawned"}, [crown, me])
    assert taken() == "tell w1:p1\n" + closed, "a herdr hiccup does not end the wait"
    saved()
    herdr_says({"name": "spawned"}, [crown, busy])
    stop(json.dumps({"last_assistant_message": full}), {**slow, "AUTO_TAB_CLOSE_WAIT": "0.2"})
    assert taken() == "", "still working when the wait ends: no tell, no close"
    assert full in saved(), "the report is kept, as it was at Stop before the wait existed"
    herdr_says({"name": "spawned"}, [crown, me])
    stop(json.dumps({"last_assistant_message": "무엇을: 앞 보고\n남은 것: 요유 몫: 없음 · 세션: 닫음"}), {**pane2, "TELL_SLEEP": "0.4"})
    time.sleep(0.2)  # the first report is on its way when the next Stop comes
    stop(json.dumps({"last_assistant_message": full}), {**slow, "TELL_SLEEP": "0"})
    time.sleep(0.5)
    taken()
    assert full in saved(), "the first step's cleanup leaves the next Stop's reply alone"
    herdr_says({"name": "spawned"}, [crown, me])
    open(reports, "w").close()
    assert run(reported, pane2) == (0, "", ""), "report not saved: the pane stays, it holds the only copy"
    os.unlink(reports)
    herdr_says({"name": "spawned"}, [crown, me], [{"pane_id": "w1:p5", "name": "spawn-2-1"}])
    open(f"{sandbox}/agents-bad", "w").close()
    assert run(reported, pane2) == (0, "", "tell w1:p1\n"), "an agent list herdr cannot give: panes it opened may run, so it stays"
    os.unlink(f"{sandbox}/agents-bad")
    assert full in saved(), "and the report is still kept"

    def agent(pane, name, sid, status="idle"):
        return {"pane_id": pane, "name": name, "agent_status": status, "agent_session": {"value": sid}}

    def transcript(sid, text, folder=".claude/projects/x"):
        os.makedirs(f"{sandbox}/{folder}", exist_ok=True)
        with open(f"{sandbox}/{folder}/{sid}.jsonl", "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"id": "m", "content": [{"type": "text", "text": text}]}}) + "\n")

    def rollout(sid, text):
        os.makedirs(f"{sandbox}/.codex/sessions/2026/10/31", exist_ok=True)
        with open(f"{sandbox}/.codex/sessions/2026/10/31/rollout-2026-10-31T00-00-00-{sid}.jsonl", "w") as f:
            f.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": text}}) + "\n")

    rows = {"w1:p2": ("🟢 끝난 일", "x", done), "w1:p3": ("🟢 코덱스", "x", done), "w1:p4": ("🟢 요유 칸", None, done),
            "w1:p5": ("🟢 도는 중", "x", done), "w1:p6": ("🔴 감시", "x", done), "w1:p8": ("🟢 👑 리드2", "x", done),
            "w1:p9": ("🟢 나", "x", done), "w1:pa": ("🟢 결정 대기", "x", keep[0]), "w1:pb": ("🟢 기록 없음", "x", None),
            "w1:pc": ("🟢 자식", "spawn-p8-000000-1", keep[0]), "w1:pd": ("🟢 👑 끝난 리드", "x", done)}
    agents: list = []
    panes: list = [crown]
    for pane, (label, name, text) in rows.items():
        sid = "sid-" + pane[-1]
        agents.append(agent(pane, name, sid, "working" if pane == "w1:p5" else "idle"))
        panes.append({**me, "pane_id": pane, "label": label, "terminal_id": "term_" + pane[-2:].replace(":", ""),
                      "agent_session": {"value": sid}})
        if text and pane == "w1:p3":
            rollout(sid, text)
        elif text:
            transcript(sid, text)
    herdr_says({"name": "x"}, panes, agents)
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep"])
    assert (code, calls) == (0, ""), "--sweep alone only lists"
    assert "w1:p2" in out and "w1:p3" in out and "w1:pd" in out, out
    assert not any(p in out for p in ("w1:p4", "w1:p5", "w1:p6", "w1:p8", "w1:p9", "w1:pa", "w1:pb", "w1:pc")), out
    assert saved() == ""
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep", "--apply"])
    assert calls.count("--close") == 3 and "pane=w1:p2 --close" in calls and "pane=w1:p3 --close" in calls, calls
    assert calls.count("tell w1:p1") == 3, calls
    assert saved().count(done) == 3, "swept panes keep their reports too"

    # The 🔴/🟢 mark follows the report's `세션:` line, freed only when the Claude transcript shows nothing still running.
    logs = os.path.join(sandbox, ".claude", "projects", "p")
    os.makedirs(logs, exist_ok=True)

    def claude_log(*records, name="s.jsonl"):
        path = os.path.join(logs, name)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        return path

    def use(tool, i, **inp):
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": tool, "input": inp}]}}

    def say(text):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

    def result(i, text, error=False):
        return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "is_error": error,
                                                         "content": [{"type": "text", "text": text}]}]}}

    def notice(i, kind="user"):
        text = f"<task-notification>\n<task-id>t</task-id>\n<tool-use-id>{i}</tool-use-id>\n<status>completed</status>"
        return {"type": "user", "message": {"content": text}} if kind == "user" else \
            {"type": "queue-operation", "operation": "enqueue", "content": text}

    bg = [use("Bash", "toolu_a", command="sleep 5", run_in_background=True),
          result("toolu_a", "Command running in background with ID: ba1. Output is being written to: /tmp/ba1.output")]
    agent_up = [use("Agent", "toolu_g", prompt="리뷰", name="rev"), result("toolu_g", "Async agent launched successfully.\nagentId: ag1")]
    cases = {
        "nothing started": ([use("Bash", "toolu_n", command="ls")], False),
        "a background command still running": (bg, True),
        "its end notice": (bg + [notice("toolu_a")], False),
        "its end notice queued mid-turn": (bg + [notice("toolu_a", "queue")], False),
        "a notice printed in some output is not its end": (bg + [result("toolu_z", "<tool-use-id>toolu_a</tool-use-id>")], True),
        "TaskStop ends it": (bg + [use("TaskStop", "toolu_s", task_id="ba1"),
                                   result("toolu_s", '{"message":"Successfully stopped task: ba1 (sleep 5)"}')], False),
        "a Monitor running": ([use("Monitor", "toolu_m", command="x"), result("toolu_m", "Monitor started (task bm1, ...)")], True),
        "an agent that finished": (agent_up + [notice("toolu_g")], False),
        "an agent woken again by SendMessage": (agent_up + [notice("toolu_g"), use("SendMessage", "toolu_q", to="rev")], True),
        "an agent a hook refused never started": ([use("Agent", "toolu_h", prompt="x"),
                                                   result("toolu_h", "PreToolUse:Agent hook error", error=True)], False),
        "a session cron": ([use("CronCreate", "toolu_c", cron="*/30 * * * *")], True),
        "a cron deleted": ([use("CronCreate", "toolu_c", cron="*/30 * * * *"), use("CronDelete", "toolu_d", id="c1")], False),
        "a wakeup pending": ([use("ScheduleWakeup", "toolu_w", delaySeconds=60)], True),
        "a loop stopped": ([use("ScheduleWakeup", "toolu_w", delaySeconds=60), use("ScheduleWakeup", "toolu_x", stop=True)], False),
        "a hand-set hold": ([use("Bash", "toolu_l", command="pane-label --hold")], True),
        "a hold freed by hand": ([use("Bash", "toolu_l", command="pane-label --hold"), use("Bash", "toolu_f", command="pane-label --free")], False),
        # Codex review, 2026-11-03: a refused stop is no stop, a searched flag is no flag, a 닫으면 report holds until replaced.
        "a CronDelete that failed": ([use("CronCreate", "toolu_c", cron="*/30 * * * *"), use("CronDelete", "toolu_d", id="c1"),
                                      result("toolu_d", "no such job", error=True)], True),
        "a CronCreate refused": ([use("CronCreate", "toolu_c", cron="*/30 * * * *"), result("toolu_c", "refused", error=True)], False),
        "a loop stop that failed": ([use("ScheduleWakeup", "toolu_w", delaySeconds=60), use("ScheduleWakeup", "toolu_x", stop=True),
                                     result("toolu_x", "failed", error=True)], True),
        "a search for --free is no free": ([use("Bash", "toolu_l", command="pane-label --hold"),
                                            use("Bash", "toolu_r", command="rg 'pane-label --free' tools/agent-hooks")], True),
        "a search for --hold is no hold": ([use("Bash", "toolu_r", command='grep -n "pane-label --hold" pane-hold.sh')], False),
        "a free by its path after &&": ([use("Bash", "toolu_l", command="pane-label --hold"),
                                         use("Bash", "toolu_f", command="cd /w && ~/.dotfiles/tools/agent-hooks/pane-label --free")], False),
        "one of two crons refused": ([use("CronCreate", "toolu_c", cron="*/30 * * * *"), use("CronCreate", "toolu_e", cron="0 * * * *"),
                                      result("toolu_c", "refused", error=True), result("toolu_e", "c2")], True),
        "a free that never ran": ([use("Bash", "toolu_l", command="pane-label --hold"),
                                   use("Bash", "toolu_f", command="cleanup && pane-label --free"),
                                   result("toolu_f", "Exit code 1", error=True)], True),
        "a free inside quotes": ([use("Bash", "toolu_l", command="pane-label --hold"),
                                  use("Bash", "toolu_p", command="printf '%s\\n' 'x; pane-label --free'")], True),
        "a free inside a heredoc": ([use("Bash", "toolu_l", command="pane-label --hold"),
                                     use("Bash", "toolu_p", command="cat > notes.md <<'EOF'\npane-label --free\nEOF")], True),
        "a free after a heredoc": ([use("Bash", "toolu_l", command="pane-label --hold"),
                                    use("Bash", "toolu_p", command="cat > n <<EOF\nx\nEOF\necho ok; pane-label --free")], False),
        "a hold in a command that failed after it": ([use("Bash", "toolu_l", command="pane-label --hold; false"),
                                                      result("toolu_l", "Exit code 1", error=True)], True),
        "a command moved to the background on its own": ([use("Bash", "toolu_o", command="npm test"),
                                                           result("toolu_o", "Command running in background with ID: bo1. Output ...")], True),
        "the later of two wakeups held": ([use("ScheduleWakeup", "toolu_w", delaySeconds=60), use("ScheduleWakeup", "toolu_x", delaySeconds=90),
                                           result("toolu_w", "failed", error=True), result("toolu_x", "ok")], True),
        "the later of two holds held": ([use("Bash", "toolu_l", command="pane-label --hold"), use("Bash", "toolu_m", command="pane-label --hold"),
                                         result("toolu_l", "Exit code 1", error=True), result("toolu_m", "")], True),
        "the last report says 닫으면": ([say(keep[1])], True),
        "a later report replaced it": ([say(keep[1]), say(done)], False),
        "a 닫으면 inside code is no report": ([say("형식은 `세션: 닫으면 Monitor 멈춤` 입니다.")], False),
    }
    for label, (records, running) in cases.items():
        assert still_running(claude_log(*records)) == running, label
    assert not still_running(claude_log(say(keep[1])), claim=False), "the reply being judged replaces the last report"
    with open(claude_log(*bg), "a") as f:
        f.write('{"type": "assistant", "mess')  # a line still being written
    try:
        still_running(os.path.join(logs, "s.jsonl"))
        raise AssertionError("a line it cannot read must not count as nothing running")
    except ValueError:
        pass

    finished = claude_log(*bg, notice("toolu_a"), name="done.jsonl")
    busy = claude_log(*bg, name="busy.jsonl")
    codex = os.path.join(sandbox, ".codex", "sessions", "rollout.jsonl")
    os.makedirs(os.path.dirname(codex), exist_ok=True)
    open(codex, "w").close()
    mine = {"HERDR_PANE_ID": "w1:p2"}

    def mark(text, path, label, env=mine):
        herdr_says({"name": None}, [{**me, "label": label}])  # a session 요유 opened: no close step, only the mark
        return run(json.dumps({"last_assistant_message": text, "transcript_path": path}), env)[2]

    freed, held_ = "reported= pane=w1:p2 --free\n", "reported= pane=w1:p2 --hold\n"
    assert mark(done, finished, "🔴 일") == freed, "닫아도 됨 and nothing running: 🟢"
    assert mark(keep[3], finished, "🔴 일") == freed, "a reply with no report frees too: the mark says what runs"
    assert mark(done, busy, "🔴 일") == "", "a command still running keeps 🔴 whatever the report says"
    assert mark(keep[3], busy, "🟢 일") == "", "a watch the agent freed by hand stays 🟢: never re-held for running work"
    assert mark(done, finished, "🟢 일") == "", "already 🟢: no call"
    waiting = claude_log(*bg, notice("toolu_a"), say(keep[1]), name="waiting.jsonl")
    assert mark("위 보고가 최종입니다.", waiting, "🔴 일") == "", "a Stop with no new report keeps the 닫으면 of the last one"
    assert mark(done, waiting, "🔴 일") == freed, "a new report replaces it"
    assert mark(done, os.path.join(logs, "gone.jsonl"), "🔴 일") == "", "a transcript it cannot read changes nothing"
    assert mark(done, codex, "🔴 일") == "", "Codex: its log is not read, so it is never freed"
    assert mark(keep[2], finished, "🟢 일") == held_, "닫으면 … : 🔴"
    assert mark(keep[2], codex, "🟢 일") == held_, "Codex holds by its report too"
    assert mark(keep[2], finished, "🔴 일") == "", "already 🔴: no call"
    assert mark(keep[2], finished, "🟢 칸 🔴 자동 해제") == held_, "the mark leads the label; 🔴 inside the name is not one"
    assert mark(done, finished, "🟢 칸 🔴 자동 해제") == "", "nor is it a 🔴 to free"
    assert not held("🟢 칸 🔴 자동 해제", "term_2", []), "a 🔴 inside the name does not keep a finished pane open"
    assert mark(done, finished, "🔴 일", {**mine, "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}) == "", "a claude -p in the pane is not its session"
    assert mark(done, finished, "🔴 일", {}) == "", "outside herdr"
    open(f"{sandbox}/herdr-bad", "w").close()
    assert mark(done, finished, "🔴 일") == "", "herdr not answering changes nothing"
    os.unlink(f"{sandbox}/herdr-bad")

    # The main pane's panes run on the workers server: the report crosses servers to the main pane, in short form.
    W = f"{sandbox}/sessions/workers/herdr.sock"
    sessions(default=f"{sandbox}/herdr.sock", workers=W)
    main = {"pane_id": "w5:p3", "tab_id": "w5:t1", "workspace_id": "w5", "label": "🟢 👑 메인 오케스트레이터", "terminal_id": "term_M"}
    os.makedirs(f"{sandbox}/.local/state/orchestrator", exist_ok=True)
    open(f"{sandbox}/.local/state/orchestrator/main", "w").write("term_M")  # stop() runs with HOME=sandbox
    wagents = [{"pane_id": "w1:p2", "name": "spawn-M-1", "terminal_id": "term_2"}]
    herdr_says({"name": "spawned"}, [main])                                     # default server
    herdr_says_on("workers", {"name": "spawn-M-1"}, [me], wagents)
    on_workers = {"HERDR_PANE_ID": "w1:p2", "HERDR_SOCKET_PATH": W}
    assert run(reported, on_workers) == (0, "", "tell default/w5:p3\n" + closed), "a workers pane reports across servers to the main pane"
    told = open(f"{sandbox}/told").read()
    assert told.startswith("이 칸의 마지막 보고입니다(") and share.DELEGATE_NOTE in told, "share-guard still sees a relayed report"
    assert share.RELAY_NOTE not in told and "pane-spawn --check" in told, f"요유 2026-11-05: the main pane hands the check to a check pane: {told!r}"
    assert "조사를 마쳤습니다." in told and "남은 것: 요유 몫: 없음 · 세션: 닫음" in told
    assert "왜: 칸이 남습니다." not in told, "the main pane gets the short form; the whole reply stays in the file"
    assert "workers/w1:p2" in told and "전체 보고:" in told
    assert os.path.exists(f"{sandbox}/.local/state/orchestrator/reported/term_2"), "the watch learns this pane reported"
    assert "workers/w1:p2" in saved()
    # A check pane's report ends the round: it asks for the `확인:` line and no other check. pane-spawn's name is what tells it apart.
    checker = [{"pane_id": "w1:p2", "name": "spawn-M-chk1", "terminal_id": "term_2"}]
    herdr_says_on("workers", {"name": "spawn-M-chk1"}, [me], checker)
    assert run(reported, on_workers) == (0, "", "tell default/w5:p3\n" + closed), "a check pane reports to the main pane"
    told = open(f"{sandbox}/told").read()
    assert told.startswith("확인 칸의 마지막 보고입니다(") and share.CHECK_NOTE in told, f"the head says it is a check pane's: {told!r}"
    assert share.RELAY_NOTE not in told and share.DELEGATE_NOTE not in told and "pane-spawn" not in told, "no check of a check"
    assert "조사를 마쳤습니다." in told and "전체 보고:" in told, "the same short form"
    main_log = os.path.join(sandbox, "main.jsonl")
    with open(main_log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": told}}, ensure_ascii=False) + "\n")
    herdr_says({"name": "spawned"}, [{**main, "agent_session": {"value": "s-main"}}])
    herdr_says_on("workers", None, [], [])  # the check pane has closed
    main_env ={"HERDR_BIN_PATH": f"{sandbox}/herdr", "HERDR_SOCKET_PATH": f"{sandbox}/herdr.sock", "HOME": sandbox, "EXPLAIN_STATE": sandbox}
    report = {"session_id": "s-main", "transcript_path": main_log}
    assert share.decide({**report, "last_assistant_message": "무엇을: 고침\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨"}, main_env) == share.CHECK_REPORT_REASON, \
        "share-guard knows the check pane's head, and sends back a report with no 확인 line"
    assert share.decide({**report, "last_assistant_message": "확인: 확인 칸이 테스트 통과를 봤습니다.\n무엇을: 고침\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨"},
                        main_env) is None, "and takes what the check pane saw"
    os.unlink(main_log)
    saved()
    # A check pane's pass or fail must arrive whatever the length: a 224-character first line ending in it was cut off (2026-11-05).
    long_line = "`npm test`, `herdr_peers.py --selftest`를 돌렸고 " + "차등 시험도 다시 돌려 보았고 " * 20 + "통과하지 못했습니다."
    assert len(long_line) > 200, "the case this pins"
    long_report = json.dumps({"last_assistant_message": f"{long_line}\n\n자세한 내용은 아래와 같습니다.\n남은 것: 요유 몫: 없음 · 세션: 닫음"})
    herdr_says({"name": "spawned"}, [main])
    herdr_says_on("workers", {"name": "spawn-M-chk1"}, [me], checker)
    assert run(long_report, on_workers) == (0, "", "tell default/w5:p3\n" + closed)
    told = open(f"{sandbox}/told").read()
    assert long_line in told, f"a check pane's first line arrives whole: {told!r}"
    assert "자세한 내용은" not in told and "남은 것: 요유 몫: 없음" in told, "and the rest stays in the file, as ever"
    saved()
    herdr_says_on("workers", {"name": "spawn-M-1"}, [me], wagents)
    assert run(long_report, on_workers) == (0, "", "tell default/w5:p3\n" + closed)
    told = open(f"{sandbox}/told").read()
    assert long_line[:200] in told and long_line[:201] not in told and "통과하지 못했습니다" not in told, f"any other pane's stays cut: {told!r}"
    saved()
    # Any other lead: the same check-pane name changes nothing, and neither does a main-pane file that names no live pane.
    herdr_says({"name": "spawned"}, [crown])
    herdr_says_on("workers", {"name": "spawn-1-chk1"}, [crown, me], [{"pane_id": "w1:p2", "name": "spawn-1-chk1", "terminal_id": "term_2"}])
    assert run(reported, on_workers) == (0, "", "tell w1:p1\n" + closed)
    told = open(f"{sandbox}/told").read()
    assert told.startswith("이 칸의 마지막 보고입니다(") and share.RELAY_NOTE in told and "pane-spawn" not in told, f"an ordinary lead hears the old rule: {told!r}"
    saved()
    # And it still reads the whole report, a long first line included.
    assert run(long_report, on_workers) == (0, "", "tell w1:p1\n" + closed)
    told = open(f"{sandbox}/told").read()
    assert long_line in told and "자세한 내용은" in told, f"an ordinary lead gets the whole reply: {told!r}"
    saved()
    state_file = f"{sandbox}/.local/state/orchestrator/main"
    for gone in ("missing", "stale"):
        if os.path.exists(state_file):
            os.unlink(state_file)
        if gone == "stale":
            open(state_file, "w").write("term_gone")
        herdr_says_on("workers", {"name": "spawn-M-1"}, [me], wagents)
        herdr_says({"name": "spawned"}, [main])
        assert run(reported, on_workers)[0] == 0
        told = open(f"{sandbox}/told").read()
        assert told.startswith("이 칸의 마지막 보고입니다(") and share.RELAY_NOTE in told and "pane-spawn" not in told, f"main file {gone}: the ordinary rule: {told!r}"
        saved()
    open(state_file, "w").write("term_M")
    herdr_says_on("workers", {"name": "spawn-M-1"}, [me], wagents)
    herdr_says({"name": "spawned"}, [main])
    # The same pane id on both servers: a Stop on one never withdraws the other's held reply.
    herdr_says_on("workers", {"name": "spawn-M-1"}, [{**me, "agent_status": "working"}], wagents)
    stop(json.dumps({"last_assistant_message": full}), {**on_workers, "AUTO_TAB_CLOSE_SETTLE": "0.1"})
    stop(json.dumps({"last_assistant_message": keep[3]}), {"HERDR_PANE_ID": "w1:p2"})   # default w1:p2: no report, withdraws its own only
    herdr_says_on("workers", {"name": "spawn-M-1"}, [me], wagents)
    assert taken() == "tell default/w5:p3\n" + closed, "the workers pane's report survives the default pane's Stop"
    saved()
    # The short form keeps code and quotes in `남은 것:`: they are what 요유 has to type.
    assert short_report("끝났습니다.\n남은 것: 요유 몫: `prod 허용` 입력 · 세션: 닫으면 \"승격\" 못 함") == \
        "끝났습니다.\n남은 것: 요유 몫: `prod 허용` 입력 · 세션: 닫으면 \"승격\" 못 함"
    assert short_report("`x` 끝났습니다.\n```\n남은 것: 코드 속\n```\n> 남은 것: 인용\n남은 것:\n- 요유 몫: `prod 허용` 입력\n- 세션: 닫아도 됨\n") == \
        "`x` 끝났습니다.\n남은 것:\n- 요유 몫: `prod 허용` 입력\n- 세션: 닫아도 됨", "a fenced or quoted 남은 것 is not the report's"
    # The first line is what a check pane ran and saw, which the `확인:` line is written from: its commands must arrive (seen 2026-11-05).
    assert short_report('`ls -l`, `wc -l`로 "hello" 한 줄을 봤습니다.\n남은 것: 세션: 닫음') == \
        '`ls -l`, `wc -l`로 "hello" 한 줄을 봤습니다.\n남은 것: 세션: 닫음', "the first line keeps its code and quotes"
    assert short_report("```\n코드\n```\n> 인용\n끝났습니다.\n남은 것: 세션: 닫음") == "끝났습니다.\n남은 것: 세션: 닫음", "a fence or a quote is still no first line"
    # Only the workers server's panes mark the watch's reported file; a pane on any other named server is not the main pane's.
    os.unlink(f"{sandbox}/.local/state/orchestrator/reported/term_2")
    herdr_says({"name": "spawned"}, [crown, me])
    assert run(reported, {"HERDR_PANE_ID": "w1:p2", "HERDR_SOCKET_PATH": f"{sandbox}/sessions/other/herdr.sock"}) == \
        (0, "", "tell w1:p1\n" + closed)
    assert not os.path.exists(f"{sandbox}/.local/state/orchestrator/reported/term_2"), "a personal server is left alone"
    herdr_says_on("workers", {"name": "spawned"}, [crown, me], [])
    assert run(reported, on_workers) == (0, "", "tell w1:p1\n" + closed)
    assert os.path.exists(f"{sandbox}/.local/state/orchestrator/reported/term_2"), "a workers pane under its own lead still marks"
    saved()

    # An ordinary lead's panes run on workers too (요유 2026-11-05). Every lead shares that server's one workspace, so its only 👑 is
    # nobody's in particular, and the same pane id on default is another pane.
    os.unlink(f"{sandbox}/.local/state/orchestrator/reported/term_2")
    lead_a = {**crown, "pane_id": "w1:p2", "label": "🟢 👑 리드 A", "terminal_id": "term_A"}
    lead_b = {**crown, "pane_id": "w5:p8", "tab_id": "w5:t8", "workspace_id": "w5", "label": "🟢 👑 리드 B", "terminal_id": "term_B"}
    stranger = {**crown, "pane_id": "w1:p9", "tab_id": "w1:t9", "label": "🟢 👑 다른 리드", "terminal_id": "term_C"}
    hidden = {**me, "label": "🟢 🫡 구현"}
    stand_in = "대신 보냅니다"

    def opened_by(name, *others):
        herdr_says_on("workers", {"name": name}, [hidden, *others], [{"pane_id": "w1:p2", "name": name, "terminal_id": "term_2"}])

    herdr_says({"name": "x"}, [main, lead_a, lead_b])
    opened_by("spawn-A-1", stranger)
    assert run(reported, on_workers) == (0, "", "tell default/w1:p2\n" + closed), "its opener hears it, not the workspace's 👑 or the main pane"
    told = open(f"{sandbox}/told").read()
    assert full in told and share.RELAY_NOTE in told and stand_in not in told, f"an ordinary lead gets the whole reply: {told!r}"
    assert os.path.exists(f"{sandbox}/.local/state/orchestrator/reported/term_2"), "the watch learns this pane reported"
    saved()
    opened_by("spawn-B-1", stranger)
    assert run(reported, on_workers) == (0, "", "tell default/w5:p8\n" + closed), "two leads at once: each hears its own panes"
    saved()
    # The lead closed: the main pane takes the report, and the head still reads as a relayed report.
    herdr_says({"name": "x"}, [main, lead_b])
    opened_by("spawn-A-1", stranger)
    assert run(reported, on_workers) == (0, "", "tell default/w5:p3\n" + closed), "a closed lead's pane reports to the main pane"
    told = open(f"{sandbox}/told").read()
    assert told.startswith("이 칸의 마지막 보고입니다(") and stand_in in told and share.DELEGATE_NOTE in told, f"and says why: {told!r}"
    assert "왜: 칸이 남습니다." not in told and "전체 보고:" in told, "the main pane's usual short form"
    assert herdr_peers.HOME_NOTE not in told, "a company pane's report has no such note"
    saved()
    opened_by("spawn-A-1", {**stranger, "tab_id": "w1:t1"})
    assert run(reported, on_workers) == (0, "", "tell w1:p9\n" + closed), "a 👑 in the same tab is sure to be its own"
    assert stand_in in open(f"{sandbox}/told").read()
    saved()
    # The lead's pane is there but it cannot hear (its agent quit): nobody would see this hidden pane stop.
    herdr_says({"name": "x"}, [main, lead_a])
    opened_by("spawn-A-1")
    assert run(reported, {**on_workers, "TELL_REFUSE": "default/w1:p2"}) == (0, "", "tell default/w1:p2\ntell default/w5:p3\n" + closed), \
        "a lead that refuses: the main pane"
    assert stand_in in open(f"{sandbox}/told").read()
    saved()
    herdr_says({"name": "x"}, [crown, me])
    assert run(reported, {**pane2, "TELL_REFUSE": "w1:p1"}) == (0, "", "tell w1:p1\n" + closed), "on default the tab is in sight: as before"
    saved()
    # Home: the note goes with the report only when it leaves the Home lead.
    herdr_says({"name": "x"}, [main, lead_a])
    opened_by("spawn-A-h1")
    assert run(reported, on_workers)[2] == "tell default/w1:p2\n" + closed
    assert herdr_peers.HOME_NOTE not in open(f"{sandbox}/told").read(), "its own lead is at home"
    saved()
    herdr_says({"name": "x"}, [main])
    assert run(reported, on_workers)[2] == "tell default/w5:p3\n" + closed
    assert herdr_peers.HOME_NOTE in open(f"{sandbox}/told").read(), "요유 2026-11-05: the main pane must keep it off company Slack and the board"
    saved()
    # No lead, no main pane: the file only, never another lead's session.
    os.unlink(state_file)
    os.unlink(f"{sandbox}/.local/state/orchestrator/reported/term_2")
    herdr_says({"name": "x"}, [lead_b])
    opened_by("spawn-A-1", stranger)
    assert run(reported, on_workers) == (0, "", closed), "no receiver: the report stays in the file, and no other lead reads it as a task"
    assert full in saved()
    assert not os.path.exists(f"{sandbox}/.local/state/orchestrator/reported/term_2"), "nobody heard it: the watch still says so"
    open(state_file, "w").write("term_M")
    # A list it cannot read is no closed lead. Sent sideways, a Home pane's report would reach the main pane with no word of it, and the
    # mark would silence the watch while its lead waits. So: the file, no mark, and the pane stays for the watch to reach its lead.
    report_mark = f"{sandbox}/.local/state/orchestrator/reported/term_2"
    herdr_says({"name": "x"}, [main, lead_a])
    opened_by("spawn-A-h1")
    open(f"{sandbox}/workers/agents-bad", "w").close()
    assert run(reported, on_workers) == (0, "", "tell default/w1:p2\n"), "its agent list unreadable: its own name still finds its lead, and it stays"
    told = open(f"{sandbox}/told").read()
    assert herdr_peers.HOME_NOTE not in told and stand_in not in told, f"to its own lead: {told!r}"
    saved(); os.unlink(report_mark)
    open(f"{sandbox}/agent-gone", "w").close()
    finish(done, {"HOME": sandbox, "HERDR_BIN_PATH": fake_herdr, "PANE_REPORT_DIR": reports, "AUTO_TAB_CLOSE_PANE_LABEL": fake,
                  "AUTO_TAB_CLOSE_PANE_TELL": fake_tell, "PATH": os.environ.get("PATH", ""), **on_workers})
    assert taken() == "", "no name at all on workers: nobody is told"
    assert done in saved() and not os.path.exists(report_mark)
    os.unlink(f"{sandbox}/agent-gone"); os.unlink(f"{sandbox}/workers/agents-bad")
    opened_by("spawn-A-1")
    open(f"{sandbox}/panes-bad", "w").close()
    assert run(reported, on_workers) == (0, "", ""), "default's list unreadable: its lead may be there"
    assert full in saved() and not os.path.exists(report_mark)
    os.unlink(f"{sandbox}/panes-bad")
    # A pane with an agent name that 요유 made a lead opens on workers: it waits for those panes as for any it opened.
    promoted = {**me, "pane_id": "w5:p2", "tab_id": "w5:t2", "workspace_id": "w5", "label": "🟢 👑 승격", "terminal_id": "term_P"}
    herdr_says({"name": "spawn-Z-1"}, [main, promoted], [{"pane_id": "w5:p2", "name": "spawn-Z-1"}])
    herdr_says_on("workers", None, [hidden], [{"pane_id": "w1:p2", "name": "spawn-P-1"}])
    assert "--close" not in run(reported, {"HERDR_PANE_ID": "w5:p2"})[2], "a pane of its own alive on workers: it stays"
    saved()
    herdr_says_on("workers", None, [], [])
    open(f"{sandbox}/workers/agents-bad", "w").close()
    assert "--close" not in run(reported, {"HERDR_PANE_ID": "w5:p2"})[2], "workers running and its list unreadable: it may have some"
    saved()
    os.unlink(f"{sandbox}/workers/agents-bad")
    os.rename(f"{sandbox}/sessions.json", f"{sandbox}/sessions.away")
    assert "--close" not in run(reported, {"HERDR_PANE_ID": "w5:p2"})[2], "no telling whether workers runs: it may have some"
    saved()
    os.rename(f"{sandbox}/sessions.away", f"{sandbox}/sessions.json")
    assert "--close" in run(reported, {"HERDR_PANE_ID": "w5:p2"})[2], "none left: it closes"
    saved()
    # --sweep visits both servers, each pane with its own server's lists. The same ids exist on both.
    for sid in ("sid-d2", "sid-d9", "sid-w2", "sid-w9"):
        transcript(sid, done)
    default_rows = [{**me, "terminal_id": "term_d2"}, {**me, "pane_id": "w1:p9", "terminal_id": "term_d9"}, main]
    workers_rows = [{**me, "terminal_id": "term_w2"}, {**me, "pane_id": "w1:p9", "terminal_id": "term_w9"}]
    herdr_says({"name": "x"}, default_rows, [agent("w1:p2", "x", "sid-d2"), agent("w1:p9", "x", "sid-d9")])
    herdr_says_on("workers", {"name": "x"}, workers_rows, [agent("w1:p2", "spawn-gone-1", "sid-w2"), agent("w1:p9", "spawn-gone-2", "sid-w9")])
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep"])
    assert (code, calls) == (0, ""), "--sweep alone only lists"
    assert sorted(line.split("\t")[0] for line in out.splitlines()) == ["w1:p2", "workers/w1:p2", "workers/w1:p9"], f"only the caller itself is skipped: {out}"
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9", "HERDR_SOCKET_PATH": W}, ["--sweep"])
    assert sorted(line.split("\t")[0] for line in out.splitlines()) == ["w1:p2", "w1:p9", "workers/w1:p2"], f"from workers, the same: {out}"
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep", "--apply"])
    assert calls.count("--close") == 3 and calls.count("tell default/w5:p3") == 2, f"workers panes whose lead closed report to the main pane: {calls}"
    text = saved()
    assert "workers/w1:p2" in text and "workers/w1:p9" in text and text.count(done) == 3, f"each saved under its own server: {text}"
    herdr_says_on("workers", {"name": "x"}, workers_rows, [agent("w1:p2", "spawn-d2-1", "sid-w2", "working")])
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep"])
    assert "w1:p2" not in out, f"a default pane with a pane of its own at work on workers stays: {out}"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    if "--sweep" in sys.argv:
        sweep(os.environ, "--apply" in sys.argv)
        sys.exit()
    if "--after-turn" in sys.argv:
        after_turn(sys.argv[2], os.environ)
        sys.exit()
    try:
        payload = json.load(sys.stdin)
        sync_mark(payload, os.environ)  # first: a stale 🔴 would also keep a finished job pane from closing
        if not no_new_report(payload):
            defer(decide(payload, os.environ), os.environ)
    except Exception:  # never break a session
        pass
    sys.exit(0)
