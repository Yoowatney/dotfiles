#!/usr/bin/env python3
"""launchd at login, on 세션맥 only: after a sudden reboot, tell every restored agent session to resume, and tell 요유 once.

2026-11-19 22:15: 세션맥 panicked and rebooted. herdr restored the panes and their agents by itself, but every session sat idle
until 요유 noticed 40 minutes later and asked the main pane to tell them all to carry on (api#1297). This is that message, sent at
once.
- Sudden: macOS writes a "shutdown" record to wtmp on a clean shutdown or restart, and none on a panic, a power cut or a hard
  reset. So the newest "reboot" line of `last reboot shutdown` with no "shutdown" line right after it is a sudden reboot.
  A clean reboot, or a reading that is unclear, sends nothing.
- Once per boot: the boot time is written to the state file before anything is sent, so a second login or a launchd restart never
  sends twice, and a crash half way never sends the rest twice.
- Wait for herdr: at login the servers are still restoring, so the agent lists of the default and workers servers are read every
  POLL seconds until two reads agree and every agent is ready (at most WAIT seconds).
- Each agent gets one pane-tell, GAP seconds apart (a burst of sessions all resuming at once is what ran the Mac out of memory).
  The main pane gets it too. A dispatch tab (auto-<repo>-<n>) is not told: a prompt there would count as 요유's and lift its guard.
  A pane-tell that fails (an ambient session refuses it) is counted, not retried.
- 요유 hears once, through main-alert-inbox (the main pane reads it and relays it to #0-yoyooyo), like load-watch's alerts.
A pane whose agent did not restart is not reached: only listed agents are told. State and log: ~/.local/state/reboot-resume/.
Fails open: any error exits 0 quietly. Drill: REBOOT_RESUME_LAST=<file with last output> REBOOT_RESUME_BOOT=<epoch> (see --selftest).

    reboot-resume.py | --selftest
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "[세션맥 재부팅 자동 안내]"
DISPATCH = lambda name: bool(re.match(r"^auto-[a-z]+-\d+$", name or ""))  # the same pattern as herdr_peers.dispatch_pane


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sudden(last_text):
    """True: the newest boot has no shutdown record right before it. False: it has one. None: the reading says nothing."""
    rows = [w[0] for w in (line.split() for line in last_text.splitlines()) if w and w[0] in ("reboot", "shutdown")]
    if not rows or rows[0] != "reboot":
        return None
    return not (len(rows) > 1 and rows[1] == "shutdown")


def resume_text(boot):
    return (f"{MARK} 세션맥이 {time.strftime('%H:%M', time.localtime(boot))}쯤 정상 종료 없이 갑자기 다시 켜져서, 이 세션이 하던 일이 끊겼을 수 있습니다. "
            "하던 일이 있으면 마지막 상태(작업 기록, git status, 보드)를 확인하고 이어서 하세요. 하던 일이 없었으면 답하지 않아도 됩니다. "
            "부하 세션의 보고는 평소처럼 리드에게 합니다.")


def run(boot, last_text, list_agents, tell, enqueue, state_dir, wait=600, poll=15, gap=2, sleep=time.sleep, now=time.time):
    """One login's work. Returns the log line (also appended to state_dir/log)."""
    os.makedirs(state_dir, exist_ok=True)
    mark = os.path.join(state_dir, "boot")
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read().strip() == str(boot):
                return "already handled"
    except OSError:
        pass
    verdict = sudden(last_text)
    if verdict is None:
        return log(state_dir, now, f"boot {boot} unclear, nothing sent")
    with open(mark + ".tmp", "w", encoding="utf-8") as f:  # before any send: at most once per boot
        f.write(f"{boot}\n")
    os.replace(mark + ".tmp", mark)
    if not verdict:
        return log(state_dir, now, f"boot {boot} planned, nothing sent")
    prev, waited, agents = None, 0, []
    while True:
        agents = list_agents()
        now_set = [a["to"] for a in agents]
        if agents and all(a["ready"] for a in agents) and now_set == prev:
            break
        prev = now_set
        if waited >= wait:
            break
        sleep(poll)
        waited += max(poll, 1)
    auto = [a for a in agents if DISPATCH(a["name"])]
    targets = [a for a in agents if not DISPATCH(a["name"])]
    ok, failed = 0, []
    for i, a in enumerate(targets):
        if i:
            sleep(gap)
        try:
            took = tell(a["to"], resume_text(boot))
        except Exception:
            took = False
        if took:
            ok += 1
        else:
            failed.append(a["to"])
    hhmm = time.strftime("%H:%M", time.localtime(boot))
    if not agents:
        what = "열려 있던 세션을 찾지 못했습니다. herdr가 세션을 되살리지 못했는지 확인이 필요합니다."
    else:
        what = f"열려 있던 세션 {ok}개에 끊긴 일을 이어서 하라고 보냈습니다."
        if failed:
            what += f" 보내지 못한 세션 {len(failed)}개: {', '.join(failed[:5])}."
        if auto:
            what += f" 자동 세션 {len(auto)}개는 직접 보내지 않았습니다."
    try:
        enqueue(f"reboot-resume-{boot}", "세션맥이 갑자기 다시 켜짐", f"세션맥이 {hhmm}쯤 정상 종료 없이 갑자기 다시 켜졌습니다. {what} 요유 할 일: 없음")
        alert = "alerted"
    except Exception as e:
        alert = f"alert failed {type(e).__name__}"
    return log(state_dir, now, f"boot {boot} sudden told {ok} failed {len(failed)} skipped {len(auto)} listed {len(agents)} {alert}")


def log(state_dir, now, line):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now()))} {line}"
    with open(os.path.join(state_dir, "log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


def main():
    env = dict(os.environ)
    try:
        if not env.get("REBOOT_RESUME_ANYHOST"):
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip()
            if name != "세션맥":  # ponytail: the plist is loaded on 세션맥 only; this is the second lock
                return 0
        base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
        boot = env.get("REBOOT_RESUME_BOOT") or subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
        boot = int(boot) if str(boot).isdigit() else int(boot.split("sec =")[1].split(",")[0])
        if env.get("REBOOT_RESUME_LAST"):
            with open(env["REBOOT_RESUME_LAST"], encoding="utf-8") as f:
                last_text = f.read()
        else:
            last_text = subprocess.run(["last", "reboot", "shutdown"], capture_output=True, text=True, timeout=10).stdout
        peers, inbox = load("herdr_peers"), load("main-alert-inbox")
        pane_tell = env.get("REBOOT_RESUME_PANE_TELL") or os.path.join(HERE, "pane-tell")

        def list_agents():
            rows = []
            for name in peers.PEERS:
                sock = peers.socket_of(env, name)
                if not sock:
                    continue
                for a in peers.herdr(env, "agent", "list", socket=sock).get("agents") or []:
                    if a.get("pane_id"):
                        rows.append({"to": a["pane_id"] if name == "default" else f"{name}/{a['pane_id']}",
                                     "name": a.get("name") or "", "ready": bool(a.get("interactive_ready"))})
            return rows

        def tell(to, text):
            try:
                return subprocess.run([pane_tell, to, text], env=env, timeout=15, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                return False

        run(boot, last_text, list_agents, tell, lambda i, t, b: inbox.enqueue(env, i, t, b), os.path.join(base, "reboot-resume"),
            wait=int(env.get("REBOOT_RESUME_WAIT") or 600), poll=int(env.get("REBOOT_RESUME_POLL") or 15), gap=int(env.get("REBOOT_RESUME_GAP") or 2))
    except Exception as e:
        print(f"reboot-resume: {type(e).__name__}", file=sys.stderr)
    return 0


def selftest():
    CLEAN = "reboot    ~                         Thu Nov  5 18:31\nshutdown  ~                         Thu Nov  5 18:29\nreboot    ~                         Fri Oct 30 15:23\n"
    SUDDEN = "reboot    ~                         Thu Nov 19 22:15\nreboot    ~                         Thu Nov  5 18:31\nshutdown  ~                         Thu Nov  5 18:29\n"
    assert sudden(CLEAN) is False, "a shutdown record right before the boot: planned"
    assert sudden(SUDDEN) is True, "no shutdown record before the newest boot: sudden"
    assert sudden("") is None and sudden("shutdown ~ Thu Nov 5 18:29\n") is None, "nothing to judge: unknown, never sudden"
    assert sudden("reboot ~ Thu Nov 19 22:15\n") is True, "the only boot ever recorded had no shutdown before it"

    class Fake:
        def __init__(self, rows):
            self.rows, self.told, self.alerts, self.fail, self.naps = rows, [], [], set(), []

        def list_agents(self):
            r = self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]
            return r

        def tell(self, to, text):
            self.told.append((to, text))
            return to not in self.fail

        def enqueue(self, i, t, b):
            self.alerts.append((i, t, b))

    def agent(to, name="spawn-X-1", ready=True):
        return {"to": to, "name": name, "ready": ready}

    def go(fake, last=SUDDEN, boot=1000, d=None, **kw):
        return run(boot, last, fake.list_agents, fake.tell, fake.enqueue, d, wait=kw.pop("wait", 100), poll=15, gap=2,
                   sleep=fake.naps.append, now=lambda: 0, **kw)

    with tempfile.TemporaryDirectory() as d:
        # sudden: every agent but the dispatch tab is told once, the main pane too, and 요유 hears once
        rows = [agent("wC:p1", "main"), agent("workers/w1:p2"), agent("workers/w1:p3", "auto-api-7"), agent("wC:p4")]
        f = Fake([rows])
        go(f, d=d)
        assert [t[0] for t in f.told] == ["wC:p1", "workers/w1:p2", "wC:p4"], f.told
        assert all(t[1].startswith(MARK) and "이어서" in t[1] for t in f.told), f.told
        assert len(f.alerts) == 1 and "3개" in f.alerts[0][2] and "자동 세션 1개" in f.alerts[0][2], f.alerts
        assert f.naps.count(2) == 2, "a gap between each send, none after the last: " + str(f.naps)
        # the same boot again: nothing
        g = Fake([rows])
        go(g, d=d)
        assert not g.told and not g.alerts, "once per boot"
        # a clean reboot: nothing, and the boot is still marked
        h = Fake([rows])
        go(h, last=CLEAN, boot=2000, d=d)
        assert not h.told and not h.alerts, "planned reboot sends nothing"
        go(h, last=SUDDEN, boot=2000, d=d)
        assert not h.told, "a planned boot stays marked"
        # a new sudden boot sends again
        i = Fake([rows])
        go(i, boot=3000, d=d)
        assert len(i.told) == 3 and len(i.alerts) == 1 and i.alerts[0][0] != f.alerts[0][0], "a later boot sends and has its own alert id"

    with tempfile.TemporaryDirectory() as d:
        # herdr still restoring: the list grows, and nothing is sent until two reads agree and all are ready
        f = Fake([[agent("wC:p1")], [agent("wC:p1"), agent("workers/w1:p2", ready=False)],
                  [agent("wC:p1"), agent("workers/w1:p2")], [agent("wC:p1"), agent("workers/w1:p2")]])
        go(f, d=d)
        assert [t[0] for t in f.told] == ["wC:p1", "workers/w1:p2"], f.told
        assert f.naps == [15, 15, 2], "waited twice for the list to settle, then one gap: " + str(f.naps)

    with tempfile.TemporaryDirectory() as d:
        # never settles: sent to what is listed when WAIT runs out
        n = [0]
        def churn():
            n[0] += 1
            return [agent("wC:p1"), agent(f"workers/w1:p{n[0]}")]
        f = Fake([[]])
        f.list_agents = churn
        go(f, d=d, wait=60)
        assert len(f.told) == 2 and f.naps.count(15) == 4, (f.told, f.naps)

    with tempfile.TemporaryDirectory() as d:
        # one pane-tell refuses: the rest still go, and the alert counts the miss by address
        f = Fake([[agent("wC:p1"), agent("workers/w1:p2"), agent("workers/w1:p3")]])
        f.fail = {"workers/w1:p2"}
        go(f, d=d)
        assert len(f.told) == 3 and "2개" in f.alerts[0][2] and "workers/w1:p2" in f.alerts[0][2], f.alerts
        # a pane-tell that raises is a miss too, never a crash
        f = Fake([[agent("wC:p1")]])
        def boom(to, text): raise OSError("x")
        f.tell = boom
        go(f, d=d, boot=4000)
        assert "0개" in f.alerts[0][2], f.alerts

    with tempfile.TemporaryDirectory() as d:
        # a sudden reboot with no agent found still tells 요유
        f = Fake([[]])
        go(f, d=d)
        assert not f.told and len(f.alerts) == 1 and "찾지 못했습니다" in f.alerts[0][2], f.alerts
        # an alert that cannot be stored does not stop the run
        f = Fake([[agent("wC:p1")]])
        def nope(*a): raise ValueError("x")
        f.enqueue = nope
        go(f, d=d, boot=5000)
        assert len(f.told) == 1, "told even when the alert fails"
        # an unclear reading sends nothing and waits for the next boot's reading
        f = Fake([[agent("wC:p1")]])
        go(f, last="", boot=6000, d=d)
        assert not f.told and not f.alerts

    # end to end: the real script with a fake herdr and pane-tell, a fake wtmp reading, and a state folder of its own
    with tempfile.TemporaryDirectory() as d:
        def put(path, text, mode=0o755):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(path, mode)
        def agents_json(rows):
            return json.dumps({"result": {"agents": [{"pane_id": p, "name": n, "interactive_ready": True} for p, n in rows]}})
        put(f"{d}/default-agents.json", agents_json([("wC:p1", "main"), ("wC:p2", "auto-api-3")]), 0o644)
        put(f"{d}/workers-agents.json", agents_json([("w1:p9", "spawn-X-1"), ("w1:p8", "spawn-X-2")]), 0o644)
        put(f"{d}/herdr", f"""#!/bin/bash
case "$*" in
  "session list --json") echo '{{"sessions":[{{"name":"default","running":true,"socket_path":"{d}/default.sock"}},{{"name":"workers","running":true,"socket_path":"{d}/sessions/workers/herdr.sock"}}]}}' ;;
  "agent list") case "$HERDR_SOCKET_PATH" in */workers/*) cat {d}/workers-agents.json ;; *) cat {d}/default-agents.json ;; esac ;;
  *) exit 1 ;;
esac
""")
        put(f"{d}/pane-tell", f"""#!/bin/bash
echo "$1|$2" >> {d}/told
[ "$1" != "workers/w1:p8" ] || {{ echo refused >&2; exit 2; }}
""")
        put(f"{d}/last", SUDDEN, 0o644)
        env = {**os.environ, "HOME": d, "XDG_STATE_HOME": f"{d}/state", "HERDR_BIN_PATH": f"{d}/herdr",
               "REBOOT_RESUME_PANE_TELL": f"{d}/pane-tell", "REBOOT_RESUME_LAST": f"{d}/last", "REBOOT_RESUME_BOOT": "1795094138",
               "REBOOT_RESUME_ANYHOST": "1", "REBOOT_RESUME_POLL": "0", "REBOOT_RESUME_GAP": "0", "REBOOT_RESUME_WAIT": "5"}
        for _ in range(2):
            subprocess.run([sys.executable, "-I", os.path.abspath(__file__)], env=env, check=True, timeout=60)
        told = open(f"{d}/told", encoding="utf-8").read().splitlines()
        assert [t.split("|")[0] for t in told] == ["wC:p1", "workers/w1:p9", "workers/w1:p8"], "once, not on the second run: " + str(told)
        log = open(f"{d}/state/reboot-resume/log", encoding="utf-8").read()
        assert "sudden" in log and "told 2 failed 1 skipped 1" in log and "alerted" in log, log
        os.remove(f"{d}/told")
        put(f"{d}/last", CLEAN, 0o644)
        env["REBOOT_RESUME_BOOT"] = "1795100000"
        subprocess.run([sys.executable, "-I", os.path.abspath(__file__)], env=env, check=True, timeout=60)
        assert not os.path.exists(f"{d}/told"), "a planned reboot sends nothing end to end"
    print("reboot-resume selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
