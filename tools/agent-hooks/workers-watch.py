#!/usr/bin/env python3
"""launchd, once a minute: tell a lead when a pane it opened on the hidden `workers` herdr server needs it.

Panes a lead opens run on the workers server, which nobody looks at, so a pane stuck in a dialog or stopped without a report
would sit there unseen. Each alert goes by `pane-tell` to the pane that opened it (the terminal in its agent name, which is
how several leads at once each hear only of their own), else the main pane, else a herdr notification on the default server:
- an agent `blocked` (an approval or question dialog) for 60 s or more, once per state_change_seq;
- an agent `idle`/`done` for 900 s or more that has not reported since it stopped (auto-tab-close.py touches
  `{state_dir}/reported/<terminal>` when its report was sent), once per state_change_seq;
- a pane whose name has 🫡 but no agent in it, once per pane (the unnamed root shell pane is never news): at once when the
  watch saw an agent there, else after 420 s, since pane-spawn names the pane before its agent starts;
- a pane the watch saw with an agent that is now in neither list and has not reported since its last state (workers
  restarted between two ticks, or the pane closed), once; then its record goes;
- a pane whose opener is on no server (its lead closed while it runs), once: closing a lead no longer stops its panes;
- the workers server down, once while it stays down, unless nobody is there and no pane was seen (a Mac with no workers).
A test pane (`임시 …`) is news only in a dialog: its lead hears of it from yoyoo-share-guard until it closes (요유 2026-11-05).
One gone without a report gets an entry in the pane report day file instead, since share-guard sees only open panes.
Dispatch tabs run here too (요유 2026-11-05), with no opener, so these reach the main pane, named `자동 칸`. No 🫡 marks them, so their
record does: an agent that ended is news, and its pane, a bare shell, closes once someone heard. A 🔴 one waits on what it started
(CI, a long check), so it is news after JOB_HELD instead of 900 s. Nothing can be typed in one, so an alert names what is left: read
it, close it, look at the board item. One closed through pane-label is marked reported, and is no news.
Such a pane is closed, with one alert, once it has been stopped with its report sent for ORPHAN_TTL, counted from the stop or
the lead's close, whichever came last (요유 2026-11-05). A pane at work, marked 🔴, without a report or with a live child stays.
A pane stopped STOPPED s whose own last reply says it may close goes too, whoever its lead is: its close failed, or a herdr
restart brought it back with no turn left to end (workers/w1:p6G, 2026-11-05). Only once its report got out, and never the main
pane's terminal (see settle()). auto-tab-close marks a report by terminal and by session: a restart keeps only the session.
An alert about a Home lead's pane that goes to anyone but its lead carries HOME_NOTE.
On 집맥 a pane workers-move opened at 퇴근 (agent name with `r`, api#1049) has its lead, the 회사맥 main pane, on no server here: it is
no orphan, and an alert nobody here takes goes to the pane report day file as `[workers-watch] …`, not to a screen nobody watches.
After each tick `workers-move bridge` runs, which carries those entries and the moved panes' reports to the 회사맥 main pane.
A pane that stopped without a report on a sleep, network or server error (Claude's `API Error: Your computer went to sleep...`,
ENOTFOUND, Cloudflare's challenge page, a 5xx) is not news but told WAKE itself at once, before the 15-minute alert (와우 카드 04,
요유 2026-11-05: on 11/05 one sat 77 minutes until the main pane said so). The main pane is told too (wake_main(), 2026-11-13: once the network was back
nothing restarted the stopped panes or the main pane). See wake(); `{state_dir}/wake.log` keeps one line per wake.
A pane list that cannot be read is no news: nothing is sent and every record stays. State: `{state_dir}/watch.json`, terminal
id -> seq, status, since (first tick that saw this state), seen (last tick that saw it), floor (`seen` of the state before:
a report touched after it belongs to this stop, since the watch sees a new state up to a minute late), opener, home, orphaned,
lost_at (first tick that found no opener), alerted, net (this stop ended on a sleep or network error), wakes and woke (how many
wakes since the last stop that was no error, and when the last went; both outlive a state), idle_why (the last 15-minute alert
decision of this stop that `{state_dir}/idle-alert.log` has: wait, sent, fallback, unsent, reported or trial, and why; see idle_log()).
By design, two stops inside one tick interval look like one: a report for the first also covers the second.
An alert nobody took marks nothing, so the next minute sends it again. Fails open: any error exits 0 quietly. Tick failures
leave classified entries in watch-errors.log and a consecutive count in watch-errors.json; after three failures the main
pane is told once, with failed delivery retried. No arbitrary exception text is persisted. Named completed panes on default
use default-cleanup.json for the same 15-minute close retry, including on 집맥; ordinary unnamed panes are excluded.

It also restarts the main pane through `main-refresh --now` once its conversation (the last response's usage in its transcript)
passes MAIN_REFRESH_AT tokens, after an hour since a restart anyone made (past MAIN_REFRESH_FULL at once), and only
when it answered something after its first 15 minutes (its handoff load alone passes the line); see refresh_main(). State: `{state_dir}/main-size.json`, each call also a line in main-refresh's `{state_dir}/refresh.log`.
An active refresh owns its queued request and retries; a run that stopped without a restart is tried again next minute.

Test env: WORKERS_WATCH_NOW (seconds), WORKERS_WATCH_PANE_TELL (a fake pane-tell), WORKERS_WATCH_PROBE (host:port online() tries),
WORKERS_WATCH_MAIN_REFRESH (a fake main-refresh).

    workers-watch.py --selftest
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import datetime
import glob
import importlib.util
import itertools
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import unicodedata

HERE = os.path.dirname(os.path.realpath(__file__))  # python3 -I drops this folder from sys.path
_spec = importlib.util.spec_from_file_location("herdr_peers", os.path.join(HERE, "herdr_peers.py"))
herdr_peers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(herdr_peers)
_spec = importlib.util.spec_from_file_location("auto_tab_close", os.path.join(HERE, "auto-tab-close.py"))
atc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atc)  # what a pane's Stop hook reads in its last reply, and how it sends a report

DOWN = "workers 서버가 꺼져 있습니다: `launchctl kickstart gui/$(id -u)/com.yoyoo.herdr-workers`"
ORPHAN_TTL = 3600
STOPPED = 900
MAIN_REFRESH_AT = 150_000  # tokens in the main pane's conversation (the lead, 2026-11-05); a fresh one reached 141k in 40 s on 11/05
MAIN_REFRESH_GAP = 3600
# Past this the hour is not waited for: the pane grows up to ~5k tokens a minute, so an hour of it is 275-360k (11/06), near
# the 367k where Claude compacts by itself (17:54 that day, at 46 min). 250k leaves room for main-refresh's wait for an idle
# pane; the last 10 sessions passed it in 18-61 min.
MAIN_REFRESH_FULL = 250_000
MAIN_REFRESH_RETRY = 60
MAIN_REFRESH_SETTLE = 900  # a session's first 15 minutes are its handoff load, not conversation
USAGE = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
JOB_HELD = 10800
UNSENT = "보내지 못한 보고가 있다면 ~/.local/state/pane-reports에 있습니다."  # auto-tab-close.py keeps a report it had nobody to send to
WAKE = "잠자기나 네트워크·API 오류로 응답이 끊겼습니다. 끊긴 곳부터 이어서 하세요."
WAKE_GAPS = (0, 120, 300, 900, 1800)  # the n-th wake of one pane waits WAKE_GAPS[n] s after the one before; then none
# What Claude ends a turn on when the Mac slept or the network dropped: every such line in the Claude transcripts on 11/05
# (sleep 66, ENOTFOUND 42, lost 3, stopped arriving 3, ECONNRESET 3, timed out 2) and ECONNREFUSED from a test pane. Not
# /login, safeguards or rate limits: going on cannot fix those. Except Cloudflare's challenge page, which Claude words as a /login
# error: 9 stops in seven panes on 11/13 18:47-18:51, and each went on with no new login once told to. And a 5xx, which Claude calls
# a server-side issue, usually temporary: 7 stops in six panes on 11/13 21:52 and 22:21, each went on once told to.
NET_ERROR = re.compile(r"⏺ (API Error: )?(Your computer went to sleep|Connection (lost|refused|dropped|error)|Can't reach the API server"
                       r"|The response stopped arriving|Request timed out)"
                       r"|⏺ API Error: .*\((ENOTFOUND|EAI_AGAIN|ECONN[A-Z]+|ETIMEDOUT|ENET[A-Z]+|EHOSTUNREACH)\)"
                       r"|⏺ (Please run /login · )?API Error: 403 Attention Required! \| Cloudflare"
                       r"|⏺ API Error: 5\d\d ")
RULE = re.compile(r"─{3,}( .* ─+)?")
TRIAL = re.compile(r"[\s🟢🔴👑🫡]*임시 ")  # a lead's test pane (pane-spawn, pane-temp); with the space: `임시저장 API 수정` is a work name


def read_screen(env, sock, pane):
    """The pane's recent screen as plain text, lines unwrapped; None when it cannot be read."""
    try:
        r = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", "agent", "read", pane, "--source", "recent-unwrapped", "--lines", "60"],
                           capture_output=True, text=True, timeout=5, env={**os.environ, **env, "HERDR_SOCKET_PATH": sock})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def net_error(text) -> bool:
    """The last message above the input box (Claude's between its two last rules, Codex's on its last › line) is a sleep or
    network error. An error further up, with work after it, is not. Codex never ends a turn on one: it shows `Reconnecting...
    waiting for network`, stays working, and goes on by itself once the network is back (test panes 11/05). A narrow pane breaks
    a message itself, its rest indented under it: those lines are read with it."""
    raw = text.replace("\r", "").split("\n")
    lines = [line.strip() for line in raw]
    rules = [i for i, line in enumerate(lines) if len(line) >= 10 and RULE.fullmatch(line)]
    prompts = [i for i, line in enumerate(lines) if line.startswith("›")]
    end = rules[-2] if len(rules) >= 2 else prompts[-1] if prompts else len(lines)
    heads, last = [], -2
    for i, line in enumerate(lines[:end]):
        if line and line[0] in "⏺■•❯›":
            heads.append(line)
            last = i
        elif line and heads and last == i - 1 and raw[i].startswith("  "):
            heads[-1] += " " + line
            last = i
    return bool(heads) and bool(NET_ERROR.match(heads[-1]))


def online(env) -> bool:
    """The API takes a connection again, so a wake will not fail at once.
    ponytail: DNS has no timeout here, so a stuck resolver can hold this tick up to ~30 s; launchd skips a run that overlaps."""
    host, _, port = (env.get("WORKERS_WATCH_PROBE") or "api.anthropic.com:443").rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=3).close()
        return True
    except (OSError, ValueError):
        return False


def wake(env, sock, rec, pane, address, job, now) -> None:
    """Tell a pane whose turn ended on the Mac's sleep or a dropped network to go on (와우 카드 04, 요유 2026-11-05): it would
    otherwise sit until the 15-minute alert and its lead's word. Only the pane hears it, never its lead or the main pane. Once per
    stop; the next one WAKE_GAPS later; none while the API cannot be reached, so a Mac still offline is tried again next minute."""
    if "net" not in rec:  # what a stop ended on does not change: its screen is read once
        text = read_screen(env, sock, pane)
        if text is None:
            return
        rec["net"] = net_error(text)
        if not rec["net"]:
            rec["wakes"] = 0
    n = rec.get("wakes", 0)
    if not rec["net"] or "woke" in rec["alerted"] or n >= len(WAKE_GAPS) or now - rec.get("woke", 0) < WAKE_GAPS[n] or not online(env):
        return
    pt = env.get("WORKERS_WATCH_PANE_TELL") or os.path.join(HERE, "pane-tell")
    # A dispatch tab refuses pane-tell, and a prompt there without its marker counts as 요유's and lifts its guard for good
    # (dispatch/guard.sh, job-host-guard.py): typed as it is, on its server, marked.
    args, extra = ([pt, "--raw", pane, f"[auto-dispatch] {WAKE}"], {"HERDR_SOCKET_PATH": sock}) if job else ([pt, address, WAKE], {})
    try:
        ok = subprocess.run(args, env={**os.environ, **env, **extra}, timeout=10, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if ok:  # "queued" counts: it goes in once the box is empty
        rec["alerted"].append("woke")
        rec.update(wakes=n + 1, woke=now)
        with open(os.path.join(herdr_peers.state_dir(env), "wake.log"), "a") as f:
            f.write(f"{time.strftime('%F %T', time.localtime(now))} {address} wake {n + 1}\n")


def clock(t) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else "-"


def idle_log(env, now, line) -> None:
    """`{state_dir}/idle-alert.log`: one line per 15-minute alert decision of a stop, kept (11/13 21:52: three panes sat 18 minutes,
    no alert reached the main pane, and watch.json, rewritten every minute, kept no trace why). Past 1 MB only the last 2000 lines
    stay, as sync-home.sh keeps its log. Never stops the tick."""
    path = os.path.join(herdr_peers.state_dir(env), "idle-alert.log")
    try:
        if os.path.getsize(path) > 1 << 20:
            with open(path) as f:
                keep = f.readlines()[-2000:]
            with open(path + ".tmp", "w") as f:
                f.writelines(keep)
            os.replace(path + ".tmp", path)
    except OSError:
        pass
    try:
        with open(path, "a") as f:
            f.write(f"{time.strftime('%F %T', time.localtime(now))} {line}\n")
    except OSError:
        pass


def wake_main(env, now, where, main) -> None:
    """The main pane, on the default server, gets the same wake (11/13 20:38: it ended on ENOTFOUND in a dark wake and sat until
    a new message came). Its record is `{state_dir}/main-wake.json`, apart from watch.json, which keeps only workers panes."""
    sock = herdr_peers.socket_of(env, "default")
    if not sock or main not in where:
        return
    pane = where[main][1].get("pane_id")
    a = next((a for a in herdr_peers.herdr(env, "agent", "list", socket=sock).get("agents") or [] if a.get("pane_id") == pane), None)
    if not a or kind(a.get("agent_status")) != "ready":
        return
    path = os.path.join(herdr_peers.state_dir(env), "main-wake.json")
    try:
        with open(path) as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    seq, same = a.get("state_change_seq"), old.get("terminal") == main  # a new main pane starts its own count
    rec = old if same and old.get("seq") == seq else {
        "seq": seq, "terminal": main, "alerted": [], "wakes": old.get("wakes", 0) if same else 0, "woke": old.get("woke", 0) if same else 0}
    # ponytail: no 15-minute alert speaks for the main pane, so its wakes never run out: every last gap (30 min) while it stays cut off
    rec["wakes"] = min(rec["wakes"], len(WAKE_GAPS) - 1)
    wake(env, sock, rec, pane, where[main][0], False, now)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(rec, f)
    os.replace(path + ".tmp", path)


def pending_messages(env, terminal: str, sock=None) -> bool:
    """Never discard a queued relay while cleaning up an unattended pane.

    Legacy senders have no receiver record: hold cleanup conservatively until those messages drain.
    Ignore stale receiver records whose payload has already been delivered. Errors hold cleanup too.
    """
    root = os.path.join(os.path.dirname(herdr_peers.state_dir(env)), "pane-tell")
    try:
        for path in glob.glob(os.path.join(root, "waiting", "*")):
            if not os.path.isfile(path):
                continue
            try:
                with open(os.path.join(root, "receivers", os.path.basename(path))) as f:
                    receiver = f.read().splitlines()
            except FileNotFoundError:
                if os.path.exists(path):
                    return True
                continue
            if len(receiver) != 2:
                return True
            if sock:
                default = herdr_peers.server_key({"HERDR_SOCKET_PATH": sock}) in ("", "default")
                same_server = receiver[1] == sock or (receiver[1] == "default" and default)
            else:
                same_server = receiver[1].endswith("/sessions/workers/herdr.sock")
            if receiver[0] == terminal and same_server:
                return True
        # Include batches being joined or waiting to enter the regular delivery queue.
        batch = os.path.join(root, "batch", terminal)
        return any(os.path.isfile(p) and not os.path.basename(p).startswith(".")
                   for p in glob.glob(os.path.join(batch, "*", "*"))) or any(
                       glob.glob(os.path.join(batch, pattern)) for pattern in (".new.*", ".joined.*"))
    except OSError:
        return True


def settle(env, sock, a, rec, pane, reported: bool, terminal: str) -> None:
    """Close a pane stopped STOPPED s or more, not held, whose report got out and whose own last reply says it may close
    (요유 2026-11-05, workers/w1:p6G): its own close can fail (the Mac was out of processes), and a herdr restart brings a pane
    back with no turn left to end, so no Stop hook comes again. A pane whose report never got out is never closed here; it gets
    the 15-minute alert (its lead, 2026-11-05). The last reply is read once per stop, and again after a read that failed; the
    close is tried every minute until the pane goes.
    ponytail: after a restart the floor is 0, so any report its session ever sent counts as this stop's."""
    session = (a.get("agent_session") or {}).get("value") or ""
    if not reported or not session or pending_messages(env, terminal, sock):
        return
    if "closes" not in rec:
        try:
            rec["closes"] = atc.should_close(atc.last_reply(session, env.get("HOME") or ""))
        except Exception:  # unreadable now: read again next minute (Codex review)
            return
    if rec["closes"] and not pending_messages(env, terminal, sock):
        herdr_peers.herdr(env, "pane", "close", pane, socket=sock)  # {} when it failed: tried again next minute


def reported_since(env, floor, *keys) -> bool:
    for key in filter(None, keys):
        try:
            if os.path.getmtime(os.path.join(herdr_peers.state_dir(env), "reported", key)) >= floor:
                return True
        except OSError:
            pass
    return False


def cleanup_default(env, now):
    """Retry completed named panes on default too, on either host. Never alert or wake personal panes here."""
    sock = herdr_peers.socket_of(env, "default")
    if sock is None:
        raise WatchReadError()
    if not sock:
        return
    agents = herdr_peers.herdr(env, "agent", "list", socket=sock).get("agents")
    panes = herdr_peers.herdr(env, "pane", "list", socket=sock).get("panes")
    other = herdr_peers.socket_of(env, "workers")
    children = [] if other == "" else herdr_peers.herdr(env, "agent", "list", socket=other).get("agents") if other else None
    if agents is None or panes is None or children is None:
        raise WatchReadError()
    path = os.path.join(herdr_peers.state_dir(env), "default-cleanup.json")
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    by_pane = {p.get("pane_id"): p for p in panes}
    records = {}
    for a in agents:
        if not a.get("name"):
            continue  # a session the user started by hand never closes here
        pane = a.get("pane_id")
        p = by_pane.get(pane, {})
        term = p.get("terminal_id") or a.get("terminal_id")
        if not term:
            continue
        session = (a.get("agent_session") or {}).get("value") or ""
        old = state.get(term) or {}
        identity = [a.get("state_change_seq"), kind(a.get("agent_status")), session]
        rec = old if old.get("identity") == identity else {"identity": identity, "since": now, "floor": old.get("seen", 0)}
        rec["seen"] = now
        records[term] = rec
        label = p.get("label") or ""
        held = "🔴" in label or "👑" in label or atc.held(label, term, agents + children) or term == herdr_peers.main_terminal(env)
        if kind(a.get("agent_status")) == "ready" and now - rec["since"] >= STOPPED and not held:
            settle(env, sock, a, rec, pane, reported_since(env, rec["floor"], term, session), term)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(records, f)
    os.replace(path + ".tmp", path)


class WatchReadError(RuntimeError):
    """An unavailable inventory is not an empty inventory."""


def run_tick(env, now):
    """Stay fail-open while leaving diagnostics without arbitrary exception text or transcript contents."""
    path = os.path.join(herdr_peers.state_dir(env), "watch-errors.json")
    try:
        with open(path) as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    try:
        tick(env, now)
    except Exception as error:
        record.update(consecutive=record.get("consecutive", 0) + 1, timestamp=now,
                      category="inventory" if isinstance(error, WatchReadError) else "io" if isinstance(error, OSError) else "unexpected",
                      exception=type(error).__name__)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path[:-5] + ".log", "a") as f:
                f.write(json.dumps({k: v for k, v in record.items() if k != "alerted"}) + "\n")
            if record["consecutive"] >= 3 and not record.get("alerted"):
                where = herdr_peers.all_panes(env) or {}
                main = where.get(herdr_peers.main_terminal(env))
                if main:
                    pt = env.get("WORKERS_WATCH_PANE_TELL") or os.path.join(HERE, "pane-tell")
                    msg = f"[workers-watch] 감시가 {record['consecutive']}회 연속 실패했습니다. 분류: {record['category']}. 기록: {path[:-5]}.log"
                    record["alerted"] = subprocess.run([pt, main[0], msg], env={**os.environ, **env}, timeout=10,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except Exception:  # diagnostics must not replace the original failure or block the hook
            pass
    else:
        record = {"consecutive": 0, "timestamp": now}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(record, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass  # an unwritable state volume still must not break launchd


def trial(name) -> bool:
    """A test pane: yoyoo-share-guard names it at each of its lead's reports until it closes, so its stop or end is no news."""
    return bool(TRIAL.match(unicodedata.normalize("NFC", name or "")))


def stamp(d) -> float:
    return datetime.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()


def last_usage(path) -> tuple:
    """(tokens in the conversation as of its last response, prompt and output; when it came), from the transcript's last 4 MB:
    a subagent's line or a synthetic one with no usage does not count."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - (4 << 20)))
        lines = f.read().splitlines()
    for raw in reversed(lines):
        if b'"usage"' not in raw:
            continue
        try:
            d = json.loads(raw)
            u = d["message"]["usage"]
            # A response that called the advisor lists its model calls in `iterations` and sums them above: the last call is the size.
            calls = [i for i in u.get("iterations") or [] if i.get("type") == "message"]
            n, at = sum(int((calls[-1] if calls else u).get(k) or 0) for k in USAGE), stamp(d)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if d.get("type") == "assistant" and not d.get("isSidechain") and n:
            return n, at
    return 0, 0.0


def born(path) -> float:
    """When the session started: its transcript's first timestamp (a restart, any caller's, makes a new transcript)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in itertools.islice(f, 200):
            try:
                return stamp(json.loads(line))
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return 0.0


def refresh_main(env, now, where, main) -> None:
    """main-refresh --now once the main pane's conversation passes MAIN_REFRESH_AT tokens: it re-reads all of it for each
    message (11/05: about 370k a notice). An hour after any restart; stopped attempts retry next minute."""
    sid = ((where.get(main, ("", {}))[1].get("agent_session") or {}).get("value") or "") if main else ""
    found = glob.glob(os.path.join(env.get("HOME") or os.path.expanduser("~"), ".claude", "projects", "*", glob.escape(sid) + ".jsonl")) if sid else []
    if not found:
        return
    path = os.path.join(herdr_peers.state_dir(env), "main-size.json")
    try:
        with open(path) as f:
            asked = json.load(f)
    except (OSError, ValueError):
        asked = {}
    asked = asked if isinstance(asked, dict) else {}
    start = born(found[0])
    if now - float(asked.get(main) or 0) < MAIN_REFRESH_RETRY:
        return
    lock = os.path.join(herdr_peers.state_dir(env), "refresh.lock")
    # main-refresh recovers locks older than two hours itself. Never spawn competing waits for a live run.
    if os.path.isdir(lock) and now - os.path.getmtime(lock) < 7200:
        return
    size, at = last_usage(found[0])
    # A fresh session is past the line once its handoff is loaded (11/05: 141k in 40 s), so a pane that has answered nothing
    # since would be restarted every hour all night for nothing: it re-reads only when a message comes.
    if size <= MAIN_REFRESH_AT or at < start + MAIN_REFRESH_SETTLE or (now - start < MAIN_REFRESH_GAP and size <= MAIN_REFRESH_FULL):
        return
    # From the workers server or launchd alike: the server variables would point main-refresh away from the main pane.
    child = {k: v for k, v in {**os.environ, **env}.items() if k not in ("HERDR_SOCKET_PATH", "HERDR_SESSION", "HERDR_PANE_ID")}
    try:
        subprocess.Popen([env.get("WORKERS_WATCH_MAIN_REFRESH") or os.path.join(HERE, "main-refresh"), "--now"], env=child,
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return  # not started: tried again next minute
    with open(path + ".tmp", "w") as f:
        json.dump({main: now}, f)
    os.replace(path + ".tmp", path)
    with open(os.path.join(herdr_peers.state_dir(env), "refresh.log"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} 메인 칸 대화 {size} 토큰: workers-watch가 main-refresh --now 부름\n")


def day_entry(env, now, pane, name, text, *, agent="", session="") -> bool:
    """One entry in the pane report day file, headed as auto-tab-close.py heads a report."""
    folder = env.get("PANE_REPORT_DIR") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state", "pane-reports")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, time.strftime("%Y-%m-%d", time.localtime(now)) + ".md"), "a", encoding="utf-8") as f:
            identity = f"세션 {session}, 폴더 , 에이전트 {agent}\n\n" if agent else ""
            f.write(f"\n## {time.strftime('%H:%M:%S', time.localtime(now))} workers/{pane or '?'} {name}\n\n{identity}{text}\n")
        return True
    except OSError:
        return False


def keep_trial(env, old, term, now) -> bool:
    """A test pane gone without a report is no news for the main pane, but its lead may still be waiting on that report: one
    entry in the pane report day file says where its session is (11/05 check: it used to vanish without a trace)."""
    return day_entry(env, now, old.get("pane"), old.get("name") or term,
                     f"workers-watch: 시험 칸이 보고 없이 사라졌습니다. 세션 {old.get('session') or '?'}, 연 칸 {old.get('opener') or '?'}. "
                     f"보고가 있었다면 그 세션 기록에 남아 있습니다.", agent=old.get("agent_name") or "", session=old.get("session") or "")


def kind(status) -> str:
    """herdr flips done<->idle when a pane is seen; that is the same stop, not a new state."""
    return "ready" if status in ("idle", "done") else status or ""


def notify(env, msg) -> bool:
    """The last resort: a durable error queue, independent of routine toasts."""
    child = {**os.environ, **env}
    override = child.get("HERDR_ERROR_NOTIFY")
    command = [override] if override else ["/usr/bin/python3", "-I", os.path.join(HERE, "herdr-error-notify.py")]
    try:
        return subprocess.run([*command, "밑 칸을 맡을 곳이 없습니다", msg],
                              env=child, timeout=15, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def startup_closed(env, old, now) -> bool:
    """A fresh pane-spawn receipt confirms intentional cleanup after failed check startup.

    Match the check token and a close since the last observation: herdr can reuse pane ids after restart.
    Missing or invalid receipts leave the ordinary lost-pane warning intact.
    """
    seen = old.get("seen")
    token = atc.CHECK_TOKEN.search(old.get("name") or "")
    if not token or not isinstance(seen, (int, float)) or not old.get("pane"):
        return False
    folder = os.path.join(herdr_peers.state_dir(env), "automatic-checks")
    for path in glob.glob(os.path.join(folder, token[1] + ".json.start.*.json")):
        try:
            with open(path, encoding="utf-8") as stream:
                if not seen <= os.fstat(stream.fileno()).st_mtime <= now:
                    continue
                receipt = json.load(stream)
            if isinstance(receipt, dict) and receipt.get("status") == "closed" and receipt.get("address") == "workers/" + old["pane"]:
                return True
        except (OSError, ValueError):
            continue
    return False


def tick(env, now: float) -> list:
    """One round; returns the alerts someone took."""
    try:
        atc.relay_watch.tick(env, now)
    except (OSError, ValueError):
        pass  # queue alerts must not stop the existing pane watch
    where = herdr_peers.all_panes(env)
    if where is None:  # no way to know who is there, so no alert and no record changes
        raise WatchReadError()
    cleanup_error = None
    try:
        cleanup_default(env, now)
    except Exception as error:
        cleanup_error = error  # independent servers keep their alerts and retries; run_tick still records this failure
    main = herdr_peers.main_terminal(env)
    try:
        refresh_main(env, now, where, main)
    except Exception:  # a fault here must not cost the alerts below
        pass
    try:
        wake_main(env, now, where, main)
    except Exception:
        pass
    path = os.path.join(herdr_peers.state_dir(env), "watch.json")
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):  # `null` or a list would raise every minute and silence the watch for good
        state = {}
    state = {k: v for k, v in state.items() if k == "_down" or isinstance(v, dict)}
    told: list = []
    failed: list = []  # each pane-tell the last tell() tried that took nothing: "<address> rc=<exit> <its last error line>"
    took = [""]  # what took the last tell(): the address of the pane-tell that did, "day file", "error queue", or "" for nothing

    def tell(msg, opener="", home=False, moved=None, urgent=True) -> bool:
        """moved: (pane, label, agent name, session) of a pane workers-move opened here, whose lead is on the 회사맥: the day file takes what nobody
        here heard, and `workers-move bridge` carries it there. It is no alert someone took, so it is not in `told`."""
        pt = env.get("WORKERS_WATCH_PANE_TELL") or os.path.join(HERE, "pane-tell")
        failed.clear()
        took[0] = ""
        to = list(dict.fromkeys(t for t in (opener, "" if moved else main) if t in where))
        if not to:
            failed.append("no lead or main pane to tell")
        for term in to:  # a lead that cannot hear (its agent ended) is no stop
            text = f"{msg} {herdr_peers.HOME_NOTE}" if home and term != opener else msg
            with tempfile.TemporaryFile() as err:  # a file, not a pipe: a delivery pane-tell leaves behind would hold a pipe open
                try:
                    rc = subprocess.run([pt, where[term][0], text], env={**os.environ, **env}, timeout=10, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=err).returncode
                except subprocess.TimeoutExpired:
                    rc = "timeout"
                except OSError as error:
                    rc = type(error).__name__
                if rc == 0:
                    told.append(text)
                    took[0] = where[term][0]
                    return True
                err.seek(max(0, err.seek(0, os.SEEK_END) - 2000))  # its end: the last line says why
                last = (err.read().decode(errors="replace").strip().splitlines() or [""])[-1][:200]
            failed.append(f"{where[term][0]} rc={rc} {last}".strip())
        if moved:
            took[0] = "day file"
            return day_entry(env, now, moved[0], moved[1], f"[workers-watch] {msg}",
                             agent=moved[2], session=moved[3])
        if not urgent:
            return True  # routine cleanup needs no retry when nobody can receive its status
        if notify(env, msg):  # 요유's own screen needs no warning about 요유's own matters
            took[0] = "error queue"
            told.append(msg)
            return True
        return False

    def reported_since(floor, *keys) -> bool:
        """auto-tab-close marks a pane by its terminal and its session; a herdr restart keeps only the session."""
        for key in filter(None, keys):
            try:
                if os.path.getmtime(os.path.join(herdr_peers.state_dir(env), "reported", key)) >= floor:
                    return True
            except OSError:
                pass
        return False

    sock = herdr_peers.socket_of(env, "workers")
    if sock is None:  # a list we could not read is no news
        raise WatchReadError()
    if not sock:
        if (main in where or any(k != "_down" for k in state)) and not state.get("_down") and tell(DOWN):
            state["_down"] = True
    else:
        state.pop("_down", None)
        agents = herdr_peers.herdr(env, "agent", "list", socket=sock).get("agents")
        panes = herdr_peers.herdr(env, "pane", "list", socket=sock).get("panes")
        if agents is None or panes is None:  # a list we could not read is no news, and keeps every record as it is
            raise WatchReadError()
        by_pane = {p.get("pane_id"): p for p in panes}
        records = {}
        for a in agents:
            pane = a.get("pane_id") or ""
            p = by_pane.get(pane, {})
            term = p.get("terminal_id") or a.get("terminal_id") or pane
            seq, status = a.get("state_change_seq"), a.get("agent_status")
            old = state.get(term) or {}
            opener = herdr_peers.opener(a.get("name"))
            home = herdr_peers.home_pane(a.get("name"))
            remote = herdr_peers.remote_pane(a.get("name"))
            job = herdr_peers.dispatch_pane(a.get("name"))
            same_lead = old.get("opener") == opener
            rec = old if old.get("seq") == seq and kind(old.get("status")) == kind(status) else {
                "seq": seq, "status": status, "since": now, "floor": old.get("seen", 0), "alerted": [],
                "orphaned": same_lead and bool(old.get("orphaned")),  # a new state of the same pane is no new orphan
                "lost_at": old.get("lost_at") if same_lead else None, "wakes": old.get("wakes", 0), "woke": old.get("woke", 0)}
            name = p.get("label") or a.get("name") or pane
            session = (a.get("agent_session") or {}).get("value") or ""
            rec.update(seen=now, name=name, agent_name=a.get("name"), pane=pane, opener=opener, home=home, job=job, session=session, remote=remote)  # to say which pane it was once it is gone
            records[term] = rec
            held = term == main or "🔴" in name or "👑" in name or any(herdr_peers.opener(c.get("name")) == term for c in agents)
            who = "자동 칸" if job else "밑 칸"
            moved = (pane, name, a.get("name") or "", session) if remote else None
            # A lead whose agent ended leaves its pane at a shell, where nobody hears. Restarted between two ticks it is told once for nothing.
            # A moved pane's lead is on the 회사맥: never here, and no orphan.
            lost = bool(opener) and not remote and not where.get(opener, ("", {}))[1].get("agent")
            if not lost:
                rec.update(orphaned=False, lost_at=None)
            elif not rec.get("lost_at"):
                rec["lost_at"] = now
            if lost and not rec.get("orphaned"):
                if tell(f"밑 칸 {name}(workers/{pane})을 연 리드가 닫혔습니다. 이 칸을 맡아 주세요. 화면: `herdr --session workers agent read {pane}`, "
                        f"닫기: `herdr --session workers pane close {pane}`. 멈추고 보고한 뒤 1시간이 지나면 저절로 닫힙니다.", home=home):
                    rec["orphaned"] = True
            if kind(status) == "blocked" and now - rec["since"] >= 60 and "blocked" not in rec["alerted"]:
                if tell(f"{who} {name}(workers/{pane})이 질문 창에서 멈춰 있습니다. 화면: `herdr --session workers agent read {pane}`, "
                        f"키 입력: `herdr --session workers agent send-keys {pane} Enter`", opener, home, moved):
                    rec["alerted"].append("blocked")
            if kind(status) == "ready":
                reported = reported_since(rec["floor"], term, session)
                if reported:
                    rec["wakes"] = 0
                elif term in where:  # missing from the earlier list it has no address: a bare id would name a default-server pane
                    try:
                        wake(env, sock, rec, pane, where[term][0], job, now)
                    except Exception:  # a fault here must not cost the alerts below or the saved state
                        pass
                if a.get("name") and now - rec["since"] >= STOPPED and not held:
                    try:
                        settle(env, sock, a, rec, pane, reported, term)
                    except Exception:
                        pass
                limit = JOB_HELD if job and "🔴" in name else STOPPED
                start = max(rec["since"], rec.get("woke", 0))
                why = ("" if "idle" in rec["alerted"] else "reported" if reported else "trial" if trial(name) else
                       f"wait since {clock(rec['since'])} woke {clock(rec.get('woke', 0))} until {clock(start + limit)}" if now - start < limit else None)
                if why is None:
                    long = "3시간째" if limit == JOB_HELD else "15분째"
                    text = (f"자동 칸 {name}(workers/{pane})이 보고 없이 {long} 멈춰 있습니다. 화면: `herdr --session workers agent read {pane}`, "
                            f"닫기: `herdr --session workers pane close {pane}`. 입력을 받지 않는 칸이니 보드 항목을 보고 이어 갈 일은 새 칸에 맡기세요. {UNSENT}"
                            if job else f"밑 칸 {name}(workers/{pane})이 보고 없이 15분째 멈춰 있습니다. {UNSENT}")
                    sent = tell(text, opener, home, moved)
                    if sent:
                        rec["alerted"].append("idle")
                    why = ("unsent" if not sent else f"fallback to {took[0]}" if took[0] in ("day file", "error queue") else
                           f"sent to {took[0]}" + (" after" if failed else "")) + "".join(f" ({x})" for x in failed)
                if why and why != rec.get("idle_why"):  # a line when the decision changes: one stop is a few lines, not one a minute
                    idle_log(env, now, f"workers/{pane} {term} {status} {why} | {name}")
                    rec["idle_why"] = why
                if lost and reported and now - max(rec["since"], rec["lost_at"]) >= ORPHAN_TTL and not held and not pending_messages(env, term):
                    closed = herdr_peers.herdr(env, "pane", "close", pane, socket=sock)  # {} when it failed: tried again next minute
                    if closed and "closed" not in rec["alerted"] and tell(f"리드가 닫힌 밑 칸 {name}(workers/{pane})을 보고 1시간 뒤라 닫았습니다", home=home, urgent=False):
                        rec["alerted"].append("closed")
        with_agent = {a.get("pane_id") for a in agents}
        for p in panes:
            term = p.get("terminal_id") or p.get("pane_id") or ""
            old = state.get(term) or {}
            job = bool(old.get("job"))
            if ("🫡" not in (p.get("label") or "") and not job) or p.get("pane_id") in with_agent:
                continue
            opener, home, remote = old.get("opener", ""), old.get("home", False), old.get("remote", False)
            agent_name, session = old.get("agent_name") or "", old.get("session") or ""
            if "status" in old:  # the watch held an agent record here: the agent really ended
                old = {"first": now, "had_agent": True}
            rec = records[term] = {"first": old.get("first", now), "had_agent": old.get("had_agent", False),
                                   "alerted": old.get("alerted", []), "opener": opener, "home": home, "job": job, "remote": remote,
                                   "agent_name": agent_name, "session": session}
            # ponytail: pane-spawn names a pane 🫡 before `agent start` (3 tries x 120 s, then it closes the pane), so a 🫡
            # pane never seen with an agent may still be starting: it is news only after a 420 s grace
            if (rec["had_agent"] or now - rec["first"] >= 420) and "ended" not in rec["alerted"] \
                    and (trial(p.get("label")) or tell(f"{'자동 칸' if job else '밑 칸'} {p.get('label')}의 에이전트가 끝났습니다" + (". 보드 항목을 보고 이어 갈 일은 새 칸에 맡기세요. 빈 칸은 닫습니다." if job else ""), opener, home,
                                                       (p.get("pane_id"), p.get("label"), agent_name, session) if remote else None)):
                rec["alerted"].append("ended")
            if job and "ended" in rec["alerted"] and not pending_messages(env, term):  # wait for queued relays too
                herdr_peers.herdr(env, "pane", "close", p.get("pane_id"), socket=sock)
        # A pane that had an agent and is in neither list went without a word: workers restarted between two ticks (launchd
        # KeepAlive is faster than this watch), or the pane closed. A report it sent since its last state covers it.
        listed = set(records) | {p.get("terminal_id") for p in panes}
        for term, old in state.items():
            if term in listed or "status" not in old:
                continue
            lost_text = (f"자동 칸 {old.get('name') or term}(workers/{old.get('pane') or '?'})이 보고 없이 사라졌습니다. 보드 항목이 In Progress로 남았을 수 있으니 보고 이어 갈 일은 새 칸에 맡기세요. {UNSENT}"
                         if old.get("job") else f"밑 칸 {old.get('name') or term}(workers/{old.get('pane') or '?'})이 보고 없이 사라졌습니다. {UNSENT}")
            if reported_since(old.get("floor", 0), term, old.get("session")) or startup_closed(env, old, now):
                continue
            moved = (old.get("pane"), old.get("name") or term, old.get("agent_name") or "", old.get("session") or "") if old.get("remote") else None
            if not (keep_trial(env, old, term, now) if trial(old.get("name")) else tell(lost_text, old.get("opener", ""), old.get("home", False), moved)):
                records[term] = old  # told nobody: try again next minute
        state = records  # panes that are gone drop out
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    if cleanup_error:
        raise cleanup_error
    return told


def selftest():
    import shutil
    import stat
    import tempfile

    os.environ.pop("HERDR_SOCKET_PATH", None)
    d = tempfile.mkdtemp()
    try:
        def write(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)

        def script(path, body):
            write(path, "#!/bin/bash\n" + body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

        cleanup_env = {"HOME": d, "XDG_STATE_HOME": f"{d}/state"}
        cleanup_path = f"{d}/state/orchestrator/automatic-checks/1111111111111111.json.start.1.json"
        write(cleanup_path, json.dumps({"status": "closed", "address": "workers/w1:pCleanup"}))
        os.utime(cleanup_path, (101, 101))
        for name in ("ordinary worker", "check [ac-2222222222222222]"):
            assert not startup_closed(cleanup_env, {"pane": "w1:pCleanup", "seen": 100, "name": name}, 102), \
                "a fresh receipt for another check must not hide a lost worker after pane-id reuse"
        os.remove(cleanup_path)

        W, DEF = f"{d}/sessions/workers/herdr.sock", f"{d}/herdr.sock"
        script(f"{d}/herdr", f'''case "$HERDR_SOCKET_PATH" in */sessions/workers/*) s=workers ;; *) s=default ;; esac
case "$1 $2" in
  "session list") cat {d}/sessions.json ;;
  "pane list") cat {d}/$s-panes.json ;;
  "agent list") cat {d}/$s-agents.json ;;
  "notification show") exit 98 ;;
  "agent read") [ "$4 $5" = "--source recent-unwrapped" ] && if [ "$s" = workers ]; then cat "{d}/screen-$3.txt"; else cat "{d}/screen-default-$3.txt"; fi ;;
  "pane close") [ -f {d}/close-fails ] && exit 1; echo "$3" >> {d}/closed; echo '{{"result":{{"type":"ok"}}}}' ;;
  "pane get") jq --arg id "$3" '{{result: {{pane: ([.result.panes[] | select(.pane_id == $id)][0])}}}}' {d}/$s-panes.json ;;
  "agent get") jq --arg id "$3" '{{result: {{agent: ([.result.agents[] | select(.pane_id == $id)][0])}}}}' {d}/$s-agents.json ;;
  *) exit 2 ;;
esac
''')
        script(f"{d}/error-notify", f'[ "${{NOTIFY_EXIT:-0}}" != 0 ] || echo "default|$1|$2" >> {d}/notified\nexit ${{NOTIFY_EXIT:-0}}\n')
        # A refused or failed pane-tell logs nothing: only what was taken is in `told`.
        # --raw logs as "raw:<pane>@<server>|<text>", the server being the one HERDR_SOCKET_PATH names
        script(f"{d}/pane-tell", f'[ "$1" != "${{TELL_REFUSE:-}}" ] || {{ echo "pane-tell: lead unavailable" >&2; exit 2; }}\n'
                                 f'[ "$1" != --raw ] || {{ s=${{HERDR_SOCKET_PATH%/herdr.sock}}; set -- "raw:$2@${{s##*/}}" "$3"; }}\n'
                                 f'[ "${{TELL_EXIT:-0}}" != 0 ] || echo "$1|$2" >> {d}/told\n'
                                 f'[ "${{TELL_EXIT:-0}}" = 0 ] || {{ [ -z "${{TELL_NOISE:-}}" ] || printf "%3000s\\n" "" | tr " " x; '
                                 f'echo "pane-tell: failed"; }} >&2\nexit ${{TELL_EXIT:-0}}\n')
        env = {"HOME": d, "XDG_STATE_HOME": f"{d}/state", "HERDR_BIN_PATH": f"{d}/herdr", "WORKERS_WATCH_PANE_TELL": f"{d}/pane-tell", "HERDR_ERROR_NOTIFY": f"{d}/error-notify"}
        main_file = f"{d}/state/orchestrator/main"

        def result(name, key, rows):
            write(f"{d}/{name}.json", json.dumps({"result": {key: list(rows)}}))

        def workers_running(up):
            write(f"{d}/sessions.json", json.dumps({"sessions": [
                {"name": "default", "socket_path": DEF, "running": True}, {"name": "workers", "socket_path": W, "running": up}]}))

        def agents(*rows):
            result("workers-agents", "agents", rows)

        def agent(pane, terminal, status, seq, name="spawn-M-1"):
            a = {"pane_id": pane, "name": name, "terminal_id": terminal, "agent_status": status, "state_change_seq": seq}
            agents(a)
            return a

        workers_panes: dict = {}

        def panes_on_workers(*rows):
            workers_panes.clear()
            workers_panes.update({p["pane_id"]: p for p in rows})
            result("workers-panes", "panes", workers_panes.values())

        def label(pane, text):
            p = {"pane_id": pane, "terminal_id": "term_" + pane.split(":p")[1], "label": text}
            workers_panes[pane] = p
            result("workers-panes", "panes", workers_panes.values())
            return p

        def touch_reported(terminal, mtime):
            write(f"{d}/state/orchestrator/reported/{terminal}", "")
            os.utime(f"{d}/state/orchestrator/reported/{terminal}", (mtime, mtime))

        def remove_main_file():
            os.remove(main_file)

        once = globals()["tick"]
        last_wakes: list = []

        def tick(now, **extra):  # what the fake pane-tell received ("<to>|<alert>") for the alerts tick says it sent
            for log in ("told", "notified", "closed"):
                if os.path.exists(f"{d}/{log}"):
                    os.remove(f"{d}/{log}")
            try:
                sent = once({**env, **extra}, now)
            except WatchReadError:
                sent = []  # main's run_tick records this failure; an unreadable inventory still sends no pane alert
            got = open(f"{d}/told").read().splitlines() if os.path.exists(f"{d}/told") else []
            last_wakes[:] = [line for line in got if WAKE in line]  # a wake goes to the pane itself and is no alert
            got = [line for line in got if WAKE not in line]
            assert extra or sorted([line.split("|", 1)[1] for line in got] + [line.split("|", 2)[2] for line in notified()]) == sorted(sent), (got, notified(), sent)
            return [line for line in got if line.split("|", 1)[1] in sent]

        def wakes():  # "<to>|<text>" of the wakes the last tick sent
            return list(last_wakes)

        def notified():  # "<server>|<title>|<body>" of the herdr notifications the last tick showed
            return open(f"{d}/notified").read().splitlines() if os.path.exists(f"{d}/notified") else []

        workers_running(True)
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude"}])
        result("default-agents", "agents", [])
        panes_on_workers()
        write(main_file, "term_M\n")

        blocked = agent("w1:p2", "term_2", "blocked", seq=5); label("w1:p2", "🟢 🫡 구현")
        assert tick(1000) == [] and tick(1059) == [], "a dialog under a minute is no news"
        told = tick(1060); assert len(told) == 1 and told[0].startswith("w5:p3|") and "질문 창" in told[0] and "agent read w1:p2" in told[0]
        assert tick(1200) == [], "once per dialog"
        agents(agent("w1:p2", "term_2", "blocked", seq=7)); tick(1300); assert len(tick(1360)) == 1, "a new dialog alerts again"
        agents(agent("w1:p2", "term_2", "idle", seq=9)); tick(2000)
        assert tick(2899) == [] and "보고 없이" in tick(2900)[0]
        agents(agent("w1:p2", "term_2", "idle", seq=11)); tick(3000); touch_reported("term_2", 3001)
        assert tick(3901) == [], "a pane that reported is waiting on the main pane, not stuck"
        # Each 15-minute decision of a stop is one kept line, written when it changes (11/13 21:52: three panes sat 18 minutes, no
        # alert reached the main pane, and watch.json, rewritten every minute, kept no trace why).
        idle_log = lambda pane: [line.split(" ", 2)[2] for line in open(f"{d}/state/orchestrator/idle-alert.log").read().splitlines()
                                 if line.split(" ")[2:3] == [f"workers/{pane}"]]
        assert [line.split()[3] for line in idle_log("w1:p2")] == ["wait", "sent", "wait", "reported"], idle_log("w1:p2")
        assert idle_log("w1:p2")[0].startswith("workers/w1:p2 term_2 idle wait since ") and " | 🟢 🫡 구현" in idle_log("w1:p2")[0]
        panes_on_workers(label("w1:p4", "🟢 🫡 정리"), label("w1:p1", "")); agents(agent("w1:p4", "term_4", "working", seq=1))
        assert tick(3950) == [], "a working agent is no news"; agents()
        assert len([t for t in tick(4000) if "에이전트가 끝났" in t]) == 1 and tick(4100) == [], "once per pane; the root shell is never news"
        workers_running(False); assert "꺼져" in tick(5000)[0] and tick(5060) == []
        workers_running(True); tick(5120); workers_running(False); assert len(tick(5180)) == 1, "down again alerts again"
        remove_main_file(); assert tick(6000) == [], "no main pane: nobody to tell"

        # beyond the brief's snippet
        write(main_file, "term_M\n"); workers_running(True); panes_on_workers(label("w1:p6", "🟢 🫡 시험"))
        agents(agent("w1:p6", "term_6", "idle", seq=1)); touch_reported("term_6", 6995)
        assert tick(7000) == [] and tick(7900) == [], "reported a moment before the watch first saw the stop: still reported"
        agents(agent("w1:p6", "term_6", "blocked", seq=3)); tick(8100)
        assert tick(8160, TELL_EXIT="1", NOTIFY_EXIT="1") == [] and len(tick(8220)) == 1, "an alert nobody took is sent again next minute"
        workers_running(False); assert tick(8300, TELL_EXIT="1", NOTIFY_EXIT="1") == [] and len(tick(8360)) == 1, "so is the down alert"
        workers_running(True); os.remove(f"{d}/workers-agents.json")
        assert tick(8420) == [] and json.load(open(f"{d}/state/orchestrator/watch.json"))["term_6"]["alerted"] == ["blocked"], \
            "an agent list that cannot be read keeps the records and tells nothing"
        write(f"{d}/default-panes.json", '{"result": {"panes": []}}'); agents(agent("w1:p6", "term_6", "blocked", seq=9))
        assert tick(8500) == [] and len(notified()) == 1 and "리드가 닫혔습니다" in notified()[0], "the main pane is on no server: 요유's screen"
        assert tick(8600) == [] and len(notified()) == 1 and "질문 창" in notified()[0], "and so does its dialog"
        assert tick(8660) == [] and notified() == [], "once each"
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "agent": "claude"}])

        # A pane with an agent gone from both lists (workers restarted between two ticks, a pane closed): news once, unless
        # it reported before it went. A list that cannot be read is no news and keeps the record.
        watch = lambda: json.load(open(f"{d}/state/orchestrator/watch.json"))
        panes_on_workers(); os.remove(f"{d}/workers-agents.json")
        assert tick(9000) == [] and "term_6" in watch(), "a list that cannot be read: no alert, the record is kept"
        agents(); told = tick(9060)
        assert len(told) == 1 and told[0] == f"w5:p3|밑 칸 🟢 🫡 시험(workers/w1:p6)이 보고 없이 사라졌습니다. {UNSENT}", told
        assert tick(9120) == [] and "term_6" not in watch(), "once; then the record is dropped"
        panes_on_workers(label("w1:p5", "🟢 🫡 보고함")); agents(agent("w1:p5", "term_5", "idle", seq=2)); tick(9200)
        touch_reported("term_5", 9230); panes_on_workers(); agents()
        assert tick(9260) == [] and "term_5" not in watch(), "a pane that reported before it went is no news"
        # pane-spawn records a successful close after failed check startup. It is intentional cleanup, not lost work.
        receipt = f"{d}/state/orchestrator/automatic-checks/1111111111111111.json.start.1.json"
        for number, (status, address, stamp, expected) in enumerate([
                ("closed", "workers/w1:pCleanup", 9271, 0),
                ("closed", "workers/w1:pCleanup", 9200, 1),  # an old receipt must not cover a reused pane id
                ("closed", "workers/w1:pCleanup", 9273, 1),  # a future receipt cannot confirm this disappearance
                ("closed", "default/w1:pCleanup", 9271, 1),
                ("started", "workers/w1:pCleanup", 9271, 1),
                (None, None, 9271, 1)]):
            panes_on_workers(label("w1:pCleanup", "🟢 🫡 시작 확인 [ac-1111111111111111]"))
            agents(agent("w1:pCleanup", "term_Cleanup", "working", seq=number))
            tick(9270)
            write(receipt, json.dumps({"status": status, "address": address}) if status else "{broken")
            os.utime(receipt, (stamp, stamp))
            panes_on_workers(); agents()
            lost = [text for text in tick(9272) if "보고 없이 사라졌" in text]
            assert len(lost) == expected, ("startup cleanup must suppress only a fresh confirmed close", status, address, stamp, lost)
            assert "term_Cleanup" not in watch(), "a confirmed cleanup or delivered warning retires the old watch record"
        os.remove(receipt)
        panes_on_workers(label("w1:p3", "🟢 🫡 재시도")); agents(agent("w1:p3", "term_3", "working", seq=1)); tick(9300)
        panes_on_workers(); agents()
        assert tick(9360, TELL_EXIT="1", NOTIFY_EXIT="1") == [] and len(tick(9420)) == 1, "an alert nobody took is sent again next minute"
        # 요유 2026-11-05: dispatch tabs run here too (agent auto-<repo>-<n>, no 🫡). A job that closes through pane-label is marked and
        # is no news; every other way of stopping is the main pane's to hear, since nobody else looks at this server.
        def job(pane, term, status, seq, text="🟢 api#820 작업"):
            label(pane, text)
            return agent(pane, term, status, seq, name="auto-api-820")

        panes_on_workers(); job("w1:pJ", "term_J", "working", seq=1); tick(9450)
        touch_reported("term_J", 9460); panes_on_workers(); agents()
        assert tick(9470) == [] and "term_J" not in watch(), "a job closed by pane-label is no news"
        panes_on_workers(); job("w1:pK", "term_K", "working", seq=1); tick(9471)
        panes_on_workers(); agents(); told = tick(9472)
        assert len(told) == 1 and told[0].startswith("w5:p3|자동 칸 🟢 api#820 작업(workers/w1:pK)이 보고 없이 사라졌습니다.") and "보드 항목" in told[0], told
        panes_on_workers(); job("w1:pL", "term_L", "idle", seq=1); tick(9480)
        told = tick(9480 + 900)
        assert len(told) == 1 and "자동 칸 🟢 api#820 작업(workers/w1:pL)이 보고 없이 15분째" in told[0] and "agent read w1:pL" in told[0] and "pane close w1:pL" in told[0], told
        assert "pane-tell" not in told[0], "it cannot take a prompt"
        panes_on_workers(); job("w1:pR", "term_R", "idle", seq=1, text="🔴 api#820 CI 대기"); tick(11000)
        assert tick(11000 + 900) == [], "🔴 is waiting on something it started, such as CI: not stuck at 15 minutes"
        told = tick(11000 + 10800); assert len(told) == 1 and "3시간째" in told[0], told
        job("w1:pR", "term_R", "idle", seq=1); assert tick(11000 + 10860) == [], "once"
        panes_on_workers(); job("w1:pB", "term_B", "blocked", seq=1); tick(23000)
        told = tick(23060); assert len(told) == 1 and "자동 칸" in told[0] and "질문 창" in told[0], "a dialog reaches the main pane"
        panes_on_workers(); job("w1:pD", "term_D", "working", seq=1); tick(23100)
        agents(); told = tick(23160)  # the agent died: a shell is left, and no 🫡 made the watch look at it
        assert len(told) == 1 and "자동 칸" in told[0] and "에이전트가 끝났" in told[0], told
        assert open(f"{d}/closed").read().split() == ["w1:pD"], "nothing is left in it: closed"
        assert tick(23220) == [], "once"
        assert open(f"{d}/closed").read().split() == ["w1:pD"], "a close that left it listed is tried again, with no second alert (Codex review)"
        panes_on_workers(); job("w1:pE", "term_E", "working", seq=1); tick(23300); agents()
        assert tick(23360, TELL_EXIT="1", NOTIFY_EXIT="1") == [] and not os.path.exists(f"{d}/closed"), "told nobody: kept, and tried again"
        assert len(tick(23420)) == 1 and os.path.exists(f"{d}/closed")
        panes_on_workers(label("w1:pS", "🟢 자동 칸을 고치는 일")); agents(); assert tick(23500) == [], "no agent and no record of one: a shell in a tab, not a job"
        panes_on_workers(); agents(); tick(23560)
        panes_on_workers(label("w1:pC", "🟢 🫡 전달")); agents(agent("w1:pC", "term_C", "blocked", seq=1)); tick(9500)
        assert tick(9560, TELL_EXIT="1") == [] and "질문 창" in notified()[0] and tick(9620) == [], "a pane-tell that failed falls to 요유's screen, once"
        panes_on_workers(); agents(); tick(9680); touch_reported("term_C", 9700)

        panes_on_workers(label("w1:p7", "🟢 🫡 시작 중")); agents()
        assert tick(10000) == [] and tick(10419) == [], "pane-spawn may still be starting a 🫡 pane never seen with an agent"
        assert len([t for t in tick(10420) if "에이전트가 끝났" in t]) == 1 and tick(10480) == [], "after the 420 s grace: once"

        panes_on_workers(label("w1:p8", "🟢 🫡 대기")); agents(agent("w1:p8", "term_8", "idle", seq=4)); tick(11000)
        agents(agent("w1:p8", "term_8", "done", seq=4)); assert tick(11600) == []
        assert "보고 없이" in tick(11900)[0], "idle -> done is the same stop: the 900 s count from 11000"

        panes_on_workers(label("w1:p9", "🟢 🫡 반복")); agents(agent("w1:p9", "term_9", "idle", seq=1)); tick(12000)
        touch_reported("term_9", 12005); tick(12060)
        agents(agent("w1:p9", "term_9", "working", seq=2)); tick(12120)
        agents(agent("w1:p9", "term_9", "idle", seq=3)); tick(12180)
        assert tick(13079) == [] and "보고 없이" in tick(13080)[0], "the last stop's report does not cover the next one"

        for bad in ("null", "[1, 2]", '{"term_9": [1]}'):  # a state file that is not a record map must not silence the watch
            write(f"{d}/state/orchestrator/watch.json", bad)
            tick(14000)
            assert isinstance(watch().get("term_9"), dict), bad

        # 요유 2026-11-05: an ordinary lead's panes run here too. Each alert goes to the pane that opened it, then the main pane, then 요유's screen.
        os.remove(f"{d}/state/orchestrator/watch.json")
        MAIN = {"pane_id": "w5:p3", "terminal_id": "term_M", "agent": "claude"}
        LEADS = [MAIN, {"pane_id": "w5:p9", "terminal_id": "term_L", "agent": "claude"}, {"pane_id": "w5:p8", "terminal_id": "term_K", "agent": "codex"}]
        result("default-panes", "panes", LEADS)
        panes_on_workers(label("w1:pA", "🟢 🫡 구현"), label("w1:pB", "🟢 🫡 다른 일"))
        L = {"name": "spawn-L-1"}; K = {"name": "spawn-K-1"}
        agents(agent("w1:pA", "term_pA", "blocked", seq=1, **L), agent("w1:pB", "term_pB", "blocked", seq=1, **K))
        tick(30000); told = tick(30060)
        assert sorted(t.split("|")[0] for t in told) == ["w5:p8", "w5:p9"], f"several leads at once: each hears of its own pane: {told}"
        assert all("send-keys" in t and "agent read" in t for t in told), "a lead learns how to read and answer the dialog"
        agents(agent("w1:pA", "term_pA", "blocked", seq=3, **L), agent("w1:pB", "term_pB", "working", seq=3, **K)); tick(30100)
        told = tick(30160, TELL_REFUSE="w5:p9")
        assert [t.split("|")[0] for t in told] == ["w5:p3"], f"a lead that cannot hear: the main pane: {told}"
        agents(agent("w1:pA", "term_pA", "idle", seq=5, **L), agent("w1:pB", "term_pB", "working", seq=3, **K)); tick(31000)
        told = tick(31900); assert [t.split("|")[0] for t in told] == ["w5:p9"] and "보고 없이" in told[0], told
        agents(agent("w1:pB", "term_pB", "working", seq=3, **K))
        told = tick(31960); assert [t.split("|")[0] for t in told] == ["w5:p9"] and "에이전트가 끝났" in told[0], f"the record remembers the opener: {told}"
        agents(agent("w1:pA", "term_pA", "working", seq=7, **L), agent("w1:pB", "term_pB", "working", seq=3, **K)); tick(32000)
        panes_on_workers(label("w1:pB", "🟢 🫡 다른 일")); agents(agent("w1:pB", "term_pB", "working", seq=3, **K))
        told = tick(32060); assert [t.split("|")[0] for t in told] == ["w5:p9"] and "사라졌습니다" in told[0], f"gone, to its opener: {told}"

        # The opener closed: the pane runs on unseen unless someone hears, once, and later alerts go there too.
        result("default-panes", "panes", [MAIN])
        told = tick(32100); assert [t.split("|")[0] for t in told] == ["w5:p3"] and "리드가 닫혔습니다" in told[0] and "w1:pB" in told[0], told
        assert tick(32160) == [], "once"
        agents(agent("w1:pB", "term_pB", "blocked", seq=5, **K)); assert tick(32200) == [], "a new state is no new orphan"
        told = tick(32260); assert [t.split("|")[0] for t in told] == ["w5:p3"] and "질문 창" in told[0], told
        # A list that cannot be read says nothing about the opener, and keeps every record.
        result("default-panes", "panes", LEADS); agents(agent("w1:pB", "term_pB", "working", seq=7, **K)); tick(33000)
        agents(agent("w1:pB", "term_pB", "working", seq=9, name="spawn-Z-1")); os.remove(f"{d}/default-panes.json")
        before = watch()
        assert tick(33060) == [] and notified() == [] and watch() == before, "the default server's list cannot be read: nothing is said"
        result("default-panes", "panes", LEADS)
        assert "리드가 닫혔습니다" in tick(33120)[0], "and the next good read says it"
        # No main pane at all: an opener still hears; with no opener either, one notification on the default server.
        os.remove(main_file); result("default-panes", "panes", LEADS[1:])
        agents(agent("w1:pB", "term_pB", "blocked", seq=11, **K)); tick(34000)
        told = tick(34060); assert [t.split("|")[0] for t in told] == ["w5:p8"] and notified() == [], f"no main pane is no reason to stop: {told}"
        result("default-panes", "panes", [])
        assert tick(34100) == [] and len(notified()) == 1 and notified()[0].startswith("default|") and "리드가 닫혔습니다" in notified()[0], notified()
        assert tick(34160) == [] and notified() == [], "one notification, not one a minute"
        agents(agent("w1:pB", "term_pB", "blocked", seq=13, name="spawned")); tick(34200)
        assert tick(34260, NOTIFY_EXIT="1") == [] and notified() == [] and tick(34320) == [] and len(notified()) == 1, "a failed notification is tried again"
        # Nobody to tell and nothing to tell of: a Mac with no workers server stays silent (집맥).
        os.remove(f"{d}/state/orchestrator/watch.json"); panes_on_workers(); agents(); workers_running(False)
        assert tick(35000) == [] and notified() == [], "no main pane, no record, workers absent: silence"
        write(f"{d}/sessions.json", json.dumps({"sessions": [{"name": "default", "socket_path": DEF, "running": True}]}))
        assert tick(35060) == [] and notified() == [], "workers not even listed: silence"
        workers_running(True); panes_on_workers(label("w1:pB", "🟢 🫡 다른 일")); agents(agent("w1:pB", "term_pB", "working", seq=1, name="spawned")); tick(35100)
        workers_running(False); assert tick(35160) == [] and "꺼져" in notified()[0], "a pane was seen there: the down alert reaches 요유's screen"
        write(main_file, "term_M\n"); result("default-panes", "panes", LEADS); workers_running(True); os.remove(f"{d}/state/orchestrator/watch.json")

        # 요유 2026-11-05: a pane that stopped and reported after its lead closed goes an hour later. A pane at work never does.
        def closed():
            return open(f"{d}/closed").read().splitlines() if os.path.exists(f"{d}/closed") else []

        result("default-panes", "panes", [MAIN])
        panes_on_workers(label("w1:pD", "🟢 🫡 끝남"), label("w1:pE", "🟢 🫡 일하는 중"), label("w1:pF", "🟢 🫡 보고 없음"), label("w1:pG", "🔴 🫡 감시 중"),
                         label("w1:pH", "🟢 🫡 자식 있음"), label("w1:pJ", "🟢 🫡 자식"), label("w1:pI", "🟢 🫡 리드 있음"), label("w1:pP", "🟢 👑 승격"))
        rows = [agent("w1:pD", "term_D", "idle", seq=1, **K), agent("w1:pE", "term_E", "working", seq=1, **K), agent("w1:pF", "term_F", "idle", seq=1, **K),
                agent("w1:pG", "term_G", "idle", seq=1, **K), agent("w1:pH", "term_H", "idle", seq=1, **K), agent("w1:pJ", "term_J", "working", seq=1, name="spawn-H-1"),
                agent("w1:pI", "term_I", "idle", seq=1, name="spawn-M-2"), agent("w1:pP", "term_P", "idle", seq=1, **K)]
        agents(*rows); tick(40000)
        for t in "DGHIP":
            touch_reported(f"term_{t}", 40010)
        tick(43599); assert closed() == [], "not before the hour"
        open(f"{d}/close-fails", "w").close()
        assert tick(43600) == [], "a close that failed tells nobody it closed"
        os.unlink(f"{d}/close-fails")
        told = tick(43660)
        assert closed() == ["w1:pD"], f"only the pane that stopped and reported, with no 🔴, no 👑 and no live child: {closed()}"
        assert [t.split("|")[0] for t in told] == ["w5:p3"] and "닫았습니다" in told[0] and "w1:pD" in told[0], told
        assert tick(43720) == [] and closed() == ["w1:pD"], "a close that did not take is tried again; it is told once"
        panes_on_workers(*[workers_panes[p] for p in ("w1:pE", "w1:pF", "w1:pG", "w1:pH", "w1:pJ", "w1:pI", "w1:pP")]); agents(*rows[1:])
        assert tick(43780) == [] and closed() == [], "gone after a report is no news"
        # The hour counts from whichever came last, the stop or the lead's close; a new stop has to report again; a lead that is back cancels it.
        os.remove(f"{d}/state/orchestrator/watch.json"); result("default-panes", "panes", LEADS); panes_on_workers(label("w1:pD", "🟢 🫡 끝남"))
        agents(agent("w1:pD", "term_D", "idle", seq=3, **K)); tick(50000); touch_reported("term_D", 50010); tick(60000)
        result("default-panes", "panes", [MAIN]); tick(60060); tick(63659); assert closed() == [], "an hour from the lead's close, not from the stop"
        tick(63660); assert closed() == ["w1:pD"]
        agents(agent("w1:pD", "term_D", "working", seq=5, **K)); tick(63700); assert closed() == [], "back at work: left alone"
        agents(agent("w1:pD", "term_D", "idle", seq=7, **K)); tick(63800); tick(67500); assert closed() == [], "a new stop with no new report: left alone"
        touch_reported("term_D", 67510); tick(67520); assert closed() == ["w1:pD"], "and it is closed once it reports"
        result("default-panes", "panes", LEADS); agents(agent("w1:pD", "term_D", "idle", seq=9, **K)); touch_reported("term_D", 67600); tick(67610)
        result("default-panes", "panes", [MAIN]); tick(67620); result("default-panes", "panes", LEADS); tick(67700)
        result("default-panes", "panes", [MAIN]); tick(71230); assert closed() == [], "a lead that came back starts the hour over"

        # Completed orphan cleanup is routine status. Missing owners remain an
        # error, but the later successful close must not use the error gateway.
        os.remove(f"{d}/state/orchestrator/watch.json")
        result("default-panes", "panes", [])
        panes_on_workers(label("w1:pNS", "🟢 🫡 상태 확인"))
        agents(agent("w1:pNS", "term_NS", "idle", seq=1, **K))
        touch_reported("term_NS", 71240)
        assert tick(71250) == [] and len(notified()) == 1 and "리드가 닫혔습니다" in notified()[0], "a missing owner still queues an error"
        assert tick(74850) == [] and closed() == ["w1:pNS"] and notified() == [], "successful orphan cleanup is not a terminal error"
        assert "closed" in watch()["term_NS"]["alerted"], "routine status without a recipient is accepted once"
        result("default-panes", "panes", [MAIN])
        assert tick(74910) == [] and notified() == [], "a later recipient gets no stale cleanup status"

        # A lead whose agent ended leaves its pane at a shell, where nobody hears: it counts as closed, or the pane would stay hidden for good.
        os.remove(f"{d}/state/orchestrator/watch.json"); panes_on_workers(label("w1:pS", "🟢 🫡 셸만 남음"))
        result("default-panes", "panes", [MAIN, {"pane_id": "w5:p9", "terminal_id": "term_L", "agent": None}])
        agents(agent("w1:pS", "term_S", "idle", seq=1, name="spawn-L-1")); touch_reported("term_S", 80010)
        told = tick(80020); assert told[0].startswith("w5:p3|") and "리드가 닫혔습니다" in told[0], f"the main pane takes it: {told}"
        tick(83619); assert closed() == []
        tick(83620); assert closed() == ["w1:pS"], "and it closes an hour later"
        result("default-panes", "panes", LEADS); tick(83680); result("default-panes", "panes", [MAIN, {**LEADS[1], "agent": None}]); tick(83740)
        result("default-panes", "panes", LEADS); tick(83800); assert closed() == [], "a new session in that pane is its lead again"
        panes_on_workers(); agents(); tick(83860)

        # 요유 2026-11-05 (workers/w1:p6G): a pane whose own report said it may close sat open for two hours. Its close came due
        # while the Mac was out of processes, and the restart brought it back under a new terminal its report mark did not name:
        # the watch told of it as 15 minutes without a report, and the hour never came. A pane whose report got out and whose
        # last reply closes it goes 15 minutes after it stopped, whoever its lead is. Its lead (2026-11-05): never the main pane,
        # a 👑, a 🔴, or a pane whose report has not gone out.
        os.remove(f"{d}/state/orchestrator/watch.json"); result("default-panes", "panes", LEADS)
        done_reply = "끝났습니다.\n\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"

        def stopped(pane, term, session, reply, text="🟢 🫡 끝남", name="spawn-L-1", status="idle", seq=1):
            """A pane that stopped on `reply`, as both lists show it."""
            workers_panes[pane] = {"pane_id": pane, "terminal_id": term, "label": text, "agent_session": {"value": session}, "agent": "claude"}
            result("workers-panes", "panes", workers_panes.values())
            write(f"{d}/.claude/projects/-w/{session}.jsonl", json.dumps({"type": "assistant", "message": {
                "id": "m1", "role": "assistant", "content": [{"type": "text", "text": reply}]}}, ensure_ascii=False) + "\n")
            return {"pane_id": pane, "name": name, "terminal_id": term, "agent_status": status, "state_change_seq": seq,
                    "agent_session": {"value": session}}

        panes_on_workers()
        rows = [stopped("w1:pQ", "term_fQ", "sid-Q", done_reply),                      # restarted: its mark is under its session only
                stopped("w1:pT", "term_fT", "sid-T", done_reply),                      # its close failed, no restart
                stopped("w1:pR", "term_fR", "sid-R", done_reply),                      # its transcript cannot be read at first
                stopped("w1:pK", "term_fK", "sid-K", "남은 것: 요유 몫: 머지 결정 · 유요 몫: 없음 · 세션: 닫아도 됨"),
                stopped("w1:pV", "term_fV", "sid-V", done_reply, text="🔴 🫡 감시 중"),
                stopped("w1:pP", "term_fP", "sid-P", done_reply, text="🟢 👑 승격된 리드"),
                stopped("w1:pM", "term_M", "sid-M", done_reply, text="🟢 메인"),        # the main pane's terminal
                stopped("w1:pW", "term_fW", "sid-W", done_reply),
                stopped("w1:pC", "term_fC", "sid-C", "진행 중입니다.", name="spawn-fW-1", status="working"),  # pW's live child
                stopped("w1:pU", "term_fU", "sid-U", done_reply),                      # its report never got out
                stopped("w1:pN", "term_fN", "sid-N", "진행 중입니다.")]                 # no report at all
        agents(*rows)
        touch_reported("sid-Q", 83990)
        for t in ("term_fT", "sid-R", "sid-K", "term_fV", "sid-P", "term_M", "term_fW"):
            touch_reported(t, 84010)
        os.chmod(f"{d}/.claude/projects/-w/sid-R.jsonl", 0)
        ever: set = set()

        def closes():
            got = closed()
            ever.update(got)
            return sorted(got)

        tick(84000)
        assert tick(84899) == [] and closes() == [], "not before 15 minutes"
        told = tick(84900)
        assert closes() == ["w1:pQ", "w1:pT"], f"only a reported pane whose reply closes it, not held: {closed()}"
        assert sorted(t for t in told if "보고 없이" in t) == [f"w5:p9|밑 칸 🟢 🫡 끝남(workers/w1:p{p})이 보고 없이 15분째 멈춰 있습니다. {UNSENT}"
                                                         for p in "NU"], f"a pane whose report never got out is news, not closed: {told}"
        os.chmod(f"{d}/.claude/projects/-w/sid-R.jsonl", 0o644)
        assert tick(84960) == [] and closes() == ["w1:pQ", "w1:pR", "w1:pT"], f"an unreadable transcript is read again; a close that did not take is tried again: {closed()}"
        open(f"{d}/close-fails", "w").close(); tick(85020); os.unlink(f"{d}/close-fails")
        assert closes() == [], "a failed close"
        tick(85080); assert closes() == ["w1:pQ", "w1:pR", "w1:pT"], "is tried again next minute"
        tick(86000); tick(90000); closes()
        assert ever == {"w1:pQ", "w1:pR", "w1:pT"}, f"never the main pane, a 👑, a 🔴, a pane with a live child, one left open or unreported: {ever}"
        panes_on_workers(); agents(); tick(90060)

        # Pending pane-tell messages must outlive every stopped-pane cleanup path.
        panes_on_workers()
        rows = [stopped("w1:pQ", "term_fQ", "sid-Q", done_reply),
                stopped("w1:pT", "term_fT", "sid-T", done_reply)]
        agents(*rows)
        tick(91000)
        for t in ("term_fQ", "term_fT"):
            touch_reported(t, 91010)
        queue = f"{d}/state/pane-tell"
        write(f"{queue}/waiting/message", "private payload")
        write(f"{queue}/receivers/message", f"term_fQ\n{W}\n")
        tick(91900)
        assert closes() == ["w1:pT"], "15-minute cleanup keeps only the receiver with a pending message"
        os.remove(f"{queue}/receivers/message")
        tick(91960)
        assert closes() == [], "a legacy queue without receiver metadata conservatively holds cleanup"
        os.remove(f"{queue}/waiting/message")
        write(f"{queue}/receivers/message", f"term_fQ\n{W}\n")
        write(f"{queue}/batch/term_fQ/.new.writer", "batch message being staged")
        tick(92000)
        assert closes() == ["w1:pT"], "a batch staging its first message keeps its receiver open"
        os.remove(f"{queue}/batch/term_fQ/.new.writer")
        write(f"{queue}/batch/term_fQ/open/00001", "held batch")
        tick(92020)
        assert closes() == ["w1:pT"], "a held batch also keeps its receiver open"
        os.remove(f"{queue}/batch/term_fQ/open/00001")
        tick(92080)
        assert closes() == ["w1:pQ", "w1:pT"], "delivery resumes cleanup; stale receiver metadata does not hold it"
        # Reading a large transcript can overlap a new relay arriving.
        original_reply = atc.last_reply

        def queue_during_reply(*args):
            write(f"{queue}/waiting/late", "message arriving during transcript read")
            write(f"{queue}/receivers/late", f"term_fQ\n{W}\n")
            return done_reply

        atc.last_reply = queue_during_reply
        try:
            os.remove(f"{d}/closed")
            settle(env, W, rows[0], {}, "w1:pQ", True, "term_fQ")
            assert closes() == [], "a message arriving during transcript read holds cleanup too"
        finally:
            atc.last_reply = original_reply
        shutil.rmtree(queue)
        panes_on_workers(); agents(); tick(92140)

        # 요유 2026-11-05: a Home lead's pane may reach the main pane, which is told to keep it off company Slack and the board.
        os.remove(f"{d}/state/orchestrator/watch.json"); result("default-panes", "panes", [MAIN]); panes_on_workers(label("w1:pX", "🟢 🫡 집 일"))
        H = {"name": "spawn-K-h123"}
        agents(agent("w1:pX", "term_X", "blocked", seq=1, **H)); told = tick(72000)
        assert "리드가 닫혔습니다" in told[0] and herdr_peers.HOME_NOTE in told[0], f"the orphan alert says so: {told}"
        told = tick(72060); assert "질문 창" in told[0] and herdr_peers.HOME_NOTE in told[0], f"and every later one: {told}"
        agents(agent("w1:pX", "term_X", "working", seq=3, **H)); tick(72100); panes_on_workers(); agents()
        told = tick(72160); assert "사라졌습니다" in told[0] and herdr_peers.HOME_NOTE in told[0], f"down to the last: {told}"
        result("default-panes", "panes", LEADS); panes_on_workers(label("w1:pX", "🟢 🫡 집 일")); agents(agent("w1:pX", "term_X", "blocked", seq=5, **H)); tick(72200)
        told = tick(72260); assert told[0].startswith("w5:p8|") and herdr_peers.HOME_NOTE not in told[0], f"its own lead needs no warning: {told}"
        agents(agent("w1:pX", "term_X", "blocked", seq=7, **H)); tick(72300)
        told = tick(72360, TELL_REFUSE="w5:p8"); assert told[0].startswith("w5:p3|") and herdr_peers.HOME_NOTE in told[0], f"its lead cannot hear: {told}"
        agents(agent("w1:pX", "term_X", "working", seq=9, **K)); tick(72400); panes_on_workers(); agents(); touch_reported("term_X", 72420); tick(72440)
        os.remove(f"{d}/state/orchestrator/watch.json"); result("default-panes", "panes", LEADS)

        # 요유 2026-11-05 (와우 카드 04): a pane whose turn ended on the Mac's sleep or a dropped network is told to go on at once,
        # not after the 15-minute alert. Only the pane hears it, and it is no alert.
        import threading
        listening = socket.socket(); listening.bind(("127.0.0.1", 0)); listening.listen(8)

        def answer():  # a backlog nobody takes from fills up, and the probe would read the API as down
            while True:
                try:
                    listening.accept()[0].close()
                except OSError:
                    return

        threading.Thread(target=answer, daemon=True).start()
        closed_port = socket.socket(); closed_port.bind(("127.0.0.1", 0)); dead_port = closed_port.getsockname()[1]; closed_port.close()
        env["WORKERS_WATCH_PROBE"] = f"127.0.0.1:{listening.getsockname()[1]}"
        offline = {"WORKERS_WATCH_PROBE": f"127.0.0.1:{dead_port}"}
        RULE_LINE = "─" * 40

        def screen(pane, *lines):  # a Claude screen: the history, then the input box between two rules
            write(f"{d}/screen-{pane}.txt", "\n".join(("❯ 이어서 확인해 줘",) + lines + ("", "✻ Cooked for 17s · done 1:09 PM",
                                                      "   0 tokens · current: 2.1.318", RULE_LINE, "❯ ", RULE_LINE, "  [Opus 5.5] │ munice", "")))

        SLEPT = "⏺ API Error: Your computer went to sleep mid-response. The response above may be incomplete."
        rec = lambda: watch()["term_V"]
        w = {"name": "spawn-L-1"}
        panes_on_workers(label("w1:pV", "🟢 🫡 확인")); agents(agent("w1:pV", "term_V", "working", seq=1, **w)); tick(90000)
        screen("w1:pV", "⏺ 확인하고 있습니다.", SLEPT); agents(agent("w1:pV", "term_V", "idle", seq=3, **w))
        assert tick(90060) == [] and wakes() == [f"workers/w1:pV|{WAKE}"], f"told at once, the pane alone: {wakes()}"
        assert tick(90120) == [] and wakes() == [], "once per stop"
        # The same pane stops on it again: the next wake waits WAKE_GAPS after the last one.
        agents(agent("w1:pV", "term_V", "working", seq=5, **w)); tick(90130); agents(agent("w1:pV", "term_V", "idle", seq=7, **w))
        assert tick(90179) == [] and wakes() == [], "not inside the gap"
        assert tick(90180) == [] and len(wakes()) == 1 and rec()["wakes"] == 2
        # The network is still down: nothing goes, and the wake goes the first minute the API answers.
        agents(agent("w1:pV", "term_V", "working", seq=9, **w)); tick(90200); agents(agent("w1:pV", "term_V", "idle", seq=11, **w))
        assert tick(90500, **offline) == [] and wakes() == [] and tick(90540, **offline) == [] and wakes() == []
        assert tick(90560) == [] and len(wakes()) == 1, "back online: told"
        # A wake that did not move it: the 15-minute alert, counted from the wake, not from the stop.
        assert tick(91459) == [] and "보고 없이 15분째" in tick(91460)[0]
        # A stop that is no error resets the count: the next error is told at once.
        screen("w1:pV", SLEPT, "❯ 이어서 하세요", "⏺ 다 했습니다.")  # the old error is still on the screen, above
        agents(agent("w1:pV", "term_V", "working", seq=13, **w)); tick(91500); agents(agent("w1:pV", "term_V", "idle", seq=15, **w))
        assert tick(91560) == [] and wakes() == [] and rec()["wakes"] == 0, "an error above later work is no error"
        screen("w1:pV", "⏺ API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)")
        agents(agent("w1:pV", "term_V", "working", seq=17, **w)); tick(91600); agents(agent("w1:pV", "term_V", "idle", seq=19, **w))
        assert tick(91610) == [] and len(wakes()) == 1, "told at once again"
        # Each next wake waits longer; after the last gap no more wakes, and the alert takes over.
        for n, gap in enumerate(WAKE_GAPS[1:], start=1):
            t = rec()["woke"]
            agents(agent("w1:pV", "term_V", "working", seq=20 + 2 * n, **w)); tick(t + 1)
            agents(agent("w1:pV", "term_V", "idle", seq=21 + 2 * n, **w))
            assert tick(t + gap - 1) == [] and wakes() == [] and tick(t + gap) == [] and len(wakes()) == 1, n
        agents(agent("w1:pV", "term_V", "working", seq=41, **w)); tick(99000); agents(agent("w1:pV", "term_V", "idle", seq=43, **w))
        assert tick(99060) == [] and wakes() == [], f"{len(WAKE_GAPS)} wakes, then none"
        told = tick(105000); assert wakes() == [] and "보고 없이 15분째" in told[0], told
        touch_reported("term_V", 105010)
        # A report resets the count (the screen still holds the error, so only the report can): the next error is told at once.
        assert tick(105060) == [] and rec()["wakes"] == 0, "a report resets the count"
        agents(agent("w1:pV", "term_V", "working", seq=45, **w)); tick(105100); agents(agent("w1:pV", "term_V", "idle", seq=47, **w))
        assert tick(105160) == [] and len(wakes()) == 1, "after a report the next error is told at once"
        touch_reported("term_V", 105170)
        # 11/13 18:47-18:51: seven Claude panes ended on Cloudflare's challenge page, which Claude words as a login error. Nothing
        # told them to go on, so they sat until the main pane did at 20:57, and then went on with no new login.
        CHALLENGE = "⏺ Please run /login · API Error: 403 Attention Required! | Cloudflare"
        panes_on_workers(label("w1:pCF", "🟢 🫡 확인")); screen("w1:pCF", "⏺ 확인하고 있습니다.", CHALLENGE)
        agents(agent("w1:pCF", "term_CF", "idle", seq=1, **w))
        assert tick(105200) == [] and wakes() == [f"workers/w1:pCF|{WAKE}"], f"Cloudflare's challenge is told like a dropped network: {wakes()}"
        touch_reported("term_CF", 105210)
        # A narrow pane breaks the line itself, and its rest comes indented under it (a 44-column test pane, 11/13).
        panes_on_workers(label("w1:pCN", "🟢 🫡 좁은 칸")); screen("w1:pCN", "⏺ Please run /login · API Error: 403", "  Attention Required! | Cloudflare")
        agents(agent("w1:pCN", "term_CN", "idle", seq=1, **w))
        assert tick(105300) == [] and wakes() == [f"workers/w1:pCN|{WAKE}"], f"a broken line is read whole: {wakes()}"
        touch_reported("term_CN", 105310)
        assert net_error("⏺ API Error: 403 Attention Required! | Cloudflare\n"), "the same page without the /login words"
        # 11/13 21:52 and 22:21: six Claude panes ended on a 503 that Claude itself calls temporary; the watch read each screen within
        # a minute, found no network error and told none, and each went on with no new login once the main pane told it to.
        panes_on_workers(label("w1:pS5", "🟢 🫡 확인")); agents(agent("w1:pS5", "term_S5", "idle", seq=1, **w))
        screen("w1:pS5", "⏺ 확인하고 있습니다.", "⏺ API Error: 503 credential validation failed. This is a server-side issue, usually",
               "  temporary — try again in a moment. If it persists, check https://status.claude.com.")
        assert tick(105400) == [] and wakes() == [f"workers/w1:pS5|{WAKE}"], f"a server error is told like a dropped network: {wakes()}"
        touch_reported("term_S5", 105410)
        # Errors that a wake cannot fix, a pane that reported, and a stop that is no error are left alone.
        for i, last in enumerate(("⏺ API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup).",
                                  "⏺ Please run /login · API Error: 403 Access to this model requires an access grant",
                                  "⏺ Not logged in · Please run /login",
                                  "⏺ Prompt is too long",
                                  "⏺ You've hit your weekly limit · resets Nov 15 at 4am (Asia/Seoul)",
                                  "⏺ 연결이 끊겼던 이유를 정리했습니다: API Error: Connection refused (ECONNREFUSED)")):
            panes_on_workers(label("w1:pX", "🟢 🫡 다른 오류")); screen("w1:pX", last)
            agents(agent("w1:pX", "term_X", "working", seq=100 + 2 * i, **w)); tick(110000 + 100 * i)
            agents(agent("w1:pX", "term_X", "idle", seq=101 + 2 * i, **w))
            assert tick(110060 + 100 * i) == [] and wakes() == [], last
        screen("w1:pX", SLEPT); agents(agent("w1:pX", "term_X", "working", seq=110, **w)); tick(111000); touch_reported("term_X", 111010)
        agents(agent("w1:pX", "term_X", "idle", seq=111, **w)); assert tick(111060) == [] and wakes() == [], "it reported: done, not cut off"
        # A Codex pane waits for the network itself (Reconnecting... waiting for network) and goes on; its screen is no error.
        write(f"{d}/screen-w1:pCX.txt", "› 1+1은?\n• Reconnecting... 4/5\n  └ Stream disconnected before completion: Connection refused (os error 61)\n"
                                        "• 2\n  Worked for 6m 51s • 13:16\n› Ask Codex to do anything\n  GPT-6.1-Sol high Fast\n")
        panes_on_workers(label("w1:pCX", "🟢 🫡 Codex")); agents({**agent("w1:pCX", "term_CX", "idle", seq=1, **w), "agent": "codex"})
        assert tick(112000) == [] and wakes() == [] and watch()["term_CX"]["net"] is False, "read, and no error"
        touch_reported("term_CX", 112010)
        # A wake nobody took is sent again the next minute; a screen that cannot be read is tried again.
        panes_on_workers(label("w1:pY", "🟢 🫡 재시도")); agents(agent("w1:pY", "term_Y", "idle", seq=1, **w))
        assert tick(113000) == [] and wakes() == [], "no screen yet"
        screen("w1:pY", SLEPT); assert tick(113060, TELL_EXIT="1") == [] and wakes() == []
        assert tick(113120) == [] and len(wakes()) == 1
        touch_reported("term_Y", 113130)
        # A pane missing from the pane list has no address: under launchd a bare id names a pane on the default server.
        panes_on_workers(); screen("w1:pN", SLEPT); agents(agent("w1:pN", "term_N", "idle", seq=1, **w))
        assert tick(113500) == [] and wakes() == [], "no address, no wake"
        touch_reported("term_N", 113510)
        # A dispatch tab takes no pane-tell prompt, and a prompt without its marker would lift its guard for good: --raw, marked.
        panes_on_workers(label("w1:pJX", "🟢 api#820 작업")); screen("w1:pJX", SLEPT); agents(agent("w1:pJX", "term_JX", "idle", seq=1, name="auto-api-820"))
        assert tick(114000) == [] and wakes() == [f"raw:w1:pJX@workers|[auto-dispatch] {WAKE}"], wakes()
        touch_reported("term_JX", 114010)
        # The main pane, on the default server, is told the same way (11/13 20:38: it ended on ENOTFOUND in a dark wake and
        # sat until a new message came). Once per stop, only while the API answers, and never while it works.
        panes_on_workers(); agents()
        main_agent = lambda status, seq: result("default-agents", "agents", [{"pane_id": "w5:p3", "terminal_id": "term_M", "agent": "claude",
                                                                            "agent_status": status, "state_change_seq": seq}])
        screen("default-w5:p3", "⏺ 확인하고 있습니다.", "⏺ API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)")
        main_agent("working", 1); assert tick(114200) == [] and wakes() == [], "a working main pane is no stop"
        main_agent("idle", 3)
        assert tick(114260, **offline) == [] and wakes() == [], "offline: not yet"
        assert tick(114320) == [] and wakes() == [f"w5:p3|{WAKE}"], f"back online: the main pane is told: {wakes()}"
        assert tick(114380) == [] and wakes() == [], "once per stop"
        main_agent("working", 5); tick(114390); main_agent("done", 7)
        assert tick(114439) == [] and wakes() == [] and tick(114440) == [] and len(wakes()) == 1, "the next stop waits the next gap"
        screen("default-w5:p3", "⏺ API Error: Can't reach the API server (ENOTFOUND)", "❯ 이어서 하세요", "⏺ 다 했습니다.")
        main_agent("working", 9); tick(114500); main_agent("idle", 11)
        assert tick(114560) == [] and wakes() == [], "a stop that is no error"
        # A new main pane does not inherit the old one's spent wakes (Codex review): its first error is told at once.
        write(f"{d}/state/orchestrator/main-wake.json", json.dumps({"seq": 1, "terminal": "term_OLD", "alerted": ["woke"], "net": True,
                                                                     "wakes": len(WAKE_GAPS), "woke": 114600}))
        screen("default-w5:p3", "⏺ API Error: Can't reach the API server (ENOTFOUND)"); main_agent("idle", 13)
        assert tick(114610) == [] and wakes() == [f"w5:p3|{WAKE}"], f"a new main pane starts its own count: {wakes()}"
        # Its wakes spent, the main pane is still told every last gap (Codex review): no 15-minute alert speaks for it.
        write(f"{d}/state/orchestrator/main-wake.json", json.dumps({"seq": 13, "terminal": "term_M", "alerted": ["woke"], "net": True,
                                                                     "wakes": len(WAKE_GAPS), "woke": 115000}))
        main_agent("idle", 15)
        assert tick(115000 + WAKE_GAPS[-1] - 1) == [] and wakes() == [], "not inside the last gap"
        assert tick(115000 + WAKE_GAPS[-1]) == [] and wakes() == [f"w5:p3|{WAKE}"], f"then told again: {wakes()}"
        result("default-agents", "agents", [])
        # A fault in the wake costs nothing else: the dialog alert goes out and the state is saved.
        real, globals()["net_error"] = globals()["net_error"], lambda text: 1 / 0
        try:
            panes_on_workers(label("w1:pZ", "🟢 🫡 고장")); agents(agent("w1:pZ", "term_Z", "idle", seq=1, **w), agent("w1:pQ", "term_Q", "blocked", seq=1, **w))
            screen("w1:pZ", SLEPT); tick(115000)
            assert len(tick(115060)) == 1 and "term_Q" in watch(), "the dialog alert and the saved state"
        finally:
            globals()["net_error"] = real
        listening.close(); del env["WORKERS_WATCH_PROBE"]
        os.remove(f"{d}/state/orchestrator/watch.json"); panes_on_workers(); agents(); tick(115120)  # leaves no log behind

        # A test pane (`임시 …`) is the lead's own trial, and yoyoo-share-guard names it at each of the lead's reports until it
        # is closed: its stop, its going and its agent's end are no news (11/05: 12 of the main pane's 24 watch alerts). A dialog still is.
        t = agent("w1:pT", "term_T", "idle", seq=1); t["agent_session"] = {"value": "sess-T"}
        panes_on_workers(label("w1:pT", "🟢 🫡 임시 시험")); agents(t); tick(116000)
        assert tick(116900) == [], "a test pane stopped without a report is no news"
        panes_on_workers(); agents(); assert tick(116960) == [] and "term_T" not in watch(), "nor one gone without a report"
        kept = f"{d}/.local/state/pane-reports/{time.strftime('%Y-%m-%d', time.localtime(116960))}.md"
        text = open(kept).read() if os.path.exists(kept) else ""
        assert "workers/w1:pT" in text and "sess-T" in text and "보고 없이 사라" in text, ("but the report file keeps it: a real report may be missing", text)
        os.remove(kept)
        panes_on_workers(label("w1:pT", "🟢 🫡 임시 시험")); agents(t); tick(116970); touch_reported("term_T", 116980)
        panes_on_workers(); agents(); tick(116990); assert not os.path.exists(kept), "one that reported leaves no line"
        panes_on_workers(label("w1:pT", "🟢 🫡 임시 시험")); agents(agent("w1:pT", "term_T", "working", seq=3)); tick(117000); agents()
        assert tick(117060) == [] and tick(117600) == [], "nor its agent's end"
        panes_on_workers(label("w1:pT", "🟢 🫡 임시 시험")); agents(agent("w1:pT", "term_T", "blocked", seq=5)); tick(118000)
        assert len(tick(118060)) == 1, "its dialog is news: nobody else can answer it"
        panes_on_workers(label("w1:pT", "🟢 🫡 임시저장 API 수정")); agents(agent("w1:pT", "term_T", "idle", seq=7)); tick(119000)
        assert len(tick(119900)) == 1, "`임시저장` is a work name, not a test pane"
        os.remove(f"{d}/state/orchestrator/watch.json"); panes_on_workers(); agents(); tick(120000)

        # api#1049: on 집맥 a pane workers-move opened (`r`) has its lead, the 회사맥 main pane, on no server here: it is no orphan,
        # and an alert nobody here takes goes to the day file, which `workers-move bridge` carries to that main pane.
        remove_main_file(); result("default-panes", "panes", [])
        day = lambda at: f"{d}/.local/state/pane-reports/{time.strftime('%Y-%m-%d', time.localtime(at))}.md"
        filed = lambda at: open(day(at)).read() if os.path.exists(day(at)) else ""
        panes_on_workers(label("w1:pV", "🟢 🫡 이어 감")); agents(agent("w1:pV", "term_V", "blocked", seq=1, name="spawn-M-r1"))
        assert tick(121000) == [] and tick(121060) == [] and notified() == [], "no lead closed, and no screen of 요유's on 집맥"
        text = filed(121060)
        assert "## " in text and "workers/w1:pV 🟢 🫡 이어 감" in text and "[workers-watch]" in text and "질문 창" in text, text
        assert "리드가 닫혔" not in text, "its lead is on the 회사맥, not closed"
        assert tick(121120) == [] and filed(121120).count("질문 창") == 1, "once, as any alert"
        agents(agent("w1:pV", "term_V", "idle", seq=3, name="spawn-M-r1")); tick(122000)
        assert tick(122900) == [] and "보고 없이 15분째" in filed(122900), "stopped without a report"
        agents(agent("w1:pV", "term_V", "idle", seq=5, name="spawn-M-r1")); tick(123000); touch_reported("term_V", 123001)
        assert tick(123000 + ORPHAN_TTL + 60) == [] and not os.path.exists(f"{d}/closed"), "reported: not closed an hour later as an orphan"
        agents(agent("w1:pV", "term_V", "working", seq=7, name="spawn-M-r1")); tick(130000); panes_on_workers(); agents()
        assert tick(130060) == [] and "보고 없이 사라졌습니다" in filed(130060), "gone without a report"
        for at in (121060, 122900, 130060):
            if os.path.exists(day(at)):
                os.remove(day(at))
        panes_on_workers(label("w1:pW", "🟢 🫡 보통 칸")); agents(agent("w1:pW", "term_W", "blocked", seq=1, name="spawn-Q-1")); tick(131000)
        assert tick(131060) == [] and len(notified()) == 1 and filed(131060) == "", "any other pane with nobody to tell: 요유's screen, as before"
        os.remove(f"{d}/state/orchestrator/watch.json"); panes_on_workers(); agents(); tick(131120)
        for at in (131060, 131120):
            if os.path.exists(day(at)):
                os.remove(day(at))
        write(main_file, "term_M\n"); result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude"}])
        # Each run, after the tick, carries what moved panes on 집맥 said (workers-move bridge), whatever the tick did.
        script(f"{d}/workers-move", f'echo "$*" >> {d}/moved\n')
        subprocess.run([sys.executable, "-I", os.path.realpath(__file__)], env={**env, "PATH": os.environ.get("PATH", ""),
                       "WORKERS_WATCH_MOVE": f"{d}/workers-move", "WORKERS_WATCH_NOW": "132000"}, timeout=30)
        for _ in range(100):  # detached
            if os.path.exists(f"{d}/moved"):
                break
            time.sleep(0.05)
        assert open(f"{d}/moved").read() == "bridge\n", "the bridge runs once a minute with the watch"

        # The main pane re-reads its whole conversation for each message, so past MAIN_REFRESH_AT tokens (the last response's
        # usage) it is restarted through main-refresh --now, with the workers server's variables dropped. Failed attempts
        # retry next minute, while a completed restart still gives the new session an hour.
        script(f"{d}/main-refresh", f'echo "$* ${{HERDR_SOCKET_PATH-none}} ${{HERDR_SESSION-none}}" >> {d}/refreshed\n')
        env["WORKERS_WATCH_MAIN_REFRESH"] = f"{d}/main-refresh"
        iso = lambda at: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))

        def conversation(sid, born, *sizes, side=0, last=0):  # the main pane's transcript: first line at `born`, then responses
            rows = [{"type": "user", "timestamp": iso(born), "message": {"content": "hi"}}]
            for n in sizes:  # the last response `last`, else an hour after `born`
                rows.append({"type": "assistant", "timestamp": iso(last or born + 3600), "message": {"id": f"m{n}", "usage": {
                    "input_tokens": 2, "cache_creation_input_tokens": 98, "cache_read_input_tokens": n - 200, "output_tokens": 100}}})
            if side:  # a subagent's line and a synthetic one after it say nothing about the main conversation
                rows += [{"type": "assistant", "isSidechain": True, "timestamp": iso(born + 70), "message": {"usage": {"cache_read_input_tokens": side}}},
                         {"type": "assistant", "timestamp": iso(born + 80), "message": {"model": "<synthetic>", "usage": {"input_tokens": 0}}}]
            write(f"{d}/.claude/projects/-w/{sid}.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
            result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude",
                                               "agent_session": {"value": sid}}])

        def refreshed(wait=3.0):  # main-refresh runs on its own: wait a moment for the fake to write
            end = time.time() + wait
            while time.time() < end and not os.path.exists(f"{d}/refreshed"):
                time.sleep(0.05)
            got = open(f"{d}/refreshed").read().splitlines() if os.path.exists(f"{d}/refreshed") else []
            if got:
                os.remove(f"{d}/refreshed")
            return got

        panes_on_workers(); agents()
        conversation("s1", 100000, 120000, 149999); tick(130000)
        assert refreshed(0.5) == [], "under the line: no restart"
        conversation("s1", 100000, 120000, 150001)
        tick(130060, HERDR_SOCKET_PATH=DEF, HERDR_SESSION="workers")
        assert refreshed() == ["--now none none"], "over it: main-refresh --now, without the server variables"
        assert "150001" in open(f"{d}/state/orchestrator/refresh.log").read(), "and the log says why"
        tick(130090); assert refreshed(0.5) == [], "a skipped attempt is not retried twice within a minute"
        tick(130120); assert refreshed() == ["--now none none"], "a skipped attempt is retried next minute, not next hour"
        lock = f"{d}/state/orchestrator/refresh.lock"
        os.mkdir(lock); os.utime(lock, (130120, 130120))
        tick(130660); assert refreshed(0.5) == [], "an active refresh keeps its queued request and handoff wait"
        os.rmdir(lock)
        tick(133700); assert refreshed() == ["--now none none"], "an hour later again"
        conversation("s2", 140000 - 600, 160000); tick(140000)
        assert refreshed(0.5) == [], "a session restarted ten minutes ago by anyone is given its hour"
        conversation("s3", 100000, 140000, side=900000); tick(150000)
        assert refreshed(0.5) == [], "a subagent's or a synthetic line is not the conversation's size"
        conversation("s5", 150000 - 7200, 170000, last=150000 - 7200 + 600); tick(150000)
        assert refreshed(0.5) == [], "a pane quiet since its handoff load is no restart: a fresh one is past the line already"
        # A response that called the advisor sums its model calls at the top level (11/06: 135k read as 272k): the last call counts.
        write(f"{d}/.claude/projects/-w/s7.jsonl", "".join(json.dumps(r) + "\n" for r in [
            {"type": "user", "timestamp": iso(155000 - 7200), "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": iso(155000 - 600), "message": {"id": "a1", "usage": {
                "input_tokens": 6, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 271347, "output_tokens": 37, "iterations": [
                    {"type": "message", "input_tokens": 2, "cache_read_input_tokens": 135493, "output_tokens": 30},
                    {"type": "advisor_message", "input_tokens": 0},
                    {"type": "message", "input_tokens": 4, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 135854, "output_tokens": 7}]}}}]))
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude",
                                           "agent_session": {"value": "s7"}}])
        tick(155000); assert refreshed(0.5) == [], "an advisor call does not double the conversation's size"
        # This pane grows up to ~5k tokens a minute from a ~125k handoff load, so an hour of it is 275-360k, near the 367k where
        # Claude compacts by itself (11/06 17:54; the pane restarted itself at 16:12 and 17:05, so no session reached its hour). Past
        # MAIN_REFRESH_FULL the hour is not waited for; the first 15 minutes still are.
        conversation("s8", 155060 - 600, 300000, last=155060 - 30); tick(155060)
        assert refreshed(0.5) == [], "a big session in its first 15 minutes is its handoff load: still no restart"
        conversation("s9", 155120 - 1200, 260000, last=155120 - 60); tick(155120)
        assert refreshed() == ["--now none none"], "past the full line a 20-minute-old session is restarted, not left for its hour"
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude"}])
        tick(160000); assert refreshed(0.5) == [], "no session id: nothing to measure"
        conversation("s4", 100000, 200000); env["WORKERS_WATCH_MAIN_REFRESH"] = f"{d}/nowhere"; tick(170000)
        env["WORKERS_WATCH_MAIN_REFRESH"] = f"{d}/main-refresh"; tick(170060)
        assert refreshed() == ["--now none none"], "a call that could not start is tried again next minute"
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude"}])
        del env["WORKERS_WATCH_MAIN_REFRESH"]

        # A pane-tell that fails says why in the line (its exit and last error line): 11/13 22:07:54-56 three 15-minute alerts fell
        # to 요유's screen instead of the main pane, and nothing kept why. Past 1 MB only the last 2000 lines stay (sync-home.sh's way).
        with open(f"{d}/state/orchestrator/idle-alert.log", "w") as f:
            f.writelines(f"2026-11-13 00:00:00 workers/w1:pOld term_Old idle wait {'x' * 120}\n" for _ in range(9000))
        panes_on_workers(label("w1:pIL", "🟢 🫡 알림")); agents(agent("w1:pIL", "term_IL", "idle", seq=1)); tick(180000)
        lines = open(f"{d}/state/orchestrator/idle-alert.log").read().splitlines()
        assert len(lines) == 2001 and " workers/w1:pIL " in lines[-1], len(lines)
        # Its last error line is the one kept, after 3000 bytes of earlier output (a check pane, 23:21: the first 2000 bytes lost it).
        assert tick(180900, TELL_EXIT="1", NOTIFY_EXIT="1") == [] and tick(180960, TELL_EXIT="1", TELL_NOISE="1") == []
        assert any("w1:pIL" in body and "보고 없이 15분째" in body for body in notified()), notified()
        assert [line.split()[3] for line in idle_log("w1:pIL")] == ["wait", "unsent", "fallback"], idle_log("w1:pIL")
        assert idle_log("w1:pIL")[2].startswith("workers/w1:pIL term_IL idle fallback to error queue (w5:p3 rc=1 pane-tell: failed)"), idle_log("w1:pIL")
        # The lead cannot hear and the main pane took it: sent, to the main pane, the lead's failure after it (the same check pane).
        result("default-panes", "panes", LEADS); panes_on_workers(label("w1:pIK", "🟢 🫡 알림"))
        agents(agent("w1:pIK", "term_IK", "idle", seq=1, **K)); tick(181000)
        assert tick(181900, TELL_REFUSE="w5:p8")[0].startswith("w5:p3|")
        assert idle_log("w1:pIK")[-1].startswith("workers/w1:pIK term_IK idle sent to w5:p3 after (w5:p8 rc=2 pane-tell: lead unavailable)"), idle_log("w1:pIK")
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인", "agent": "claude"}])
        # end to end, the way launchd runs it: python3 -I drops the script folder from sys.path
        panes_on_workers(label("w1:p6", "🟢 🫡 시험")); agents(agent("w1:p6", "term_6", "blocked", seq=11))
        run = lambda now, **e: subprocess.run(["/usr/bin/python3", "-I", os.path.realpath(__file__)], capture_output=True, text=True,
                                              env={"PATH": "/usr/bin:/bin", **env, "WORKERS_WATCH_NOW": str(now), **e})
        assert run(20000).returncode == 0 and run(20060).returncode == 0
        assert open(f"{d}/told").read().count("질문 창") == 1, "main() runs a tick at WORKERS_WATCH_NOW"
        os.remove(f"{d}/state/orchestrator/watch.json"); os.mkdir(f"{d}/state/orchestrator/watch.json")  # the state cannot be saved
        r = run(20120)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), "fails open, quietly"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("selftest ok")


def main():
    # Error banners bypass herdr's ordinary status toasts. Run even if tick fails:
    # previously queued warnings must resume when a terminal attaches again.
    try:
        subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "herdr-error-drain.py")], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    try:
        run_tick(os.environ, float(os.environ.get("WORKERS_WATCH_NOW") or time.time()))
    except Exception:
        pass
    # What moved panes said on 집맥 reaches the main pane here; with no open move it returns at once. Detached, never killed at a
    # timeout: one cut between a send and its save would send it again or open a second check pane (Claude review). bridge.lock
    # keeps one at a time.
    try:
        subprocess.Popen([os.environ.get("WORKERS_WATCH_MOVE") or os.path.join(HERE, "workers-move"), "bridge"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
