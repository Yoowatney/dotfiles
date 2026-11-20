#!/usr/bin/env python3
"""launchd, every 30 s, on 세션맥 only: watch memory pressure, alert the main pane, and in a critical state end test workers.

2026-11-19 22:15: 세션맥 panicked after worker panes ran jest and builds all at once (load-guard.py has the numbers). This is the
backstop that needs no session to behave: it also covers a jest started from a script file, which the Bash hook cannot see.
- pressure at warn or worse for 4 ticks (2 minutes): one alert per episode to the main pane (main-alert-inbox, which the main pane
  reads and relays to #0). The episode ends after 10 quiet ticks (5 minutes);
- pressure critical for 2 ticks (1 minute): SIGTERM to the 8 largest jest/vitest workers (SIGKILL for one already sent SIGTERM on
  an earlier tick), one alert per 5 minutes saying how many and how much. A test run is rerun with --maxWorkers=2.
It never ends an agent (claude, codex, herdr) or docker/OrbStack: only a command line matching a jest-worker or vitest worker, and a
line naming claude, codex or herdr is skipped even then. Critical with no worker to end is alerted too, naming the biggest process
kinds by program name only (no arguments).
State and log: ~/.local/state/load-watch/ (LOAD_WATCH_DIR). Fails open: any error exits 0 quietly.
Drill: LOAD_GUARD_MEM="4,3" python3 load-watch.py (two ticks, so run it twice).

    load-watch.py | --selftest
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
WARN_TICKS, CRIT_TICKS, QUIET_TICKS, KILL_BATCH, ALERT_EVERY = 4, 2, 10, 8, 300
NEVER = ("claude", "codex", "herdr")


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kinds(ps_text: str, top: int = 5) -> str:
    """Resident memory by program name only, e.g. "node 40개 104.0GB". Arguments never leave the Mac."""
    total: dict = {}
    for line in ps_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        name = os.path.basename(parts[2].split()[0])
        count, kb = total.get(name, (0, 0))
        total[name] = (count + 1, kb + int(parts[1]))
    best = sorted(total.items(), key=lambda kv: -kv[1][1])[:top]
    return ", ".join(f"{n} {c}개 {kb / 2**20:.1f}GB" for n, (c, kb) in best)


def tick(env, state: dict, guard, text: str, mem: tuple, now: float, kill=os.kill, enqueue=None) -> dict:
    """One pass; returns the new state. enqueue(event_id, title, body) delivers an alert."""
    level, free = mem
    st = guard.status(level, free)
    log: list = []
    alerts: list = []
    state = {"warn": 0, "crit": 0, "quiet": 0, "alerted": False, "alert_at": 0, "termed": [], **state}
    if st == "ok":
        state["warn"] = state["crit"] = 0
        state["quiet"] += 1
        if state["quiet"] >= QUIET_TICKS:
            state["alerted"] = False
    else:
        state["quiet"] = 0
        state["warn"] += 1
        state["crit"] = state["crit"] + 1 if st == "critical" else 0
    detail = f"압박 {level}, 회수 가능 {free}%"
    if state["warn"] >= WARN_TICKS and not state["alerted"]:
        state["alerted"] = True
        alerts.append((f"load-watch-warn-{int(now)}", "세션맥 메모리 압박이 2분 이어짐", f"{detail}. 많이 쓰는 프로그램: {kinds(text)}. "
                       "시험 워커나 docker가 원인이면 그 칸에 알려 주세요. 위험 단계가 1분 이어지면 시험 워커를 자동으로 끕니다."))
    if state["crit"] >= CRIT_TICKS:
        counts = guard.census(text, os.getpid())
        workers = [w for w in sorted(counts["workers"], key=lambda w: -w[1]) if not any(n in w[2] for n in NEVER)][:KILL_BATCH]
        ended, kb = 0, 0
        for pid, rss, _ in workers:
            try:
                kill(pid, signal.SIGKILL if pid in state["termed"] else signal.SIGTERM)
                ended, kb = ended + 1, kb + rss
            except OSError:
                pass
        state["termed"] = [w[0] for w in workers]
        if now - state["alert_at"] >= ALERT_EVERY:
            state["alert_at"] = now
            what = (f"시험 워커 {ended}개({kb / 2**20:.1f}GB)를 끝냈습니다. 시험은 --maxWorkers=2로 다시 돌리면 됩니다." if ended else
                    f"끝낼 시험 워커가 없습니다. 많이 쓰는 프로그램: {kinds(text)}")
            alerts.append((f"load-watch-crit-{int(now)}", "세션맥 메모리 위험: " + ("시험 워커 종료" if ended else "원인 프로세스를 직접 확인"), f"{detail}. {what}"))
        log.append(f"critical ended={ended} {kb // 1024}MB")
    if st != "ok":
        log.append(f"{st} {detail} warn={state['warn']} crit={state['crit']}")
    for alert in alerts:
        try:
            if enqueue:
                enqueue(*alert)
            log.append("alert " + alert[1])
        except Exception as e:  # the next tick tries again only for a new episode; the log keeps the miss
            log.append(f"alert failed {type(e).__name__}")
    state["log"] = log
    return state


def main() -> int:
    d = os.environ.get("LOAD_WATCH_DIR") or os.path.expanduser("~/.local/state/load-watch")
    try:
        guard = load("load-guard")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "state.json")
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
        inbox = load("main-alert-inbox")
        now = time.time()
        state = tick(os.environ, state, guard, guard.ps_text(), guard.memory(os.environ), now,
                     enqueue=lambda i, t, b: inbox.enqueue(dict(os.environ), i, t, b))
        log = state.pop("log", [])
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(path + ".tmp", path)
        if log:
            with open(os.path.join(d, "log"), "a", encoding="utf-8") as f:
                for line in log:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} {line}\n")
    except Exception as e:
        print(f"load-watch: {type(e).__name__}", file=sys.stderr)
    return 0


def selftest():
    guard = load("load-guard")
    sent, killed = [], []
    enqueue = lambda i, t, b: sent.append((i, t, b))
    kill = lambda pid, sig: killed.append((pid, sig))
    ps = "\n".join([
        "101 2600000 /usr/local/bin/node /r/node_modules/jest-worker/build/workers/processChild.js --secret-arg",
        "102 2900000 node /r/node_modules/jest-worker/build/workers/processChild.js",
        "103 800000 claude --settings /x/s.json",
        "104 700000 node /r/node_modules/jest-worker/build/workers/processChild.js claude-fixture",
        "105 50000 node /r/node_modules/.bin/jest"])
    ok, warn, crit = (1, 80), (2, 60), (4, 3)
    s, t = {}, 1000.0

    def go(mem, **kw):
        nonlocal s, t
        t += 30
        s = tick({}, s, guard, ps, mem, t, kill=kill, enqueue=enqueue, **kw)
        return s

    for _ in range(3):
        go(warn)
    assert not sent, "3 warn ticks: no alert yet"
    go(warn)
    assert len(sent) == 1 and "2분" in sent[0][1] and "node 4개" in sent[0][2] and "secret-arg" not in sent[0][2], sent
    for _ in range(5):
        go(warn)
    assert len(sent) == 1 and not killed, "one alert per episode; warn never kills"
    go(crit)
    assert not killed, "one critical tick is not enough"
    go(crit)
    assert [k[0] for k in killed] == [102, 101] and all(k[1] == signal.SIGTERM for k in killed), "largest first; claude-named and non-workers skipped"
    assert 103 not in [k[0] for k in killed] and 105 not in [k[0] for k in killed] and 104 not in [k[0] for k in killed]
    assert len(sent) == 2 and "시험 워커 2개" in sent[1][2], sent
    killed.clear()
    go(crit)
    assert [k[1] for k in killed] == [signal.SIGKILL] * 2, "a worker that outlived SIGTERM gets SIGKILL"
    assert len(sent) == 2, "kill alerts are limited to one per 5 minutes"
    for _ in range(QUIET_TICKS):
        go(ok)
    assert s["warn"] == 0 and s["crit"] == 0 and s["alerted"] is False, "episode over after quiet ticks"
    # critical with nothing to end still tells
    sent.clear(); killed.clear(); s = {}
    ps = "103 800000 claude --settings /x/s.json\n106 600000 node /r/server.js"
    go(crit); go(crit)
    assert not killed and len(sent) == 1 and "끝낼 시험 워커가 없습니다" in sent[0][2] and "claude 1개" in sent[0][2], sent
    # a kill that fails (process gone) and an alert that fails are logged, not raised
    ps = "101 2600000 node /r/node_modules/jest-worker/build/workers/processChild.js"
    def boom(pid, sig): raise ProcessLookupError()
    s = {}
    for _ in range(2):
        t += 400
        s = tick({}, s, guard, ps, crit, t, kill=boom, enqueue=lambda *a: 1 / 0)
    assert "alert failed ZeroDivisionError" in s["log"][-1] or any("alert failed" in x for x in s["log"]), s["log"]
    # a real process end to end: a fake worker is ended, an agent-looking process is not
    import subprocess, tempfile
    fake = subprocess.Popen(["bash", "-c", "exec -a '/usr/local/bin/node /tmp/x/node_modules/jest-worker/build/workers/processChild.js' sleep 60"])
    agent = subprocess.Popen(["bash", "-c", "exec -a 'claude --fake' sleep 60"])
    # The real hook sends real signals, so it must see only these two processes: any other jest worker on the host (another
    # session's test run) would be among the 8 it ends, and a busy host could outlast ps's 5 s limit. The wrapper filters the
    # hook's `ps` to the two pids and lifts that limit; the hook itself is unchanged.
    wrapper = ("import runpy, subprocess, sys\n"
               "real, keep, script = subprocess.run, set(sys.argv[2:]), sys.argv[1]\n"
               "def run(cmd, *a, **k):\n"
               "    if cmd[0] == '/bin/ps':\n"
               "        k['timeout'] = None\n"
               "        done = real(cmd, *a, **k)\n"
               "        done.stdout = '\\n'.join(l for l in done.stdout.splitlines() if l.split(None, 1)[0] in keep)\n"
               "        return done\n"
               "    return real(cmd, *a, **k)\n"
               "subprocess.run, sys.argv = run, [script]\n"
               "runpy.run_path(script, run_name='__main__')\n")
    try:
        seen = lambda: [w[0] for w in guard.census("\n".join(l for l in guard.ps_text().splitlines()
                                                              if l.split(None, 1)[0] in (str(fake.pid), str(agent.pid))))["workers"]]
        # Wait until the fake worker's command line is what the census reads (bash has exec'd), however long a busy host takes.
        deadline = time.monotonic() + 120
        while fake.pid not in seen() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert fake.pid in seen() and agent.pid not in seen(), "census finds the fake worker only"
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "LOAD_GUARD_MEM": "4,3", "LOAD_WATCH_DIR": d, "HOME": d, "XDG_STATE_HOME": d}
            for _ in range(2):
                subprocess.run([sys.executable, "-I", "-c", wrapper, os.path.abspath(__file__), str(fake.pid), str(agent.pid)],
                               env=env, check=True)
            assert fake.wait(timeout=60) == -signal.SIGTERM or fake.poll() is not None, "the fake worker ended"
            assert agent.poll() is None, "the agent-like process is untouched"
            assert "critical ended=1" in open(os.path.join(d, "log")).read()
    finally:
        for p in (fake, agent):
            p.kill()
    print("load-watch selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
