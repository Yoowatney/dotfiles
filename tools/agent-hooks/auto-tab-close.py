#!/usr/bin/env python3
"""Stop hook (Claude Code and Codex): a pane an agent opened closes itself when its report says nothing is left there.

The global rule asks a dispatch tab (MUNICE_AUTO_DISPATCH=1) or a Slack ambient tab (MUNICE_AMBIENT=1) to end with
`세션: 닫음` and `pane-label --close` as its last tool call. Sessions kept writing `세션: 닫아도 됨` and stopping, so 요유
closed the tabs by hand (요유 2026-10-28: "자동탭에서 할거없으면 좀 알아서 닫으라니까 죽어도 안닫네 ... 로직을
개선하라고"). This hook reads the report instead of trusting the session to remember: `세션: 닫음`, or `세션: 닫아도 됨`
with `요유 몫: 없음`, runs `pane-label --close`. That command keeps its own guards: only job tabs, never a 🔴 pane, and it
waits for the turn to end and cancels if the agent works again within 20 seconds (요유 typed, a goal judge sent it back).
A `요유 몫` with something in it keeps the tab open even at `닫아도 됨`: 요유 may need to type in it (`결정 필요`, `승격`).
A pane `herdr agent start` opened closes the same way, whichever session started it and whether or not that session still
lives: herdr's agent name tells it from a session 요유 started by hand, which has none (요유 2026-10-31). A closing pane
takes its report with it, so the whole reply is first appended to a day file under ~/.local/state/pane-reports/, and the
closing lines go to the pane that opened it, else a 👑 it can be sure of. A reply that cannot be saved keeps the pane open; a
👑 that cannot hear does not, and job tabs tell no 👑 (their reports go to the board and Slack). A 🔴 pane stays, and so does
any pane while a pane it opened is alive; one whose panes are gone closes like the rest. A report that keeps the pane open
(요유 몫 left, `세션: 닫으면 …`, held as above) goes up the same way and the pane stays: an implementer finished with two
questions for 요유 and its lead never heard (요유 2026-11-01). A reply without `남은 것:` is no report and tells nobody.
A pane moved to another workspace gets a new id, and only `herdr pane get` still knows the old one from its env, so the
current pane comes from there, and pane-spawn records its caller by terminal id, which a move does not change.
A Stop hook runs beside the other Stop hooks, and any of them can send the reply back to be rewritten, so a Stop is not the
end of a turn: a draft went to the lead, then its rewrite (2026-10-31, share-guard asked for pane names). The hook only leaves
the reply in a per-pane file and detaches. That step waits until the agent has stopped working, and each Stop of the pane
replaces or withdraws the file, so the last reply of the turn is the only one acted on.
Never blocks; prints nothing. Fails open.

--sweep lists the panes that already finished, which no Stop hook will visit again: named, idle, not held as above, and a
last reply that passes the same test. --sweep --apply closes them the same way.

    auto-tab-close.py --selftest
    auto-tab-close.py --sweep [--apply]
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import datetime
import glob
import importlib.util
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

SESSION = re.compile(r"세션[ \t]*\**[ \t]*[:：][ \t]*\**([^·\n]*)")
REPORT = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\**(?:무엇을|왜|남은[ \t]*것)[ \t]*\**[ \t]*[:：]", re.M)
LEFT = re.compile(r"남은[ \t]*것[ \t]*\**[ \t]*[:：]")
OPENER = re.compile(r"^spawn-([0-9A-Za-z]+)-")  # pane-spawn's agent name: spawn-<caller's terminal>-<random>


def should_close(text: str) -> bool:
    """The last `세션:` value says 닫음, or 닫아도 됨 with nothing in 요유 몫. Code and quotes are not a report."""
    text = unicodedata.normalize("NFC", text)
    for pat in share.STRIP:
        text = pat.sub(" ", text)
    values = [m.group(1).strip(" \t*_.,;") for m in SESSION.finditer(text)]
    if not values:
        return False
    if values[-1].startswith("닫음"):
        return True
    return values[-1].startswith("닫아도 됨") and not share.leaves_yoyoo_work(text)


def herdr(env, *args) -> dict:
    r = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", *args], capture_output=True, text=True, timeout=2)
    return (json.loads(r.stdout).get("result") or {}) if r.returncode == 0 else {}


def here(env) -> dict:
    """This pane as herdr has it now. HERDR_PANE_ID goes stale when the pane moves; `pane get` alone still resolves it."""
    pane = env.get("HERDR_PANE_ID")
    return (herdr(env, "pane", "get", pane).get("pane") or {}) if pane else {}


def agent_name(env) -> str:
    """`herdr agent start` names the agent; a session started by hand, even in that pane afterwards, has no name."""
    pane = here(env).get("pane_id") or env.get("HERDR_PANE_ID")
    return ((herdr(env, "agent", "get", pane).get("agent") or {}).get("name") or "") if pane else ""


def opener(name) -> str:
    """The terminal of the pane that opened this agent, from pane-spawn's name."""
    m = OPENER.match(name or "")
    return f"term_{m[1]}" if m else ""


def held(label: str, terminal, agents: list) -> bool:
    """🔴 stops something; a pane waits for the panes it opened. 요유's own leads never get here: they have no agent name."""
    return "🔴" in label or any(opener(a.get("name")) == terminal for a in agents)


def report(text: str) -> str:
    """The closing lines of a reply: from the first 무엇을, 왜 or 남은 것 label to the end, else all of it."""
    text = unicodedata.normalize("NFC", text)
    m = REPORT.search(text)
    return text[m.start() if m else 0:].strip()


def reply_of(payload: dict):
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = share.final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    return text


def ended_in_silence(payload: dict) -> bool:
    """yoyoo-share-guard tells a session with nothing to do to end without a word. That Stop has no reply of its own, so the
    reply held from the Stop before is the report, and withdrawing it would leave the pane open with no report saved."""
    return bool(payload.get("stop_hook_active")) and not (reply_of(payload) or "").strip()


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


def nearest_lead(me: dict, panes: list, name: str = "", sideways: bool = True) -> str:
    """The pane that opened this one, else the 👑 in this tab, else the only 👑 in this workspace. With several, none is
    known to be ours, and a report in the wrong session reads as a task: the saved copy is enough. Another workspace is
    another life (company, home). Without `sideways` only the opener counts: two open 👑 would answer each other for ever."""
    others = [p for p in panes if p.get("pane_id") != me.get("pane_id")]
    parent = opener(name)
    for p in others:
        if parent and p.get("terminal_id") == parent:
            return p["pane_id"]
    if not sideways:
        return ""
    leads = [p for p in others if "👑" in (p.get("label") or "")]
    tab = [p for p in leads if p.get("tab_id") == me.get("tab_id")]
    if tab:
        return tab[0]["pane_id"]
    workspace = [p for p in leads if p.get("workspace_id") == me.get("workspace_id")]
    return workspace[0]["pane_id"] if len(workspace) == 1 else ""


def save(reply: str, me: dict, pane: str, env) -> str:
    """Outside any session and repo: reports can name customers, and the dotfiles are public."""
    folder = env.get("PANE_REPORT_DIR") or os.path.expanduser("~/.local/state/pane-reports")
    os.makedirs(folder, exist_ok=True)
    now = datetime.datetime.now()
    session = (me.get("agent_session") or {}).get("value", "")
    path = os.path.join(folder, f"{now:%Y-%m-%d}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## {now:%H:%M:%S} {pane} {me.get('label') or ''}\n\n"
                f"세션 {session}, 폴더 {me.get('cwd') or ''}\n\n{reply.strip()}\n")
    return path


def finish(reply: str, env):
    """Keep the report, tell the pane above, then close unless the report or the pane says to stay. Only a failed save stops
    the close (it raises)."""
    me = here(env)
    pane = me.get("pane_id") or env.get("HERDR_PANE_ID", "")
    env = {**env, "HERDR_PANE_ID": pane}
    panes = herdr(env, "pane", "list").get("panes") or []
    agents = herdr(env, "agent", "list").get("agents") or []
    close = should_close(reply) and not held(me.get("label") or "", me.get("terminal_id"), agents)
    name = next((a.get("name") for a in agents if a.get("pane_id") == pane), "")
    path = save(reply, me, pane, env)
    quiet: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    sideways = close or "👑" not in (me.get("label") or "")
    lead = "" if env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT") else nearest_lead(me, panes, name, sideways)
    if lead:
        pt = env.get("AUTO_TAB_CLOSE_PANE_TELL") or os.path.join(HERE, "pane-tell")
        head = "이 칸의 마지막 보고입니다(칸을 닫기 전에 보냅니다)" if close else "이 칸의 보고입니다(칸은 열어 둡니다)"
        try:
            subprocess.run([pt, lead, f"{head}:\n{report(reply)}\n\n전체 보고: {path}"], env=dict(env), timeout=5, **quiet)
        except subprocess.TimeoutExpired:
            pass
    if close:
        pl = env.get("AUTO_TAB_CLOSE_PANE_LABEL") or os.path.join(HERE, "pane-label")
        subprocess.run([pl, "--close"], env={**env, "PANE_CLOSE_REPORTED": "1"}, timeout=3, **quiet)


def pending_file(env) -> str:
    folder = env.get("PANE_REPORT_DIR") or os.path.expanduser("~/.local/state/pane-reports")
    return os.path.join(folder, ".pending-" + env["HERDR_PANE_ID"])


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
    panes = {p.get("pane_id"): p for p in herdr(env, "pane", "list").get("panes") or []}
    agents = herdr(env, "agent", "list").get("agents") or []
    me = here(env).get("pane_id") or env.get("HERDR_PANE_ID")
    for a in agents:
        pane, session = a.get("pane_id"), (a.get("agent_session") or {}).get("value")
        label = panes.get(pane, {}).get("label") or ""
        if (not a.get("name") or not session or a.get("agent_status") not in ("idle", "done") or pane == me
                or held(label, panes.get(pane, {}).get("terminal_id"), agents)):
            continue
        try:
            reply = last_reply(session)
            if not should_close(reply):
                continue
            print(f"{pane}\t{a['name']}\t{label}")
            if apply:
                finish(reply, {**env, "HERDR_PANE_ID": pane})
        except Exception as e:  # one unreadable pane must not stop the others
            print(f"{pane}\t건너뜀: {e}", file=sys.stderr)


def selftest():
    import shutil
    import tempfile

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
    assert ended_in_silence({"stop_hook_active": True, "last_assistant_message": " \n"}), "the reply held before it is the report"
    assert not ended_in_silence({"stop_hook_active": True, "last_assistant_message": done}), "a rewrite replaces it"
    assert not ended_in_silence({"last_assistant_message": ""}), "a first stop with no reply holds nothing"
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

    def herdr_says(agent=None, panes=(), agents=()):
        for name, result in (("agent", {"agent": agent}), ("panes", {"panes": list(panes)}), ("agents", {"agents": list(agents)})):
            with open(f"{sandbox}/{name}.json", "w") as f:
                json.dump({"result": result}, f)

    fake_herdr = script("herdr", f'''case "$1 $2" in
  "agent get") [ -f {sandbox}/agent-gone ] || [ "$3" = "$(cat {sandbox}/old-id 2>/dev/null)" ] && exit 1; cat {sandbox}/agent.json ;;
  "pane get") [ -f {sandbox}/herdr-bad ] && {{ echo "not json"; exit 0; }}; jq --arg id "$3" --arg new "$(cat {sandbox}/new-id 2>/dev/null)" \
    '{{result: {{pane: ([.result.panes[] | select(.pane_id == $id or .pane_id == $new)][0])}}}}' {sandbox}/panes.json ;;
  "agent list") cat {sandbox}/agents.json ;;
  "pane list") cat {sandbox}/panes.json ;;
  *) exit 2 ;;
esac''')
    fake = script("pane-label", f'echo "reported=$PANE_CLOSE_REPORTED pane=$HERDR_PANE_ID $@" >> {sandbox}/calls')
    fake_tell = script("pane-tell", f'echo "tell $1" >> {sandbox}/calls\necho "$2" > {sandbox}/told\nsleep ${{TELL_SLEEP:-0}}\nexit ${{TELL_EXIT:-0}}')

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
    assert report(full) == full[full.index("무엇을"):], "the report starts at 무엇을"
    assert report("끝났습니다.\n\n**남은 것:** 요유 몫: 없음") == "**남은 것:** 요유 몫: 없음", "남은 것 alone is a report"
    assert report("남은 것이 없습니다.") == "남은 것이 없습니다.", "without labels the whole text goes"
    assert report(unicodedata.normalize("NFD", "끝.\n왜: 칸.")) == "왜: 칸.", "NFD text from a macOS paste"

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
            if not (os.path.isdir(reports) and any(n.startswith(".pending") for n in os.listdir(reports))):
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
        text = "".join(open(os.path.join(reports, n)).read() for n in os.listdir(reports)) if os.path.isdir(reports) else ""
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
    assert full[full.index("무엇을"):] in told and reports in told, f"the report and its file must reach the lead: {told!r}"
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
        if not ended_in_silence(payload):
            defer(decide(payload, os.environ), os.environ)
    except Exception:  # never break a session
        pass
    sys.exit(0)
