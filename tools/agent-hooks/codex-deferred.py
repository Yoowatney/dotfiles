#!/usr/bin/env python3
"""Private deferred-work register; recovery notices go only to the local main pane.

The existing Claude/Codex task-ledger Stop hook calls observe, including in open
sessions. SessionStart retries previously unsent notices, never infers recovery.
Only a fresh final Codex response proves availability for that session/provider;
it does not prove that another account's quota has reset. A notice asks the main
pane to reassess pending work. It never executes, closes, or reassigns that work.

Manual records: python3 codex-deferred.py defer --id JOB --summary TEXT
  --source REPORT --link URL. Inspect with list; resolve only after main triage.
Natural-language capture is a safety net, not an exhaustive quota error parser.
Private state: XDG_STATE_HOME/codex-deferred/state.json (or CODEX_DEFERRED_DIR).
"""
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def epoch(value):
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0


def state_dir():
    return Path(os.environ.get("CODEX_DEFERRED_DIR") or
                Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "codex-deferred")


def transaction(change):
    folder = state_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {"version": 1, "items": []}
        if state.get("version") != 1 or not isinstance(state.get("items"), list):
            raise ValueError("Unsupported deferred-work store; preserving it")
        change(state)
        temporary = folder / "state.tmp"
        with temporary.open("w") as stream:
            os.chmod(temporary, 0o600)
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        return state


def defer(state, identity, summary, source, links, replay_safe=False):
    previous = next((i for i in state["items"] if i["id"] == identity), None)
    content = {"summary": summary[:6000], "source": source[:2000], "links": sorted(set(links))}
    if previous and all(previous.get(k) == v for k, v in content.items()):
        return  # Replayed Stop hooks must not reopen or renotify the same task.
    if replay_safe and previous and any(all(old.get(k) == v for k, v in content.items())
                                       for old in previous.get("history", [])):
        return  # An older Stop must not replace a newer commitment or resolution.
    item = previous or {"id": identity}
    if not previous:
        state["items"].append(item)
    else:
        history = item.setdefault("history", [])
        history.append({k: v for k, v in item.items() if k != "history"})
    # A changed commitment in the same job needs a new recovery and notice.
    for key in ("queued_at", "recovery_evidence", "resolved_at", "resolution"):
        item.pop(key, None)
    item.update(content, created_at=stamp(), status="pending", updated_at=stamp())


def transcript(path):
    """Read bounded tail; only the final turn can demonstrate availability."""
    if not path:
        return "", None
    with Path(path).expanduser().open("rb") as stream:
        meta = json.loads(stream.readline())
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 512000))
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    codex = meta.get("type") == "session_meta"
    text, success = "", None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") or {}
        if codex and row.get("type") == "event_msg":
            if payload.get("type") in ("task_started", "user_message") or payload.get("error") or payload.get("type") in ("error", "turn_aborted"):
                text, success = "", None
        if codex and row.get("type") == "response_item" and payload.get("role") == "user":
            text, success = "", None
        if codex and row.get("type") == "response_item" and payload.get("role") == "assistant" and payload.get("phase") in ("final", "final_answer"):
            text = "\n".join(c.get("text", "") for c in payload.get("content", []) if c.get("type") == "output_text")
            if text and 0 <= dt.datetime.now(dt.timezone.utc).timestamp() - epoch(row.get("timestamp")) <= 600:
                success = {"at": row["timestamp"], "provider": meta.get("payload", {}).get("model_provider", "unknown"),
                           "session_id": meta.get("payload", {}).get("id", "unknown"), "transcript": str(path)}
        elif not codex and row.get("type") == "assistant":
            content = (row.get("message") or {}).get("content", [])
            text = content if isinstance(content, str) else "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    return text, success


def blocked(text):
    # Match a single paragraph, not a quota discussion in one section and an
    # unrelated delayed task in another. Quotes/code are not new commitments.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    for paragraph in text.split("\n\n"):
        paragraph = "\n".join(l for l in paragraph.splitlines() if not l.lstrip().startswith(">"))
        sentences = re.split(r"(?<=[.!?。])\s+|\n", paragraph)
        current = []
        for sentence in sentences:
            resumed = re.search(r"(?:재개했습니다|이어받았습니다|다시 시작했습니다|(?:have|has|now|all).*resumed)[.!\s]*$", sentence, re.I)
            negative = re.search(r"\b(?:not|never)\b|않|못|미재개", sentence, re.I)
            if not resumed or negative:
                current.append(sentence)
        paragraph = "\n".join(current)
        if (re.search(r"codex|코덱스", paragraph, re.I) and
                re.search(r"사용량|quota|usage.limit|주간.*(?:0%|소진)", paragraph, re.I) and
                re.search(r"미[룬룹뤄뤘]|보류|막[혀힘혔]|멈[춰췄춥춤]|중단|못|불가|쓸\s*수\s*없|내일.{0,60}(?:자동\s*배정|맡|넘)|defer|blocked|unavailable", paragraph, re.I)):
            return True
    return False


def command(args, env=None, timeout=2):
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)


def notify(state):
    recovery = state.get("recovery")
    items = [i for i in state["items"] if i["status"] == "pending" and not i.get("queued_at")]
    if not recovery:
        return
    items = [i for i in items if epoch(i["created_at"]) <= epoch(recovery["at"])]
    if not items:
        return
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    terminal = (root / "orchestrator/main").read_text().strip()
    if not terminal:
        return
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    sessions = json.loads(command([binary, "session", "list", "--json"]).stdout).get("sessions", [])
    for session in sessions:
        if session.get("name") not in ("default", "workers") or not session.get("running"):
            continue
        env = dict(os.environ, HERDR_SOCKET_PATH=session.get("socket_path") or "")
        rows = json.loads(command([binary, "pane", "list"], env).stdout).get("result", {}).get("panes", [])
        pane = next((p for p in rows if p.get("terminal_id") == terminal), None)
        if not pane:
            continue
        address = session["name"] + "/" + pane["pane_id"]
        message = ("Codex 실제 답변 성공을 확인했습니다: " + recovery["at"] + " (" + recovery["provider"] + "). "
                   "다른 계정의 사용량 복구까지 뜻하지는 않습니다. 아래 보류 원인을 다시 확인하고 메인 세션에서 재배정하세요. "
                   "자동으로 일을 재개하거나 완료 처리하지 않았습니다.\n" + "\n".join(
                       f"- {i['id']}: {i['summary'][:800]} | {i['source']} | {' '.join(i['links'])}" for i in items[:10]) +
                   "\n전체 목록: " + str(state_dir() / "state.json"))
        # pane-tell can accept the message before its final input/receipt checks
        # finish. Killing it at an arbitrary timeout makes acceptance ambiguous
        # and causes a retry to send the same notice again. A detached notifier
        # waits for its receipt without holding up either agent's 10s hook.
        result = command([str(Path(__file__).with_name("pane-tell")), address, message], timeout=None)
        if result.returncode == 0 and result.stdout.startswith(("told ", "queued ")):
            for item in items[:10]:
                item["queued_at"] = stamp()  # Queue acceptance, never a claim of receipt.
                item["recovery_evidence"] = recovery
        return


def notify_pending(wait=False):
    folder = state_dir()
    if not (folder / "state.json").exists():
        return
    with (folder / ".notify.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            return  # One notifier owns the outstanding receipt; never resend it.
        try:
            while True:
                snapshot = transaction(lambda state: None)
                unsent = {i["id"] for i in snapshot["items"] if not i.get("queued_at")}
                notify(snapshot)
                accepted = {i["id"]: i for i in snapshot["items"] if i["id"] in unsent and i.get("queued_at")}
                if not accepted:
                    return
                def acknowledge(state):
                    for item in state["items"]:
                        old = accepted.get(item["id"])
                        if (old and item["status"] == "pending" and not item.get("queued_at")
                                and item["updated_at"] == old["updated_at"]):
                            item.update(queued_at=old["queued_at"], recovery_evidence=old["recovery_evidence"])
                transaction(acknowledge)
                # A concurrent hook may have added work/recovery while this
                # receipt was pending. Drain it before releasing notifier duty.
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass  # Preserve pending notices for the next hook if lookup/send fails.


def start_notifier():
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "notify"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def observe(event, payload):
    try:
        if event == "Stop" and os.environ.get("HERDR_PANE_ID"):
            spec = importlib.util.spec_from_file_location("herdr_peers", Path(__file__).with_name("herdr_peers.py"))
            peers = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(peers)
            pane = peers.herdr(os.environ, "pane", "get", os.environ["HERDR_PANE_ID"]).get("pane") or {}
            if peers.delegates_checks(os.environ, pane.get("terminal_id")):
                return  # Main triage reports must not become new deferred work.
        text, success = "", None
        if event == "Stop":
            try:
                text, success = transcript(payload.get("transcript_path"))
            except (OSError, ValueError, TypeError):
                pass  # A valid last_assistant_message still records a deferral.
        text = payload.get("last_assistant_message") or text
        capture = event == "Stop" and blocked(text)
        if not capture and not (state_dir() / "state.json").exists():
            return
        def update(state):
            if capture:
                identity = os.environ.get("HERDR_TASK_ID") or payload.get("task_id") or payload.get("session_id") or payload.get("sessionId")
                if identity:
                    defer(state, identity, text, str(payload.get("transcript_path") or identity),
                          re.findall(r"https?://[^\s<>\])]+", text), replay_safe=True)
            if success and not capture:
                state["recovery"] = success
        transaction(update)
        start_notifier()
    except Exception:
        pass  # A bookkeeping problem must not block either agent or destroy corrupt state.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("defer")
    for name in ("id", "summary", "source"):
        add.add_argument("--" + name, required=True)
    add.add_argument("--link", action="append", default=[])
    sub.add_parser("list")
    send = sub.add_parser("notify", help="Retry pending main-pane notices")
    send.add_argument("--wait", action="store_true", help="Wait for any active notifier's receipt")
    close = sub.add_parser("resolve")
    close.add_argument("--id", required=True)
    close.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.action == "notify":
        notify_pending(args.wait)
        return
    def change(state):
        if args.action == "defer":
            defer(state, args.id, args.summary, args.source, args.link)
        elif args.action == "resolve":
            item = next(i for i in state["items"] if i["id"] == args.id)
            item.update(status="resolved", resolved_at=stamp(), resolution=args.reason)
    print(json.dumps(transaction(change), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
