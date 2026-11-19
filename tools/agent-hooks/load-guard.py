#!/usr/bin/env python3
"""Memory-load guard for 세션맥 (Claude Code and Codex): keep agent sessions from starving the Mac of memory.

On 2026-11-19 22:15 세션맥 panicked and rebooted (WindowServer watchdog). The Jetsam records just before it list 112 node
processes, 40 of them over 1 GB (jest workers at about 2.6 GB each) on a 48 GB Mac with the compressor full. A spoken instruction to
the worker panes did not outlive the session, so three standing devices exist (runbook: munice-claude scripts/load-guard/README.md):
  1. pane-spawn runs "load-guard.py spawn": under memory pressure it waits up to 90 s, then refuses (exit 3) and opens no pane.
  2. check() below, a PreToolUse rider of gcp-account-guard.py (already registered in both tools with the widest matcher, so it reaches
     open sessions and needs no new Codex approval; see pane-fit-guard.py): refuses a jest run without --maxWorkers, and a new
     test or build (jest, vitest, npm test/build, docker build, xcodebuild, gradle) while the Mac is under pressure or already busy.
  3. load-watch.py (launchd, every 30 s, 세션맥 only) alerts the main pane and, in a critical state, ends only jest/vitest workers.

Pressure: kern.memorystatus_vm_pressure_level (1 normal, 2 warn, 4 critical) and kern.memorystatus_level (reclaimable percent).
It reads command lines only, so a jest started inside a script file is not seen by check(); load-watch is the backstop.
Fails open: any error passes the call.

    load-guard.py status | spawn | --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every tool call
WARN_LEVEL, CRIT_LEVEL = 2, 4
WARN_FREE, CRIT_FREE = 15, 5  # percent; the Mac read 85 when idle on 11/19 at 23:10, and about 0 at 22:05
MAX_RUNS, MAX_WORKERS = 3, 8  # runner processes (jest, vitest, docker build, xcodebuild), and worker processes (8 is about 21 GB)
SPAWN_WAIT, SPAWN_POLL = 90, 15
RUNNERS = {"jest", "jest.js", "vitest", "vitest.mjs"}
WORKER_PATH = re.compile(r"jest-worker/build/workers/|/tinypool/dist/|vitest/dist/workers/")
FLAGS = re.compile(r"^(?:--maxWorkers(?:=\S+)?|-w=?\d*|--runInBand|-i|--no-file-parallelism)$")
TEST_FILE = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
WORKERS_SET = re.compile(r"maxWorkers|runInBand|\s-i(?:\s|$)|no-file-parallelism")
WRAPPERS = {"time", "command", "exec", "env", "sudo", "nohup", "xargs"}

FLAG_MSG = ("load-guard: jest를 --maxWorkers 없이 돌리려 합니다. jest는 코어 수만큼 워커를 띄우고 워커마다 약 2.6GB를 씁니다(2026-11-19 세션맥이 "
            "메모리 부족으로 재부팅됨). 이 호출을 버리고 --maxWorkers=2를 붙여 다시 실행하세요(npm이면 npm test -- --maxWorkers=2). "
            "시험 파일 하나만 지정해도 됩니다. 다른 칸이 시험을 돌리고 있으면 끝난 뒤에 하세요.")
LOAD_MSG = ("load-guard: 지금 세션맥이 무겁습니다({why}). 새 시험·빌드를 시작하지 않습니다. 이 호출을 버리고 sleep 90 뒤에 같은 명령을 다시 "
            "실행하세요. 계속 막히면 python3 -I ~/.dotfiles/tools/agent-hooks/load-guard.py status 로 얼마나 돌고 있는지 확인하고, 다른 칸의 시험이 "
            "끝나기를 기다립니다.")


def _sysctl(name: str) -> int | None:
    try:
        return int(subprocess.run(["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True, timeout=3).stdout.strip())
    except Exception:
        return None


def memory(env) -> tuple[int | None, int | None]:
    """(pressure level, reclaimable percent). LOAD_GUARD_MEM="level,percent" overrides it for tests and drills."""
    fake = env.get("LOAD_GUARD_MEM")
    if fake:
        level, free = fake.split(",")
        return int(level), int(free)
    return _sysctl("kern.memorystatus_vm_pressure_level"), _sysctl("kern.memorystatus_level")


def status(level: int | None, free: int | None) -> str:
    """ok, warn or critical. An unreadable value counts as fine: the guard never blocks on its own failure."""
    if (level or 0) >= CRIT_LEVEL or (free is not None and free <= CRIT_FREE):
        return "critical"
    if (level or 0) >= WARN_LEVEL or (free is not None and free <= WARN_FREE):
        return "warn"
    return "ok"


def classify(argv: list[str]) -> str | None:
    """"worker" for a jest/vitest worker process, "run" for a test or build runner itself, else None."""
    head = [os.path.basename(a) for a in argv[:2]]
    if WORKER_PATH.search(" ".join(argv[:3])):
        return "worker"
    if head and (head[0] in RUNNERS or (head[0] == "node" and len(head) > 1 and head[1] in RUNNERS)):
        return "run"
    if head and head[0] == "xcodebuild":
        return "run"
    if head and head[0] == "docker" and (argv[1:2] == ["build"] or argv[1:3] == ["buildx", "build"]):
        return "run"
    return None


def census(ps_text: str, own_pid: int | None = None) -> dict:
    """Counts from "ps -axo pid=,rss=,command=" output: runner processes, and workers as (pid, rss in KB, command)."""
    runs, workers = 0, []
    for line in ps_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[0]) == own_pid:
            continue
        kind = classify(parts[2].split())
        if kind == "run":
            runs += 1
        elif kind == "worker":
            workers.append((int(parts[0]), int(parts[1]), parts[2]))
    return {"runs": runs, "workers": workers}


def ps_text() -> str:
    return subprocess.run(["/bin/ps", "-axo", "pid=,rss=,command="], capture_output=True, text=True, timeout=5).stdout


def busy(counts: dict) -> str:
    """Why a new test or build should wait, or ''."""
    if counts["runs"] >= MAX_RUNS:
        return f"시험·빌드 {counts['runs']}개가 이미 돌고 있음"
    if len(counts["workers"]) >= MAX_WORKERS:
        return f"시험 워커 {len(counts['workers'])}개가 이미 돌고 있음"
    return ""


def segments(command: str):
    for part in re.split(r"&&|\|\||[;|&\n]", command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        while tokens and (re.match(r"^[A-Za-z_]\w*=", tokens[0]) or tokens[0] in WRAPPERS or tokens[0].startswith("-")):
            tokens = tokens[1:]
        if tokens and tokens[0].lstrip("({") != tokens[0]:  # a subshell or group: (cd repo && npm test)
            tokens[0] = tokens[0].lstrip("({")
            tokens = tokens if tokens[0] else tokens[1:]
        if tokens and tokens[-1].count(")") > tokens[-1].count("("):  # the end of a subshell: npm test)
            tokens[-1] = tokens[-1].rstrip(")") or "true"
        if tokens:
            yield tokens


def package_script(cwd: str, name: str) -> str | None:
    try:
        with open(os.path.join(cwd, "package.json"), encoding="utf-8") as f:
            return str(json.load(f).get("scripts", {}).get(name) or "")
    except Exception:
        return None


def config_sets_workers(cwd: str) -> bool:
    """A repo jest config that already fixes maxWorkers (jest.config.* or package.json's jest key)."""
    for name in ("jest.config.js", "jest.config.ts", "jest.config.mjs", "jest.config.cjs", "jest.config.json", "package.json"):
        try:
            with open(os.path.join(cwd, name), encoding="utf-8") as f:
                text = f.read()
            if name == "package.json":
                text = json.dumps(json.loads(text).get("jest") or "")
        except Exception:
            continue
        if "maxWorkers" in text:
            return True
    return False


def start_kind(tokens: list[str], cwd: str, depth: int = 0) -> tuple[str, list[str]] | None:
    """What a command segment starts: ("jest", args) for a jest run, ("build", args) for another heavy run, or None."""
    exe, rest = os.path.basename(tokens[0]), tokens[1:]
    if exe in ("bash", "sh", "zsh") and depth == 0 and "-c" in rest[:3] and rest.index("-c") + 1 < len(rest):
        for inner in segments(rest[rest.index("-c") + 1]):
            found = start_kind(inner, cwd, 1)
            if found:
                return found
        return None
    if exe in ("npx", "bunx") or (exe in ("yarn", "pnpm") and rest[:1] in (["exec"], ["dlx"])) or (exe == "npm" and rest[:1] == ["exec"]):
        inner = [a for a in rest if a not in ("exec", "dlx", "--")]
        while inner and inner[0].startswith("-"):
            inner.pop(0)  # npx -y jest
        return start_kind(inner, cwd, depth) if inner else None
    if exe == "node" and rest and os.path.basename(rest[0]) in RUNNERS:
        exe, rest = os.path.basename(rest[0]), rest[1:]
    if exe in ("jest", "jest.js"):
        return "jest", rest
    if exe in ("vitest", "vitest.mjs", "xcodebuild", "gradle", "gradlew"):
        return "build", rest
    if exe == "docker" and (rest[:1] == ["build"] or rest[:2] == ["buildx", "build"] or "--build" in rest or (rest[:1] == ["compose"] and "build" in rest)):
        return "build", rest
    if exe in ("npm", "yarn", "pnpm", "bun") and rest:
        name = rest[0] if rest[0] != "run" else (rest[1] if len(rest) > 1 else "")
        args = rest[1:] if rest[0] != "run" else rest[2:]
        if name in ("t", "test") or name.startswith("test:"):
            script = package_script(cwd, "test" if name == "t" else name)
            if script is None or not re.search(r"jest|vitest", script):
                return None
            if WORKERS_SET.search(script):
                return "build", args
            return ("jest" if "jest" in script else "build"), args
        if name == "build" or name.startswith("build:"):
            return "build", args
    return None


def check(payload: dict, env, counts: dict | None = None, mem: tuple | None = None) -> str | None:
    """The reason to refuse this tool call with, or None."""
    if payload.get("tool_name") != "Bash":
        return None
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not re.search(r"jest|vitest|docker|build|test|gradle|xcode|\bt\b", command):
        return None  # the usual case, and it must stay cheap: every Bash call lands here
    cwd = str(payload.get("cwd") or env.get("PWD") or os.getcwd())
    found = []
    for tokens in segments(command):
        # The command's own cd (or npm --prefix, yarn --cwd) decides whose package.json counts, not the session's folder.
        if os.path.basename(tokens[0]) in ("cd", "pushd") and len(tokens) > 1:
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(tokens[1])))
            continue
        here = cwd
        for flag in ("--prefix", "--cwd"):
            if flag in tokens and tokens.index(flag) + 1 < len(tokens):
                i = tokens.index(flag)
                here = os.path.normpath(os.path.join(cwd, os.path.expanduser(tokens[i + 1])))
                tokens = tokens[:i] + tokens[i + 2:]
        kind = start_kind(tokens, here)
        if kind:
            found.append((*kind, here))
    if not found:
        return None
    for kind, args, here in found:
        if kind == "jest" and not any(FLAGS.match(a) or TEST_FILE.search(a) for a in args) and not config_sets_workers(here):
            return FLAG_MSG
    level, free = mem if mem is not None else memory(env)
    why = {"warn": "메모리 압박 경고", "critical": "메모리 압박 위험"}.get(status(level, free), "")
    if not why:
        why = busy(counts if counts is not None else census(ps_text(), os.getpid()))
    return LOAD_MSG.format(why=why) if why else None


def spawn_gate(env, sleep=time.sleep, clock=time.monotonic) -> int:
    """pane-spawn: 0 to open the pane; 3 once the wait ran out with the Mac still under pressure."""
    deadline = clock() + int(env.get("LOAD_GUARD_SPAWN_WAIT") or SPAWN_WAIT)
    while True:
        level, free = memory(env)
        state = status(level, free)
        if state == "ok":
            return 0
        if clock() >= deadline:
            print(f"pane-spawn: 세션맥 메모리 압박이 {state} 단계입니다(압박 {level}, 회수 가능 {free}%). 새 칸을 열지 않습니다. "
                  f"몇 분 뒤 다시 시도하세요. 상태: python3 -I {os.path.abspath(__file__)} status", file=sys.stderr)
            return 3
        sleep(SPAWN_POLL)


def selftest():
    import tempfile

    ok, warn, crit = (1, 80), (2, 80), (4, 3)
    assert [status(*m) for m in (ok, warn, crit, (1, 15), (1, 5), (None, None), (1, None))] == ["ok", "warn", "critical", "warn", "critical", "ok", "ok"]
    assert status(1, 16) == "ok" and status(1, 6) == "warn", "free percent edges"

    ps = "\n".join([
        "100 5000 node /r/node_modules/.bin/jest --coverage",
        "101 2600000 /usr/local/bin/node /r/node_modules/jest-worker/build/workers/processChild.js",
        "102 2700000 node /r/node_modules/jest-worker/build/workers/threadChild.js",
        "103 40000 docker build -t x .",
        "104 800000 claude --settings /x/s.json",
        "105 900 grep jest /x",
        "106 20000 node /r/node_modules/vitest/dist/workers/forks.js",
        "107 900 node /r/node_modules/.bin/webpack",
        "garbage line"])
    c = census(ps)
    assert c["runs"] == 2 and [w[0] for w in c["workers"]] == [101, 102, 106], c
    assert census(ps, own_pid=100)["runs"] == 1, "own pid is skipped"
    assert busy({"runs": 3, "workers": []}) and busy({"runs": 0, "workers": [()] * 8}) and not busy({"runs": 2, "workers": [()] * 7})

    with tempfile.TemporaryDirectory() as d:
        def pkg(**scripts):
            with open(os.path.join(d, "package.json"), "w") as f:
                json.dump({"scripts": scripts}, f)

        def run(cmd, counts=None, mem=ok, cwd=d, tool="Bash"):
            return check({"tool_name": tool, "tool_input": {"command": cmd}, "cwd": cwd}, {}, counts or {"runs": 0, "workers": []}, mem)

        pkg(test="jest", **{"test:cov": "jest --coverage", "test:smoke": "jest --runInBand", "build": "nest build", "lint": "eslint ."})
        # the command's own cd decides which package.json counts, not the session's folder (found live: "cd repo && npm test" passed)
        elsewhere = os.path.join(d, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        for cmd in (f"cd {d} && npm test", f"(cd {d} && npm test)", f"cd {d}; npx jest", "cd .. && npm test",
                    f"npm --prefix {d} test", f"npm test --prefix {d}", f"yarn --cwd {d} test", f"cd {d} && cd . && npm run test:cov"):
            assert run(cmd, cwd=elsewhere) == FLAG_MSG, cmd
        assert run(f"cd {d} && npm test", cwd=elsewhere) == FLAG_MSG
        assert run(f"cd {d} && npm test", cwd="/nonexistent") == FLAG_MSG, "the cd target is read, not the session folder"
        assert run("cd ~/nowhere-at-all && npm test", cwd=d) is None, "a target with no package.json is no known runner"
        assert run(f"cd {d} && npm test -- --maxWorkers=2", cwd=elsewhere) is None
        assert run("npm test", cwd=elsewhere) is None, "no cd and no package.json here"
        for cmd in ("npx jest", "jest", "npm test", "npm run test:cov", "yarn test", "node node_modules/.bin/jest", "cd x && npx jest --coverage", "npx -y jest",
                    "FOO=1 npx jest src", "bash -c 'npx jest'", "npm test -- --coverage", "pnpm exec jest", "npx jest --watch"):
            assert run(cmd) == FLAG_MSG, cmd
        for cmd in ("npx jest --maxWorkers=2", "npx jest -w 2", "npx jest --runInBand", "npx jest -i", "npm test -- --maxWorkers=50%", "npm run test:smoke",
                    "npx jest src/a.service.spec.ts", "npx jest test/a.test.ts --coverage", "npm test -- src/b.spec.ts"):
            assert run(cmd) is None, cmd
        for cmd in ("npm run lint", "git status", "echo npx jest", "ls test/", "grep -rn jest src", "cat jest.config.js", "python3 -m pytest", "uv run pytest -q"):
            assert run(cmd, mem=crit) is None, f"not a runner: {cmd}"
        assert run("npx jest", tool="Read") is None
        # load: a new test or build waits under pressure, or when runs/workers already crowd the Mac
        for cmd in ("npx jest --maxWorkers=2", "npm run build", "docker build -t x .", "docker compose up --build", "xcodebuild -scheme A", "./gradlew test",
                    "npx vitest run", "npm run test:smoke"):
            assert "무겁습니다(메모리 압박 경고)" in (run(cmd, mem=warn) or ""), cmd
            assert "압박 위험" in (run(cmd, mem=crit) or ""), cmd
            assert "3개가 이미" in (run(cmd, {"runs": 3, "workers": []}) or ""), cmd
            assert "워커 8개" in (run(cmd, {"runs": 0, "workers": [()] * 8}) or ""), cmd
            assert run(cmd) is None, f"idle Mac passes: {cmd}"
        assert run("npx jest", mem=crit) == FLAG_MSG, "the flag rule comes first"
        # repo config that already fixes the workers
        with open(os.path.join(d, "jest.config.js"), "w") as f:
            f.write("module.exports = { maxWorkers: 2 }")
        assert run("npx jest") is None and run("npm test") is None, "configured maxWorkers passes"
        os.unlink(os.path.join(d, "jest.config.js"))
        pkg(test="jest --maxWorkers=2")
        assert run("npm test") is None, "script with maxWorkers passes"
        pkg(test="echo no test")
        assert run("npm test", mem=crit) is None, "a test script that is not jest/vitest is no heavy run"
        os.unlink(os.path.join(d, "package.json"))
        assert run("npm test", mem=crit) is None, "no package.json: not a known runner"
    # fail open: odd payloads and an unreadable override never raise out of the rider contract's callers
    assert check({"tool_name": "Bash"}, {}) is None and check({}, {}) is None and check({"tool_name": "Bash", "tool_input": None}, {}) is None

    # spawn gate: waits, then refuses; a pressure that clears opens the pane
    sleeps, t = [], [0.0]

    def nap(s):
        sleeps.append(s)
        t[0] += s

    assert spawn_gate({"LOAD_GUARD_MEM": "1,80"}, nap, lambda: t[0]) == 0 and not sleeps
    assert spawn_gate({"LOAD_GUARD_MEM": "4,3", "LOAD_GUARD_SPAWN_WAIT": "30"}, nap, lambda: t[0]) == 3 and sleeps == [15, 15], sleeps
    levels = iter(["4,3", "2,60", "1,80"])
    sleeps.clear()

    class Env(dict):
        def get(self, k, d=None):
            return next(levels) if k == "LOAD_GUARD_MEM" else dict.get(self, k, d)

    assert spawn_gate(Env(), nap, lambda: t[0]) == 0 and sleeps == [15, 15], sleeps
    print("load-guard selftest ok")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        selftest()
    elif arg == "spawn":
        return spawn_gate(os.environ)
    elif arg == "status":
        level, free = memory(os.environ)
        c = census(ps_text(), os.getpid())
        print(f"pressure={level} reclaimable={free}% runs={c['runs']} workers={len(c['workers'])} -> {status(level, free)}"
              + (f"; {busy(c)}" if busy(c) else ""))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
