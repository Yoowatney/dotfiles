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
Dispatch tabs run here too (요유 2026-11-05), with no opener, so these reach the main pane, named `자동 칸`. No 🫡 marks them, so their
record does: an agent that ended is news, and its pane, a bare shell, closes once someone heard. A 🔴 one waits on what it started
(CI, a long check), so it is news after JOB_HELD instead of 900 s. Nothing can be typed in one, so an alert names what is left: read
it, close it, look at the board item. One closed through pane-label is marked reported, and is no news.
Such a pane is closed, with one alert, once it has been stopped with its report sent for ORPHAN_TTL, counted from the stop or
the lead's close, whichever came last (요유 2026-11-05). A pane at work, marked 🔴, without a report or with a live child stays.
An alert about a Home lead's pane that goes to anyone but its lead carries HOME_NOTE.
A pane list that cannot be read is no news: nothing is sent and every record stays. State: `{state_dir}/watch.json`, terminal
id -> seq, status, since (first tick that saw this state), seen (last tick that saw it), floor (`seen` of the state before:
a report touched after it belongs to this stop, since the watch sees a new state up to a minute late), opener, home, orphaned,
lost_at (first tick that found no opener), alerted.
By design, two stops inside one tick interval look like one: a report for the first also covers the second.
An alert nobody took marks nothing, so the next minute sends it again. Fails open: any error exits 0 quietly.

Test env: WORKERS_WATCH_NOW (seconds), WORKERS_WATCH_PANE_TELL (a fake pane-tell).

    workers-watch.py --selftest
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import importlib.util
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.realpath(__file__))  # python3 -I drops this folder from sys.path
_spec = importlib.util.spec_from_file_location("herdr_peers", os.path.join(HERE, "herdr_peers.py"))
herdr_peers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(herdr_peers)

DOWN = "workers 서버가 꺼져 있습니다: `launchctl kickstart gui/$(id -u)/com.yoyoo.herdr-workers`"
ORPHAN_TTL = 3600
JOB_HELD = 10800
UNSENT = "보내지 못한 보고가 있다면 ~/.local/state/pane-reports에 있습니다."  # auto-tab-close.py keeps a report it had nobody to send to


def kind(status) -> str:
    """herdr flips done<->idle when a pane is seen; that is the same stop, not a new state."""
    return "ready" if status in ("idle", "done") else status or ""


def notify(env, msg) -> bool:
    """The last resort: 요유's own screen, on the server 요유 is attached to."""
    sock = herdr_peers.socket_of(env, "default")
    try:
        return subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", "notification", "show", "밑 칸을 맡을 곳이 없습니다", "--body", msg],
                              env={**os.environ, **env, **({"HERDR_SOCKET_PATH": sock} if sock else {})}, timeout=5, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tick(env, now: float) -> list:
    """One round; returns the alerts someone took."""
    where = herdr_peers.all_panes(env)
    if where is None:  # no way to know who is there, so no alert and no record changes
        return []
    main = herdr_peers.main_terminal(env)
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

    def tell(msg, opener="", home=False) -> bool:
        pt = env.get("WORKERS_WATCH_PANE_TELL") or os.path.join(HERE, "pane-tell")
        for term in dict.fromkeys(t for t in (opener, main) if t in where):  # a lead that cannot hear (its agent ended) is no stop
            text = f"{msg} {herdr_peers.HOME_NOTE}" if home and term != opener else msg
            try:
                ok = subprocess.run([pt, where[term][0], text], env={**os.environ, **env}, timeout=10, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                told.append(text)
                return True
        if notify(env, msg):  # 요유's own screen needs no warning about 요유's own matters
            told.append(msg)
            return True
        return False

    def reported_since(term, floor) -> bool:
        try:
            return os.path.getmtime(os.path.join(herdr_peers.state_dir(env), "reported", term)) >= floor
        except OSError:
            return False

    sock = herdr_peers.socket_of(env, "workers")
    if sock is None:  # a list we could not read is no news
        return []
    if not sock:
        if (main in where or any(k != "_down" for k in state)) and not state.get("_down") and tell(DOWN):
            state["_down"] = True
    else:
        state.pop("_down", None)
        agents = herdr_peers.herdr(env, "agent", "list", socket=sock).get("agents")
        panes = herdr_peers.herdr(env, "pane", "list", socket=sock).get("panes")
        if agents is None or panes is None:  # a list we could not read is no news, and keeps every record as it is
            return []
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
            job = herdr_peers.dispatch_pane(a.get("name"))
            same_lead = old.get("opener") == opener
            rec = old if old.get("seq") == seq and kind(old.get("status")) == kind(status) else {
                "seq": seq, "status": status, "since": now, "floor": old.get("seen", 0), "alerted": [],
                "orphaned": same_lead and bool(old.get("orphaned")),  # a new state of the same pane is no new orphan
                "lost_at": old.get("lost_at") if same_lead else None}
            name = p.get("label") or a.get("name") or pane
            rec.update(seen=now, name=name, pane=pane, opener=opener, home=home, job=job)  # to say which pane it was once it is gone
            records[term] = rec
            who = "자동 칸" if job else "밑 칸"
            # A lead whose agent ended leaves its pane at a shell, where nobody hears. Restarted between two ticks it is told once for nothing.
            lost = bool(opener) and not where.get(opener, ("", {}))[1].get("agent")
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
                        f"키 입력: `herdr --session workers agent send-keys {pane} Enter`", opener, home):
                    rec["alerted"].append("blocked")
            if kind(status) == "ready":
                reported = reported_since(term, rec["floor"])
                limit = JOB_HELD if job and "🔴" in name else 900
                if not reported and now - rec["since"] >= limit and "idle" not in rec["alerted"]:
                    long = "3시간째" if limit == JOB_HELD else "15분째"
                    text = (f"자동 칸 {name}(workers/{pane})이 보고 없이 {long} 멈춰 있습니다. 화면: `herdr --session workers agent read {pane}`, "
                            f"닫기: `herdr --session workers pane close {pane}`. 입력을 받지 않는 칸이니 보드 항목을 보고 이어 갈 일은 새 칸에 맡기세요. {UNSENT}"
                            if job else f"밑 칸 {name}(workers/{pane})이 보고 없이 15분째 멈춰 있습니다. {UNSENT}")
                    if tell(text, opener, home):
                        rec["alerted"].append("idle")
                if lost and reported and now - max(rec["since"], rec["lost_at"]) >= ORPHAN_TTL and "🔴" not in name and "👑" not in name \
                        and not any(herdr_peers.opener(c.get("name")) == term for c in agents):
                    closed = herdr_peers.herdr(env, "pane", "close", pane, socket=sock)  # {} when it failed: tried again next minute
                    if closed and "closed" not in rec["alerted"] and tell(f"리드가 닫힌 밑 칸 {name}(workers/{pane})을 보고 1시간 뒤라 닫았습니다", home=home):
                        rec["alerted"].append("closed")
        with_agent = {a.get("pane_id") for a in agents}
        for p in panes:
            term = p.get("terminal_id") or p.get("pane_id") or ""
            old = state.get(term) or {}
            job = bool(old.get("job"))
            if ("🫡" not in (p.get("label") or "") and not job) or p.get("pane_id") in with_agent:
                continue
            opener, home = old.get("opener", ""), old.get("home", False)
            if "status" in old:  # the watch held an agent record here: the agent really ended
                old = {"first": now, "had_agent": True}
            rec = records[term] = {"first": old.get("first", now), "had_agent": old.get("had_agent", False),
                                   "alerted": old.get("alerted", []), "opener": opener, "home": home, "job": job}
            # ponytail: pane-spawn names a pane 🫡 before `agent start` (3 tries x 120 s, then it closes the pane), so a 🫡
            # pane never seen with an agent may still be starting: it is news only after a 420 s grace
            if (rec["had_agent"] or now - rec["first"] >= 420) and "ended" not in rec["alerted"] \
                    and tell(f"{'자동 칸' if job else '밑 칸'} {p.get('label')}의 에이전트가 끝났습니다" + (". 보드 항목을 보고 이어 갈 일은 새 칸에 맡기세요. 빈 칸은 닫습니다." if job else ""), opener, home):
                rec["alerted"].append("ended")
            if job and "ended" in rec["alerted"]:  # a shell and no agent, once someone heard: tried every tick while it is listed
                herdr_peers.herdr(env, "pane", "close", p.get("pane_id"), socket=sock)
        # A pane that had an agent and is in neither list went without a word: workers restarted between two ticks (launchd
        # KeepAlive is faster than this watch), or the pane closed. A report it sent since its last state covers it.
        listed = set(records) | {p.get("terminal_id") for p in panes}
        for term, old in state.items():
            if term in listed or "status" not in old:
                continue
            lost_text = (f"자동 칸 {old.get('name') or term}(workers/{old.get('pane') or '?'})이 보고 없이 사라졌습니다. 보드 항목이 In Progress로 남았을 수 있으니 보고 이어 갈 일은 새 칸에 맡기세요. {UNSENT}"
                         if old.get("job") else f"밑 칸 {old.get('name') or term}(workers/{old.get('pane') or '?'})이 보고 없이 사라졌습니다. {UNSENT}")
            if not reported_since(term, old.get("floor", 0)) and not tell(lost_text, old.get("opener", ""), old.get("home", False)):
                records[term] = old  # told nobody: try again next minute
        state = records  # panes that are gone drop out
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
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

        W, DEF = f"{d}/sessions/workers/herdr.sock", f"{d}/herdr.sock"
        script(f"{d}/herdr", f'''case "$HERDR_SOCKET_PATH" in */sessions/workers/*) s=workers ;; *) s=default ;; esac
case "$1 $2" in
  "session list") cat {d}/sessions.json ;;
  "pane list") cat {d}/$s-panes.json ;;
  "agent list") cat {d}/$s-agents.json ;;
  "notification show") [ "${{NOTIFY_EXIT:-0}}" != 0 ] || echo "$s|$3|$5" >> {d}/notified; exit ${{NOTIFY_EXIT:-0}} ;;
  "pane close") [ -f {d}/close-fails ] && exit 1; echo "$3" >> {d}/closed; echo '{{"result":{{"type":"ok"}}}}' ;;
  *) exit 2 ;;
esac
''')
        # A refused or failed pane-tell logs nothing: only what was taken is in `told`.
        script(f"{d}/pane-tell", f'[ "$1" != "${{TELL_REFUSE:-}}" ] || exit 2\n[ "${{TELL_EXIT:-0}}" != 0 ] || echo "$1|$2" >> {d}/told\nexit ${{TELL_EXIT:-0}}\n')
        env = {"HOME": d, "XDG_STATE_HOME": f"{d}/state", "HERDR_BIN_PATH": f"{d}/herdr", "WORKERS_WATCH_PANE_TELL": f"{d}/pane-tell"}
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

        def tick(now, **extra):  # what the fake pane-tell received ("<to>|<alert>") for the alerts tick says it sent
            for log in ("told", "notified", "closed"):
                if os.path.exists(f"{d}/{log}"):
                    os.remove(f"{d}/{log}")
            sent = once({**env, **extra}, now)
            got = open(f"{d}/told").read().splitlines() if os.path.exists(f"{d}/told") else []
            assert extra or sorted([line.split("|", 1)[1] for line in got] + [line.split("|", 2)[2] for line in notified()]) == sorted(sent), (got, notified(), sent)
            return [line for line in got if line.split("|", 1)[1] in sent]

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
    try:
        tick(os.environ, float(os.environ.get("WORKERS_WATCH_NOW") or time.time()))
    except Exception:
        pass


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
