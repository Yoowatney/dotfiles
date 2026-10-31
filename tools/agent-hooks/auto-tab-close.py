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
closing lines go to the nearest 👑 pane if one is alive. A reply that cannot be saved keeps the pane open; a 👑 that cannot
hear does not, and job tabs tell no 👑 (their reports go to the board and Slack). 🔴 and 👑 panes stay and tell nobody.
Never blocks; prints nothing. Fails open.

--sweep lists the panes that already finished, which no Stop hook will visit again: named, idle, neither 🔴 nor 👑, and a
last reply that passes the same test. --sweep --apply closes them the same way.

    auto-tab-close.py --selftest
    auto-tab-close.py --sweep [--apply]
"""
import datetime
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every stop
HERE = os.path.dirname(os.path.realpath(__file__))
_spec = importlib.util.spec_from_file_location("share", os.path.join(HERE, "yoyoo-share-guard.py"))
share = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(share)  # reuse its 요유 몫 reader, code and quote stripping, and transcript fallback

SESSION = re.compile(r"세션[ \t]*\**[ \t]*[:：][ \t]*\**([^·\n]*)")
REPORT = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\**(?:무엇을|왜|남은[ \t]*것)[ \t]*\**[ \t]*[:：]", re.M)


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


def agent_name(env) -> str:
    """`herdr agent start` names the agent; a session started by hand, even in that pane afterwards, has no name."""
    pane = env.get("HERDR_PANE_ID")
    return ((herdr(env, "agent", "get", pane).get("agent") or {}).get("name") or "") if pane else ""


def report(text: str) -> str:
    """The closing lines of a reply: from the first 무엇을, 왜 or 남은 것 label to the end, else all of it."""
    text = unicodedata.normalize("NFC", text)
    m = REPORT.search(text)
    return text[m.start() if m else 0:].strip()


def decide(payload: dict, env) -> str | None:
    """The reply to close on, or None. The nested claude -p inherits this pane's env but is not its session."""
    if env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = share.final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    if not (isinstance(text, str) and should_close(text)):
        return None
    return text if env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT") or agent_name(env) else None


def nearest_lead(me: dict, panes: list) -> str:
    """The 👑 pane in this tab, else in this workspace. Another workspace is another life (company, home)."""
    leads = [p for p in panes if "👑" in (p.get("label") or "") and p.get("pane_id") != me.get("pane_id")]
    for scope in ("tab_id", "workspace_id"):
        for p in leads:
            if p.get(scope) == me.get(scope):
                return p["pane_id"]
    return ""


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
    """Keep the report, tell the 👑, then close. Only a failed save stops the close (it raises)."""
    pane = env.get("HERDR_PANE_ID", "")
    panes = herdr(env, "pane", "list").get("panes") or []
    me = next((p for p in panes if p.get("pane_id") == pane), {})
    if any(mark in (me.get("label") or "") for mark in "🔴👑"):
        return
    path = save(reply, me, pane, env)
    quiet: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    lead = "" if env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT") else nearest_lead(me, panes)
    if lead:
        pt = env.get("AUTO_TAB_CLOSE_PANE_TELL") or os.path.join(HERE, "pane-tell")
        try:
            subprocess.run([pt, lead, f"이 칸의 마지막 보고입니다(칸을 닫기 전에 보냅니다):\n{report(reply)}\n\n전체 보고: {path}"],
                           env=dict(env), timeout=5, **quiet)
        except subprocess.TimeoutExpired:
            pass
    pl = env.get("AUTO_TAB_CLOSE_PANE_LABEL") or os.path.join(HERE, "pane-label")
    subprocess.run([pl, "--close"], env={**env, "PANE_CLOSE_REPORTED": "1"}, timeout=3, **quiet)


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
    labels = {p.get("pane_id"): p.get("label") or "" for p in herdr(env, "pane", "list").get("panes") or []}
    for a in herdr(env, "agent", "list").get("agents") or []:
        pane, session = a.get("pane_id"), (a.get("agent_session") or {}).get("value")
        label = labels.get(pane, "")
        if (not a.get("name") or not session or a.get("agent_status") not in ("idle", "done")
                or pane == env.get("HERDR_PANE_ID") or any(mark in label for mark in "🔴👑")):
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
  "agent get") [ -f {sandbox}/agent-gone ] && exit 1; cat {sandbox}/agent.json ;;
  "agent list") cat {sandbox}/agents.json ;;
  "pane list") cat {sandbox}/panes.json ;;
  *) exit 2 ;;
esac''')
    fake = script("pane-label", f'echo "reported=$PANE_CLOSE_REPORTED pane=$HERDR_PANE_ID $@" >> {sandbox}/calls')
    fake_tell = script("pane-tell", f'echo "tell $1" >> {sandbox}/calls\necho "$2" > {sandbox}/told\nexit ${{TELL_EXIT:-0}}')

    me = {"pane_id": "w1:p2", "tab_id": "w1:t1", "workspace_id": "w1", "label": "🟢 일", "cwd": "/w",
          "agent_session": {"value": "sid-2"}}
    spawned = {"HERDR_BIN_PATH": fake_herdr, "HERDR_PANE_ID": "w1:p2"}
    herdr_says({"name": "spawned"})
    assert decide({"last_assistant_message": done}, spawned), "a pane `herdr agent start` opened closes"
    assert not decide({"last_assistant_message": keep[0]}, spawned), "요유 몫 left keeps it"
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

    def run(payload, env, args=()):
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__), *args], input=payload, capture_output=True,
                           text=True, env={"PATH": os.environ.get("PATH", ""), "HOME": sandbox, "HERDR_BIN_PATH": fake_herdr,
                                           "PANE_REPORT_DIR": reports, "AUTO_TAB_CLOSE_PANE_LABEL": fake,
                                           "AUTO_TAB_CLOSE_PANE_TELL": fake_tell, **env},
                           timeout=10)
        calls = open(f"{sandbox}/calls").read() if os.path.exists(f"{sandbox}/calls") else ""
        if os.path.exists(f"{sandbox}/calls"):
            os.unlink(f"{sandbox}/calls")
        return r.returncode, r.stdout, calls

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

    crown = {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "label": "🟢 👑 리드"}
    other_tab = {**crown, "pane_id": "w1:p7", "tab_id": "w1:t2"}
    other_space = {**crown, "pane_id": "w2:p1", "tab_id": "w2:t1", "workspace_id": "w2"}
    herdr_says({"name": "spawned"}, [other_tab, crown, me])
    assert run(reported, pane2) == (0, "", "tell w1:p1\n" + closed), "the 👑 in this tab first"
    told = open(f"{sandbox}/told").read()
    assert full[full.index("무엇을"):] in told and reports in told, f"the report and its file must reach the lead: {told!r}"
    assert full in saved(), "kept even when a 👑 hears it"
    assert run(reported, {**pane2, "TELL_EXIT": "3"}) == (0, "", "tell w1:p1\n" + closed), "a 👑 that cannot hear holds nothing"
    herdr_says({"name": "spawned"}, [other_space, other_tab, me])
    assert run(reported, pane2) == (0, "", "tell w1:p7\n" + closed), "no 👑 in the tab: one in the workspace"
    herdr_says({"name": "spawned"}, [other_space, me])
    assert run(reported, pane2) == (0, "", closed), "a 👑 in another workspace is another life: company, home"
    herdr_says({"name": "spawned"}, [crown, me])
    assert run(reported, {**pane2, "MUNICE_AUTO_DISPATCH": "1"}) == (0, "", closed), "job tabs report on the board, not to a 👑"
    saved()

    assert run(json.dumps({"last_assistant_message": keep[0]}), pane2) == (0, "", ""), "요유 몫 left: no report, no close"
    assert saved() == ""
    for label in ("🔴 감시", "🟢 👑 리드"):
        herdr_says({"name": "spawned"}, [crown, {**me, "label": label}])
        assert run(reported, pane2) == (0, "", ""), f"{label} stays, and tells nobody it is closing"
    assert saved() == ""
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
            "w1:p9": ("🟢 나", "x", done), "w1:pa": ("🟢 결정 대기", "x", keep[0]), "w1:pb": ("🟢 기록 없음", "x", None)}
    agents: list = []
    panes: list = [crown]
    for pane, (label, name, text) in rows.items():
        sid = "sid-" + pane[-1]
        agents.append(agent(pane, name, sid, "working" if pane == "w1:p5" else "idle"))
        panes.append({**me, "pane_id": pane, "label": label, "agent_session": {"value": sid}})
        if text and pane == "w1:p3":
            rollout(sid, text)
        elif text:
            transcript(sid, text)
    herdr_says({"name": "x"}, panes, agents)
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep"])
    assert (code, calls) == (0, ""), "--sweep alone only lists"
    assert "w1:p2" in out and "w1:p3" in out and not any(p in out for p in ("w1:p4", "w1:p5", "w1:p6", "w1:p8", "w1:p9", "w1:pa", "w1:pb")), out
    assert saved() == ""
    code, out, calls = run("", {"HERDR_PANE_ID": "w1:p9"}, ["--sweep", "--apply"])
    assert calls.count("--close") == 2 and "pane=w1:p2 --close" in calls and "pane=w1:p3 --close" in calls, calls
    assert calls.count("tell w1:p1") == 2, calls
    assert saved().count(done) == 2, "swept panes keep their reports too"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    if "--sweep" in sys.argv:
        sweep(os.environ, "--apply" in sys.argv)
        sys.exit()
    try:
        reply = decide(json.load(sys.stdin), os.environ)
        if reply:
            finish(reply, os.environ)
    except Exception:  # never break a session
        pass
    sys.exit(0)
