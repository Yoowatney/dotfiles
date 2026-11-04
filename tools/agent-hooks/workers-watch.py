#!/usr/bin/env python3
"""launchd, once a minute: tell the main pane when a pane on the hidden `workers` herdr server needs it.

Panes the main pane opens run on the workers server, which nobody looks at, so a pane stuck in a dialog or stopped without
a report would sit there unseen. This watch sends the main pane one `pane-tell` line for each of these:
- an agent `blocked` (an approval or question dialog) for 60 s or more, once per state_change_seq;
- an agent `idle`/`done` for 900 s or more that has not reported since it stopped (auto-tab-close.py touches
  `{state_dir}/reported/<terminal>` when its report was sent), once per state_change_seq;
- a pane whose name has 🫡 but no agent in it, once per pane (the unnamed root shell pane is never news): at once when the
  watch saw an agent there, else after 420 s, since pane-spawn names the pane before its agent starts;
- the workers server down, once while it stays down.
No main pane (no file, or its terminal is on no server): nothing is sent. State: `{state_dir}/watch.json`, terminal id ->
seq, status, since (first tick that saw this state), seen (last tick that saw it), floor (`seen` of the state before:
a report touched after it belongs to this stop, since the watch sees a new state up to a minute late), alerted.
By design, two stops inside one tick interval look like one: a report for the first also covers the second.
A pane-tell that fails marks nothing, so the next minute sends it again. Fails open: any error exits 0 quietly.

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


def kind(status) -> str:
    """herdr flips done<->idle when a pane is seen; that is the same stop, not a new state."""
    return "ready" if status in ("idle", "done") else status or ""


def tick(env, now: float) -> list:
    """One round; returns the alerts pane-tell took."""
    main = herdr_peers.main_terminal(env)
    found = herdr_peers.find_terminal(env, main) if main else None
    if not found:
        return []
    to = found[0]
    path = os.path.join(herdr_peers.state_dir(env), "watch.json")
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    told: list = []

    def tell(msg) -> bool:
        pt = env.get("WORKERS_WATCH_PANE_TELL") or os.path.join(HERE, "pane-tell")
        try:
            ok = subprocess.run([pt, to, msg], env={**os.environ, **env}, timeout=10, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok:
            told.append(msg)
        return ok

    sock = herdr_peers.socket_of(env, "workers")
    if not sock:
        if not state.get("_down") and tell(DOWN):
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
            rec = old if old.get("seq") == seq and kind(old.get("status")) == kind(status) else {
                "seq": seq, "status": status, "since": now, "floor": old.get("seen", 0), "alerted": []}
            rec["seen"] = now
            records[term] = rec
            name = p.get("label") or a.get("name") or pane
            if kind(status) == "blocked" and now - rec["since"] >= 60 and "blocked" not in rec["alerted"]:
                if tell(f"밑 칸 {name}(workers/{pane})이 질문 창에서 멈춰 있습니다. 화면: `herdr --session workers agent read {pane}`"):
                    rec["alerted"].append("blocked")
            if kind(status) == "ready" and now - rec["since"] >= 900 and "idle" not in rec["alerted"]:
                try:
                    reported = os.path.getmtime(os.path.join(herdr_peers.state_dir(env), "reported", term)) >= rec["floor"]
                except OSError:
                    reported = False
                if not reported and tell(f"밑 칸 {name}(workers/{pane})이 보고 없이 15분째 멈춰 있습니다"):
                    rec["alerted"].append("idle")
        with_agent = {a.get("pane_id") for a in agents}
        for p in panes:
            term = p.get("terminal_id") or p.get("pane_id") or ""
            if "🫡" not in (p.get("label") or "") or p.get("pane_id") in with_agent:
                continue
            old = state.get(term) or {}
            if "status" in old:  # the watch held an agent record here: the agent really ended
                old = {"first": now, "had_agent": True}
            rec = records[term] = {"first": old.get("first", now), "had_agent": old.get("had_agent", False),
                                   "alerted": old.get("alerted", [])}
            # ponytail: pane-spawn names a pane 🫡 before `agent start` (3 tries x 120 s, then it closes the pane), so a 🫡
            # pane never seen with an agent may still be starting: it is news only after a 420 s grace
            if (rec["had_agent"] or now - rec["first"] >= 420) and "ended" not in rec["alerted"] \
                    and tell(f"밑 칸 {p.get('label')}의 에이전트가 끝났습니다"):
                rec["alerted"].append("ended")
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
  *) exit 2 ;;
esac
''')
        script(f"{d}/pane-tell", f'echo "$1|$2" >> {d}/told\nexit ${{TELL_EXIT:-0}}\n')
        env = {"HOME": d, "XDG_STATE_HOME": f"{d}/state", "HERDR_BIN_PATH": f"{d}/herdr", "WORKERS_WATCH_PANE_TELL": f"{d}/pane-tell"}
        main_file = f"{d}/state/orchestrator/main"

        def result(name, key, rows):
            write(f"{d}/{name}.json", json.dumps({"result": {key: list(rows)}}))

        def workers_running(up):
            write(f"{d}/sessions.json", json.dumps({"sessions": [
                {"name": "default", "socket_path": DEF, "running": True}, {"name": "workers", "socket_path": W, "running": up}]}))

        def agents(*rows):
            result("workers-agents", "agents", rows)

        def agent(pane, terminal, status, seq):
            a = {"pane_id": pane, "name": "spawn-M-1", "terminal_id": terminal, "agent_status": status, "state_change_seq": seq}
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
            if os.path.exists(f"{d}/told"):
                os.remove(f"{d}/told")
            sent = once({**env, **extra}, now)
            got = open(f"{d}/told").read().splitlines() if os.path.exists(f"{d}/told") else []
            assert extra or [line.split("|", 1)[1] for line in got] == sent, (got, sent)
            return [line for line in got if line.split("|", 1)[1] in sent]

        workers_running(True)
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M", "label": "👑 메인"}])
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
        assert tick(8160, TELL_EXIT="1") == [] and len(tick(8220)) == 1, "a pane-tell that failed is sent again next minute"
        workers_running(False); assert tick(8300, TELL_EXIT="1") == [] and len(tick(8360)) == 1, "so is the down alert"
        workers_running(True); os.remove(f"{d}/workers-agents.json")
        assert tick(8420) == [] and json.load(open(f"{d}/state/orchestrator/watch.json"))["term_6"]["alerted"] == ["blocked"], \
            "an agent list that cannot be read keeps the records and tells nothing"
        write(f"{d}/default-panes.json", '{"result": {"panes": []}}'); agents(agent("w1:p6", "term_6", "blocked", seq=9))
        assert tick(8500) == [] and tick(8600) == [], "the main pane is on no server: nobody to tell"
        result("default-panes", "panes", [{"pane_id": "w5:p3", "terminal_id": "term_M"}])

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
