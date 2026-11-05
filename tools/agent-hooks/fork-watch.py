#!/usr/bin/env python3
"""launchd, always on (com.yoyoo.fork-watch, 회사맥 only): stop a process runaway before it fills the user's process limit.

On 2026-11-05 a runaway filled the per-user limit (8000 on 회사맥) and froze every pane and launchd job until a reboot.
Stopping its top process did not help: the children went on, adopted by launchd.
Every INTERVAL (10 s) this counts the user's processes from the kernel table (sysctl, no fork: near the limit even a ps
could not start; normal is 650-790). Over LIMIT (4000) it picks the group that grew and stops all of it:
- groups: a process hangs under its parent; one whose parent ended, or is not ours (launchd, a root `login`), hangs under
  its process group, so a runaway whose top was killed is still one group;
- protected: herdr servers, the main pane's agent (~/.local/state/orchestrator/main), this watch, and every ancestor of
  those (launchd among them);
- the candidates are the unprotected children of protected nodes; take the biggest, then walk down into a child while it
  still holds 90 % of that candidate, so a pane's agent survives a runaway in one of its commands;
- the biggest is stopped only when it holds a quarter of all the user's processes: a runaway spread over thousands of groups
  leaves a normal pane biggest, and that round is logged and told, nothing killed;
- the main pane's agent is whatever runs in the front of its shell's terminal, read from the kernel table each round; while
  herdr cannot say which shell that is (not up yet, or no fork left), the front of every herdr pane is kept instead;
- while a group is stopped, a pid counts only as the process it was (pid and start), and a child launchd adopted out of a
  member's process group counts too;
- STOP every member (a frozen fork bomb forks no more; new children are caught in up to 5 rounds), TERM and CONT them,
  KILL what is left after 3 s.
Then a line in ~/.local/state/fork-watch/kills.log (time, count, the top's command line), the main pane told by pane-tell
when there is one, and a macOS notification. Over the limit with nothing to stop: the same, once in 10 minutes.
Every round overwrites ~/.local/state/fork-watch/heartbeat with its time and count: an old time there means the watch stopped.

    fork-watch.py              run (launchd)
    fork-watch.py --once       one round, then exit
    fork-watch.py --status     count, limit, the main pane's agent, the herdr servers
    fork-watch.py --selftest
Env: FORK_WATCH_LIMIT, FORK_WATCH_INTERVAL, FORK_WATCH_PANE_TELL and FORK_WATCH_NOTIFY (fakes for tests: argv
<address> <message> and <title> <body>), XDG_STATE_HOME. The live check is fork-watch.test.sh.
"""
from __future__ import annotations  # macOS's /usr/bin/python3 is 3.9

import sys

sys.dont_write_bytecode = True

import collections
import ctypes
import importlib.util
import os
import signal
import struct
import subprocess
import time
import traceback

HERE = os.path.dirname(os.path.realpath(__file__))  # python3 -I drops this folder from sys.path
_spec = importlib.util.spec_from_file_location("herdr_peers", os.path.join(HERE, "herdr_peers.py"))
herdr_peers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(herdr_peers)

LIMIT = 4000
INTERVAL = 10
MIN_GROUP = 50  # a pick smaller than this is no runaway: it is logged and nothing is killed
SHARE = 0.25  # nor one under this share of all the user's processes: beside a runaway spread flat it is a normal pane
ROOT = 0  # the node above every process; a negative key is a process group whose leader is gone
Proc = collections.namedtuple("Proc", "pid ppid pgid start stat comm tpgid")


_libc = ctypes.CDLL(None, use_errno=True)
KINFO = 648  # sizeof(struct kinfo_proc) on 64-bit macOS; the selftest checks the fields below against ps
SZOMB = 5


def _sysctl(mib) -> bytes:
    m = (ctypes.c_int * len(mib))(*mib)
    for _ in range(5):  # the table can grow between the size call and the read (ENOMEM): ask again
        n = ctypes.c_size_t()
        if _libc.sysctl(m, len(mib), None, ctypes.byref(n), None, 0):
            raise OSError(ctypes.get_errno(), "sysctl size")
        n.value += n.value // 4 + 4096
        buf = ctypes.create_string_buffer(n.value)
        if _libc.sysctl(m, len(mib), buf, ctypes.byref(n), None, 0) == 0:
            return buf.raw[:n.value]
        if ctypes.get_errno() != 12:
            raise OSError(ctypes.get_errno(), "sysctl read")
    raise OSError(12, "sysctl kept growing")


def list_procs() -> list:
    """Every process of this real uid (what the process limit counts, zombies included), from the kernel: no fork."""
    raw = _sysctl([1, 14, 6, os.getuid()])  # CTL_KERN, KERN_PROC, KERN_PROC_RUID
    out = []
    for off in range(0, len(raw) - KINFO + 1, KINFO):
        sec, usec = struct.unpack_from("qi", raw, off)  # p_starttime: with the pid, which process this is
        stat, = struct.unpack_from("b", raw, off + 36)
        pid, = struct.unpack_from("i", raw, off + 40)
        ppid, pgid = struct.unpack_from("ii", raw, off + 560)  # kp_eproc.e_ppid, e_pgid
        tpgid, = struct.unpack_from("i", raw, off + 576)  # e_tpgid: its terminal's foreground process group
        comm = raw[off + 243:off + 260].split(b"\0")[0].decode(errors="replace")
        out.append(Proc(pid, ppid, pgid, sec * 1_000_000 + usec, stat, comm, tpgid))
    return out


def argv_of(pid: int) -> list:
    """The command line of one of our processes (KERN_PROCARGS2), [] when it is gone or unreadable."""
    try:
        raw = _sysctl([1, 49, pid])
    except OSError:
        return []
    argc, = struct.unpack_from("i", raw, 0)
    rest = raw[4:].split(b"\0")
    i = 1  # rest[0] is the executable path, then NUL padding, then argv
    while i < len(rest) and not rest[i]:
        i += 1
    return [a.decode(errors="replace") for a in rest[i:i + argc]]


def over(count: int, limit: int) -> bool:
    return count > limit


def tree(procs) -> tuple:
    """(parent, kids): each pid's parent node. A process whose parent is not one of ours (launchd, root's `login`, or gone)
    hangs under its process group leader, or under the group itself (-pgid) when the leader is gone too."""
    by = {p.pid: p for p in procs}
    parent = {}
    for p in procs:
        if p.ppid in by and p.ppid != p.pid:
            parent[p.pid] = p.ppid
        elif p.pgid != p.pid:
            parent[p.pid] = p.pgid if p.pgid in by else -p.pgid
        else:
            parent[p.pid] = ROOT
    for g in {v for v in parent.values() if v < 0}:
        parent[g] = ROOT
    ok = {ROOT}  # nodes known to reach ROOT; the pgid rule can close a loop, cut where the walk comes back
    for k in list(parent):
        path, on = [], set()
        while k not in ok:
            if k in on:
                parent[k] = ROOT
                break
            on.add(k)
            path.append(k)
            k = parent[k]
        ok.update(path)
    kids = collections.defaultdict(list)
    for k, v in parent.items():
        kids[v].append(k)
    return parent, kids


def keepers(procs, args, main_pid, self_pid: int) -> set:
    """Never killed: herdr servers (herdr ... server), the main pane's agent, this watch. Their ancestors come in guard().
    main_pid None: there is a main pane but herdr could not say which, so the front of every herdr pane's shell is kept."""
    servers = {p.pid for p in procs if p.comm == "herdr" and (lambda a: a and os.path.basename(a[0]) == "herdr" and "server" in a[1:])(args(p.pid))}
    keep = servers | {pid for pid in (main_pid, self_pid) if pid}
    if main_pid is None:
        pids = {p.pid for p in procs}
        keep |= {p.tpgid if p.tpgid in pids else p.pid for p in procs if p.ppid in servers}
    return keep


def guard(parent, keep) -> set:
    out = {ROOT}
    for k in keep:
        while k in parent and k not in out:
            out.add(k)
            k = parent[k]
    return out


def under(procs, tops, keep, pgids=()) -> list:
    """The processes at or below any node in `tops`, or below one whose parent is not ours (launchd adopted it) and whose
    process group is in `pgids`, its leader aside (a leader is no child adopted just now); protected ones left out."""
    parent, _ = tree(procs)
    safe = guard(parent, keep)
    by = {p.pid: p for p in procs}
    memo = {ROOT: False}

    def top(k):
        p = by.get(k)
        return k in tops or bool(p and p.pgid in pgids and p.pid != p.pgid and p.ppid not in by)

    def hit(k):
        path = []
        while k not in memo and not top(k):
            path.append(k)
            k = parent.get(k, ROOT)
        v = memo.get(k, True)
        for q in path:
            memo[q] = v
        return v

    return [p for p in procs if p.pid not in safe and hit(p.pid)]


def members_of(rows, ident, pgids, keep) -> list:
    """In a fresh table, the live processes of a group being stopped: at or below a member that is still the process it was
    (pid and start: a pid reused meanwhile is not ours), or adopted by launchd out of a member's process group, even while that
    group's leader lives on outside the group."""
    by = {p.pid: p for p in rows}
    tops = {pid for pid, start in ident.items() if pid in by and by[pid].start == start} | {-g for g in pgids}
    return [p for p in under(rows, tops, keep, pgids) if p.stat != SZOMB]


def pick(procs, keep, min_group: int = MIN_GROUP):
    """(top node, members) of the group to stop, or None. Candidates are the unprotected children of protected nodes; the
    biggest one, if it has min_group processes and SHARE of all, is walked down into a child while that child still holds
    90 % of the candidate: a pane's agent survives a runaway in one of its commands, and 90 % of the group always goes, a
    chain too.
    ponytail: a runaway spread flat over many groups under a protected node (each member its own process group) is not
    found; the biggest candidate is then a normal one, under SHARE, and the round is only reported. Grouping by command
    line would find it, if one ever comes."""
    parent, kids = tree(procs)
    size = {}
    order, stack = [], [ROOT]
    while stack:
        k = stack.pop()
        order.append(k)
        stack.extend(kids.get(k, ()))
    for k in reversed(order):
        size[k] = (k > 0) + sum(size[c] for c in kids.get(k, ()))
    safe = guard(parent, keep)
    cands = [k for k, v in parent.items() if k not in safe and v in safe]
    if not cands:
        return None
    node = max(cands, key=size.__getitem__)
    if size[node] < max(min_group, SHARE * len(procs)):
        return None
    floor = 0.9 * size[node]
    while kids.get(node):
        big = max(kids[node], key=size.__getitem__)
        if size[big] < floor:
            break
        node = big
    return node, under(procs, {node}, keep)


def _signal(rows, sig) -> None:
    for p in rows:
        if p.pid > 1 and p.stat != SZOMB:
            try:
                os.kill(p.pid, sig)
            except OSError:
                pass  # gone already, or not ours any more


def stop_group(members, keep_for, wait: float = 3.0) -> list:
    """STOP the group pick() found, and what it makes meanwhile, until no new process appears (5 rounds); TERM and CONT them;
    KILL what is still there after `wait` s. Each round reads a fresh table (members_of), so a pid is signalled only as the
    process it is now; keep_for(rows) gives the protected pids for that table. Returns the processes it stopped."""
    ident, pgids, stopped = {}, set(), {}

    def add(rows):
        for p in rows:
            ident[p.pid] = p.start
            pgids.add(p.pgid)

    def fresh():
        rows = list_procs()
        return members_of(rows, ident, pgids, keep_for(rows))

    add(members)
    for _ in range(5):
        new = [p for p in fresh() if (p.pid, p.start) not in stopped]
        if not new:
            break
        _signal(new, signal.SIGSTOP)
        for p in new:
            stopped[(p.pid, p.start)] = p
        add(new)
    now = fresh()  # only the processes they still are: one ended meanwhile may have left its pid to another
    for p in now:
        stopped.setdefault((p.pid, p.start), p)
    add(now)
    group = list(stopped.values())
    _signal(now, signal.SIGTERM)
    _signal(now, signal.SIGCONT)
    end = time.time() + wait
    while time.time() < end and fresh():
        time.sleep(0.1)
    for _ in range(5):
        left = fresh()
        if not left:
            break
        add(left)
        _signal(left, signal.SIGKILL)
        time.sleep(0.1)
    return group


def main_shell(env):
    """pid of the main pane's shell; 0 when there is no main pane; None when herdr could not tell (not up yet, a call that
    timed out or could not fork). Runs herdr a few times, so the watch asks only when it has to."""
    term = herdr_peers.main_terminal(env)
    if not term:
        return 0
    where = herdr_peers.all_panes(env)
    if where is None:
        return None
    if term not in where:
        return 0  # the main pane closed and left its file
    address, pane = where[term]
    server = address.split("/")[0] if "/" in address else "default"
    sock = herdr_peers.socket_of(env, server) or ""
    info = herdr_peers.herdr(env, "pane", "process-info", "--pane", pane.get("pane_id") or "", socket=sock)
    return int((info.get("process_info") or {}).get("shell_pid") or 0) or None


def fg_of(by, shell: int, start) -> int:
    """What runs in the front of that shell's terminal (the agent, or the shell itself at a prompt), 0 when the shell is gone.
    Read from the kernel table each round, so an agent started or restarted in the pane is protected at once; protecting the
    group leader protects the agent above it too, when the agent has handed the terminal to a child."""
    sh = by.get(shell)
    if not sh or sh.start != start:
        return 0
    return sh.tpgid if sh.tpgid in by else shell


class Main:
    """The main pane's shell as (pid, start), asked again when the file changes, every minute while herdr could not tell,
    and every 5 minutes while the shell it knew is gone or the file names no pane."""

    def __init__(self, ask=None):
        self.ask = ask or main_shell
        self.mtime = self.start = None
        self.asked = 0.0
        self.pid = 0
        self.missed = 0.0  # when a round last found no group to stop: that is said once in 10 minutes

    def get(self, env, by, now):
        """The pid to protect in the main pane; 0 when there is none; None when herdr could not say (keepers() then keeps
        the front of every pane)."""
        try:
            mtime = os.stat(os.path.join(herdr_peers.state_dir(env), "main")).st_mtime
        except OSError:
            mtime = None
        again = 60 if self.pid is None else 300
        if mtime != self.mtime or (mtime is not None and not fg_of(by, self.pid, self.start) and now - self.asked >= again):
            self.mtime, self.asked = mtime, now
            self.pid = self.ask(env) if mtime is not None else 0
            if self.pid and self.pid not in by:  # it may have started after the round's table was read
                by = {p.pid: p for p in list_procs()}
            if self.pid and self.pid not in by:
                self.pid = None  # not known: keep every pane's front, ask again in a minute
            self.start = by[self.pid].start if self.pid else None
        return None if self.pid is None else fg_of(by, self.pid, self.start)


def state_file(env, name) -> str:
    d = os.path.join(env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state"), "fork-watch")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def run(args, env) -> bool:
    try:
        return subprocess.run(args, env={**os.environ, **env}, timeout=15, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tell_main(env, msg) -> bool:
    term = herdr_peers.main_terminal(env)
    where = (herdr_peers.all_panes(env) or {}) if term else {}
    if term not in where:
        return False
    return run([env.get("FORK_WATCH_PANE_TELL") or os.path.join(HERE, "pane-tell"), where[term][0], msg], env)


def notify(env, title, body) -> bool:
    fake = env.get("FORK_WATCH_NOTIFY")
    return run([fake, title, body] if fake else
               ["/usr/bin/osascript", "-e", "on run a", "-e", "display notification (item 2 of a) with title (item 1 of a)",
                "-e", "end run", title, body], env)


def tick(env, main: Main, now: float, limit: int):
    """One round. Returns what it logged, or None when the count is under the limit."""
    procs = list_procs()
    stamp = time.strftime("%F %T", time.localtime(now))
    with open(state_file(env, "heartbeat"), "w") as f:  # every round: an old time here means the watch stopped
        f.write(f"{stamp} 사용자 프로세스 {len(procs):,}개, 기준 {limit:,}개\n")
    by = {p.pid: p for p in procs}
    main_pid = main.get(env, by, now)
    if not over(len(procs), limit):
        return None
    me = os.getpid()
    keep_for = lambda rows: keepers(rows, argv_of, main_pid, me)
    got = pick(procs, keep_for(procs))
    if not got:
        least = max(MIN_GROUP, int(SHARE * len(procs)))
        line = (f"{stamp} 못 찾음 | 사용자 프로세스 {len(procs):,}개 > {limit:,} | {least:,}개(전체의 {SHARE:.0%}) 이상인 묶음이 "
                f"없어 아무것도 끄지 않음")
        if now - main.missed >= 600:
            main.missed = now
            with open(state_file(env, "kills.log"), "a") as f:
                f.write(line + "\n")
            tell_main(env, f"fork-watch: 사용자 프로세스가 {len(procs):,}개로 기준 {limit:,}개를 넘었지만, 전체의 {SHARE:.0%} "
                           f"이상을 차지하는 묶음이 없어 아무것도 끄지 않았습니다. 여러 묶음에 흩어진 폭주일 수 있으니 직접 확인해야 "
                           f"합니다. 기록: {state_file(env, 'kills.log')}")
            notify(env, "프로세스가 너무 많습니다", f"사용자 프로세스 {len(procs):,}개, 끌 묶음을 찾지 못했습니다")
        return line
    top, members = got
    head = by.get(top) or min(members, key=lambda p: p.start)  # a group whose leader is gone: its oldest member
    cmd = " ".join(argv_of(head.pid)) or head.comm
    what = f"pid {top}" if top > 0 else f"프로세스 그룹 {-top}(맨 위는 이미 끝남, 가장 오래된 pid {head.pid})"
    gone = stop_group(members, keep_for)
    line = f"{stamp} 끔 {len(gone):,}개 | 사용자 프로세스 {len(procs):,}개 > {limit:,} | 맨 위 {what}: {cmd[:500]}"
    with open(state_file(env, "kills.log"), "a") as f:
        f.write(line + "\n")
    msg = (f"fork-watch: 사용자 프로세스가 {len(procs):,}개로 기준 {limit:,}개를 넘어서, 가장 크게 불어난 묶음 {len(gone):,}개를 "
           f"자손까지 껐습니다. 맨 위 {what}: `{cmd[:200]}`. 기록: {state_file(env, 'kills.log')}")
    tell_main(env, msg)
    notify(env, "프로세스 폭주를 껐습니다", f"{len(gone):,}개, 맨 위: {cmd[:120]}")
    return line


def selftest() -> None:
    def P(pid, ppid, pgid=None, comm="x", start=0, tpgid=0):
        return Proc(pid, ppid, pgid if pgid is not None else pid, start or pid, 2, comm, tpgid)

    def fan(root, pgid, first, kids, leaves):
        """root's children `kids`, each with `leaves` children; pids from `first`."""
        out, n = [], first
        for _ in range(kids):
            k = n; n += 1
            out.append(P(k, root, pgid))
            for _ in range(leaves):
                out.append(P(n, k, pgid)); n += 1
        return out

    # over(): more than the limit, not equal to it
    assert over(4001, 4000) and not over(4000, 4000) and not over(10, 4000)

    # keepers(): herdr servers by their argv, the main agent, this watch
    argv = {100: ["/opt/homebrew/opt/herdr/bin/herdr", "server"],
            101: ["/opt/homebrew/opt/herdr/bin/herdr", "--session", "workers", "server"],
            102: ["herdr", "session", "attach", "workers"], 103: ["herdr"], 104: ["/bin/zsh", "-c", "herdr server"]}
    procs = [P(p, 1, comm=os.path.basename(a[0])) for p, a in argv.items()] + [P(111, 110), P(300, 1)]
    assert keepers(procs, lambda p: argv.get(p, []), 111, 300) == {100, 101, 111, 300}, keepers(procs, lambda p: argv.get(p, []), 111, 300)
    # the main pane could not be looked up (None, Codex review): the front of every herdr pane's shell is kept meanwhile
    panes = procs + [P(110, 100, tpgid=111), P(120, 101, tpgid=121), P(121, 120), P(130, 101, tpgid=130), P(140, 104, tpgid=141), P(141, 140)]
    assert keepers(panes, lambda p: argv.get(p, []), None, 300) == {100, 101, 111, 121, 130, 300}, keepers(panes, lambda p: argv.get(p, []), None, 300)
    assert 121 not in keepers(panes, lambda p: argv.get(p, []), 111, 300), "a known main pane keeps only its own agent"

    # a desk: two herdr servers, the main pane (zsh 110 > claude 111 > 2 MCP + a tool shell), a worker pane
    # (zsh 120 > claude 121 > 2 MCP + tool shell 122, its own session), an app with helpers, this watch
    base = [P(100, 1), P(101, 1),
            P(110, 100), P(111, 110), P(112, 111, 111), P(113, 111, 111), P(114, 111), P(115, 114, 114),
            P(120, 101), P(121, 120), P(124, 121, 121), P(125, 121, 121), P(122, 121),
            P(400, 1)] + [P(401 + i, 400, 400) for i in range(20)] + [P(300, 1)]
    keep = {100, 101, 111, 300}

    # a runaway in a worker pane's command: the bomb root 123 and all below it go; its tool shell, agent and pane stay
    bomb = [P(123, 122, 122)] + fan(123, 122, 1000, 3, 30)
    got = pick(base + bomb, keep)
    assert got and got[0] == 123, got and got[0]
    assert sorted(p.pid for p in got[1]) == sorted(p.pid for p in bomb), "every member, not the top alone"

    # its tool shell and the bomb root killed (TaskStop): the orphans, adopted by launchd, are still one group (-122)
    orphans = [P(p.pid, 1 if p.ppid == 123 else p.ppid, 122) for p in bomb if p.pid != 123]
    no_shell = [p for p in base if p.pid != 122]
    got = pick(no_shell + orphans, keep)
    assert got and got[0] == -122, got and got[0]
    assert sorted(p.pid for p in got[1]) == sorted(p.pid for p in orphans)

    # a runaway under the main pane's agent: the agent and its pane stay
    main_bomb = [P(130, 114, 114)] + fan(130, 114, 2000, 3, 30)
    got = pick(base + main_bomb, keep)
    assert got and got[0] == 130 and not {110, 111, 114} & {p.pid for p in got[1]}, got and got[0]

    # beside this watch (the live test runs it from the bomb's own shell): the shells above the watch stay
    beside = [P(299, 121), P(301, 299)] + fan(301, 299, 3000, 3, 30) + [P(302, 299)]
    got = pick([p for p in base if p.pid != 300] + beside, {100, 101, 111, 302})
    assert got and got[0] == 301, got and got[0]

    # an app with a runaway inside it of 90 % or more: the walk goes down to the runaway, the app stays
    app = [P(500, 1)] + [P(501 + i, 500, 500) for i in range(40)] + [P(600, 500, 500)] + fan(600, 500, 4000, 2, 199)
    got = pick(base + app, keep)
    assert got and got[0] == 600, got and got[0]

    # nothing big enough to be a runaway: no pick
    assert pick(base, keep) is None

    # a candidate over min_group whose biggest child is under it (Codex review): the candidate goes, not nothing
    mid = [P(800, 1)] + [P(801 + i, 800, 800) for i in range(30)] + [P(850, 800, 800)] + [P(851 + i, 850, 800) for i in range(48)]
    got = pick(base + mid, keep)
    assert got and got[0] == 800 and len(got[1]) == 80, got and (got[0], len(got[1]))

    # a chain, each process the only child of the one before (Codex review): at least 90 % of it goes, not the last 50
    chain = [P(900, 1)] + [P(901 + i, 900 + i, 900) for i in range(4100)]
    got = pick(base + chain, keep)
    assert got and len(got[1]) >= 0.9 * 4101, got and (got[0], len(got[1]))

    # the main pane's agent is its shell's terminal foreground group, read each round: one started after the shell was
    # found is protected, one at a prompt protects the shell, a shell gone protects nothing
    table = {p.pid: p for p in [P(110, 100, tpgid=111), P(111, 110, tpgid=111)]}
    assert fg_of(table, 110, 110) == 111 and fg_of(table, 110, 999) == 0 and fg_of(table, 77, 77) == 0
    assert fg_of({110: P(110, 100, tpgid=110)}, 110, 110) == 110 and fg_of({110: P(110, 100, tpgid=4242)}, 110, 110) == 110
    # Main.get(): a shell herdr names but no table shows (Codex review: it appeared between the two) is not "no main pane":
    # it counts as not known (None), so keepers() keeps every pane's front; a shell herdr cannot name (None) the same
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "orchestrator"))
        with open(os.path.join(d, "orchestrator", "main"), "w") as f:
            f.write("term_x\n")
        e = {"XDG_STATE_HOME": d}
        assert Main(ask=lambda env: 1).get(e, {}, 0.0) is None, "pid 1 is in no table of ours"
        assert Main(ask=lambda env: None).get(e, {}, 0.0) is None
        assert Main(ask=lambda env: 110).get(e, {110: P(110, 100, tpgid=111), 111: P(111, 110)}, 0.0) == 111
        assert Main(ask=lambda env: 0).get(e, {}, 0.0) == 0, "no main pane"

    # every killable group under a protected node is weighed, not just the biggest protected one: the orphan group
    # (-122, 93) beats the protected main pane's whole tree (106), whose own children are 1 each
    crowd = [P(140 + i, 111, 111) for i in range(100)]
    got = pick(no_shell + crowd + orphans, keep)
    assert got and got[0] == -122, got and got[0]

    # a process group loop (each one's leader is the other, both parents gone) ends, does not hang
    loop = [P(700, 1, 701), P(701, 1, 700)] + [P(702 + i, 700, 701) for i in range(60)]
    got = pick(base + loop, keep)
    assert got and 700 in {p.pid for p in got[1]}, got

    # a runaway spread flat (3,600 processes, each its own group adopted by launchd) beside three normal panes of 56
    # (zsh > claude > 54 tools): the biggest candidate is a normal pane, far under a quarter of all, so nothing goes
    # (check pane 2026-11-05: before, one such pane went each round, its agent too)
    def pane(shell, n):
        return [P(shell, 101), P(shell + 1, shell)] + [P(shell + 2 + i, shell + 1, shell + 1) for i in range(n - 2)]
    panes3 = pane(5000, 56) + pane(5100, 56) + pane(5200, 56)
    flat = [P(10000 + i, 1) for i in range(3600)]
    got = pick(base + panes3 + flat, keep)
    assert got is None, (got[0], len(got[1]))
    # the same desk with a runaway of 3,500 in one group: it still goes
    bomb35 = [P(123, 122, 122)] + fan(123, 122, 20000, 5, 699)
    got = pick(base + panes3 + bomb35, keep)
    assert got and got[0] == 123 and len(got[1]) == 3501, got and (got[0], len(got[1]))

    # the line is a quarter of all the user's processes: 1,000 of 4,000 goes, 999 does not
    def desk(n):
        group = [P(30000, 1)] + [P(30001 + i, 30000, 30000) for i in range(n - 1)]
        return base + group + [P(40000 + i, 1) for i in range(4000 - len(base) - n)]
    got = pick(desk(1000), keep)
    assert got and got[0] == 30000, got and got[0]
    assert pick(desk(999), keep) is None

    # members_of(), the fresh table while a group is stopped (Codex review): a pid counts only as the process it was
    # (pid and start); a process adopted by launchd out of a member's process group counts even when that group's leader
    # is alive and not a member; a protected one never
    shell, member = P(122, 121), P(123, 122, 122, start=5)
    assert [p.pid for p in members_of([P(121, 1), shell, member], {123: 5}, {122}, set())] == [123]
    assert members_of([P(121, 1), shell, P(123, 122, 122, start=6)], {123: 5}, {122}, set()) == [], "a reused pid is not ours"
    adopted = P(131, 1, 122)
    assert sorted(p.pid for p in members_of([P(121, 1), shell, member, adopted, P(132, 131, 122)], {123: 5}, {122}, set())) == [123, 131, 132]
    assert [p.pid for p in members_of([P(121, 1), shell, member, adopted], {123: 5}, {122}, {131})] == [123]
    # the group's own leader, a launchd child above what pick() chose, is no adopted child: it stays
    lead, low = P(140, 1), P(141, 140, 140, start=7)
    assert sorted(p.pid for p in members_of([lead, low, P(142, 1, 140)], {141: 7}, {140}, set())) == [141, 142]

    # tick() over the limit on the flat runaway: nothing stopped, one log line, the main pane told, a notification; every
    # round, a quiet one too, overwrites the heartbeat file, so a watch that stopped shows (check pane 2026-11-05).
    # The table, herdr and stop_group are fakes here: a regression stops nobody real.
    g = globals()
    saved = g["list_procs"], g["stop_group"], herdr_peers.all_panes
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "orchestrator"))
        with open(os.path.join(d, "orchestrator", "main"), "w") as f:
            f.write("term_m\n")
        for name in ("pt", "nt"):
            with open(os.path.join(d, name), "w") as f:
                f.write(f'#!/bin/sh\nprintf "%s|%s\\n" "$1" "$2" >> {d}/{name}.out\n')
            os.chmod(os.path.join(d, name), 0o755)
        e = {"XDG_STATE_HOME": d, "FORK_WATCH_PANE_TELL": os.path.join(d, "pt"), "FORK_WATCH_NOTIFY": os.path.join(d, "nt")}
        read = lambda path: open(path).read() if os.path.exists(path) else ""
        stamp = lambda t: time.strftime("%F %T", time.localtime(t))
        table, stopped = base + panes3 + flat, []
        try:
            g["list_procs"] = lambda: table
            g["stop_group"] = lambda members, keep_for: stopped.extend(members) or members
            herdr_peers.all_panes = lambda env: {"term_m": ("default/w1:p1", {})}
            line = tick(e, Main(ask=lambda env: 0), 1000.0, 3000)
            assert line and "못 찾음" in line and not stopped, (line, len(stopped))
            log = read(state_file(e, "kills.log")).splitlines()
            assert len(log) == 1 and "못 찾음" in log[0], log
            told = read(os.path.join(d, "pt.out")).splitlines()
            assert len(told) == 1 and told[0].startswith("default/w1:p1|fork-watch: "), told
            assert read(os.path.join(d, "nt.out")).count("\n") == 1
            beat = read(state_file(e, "heartbeat"))
            assert beat.startswith(stamp(1000.0)) and beat.count("\n") == 1, beat
            table = base
            assert tick(e, Main(ask=lambda env: 0), 2000.0, 3000) is None
            beat = read(state_file(e, "heartbeat"))
            assert beat.startswith(stamp(2000.0)) and beat.count("\n") == 1, beat
        finally:
            g["list_procs"], g["stop_group"], herdr_peers.all_panes = saved

    # the kernel table: every row matches ps, and this process is in it
    rows = list_procs()
    mine = {p.pid: p for p in rows}
    assert os.getpid() in mine and mine[os.getpid()].ppid == os.getppid()
    out = subprocess.run(["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,ppid=,pgid=,tpgid="], capture_output=True, text=True).stdout
    seen = 0
    for line in out.splitlines():
        pid, ppid, pgid, tpgid = map(int, line.split())
        if pid in mine:
            seen += 1
            assert (mine[pid].ppid, mine[pid].pgid, mine[pid].tpgid) == (ppid, pgid, tpgid), (pid, mine[pid], line)
    assert seen > 10, seen
    assert argv_of(os.getpid())[-1] == "--selftest", argv_of(os.getpid())

    # stop_group() on real processes: a shell that ignores TERM, in its own session, and its two children all end
    sh = subprocess.Popen(["/bin/sh", "-c", "trap '' TERM; sleep 41 & sleep 42 & wait; wait"], start_new_session=True)
    kids = []
    for _ in range(50):
        kids = [p for p in list_procs() if p.ppid == sh.pid]
        if len(kids) == 2:
            break
        time.sleep(0.05)
    assert len(kids) == 2, kids
    gone = stop_group([p for p in list_procs() if p.pid == sh.pid], lambda rows: {os.getpid()}, wait=1.0)
    assert sorted(p.pid for p in gone) == sorted([sh.pid] + [k.pid for k in kids]), gone
    sh.wait(timeout=5)
    left = {p.pid for p in list_procs()} & {k.pid for k in kids}
    assert not left, left
    print("fork-watch selftest: ok")


if __name__ == "__main__":
    env = dict(os.environ)
    limit = int(env.get("FORK_WATCH_LIMIT") or LIMIT)
    arg = sys.argv[1:]
    if arg == ["--selftest"]:
        selftest()
    elif arg == ["--status"]:
        rows = list_procs()
        main = Main()
        main_pid = main.get(env, {p.pid: p for p in rows}, time.time())
        herdrs = sorted(keepers(rows, argv_of, 0, 0))
        front = "herdr가 답하지 않아 모든 칸의 앞 프로세스" if main_pid is None else f"pid {main_pid or '없음'}"
        print(f"사용자 프로세스 {len(rows):,}개, 기준 {limit:,}개, 메인 칸 셸 pid {main.pid or '없음'}, "
              f"그 앞에서 도는 것(보호) {front}, herdr 서버 {herdrs}")
    elif arg in ([], ["--once"]):
        main = Main()
        while True:
            try:
                line = tick(env, main, time.time(), limit)
                if line and arg:
                    print(line)
            except Exception:  # one bad round must not end the watch; launchd would restart it, 10 s later
                traceback.print_exc()
            if arg:
                break
            time.sleep(float(env.get("FORK_WATCH_INTERVAL") or INTERVAL))
    else:
        sys.exit(__doc__)
