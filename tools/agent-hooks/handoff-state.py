#!/usr/bin/env python3
"""Validated ordinary-session handoffs; no network or external writes."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

FENCE = re.compile(r"^```handoff\s*\n(.*?)\n```[ \t]*$", re.M | re.S)
FIELDS = ("task", "completed", "remaining", "verification", "resume", "code", "repositories")
TYPED = "Any text before the [handoff: marker in that message is 요유's own, typed while the prompt was on its way: answer it first."


def pending():
    folder = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "agent-handoff"
    socket = os.environ.get("HERDR_SOCKET_PATH", "")
    if "/sessions/" in socket and socket.endswith("/herdr.sock"):
        folder /= Path(socket).parent.name
    return folder / (os.environ.get("HERDR_PANE_ID", "") + ".md")


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True,
                                   stderr=subprocess.DEVNULL, timeout=5).strip()


def snapshot(path):
    root = Path(path).expanduser().resolve()
    top = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    if root != top:
        raise ValueError("repository path must name its checkout root: " + str(root))
    return dict(path=str(root), branch=git(root, "rev-parse", "--abbrev-ref", "HEAD"),
                commit=git(root, "rev-parse", "HEAD"),
                dirty=bool(git(root, "status", "--porcelain", "--untracked-files=normal")))


def metadata(text):
    matches = list(FENCE.finditer(text))
    if len(matches) != 1:
        raise ValueError("exactly one ```handoff JSON block is required")
    data = json.loads(matches[0].group(1))
    if not isinstance(data, dict):
        raise ValueError("handoff must be an object")
    for field in FIELDS:
        if field not in data or (field != "repositories" and
                                 (not isinstance(data[field], str) or not data[field].strip())):
            raise ValueError("missing/non-text field: " + field)
    if data["code"] not in ("none", "repositories") or not isinstance(data["repositories"], list):
        raise ValueError("code must be none or repositories; repositories must be a list")
    if (data["code"] == "none") != (len(data["repositories"]) == 0):
        raise ValueError("code=none requires []; otherwise name every repository")
    paths = set()
    for repo in data["repositories"]:
        if not isinstance(repo, dict) or not all(k in repo for k in
                ("path", "verified_commit", "verified_dirty", "evidence")):
            raise ValueError("repository needs path/verified_commit/verified_dirty/evidence")
        if not isinstance(repo["path"], str) or not Path(repo["path"]).is_absolute():
            raise ValueError("repository path must be absolute")
        if not isinstance(repo["evidence"], str) or not repo["evidence"].strip():
            raise ValueError("repository evidence is required (use not verified when unverified)")
        if repo["verified_dirty"] is not None and type(repo["verified_dirty"]) is not bool:
            raise ValueError("verified_dirty must be true, false or null")
        commit = repo["verified_commit"]
        if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit)):
            raise ValueError("verified_commit must be a full commit hash or null")
        current = snapshot(repo["path"])
        if current["path"] in paths:
            raise ValueError("duplicate repository path")
        paths.add(current["path"])
        repo["path"] = current["path"]
        repo["current"] = current
    data["version"] = 1
    return matches[0], data


def is_main():
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    try:
        terminal = (state / "orchestrator/main").read_text().strip()
        if not terminal:
            return False
        result = subprocess.check_output([os.environ.get("HERDR_BIN_PATH", "herdr"), "pane", "get",
                                          os.environ["HERDR_PANE_ID"]], text=True, timeout=3)
        return json.loads(result)["result"]["pane"]["terminal_id"] == terminal
    except (OSError, KeyError, ValueError, subprocess.SubprocessError):
        return False


def save(text):
    if not is_main():
        match, data = metadata(text)
        text = text[:match.start()] + "```handoff\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```" + text[match.end():]
    atomic_write(pending(), text)
    return "handoff: saved for the next session; 요유 only types /clear"


def verification_status(text):
    try:
        # Reading never re-collects the saved snapshot or upgrades an old memo.
        matches = list(FENCE.finditer(text))
        data = json.loads(matches[0].group(1)) if len(matches) == 1 else {}
        if data.get("version") != 1 or any(k not in data for k in FIELDS):
            raise ValueError("legacy")
        if data["code"] == "none" and data["repositories"] == []:
            return "코드: 해당 없음. 검증 근거와 다음 시작 조건은 인계 본문을 읽으세요."
        lines = []
        if not data["repositories"]:
            raise ValueError("no repository")
        for repo in data["repositories"]:
            path = repo.get("path", "대상 미상")
            try:
                now = snapshot(path)
                valid = (now == repo.get("current") and not now["dirty"] and
                         repo.get("verified_dirty") is False and
                         now["commit"] == repo.get("verified_commit") and bool(repo.get("evidence")))
            except (OSError, ValueError, subprocess.SubprocessError):
                valid = False
            lines.append(path + ": " + ("코드 상태 일치 (환경·설정·외부 상태는 별도 확인)" if valid else
                         "이전 검증을 그대로 사용할 수 없음 — 코드 변경·미커밋 변경·검증 상태 미상 여부를 다시 확인하세요."))
        return "\n".join(lines)
    except (ValueError, KeyError, TypeError, AttributeError):
        return "필수 항목 미확인. 이전 검증을 그대로 사용할 수 없음 — 옛 메모 또는 손상된 항목을 다시 확인하세요."


def context(text):
    return json.dumps(dict(hookSpecificOutput=dict(hookEventName="SessionStart", additionalContext=text)), ensure_ascii=False)


def codex_session(payload):
    sid = payload.get("session_id")
    transcript = payload.get("transcript_path")
    # Nested codex exec inherits the pane's environment; it must not consume its parent's memo.
    inherited = os.environ.get("CODEX_THREAD_ID")
    if not isinstance(sid, str) or not sid or (inherited and inherited != sid):
        return ""
    if not isinstance(transcript, str) or not transcript:
        return ""
    record = Path(str(pending()) + ".codex-session")
    try:
        previous = json.loads(record.read_text())
    except (OSError, ValueError):
        previous = {}
    atomic_write(record, json.dumps(dict(session_id=sid, transcript_path=transcript)))
    result = load()
    if result:
        return result
    if previous.get("session_id") == sid or not previous.get("transcript_path"):
        return ""
    path = Path(previous["transcript_path"])
    try:
        # Bounded tail, including malformed/interrupted last lines.
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 2_000_000))
            rows = stream.read().decode("utf-8", errors="replace").splitlines()[-2000:]
        users, answers = [], []
        for row in rows:
            try:
                item = json.loads(row)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("type") != "response_item":
                continue
            message = item.get("payload", {})
            if not isinstance(message, dict) or message.get("type") != "message":
                continue
            parts = message.get("content")
            if not isinstance(parts, list):
                continue
            text = "\n".join(part["text"] for part in parts
                             if isinstance(part, dict) and isinstance(part.get("text"), str)
                             and part.get("type") in ("input_text", "output_text", "text"))
            if message.get("role") == "user" and not text.lstrip().startswith(("<", "[", "# AGENTS.md")):
                users.append(text)
            elif message.get("role") == "assistant" and text:
                answers.append(text)
        if not users and not answers:
            return ""
        last = next((line for answer in reversed(answers) for line in reversed(answer.splitlines())
                     if line.startswith("남은 것:")), "")
        return context(f"필수 항목 미확인. 이전 검증을 그대로 사용할 수 없음. The previous Codex session ended without saving a handoff. Its full transcript is {path}. {TYPED}\n요유의 마지막 메시지:\n{(users[-1] if users else '(없음)')[:600]}\n유요의 마지막 답:\n{(answers[-1] if answers else '(없음)')[:1500]}\n{last}")
    except OSError:
        return ""


def load():
    path = pending()
    if not path.is_file():
        return ""
    if time.time() - path.stat().st_mtime > 86400:
        path.unlink()
        return ""
    text = path.read_text()
    status = verification_status(text)
    body = text if len(text) <= 2000 else f"The complete handoff is saved at {path}.loaded. Read it in full before continuing."
    result = context("The previous session in this pane saved this handoff before /clear. Unless 요유's first message asks for something else, continue from it. " + TYPED +
                     "\n" + status + "\n인계는 배포나 외부 쓰기에 대한 새 승인이 아닙니다.\n\n" + body)
    os.replace(path, str(path) + ".loaded")
    return result


def main():
    try:
        if sys.argv[1] == "save":
            text = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else sys.stdin.read()
            print(save(text))
        elif sys.argv[1] == "load":
            result = load()
            if result:
                print(result)
        elif sys.argv[1] == "session-codex":
            result = codex_session(json.load(sys.stdin))
            if result:
                print(result)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("handoff: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
