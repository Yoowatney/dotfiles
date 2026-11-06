#!/usr/bin/env python3
"""PreToolUse hook: no hand-typed bullets in anything bound for Slack.

Slack MCP post tools (slack_reply_to_thread, slack_post_message) send plain
`text`, which Slack renders as mrkdwn: no real lists, no `**bold**`, no
headings. A typed `•` or `- ` shows up as a fake bullet whose wrapped lines
don't indent. Those calls get the full mrkdwn check.

Code that posts to Slack itself (chat.postMessage, webhooks) runs later, from
CI or launchd, where no hook can see the message. So file writes (Claude
Write/Edit/MultiEdit, Codex apply_patch) and Bash commands are checked when
they touch Slack-posting code: adding a typed bullet there is denied. Only
added bullets count, so editing a file that already has some still works.
A line with `slack-format: ok` opts out.

A JSON deny blocks the call and tells the model why.

    slack-format-check.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import importlib.util
import json
import os
import re
import sys

FAKE_BULLET = re.compile(r"^\s*[•◦▪‣●○∙·]\s")
MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
DOUBLE_BOLD = re.compile(r"\*\*[^*\n]+\*\*")
HEADING = re.compile(r"^#{1,6}\s")
INLINE_CODE = re.compile(r"`[^`\n]*`")

FIX = (
    "Slack MCP는 text만 보내서 진짜 목록을 만들 수 없다. "
    "요유 이름으로 보낼 글이면 마크다운 파일에 써서 `slackcopy`로 클립보드에 넣고, "
    "봇으로 올려야 하면 agent-board `main/main-channel.py reply`(또는 `post`)로 보낸다: 목록 줄을 진짜 목록으로 바꾼다 "
    "(memory: reference_slack_mcp_no_real_bullets)."
)


MEMBERS = os.path.expanduser("~/.config/slackcopy/members.json")
KOREAN_MENTION = re.compile(r"(?<![\w./@])@([가-힣]+)")
MENTION_FIX = (
    "Slack 멘션은 영어 Slack 이름으로 쓴다(예: @Jane). 한글 @이름은 원문과 일반 텍스트 붙여넣기에 그대로 남아 "
    "멘션이 되지 않는다. slackcopy는 영어 이름도 진짜 멘션으로 바꾼다. 멤버 목록: ~/.config/slackcopy/members.json"
)


def load_members(path: str = MEMBERS) -> dict[str, str]:
    """members.json is {user id: [English label, alias, ...]}; returns alias -> English label."""
    try:
        with open(path, encoding="utf-8") as f:
            return {alias.lower(): names[0] for names in json.load(f).values() for alias in names}
    except (OSError, ValueError):
        return {}


LEFT = os.path.expanduser("~/.config/slackcopy/left.json")  # {user id: [label, alias, ...]} of teammates who left
LEFT_FIX = (
    "회사를 떠난 사람은 Slack에서 태그하지 않는다(요유 2026-11-06). 그 사람을 빼고 다시 쓴다. "
    "목록은 로컬 파일 ~/.config/slackcopy/left.json이고, 인사 정보라 리포·PR·이슈·Slack에는 적지 않는다."
)


def left_problems(text: str, left: dict | None = None) -> list[str]:
    """Teammates who left that the text tags: <@id> or @name. Same rule as agent-board ambient/slack.py left_mentions."""
    if left is None:
        try:
            with open(LEFT, encoding="utf-8") as f:
                left = {u: n for u, n in json.load(f).items() if not u.startswith("_")}
        except (OSError, ValueError):
            left = {}
    return [f"@{names[0]}: 회사를 떠난 사람이라 태그하지 않는다" for uid, names in left.items()
            if re.search(r"<@%s\b" % uid, text)
            or any(re.search(r"(?<![\w./@])@" + re.escape(n) + r"(?![A-Za-z0-9_])", text, re.I) for n in names)]


def mention_problems(text: str, members: dict[str, str]) -> list[str]:
    """Korean @mentions, with the English Slack name to use instead (Korean particles may follow: @홍길동님)."""
    found, fenced = [], False
    for n, raw in enumerate(text.split("\n"), 1):
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        for word in KOREAN_MENTION.findall(INLINE_CODE.sub("", raw)):
            name = max((a for a in members if word.startswith(a)), key=len, default=None)
            found.append(f"{n}행: @{name} → @{members[name]} (Slack 이름은 영어로)" if name
                         else f"{n}행: @{word} → Slack 영어 이름으로 (members.json에 없음)")
    return found


def slackcopy_problems(command: str, members: dict[str, str]) -> list[str]:
    """Korean @mentions in the files a `slackcopy <file>` command is about to put on the clipboard."""
    found = []
    for segment in re.split(r"\|\||&&|[;|]", command):
        words = segment.split()
        if not words or os.path.basename(words[0]) != "slackcopy":
            continue
        for arg in words[1:]:
            if arg.startswith("-") or ">" in arg or "<" in arg:
                continue
            path = os.path.expanduser(arg.strip("'\""))
            if os.path.isfile(path):
                found += [f"{os.path.basename(path)} {p}" for p in mention_problems(on_disk(path), members)]
    return found


def problems(text: str, members: dict[str, str] | None = None) -> list[str]:
    found, fenced = mention_problems(text, load_members() if members is None else members), False
    for n, raw in enumerate(text.split("\n"), 1):
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = INLINE_CODE.sub("", raw)
        if FAKE_BULLET.match(line):
            found.append(f"{n}행: 손으로 친 글머리 기호(•, ◦ 등)")
        elif MD_LIST.match(line):
            found.append(f"{n}행: 마크다운 목록(-, *, 1.)은 text로 보내면 목록이 되지 않음")
        if DOUBLE_BOLD.search(line):
            found.append(f"{n}행: **굵게**는 Slack text에서 별표가 그대로 보임 (*한 개*를 쓸 것)")
        if HEADING.match(line):
            found.append(f"{n}행: # 헤더는 Slack에 없음")
        if "·" in line and not FAKE_BULLET.match(line):
            found.append(f"{n}행: 가운뎃점 · 금지, 쉼표를 쓸 것")
    return found


# Code that sends to Slack on its own. Bullets anywhere else (UI strings, docs) are not our business.
SLACK_CODE = re.compile(
    r"chat[._](?:postMessage|update|postEphemeral|scheduleMessage)|hooks\.slack\.com|slack\.com/api"
    r"|slack_sdk|@slack/|SLACK_[A-Z_]*(?:TOKEN|WEBHOOK)"
    # wrappers that post their stdin as text (nightly_api scripts/deploy/slack-post.sh, 10/18)
    r"|slack-post\.sh"
)
# A bullet that starts a line of message text: after a newline (real or a literal \n),
# or right after a string opener / concatenation, then a space.
CODE_BULLET = re.compile(r"""(?:^|\\n|["'`(\[{+,])[ \t]*[•◦▪‣][ \t]""", re.M)
OPT_OUT = "slack-format: ok"

CODE_FIX = (
    "Slack 글을 만드는 코드에 손으로 친 글머리 기호(•, ◦)를 새로 넣으려 했다. "
    "chat.postMessage의 text 안 기호는 그냥 글자라서 목록이 되지 않는다. "
    "rich_text 블록의 rich_text_list(style bullet, indent 0/1)로 만들 것. "
    "예: monaural_beat_recommend scripts/notify_slack.py의 to_blocks()는 `- ` 줄을 진짜 목록으로 바꾼다. "
    "그런 변환 없이 기호를 `- `나 `[라벨]` 문단으로 바꾸기만 하는 것은 해결이 아니다(mrkdwn text에서는 똑같이 깨진다). "
    "보낸 뒤에는 conversations.history로 블록 종류를 다시 확인한다. "
    "기호를 지우는 편집이면 Edit 도구로 하고, 정말 글자 그대로 필요하면 그 줄에 `slack-format: ok`를 적는다 "
    "(memory: reference_slack_mcp_no_real_bullets)."
)


def bullets(text: str) -> int:
    return sum(len(CODE_BULLET.findall(line)) for line in text.split("\n") if OPT_OUT not in line)


def on_disk(path: str) -> str:
    try:
        if os.path.getsize(path) <= 2_000_000:
            with open(path, encoding="utf-8") as f:
                return f.read()
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def added_bullets(path: str, old: str, new: str) -> int:
    """Typed bullets this change adds to Slack-posting code (0 for anything else)."""
    if path.endswith((".md", ".markdown")):
        return 0  # docs are read, not sent
    if not SLACK_CODE.search(old + new + on_disk(path)):
        return 0
    return bullets(new) - bullets(old)


def patch_changes(patch: str, cwd: str):
    """(path, removed, added) per file of a Codex apply_patch body."""
    path, removed, added = None, [], []
    for line in patch.split("\n"):
        m = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)", line)
        if m or line.startswith("*** End Patch"):
            if path:
                yield path, "\n".join(removed), "\n".join(added)
            path, removed, added = (os.path.join(cwd, m[1].strip()) if m else None), [], []
        elif path and line.startswith("+"):
            added.append(line[1:])
        elif path and line.startswith("-"):
            removed.append(line[1:])
    if path:
        yield path, "\n".join(removed), "\n".join(added)


def code_problems(tool: str, inp: dict, cwd: str) -> list[str]:
    """Where a file write or command adds typed bullets to Slack-bound code."""
    command = inp.get("command") or ""
    if tool in ("Write", "Edit", "MultiEdit"):
        path = inp.get("file_path") or ""
        if tool == "Write":
            changes = [(path, on_disk(path), inp.get("content") or "")]
        else:
            edits = inp.get("edits") or [inp]
            changes = [(path, e.get("old_string") or "", e.get("new_string") or "") for e in edits]
    elif "*** Begin Patch" in command:  # Codex apply_patch, as its own tool or through the shell
        changes = list(patch_changes(command, cwd))
    elif command:
        changes = [("", "", command)] if SLACK_CODE.search(command) else []
    else:
        changes = []
    return [
        f"{os.path.basename(path) or 'Bash 명령'}: 글머리 기호 {n}개 추가"
        for path, old, new in changes
        if (n := added_bullets(path, old, new) if path else bullets(new)) > 0
    ]


# A session that posts text to Slack's API itself skips the shared client, the one that sends list lines as a real list:
# 7 posts went out with typed lists that way in 11/03-11/06 (scratch wrappers, curl, inline scripts; cf#752). Only a post
# without blocks is refused: one that builds its own blocks draws its own lists. Repo code is reviewed in its own PR, so only
# scratch code files and commands that run code are checked. Replayed over the 7 days to 11/06: see the dotfiles commit.
# ponytail: a script written outside the temp dirs, or through $TMPDIR, passes; add its place when one shows up.
POST_API = re.compile(r"chat[._](?:postMessage|update|postEphemeral|scheduleMessage)\b|\binitial_comment\b")
BLOCKS = re.compile(r"\bblocks\b")
SCRATCH_PATH = re.compile(r"(?:/private)?/(?:tmp|var/folders)/")
NOT_CODE = re.compile(r"\.(?:md|markdown|txt|json|jsonl|log|csv|html)$|/(?:tests?|__tests__)/|(?:^|/)test_|\.(?:test|spec)\.|fake")
# what sends: an HTTP call, a Slack SDK client, or the shared client's raw call (Slack()._do)
SENDS = re.compile(r"slack\.com/api|urlopen|requests\.post|WebClient|slack_sdk|getSlackClient|\._do\(|\bfetch\(")
# a command word's own place: past do/then/env/timeout N and VAR=value, its path allowed (/usr/bin/python3)
_AT = r"(?:^|[;&|(`]|\$\()\s*(?:(?:do|then|else|exec|nohup|env|time|timeout\s+\S+)\s+)*(?:\w+=\S*\s+)*(?:\S*/)?"
_RUNNER = _AT + r"(?:uv\s+run\s+(?:\S+\s+)*?)?"
RUNS_CODE = re.compile(
    _AT + r"(?:curl|wget)\b"
    r"|" + _RUNNER + r"(?:python3?|node|deno|bun|ruby|perl)\b[^\n;&|]*?(?:\s-[ce]\b|\s-(?=\s|$)|<<)", re.M)
SHELL_READS = re.compile(_RUNNER + r"(?:python3?|node|deno|bun|ruby|perl|bash|sh|zsh|curl)\b", re.M)  # curl -d @-
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n(.*?)(?:^[ \t]*\2[ \t]*$|\Z)", re.S | re.M)
# a quoted -c/-e code or curl body (-d, --data…, --json) stays; any other multi-line quote is words, not a command
QUOTED = re.compile(r"(\s(?:-[ced]|--data(?:-\w+)?|--json)[=\s]+)?(\"(?:[^\"\\]|\\.)*\"|'[^']*')")
# how Codex writes a scratch script: cat > /tmp/…/post.py <<'EOF'
WRITES_SCRIPT = re.compile(r"\bcat\b[^\n;&|]*>\s*['\"]?(" + SCRATCH_PATH.pattern + r"[^\s'\";&|]*?\.(?:py|[cm]?js|ts|sh))(?=['\"\s;&|)]|$)")
# a curl body sent from a file (-d @body.json), and the simple VAR=value and cd that name its place
DATA_FILE = re.compile(r"(?:-d|--data(?:-binary|-raw)?|-F|--form)[=\s]+['\"]?(?:\w+=)?@([^\s'\";&|)]+)")
ASSIGN = re.compile(r"(?:^|[\s;&|(])(\w+)=(\"[^\"\n]*\"|'[^'\n]*'|[^\s;&|]+)")
CD = re.compile(r"(?:^|[;&|]\s*)cd\s+(['\"]?)([^\s;&|'\"]+)\1", re.M)
AGENT_BOARD = "/Users/yoyoo/work/munice/.agents/skills/agent-board"
DIRECT_FIX = (
    "세션이 블록 없이 Slack API를 직접 불러 글을 쓰려 했다. 그러면 `1.`, `- ` 줄이 목록이 아니라 글자로 나간다(cf#752). "
    f"공용 클라이언트를 쓸 것: 팀 채널·스레드·DM은 `python3 {AGENT_BOARD}/main/main-channel.py reply --channel <C|D|G…> "
    "--thread <ts> <file>`(새 글은 `--thread` 대신 `--new`, 밤 글은 08:00까지 기다리고 `--urgent`는 바로 보냄), "
    "#0-yoyooyo는 같은 파일의 `post`/`edit`. Python 코드는 "
    f"`sys.path.insert(0, \"{AGENT_BOARD}/ambient\")` 뒤 `slack.Slack().post(channel, thread_ts, text)`/`.update(channel, ts, text)`"
    "(`_do`로 직접 부르지 말 것). 파일은 initial_comment 없이 올리고 글은 reply로 단다. "
    "블록을 직접 만들어 보내는 글(버튼 등, 목록은 `slack.rich_text(text)`)은 막지 않는다. "
    "그래도 text로 직접 보내야 하면 그 줄에 `slack-format: ok`를 적는다."
)


def _expand(command: str):
    """The command's own VAR=value words, then a function that puts them into a string ($VAR, ${VAR})."""
    env = {m[1]: m[2].strip("'\"") for m in ASSIGN.finditer(command)}
    return lambda s: re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m[1], os.environ.get(m[1], m[0])), s)


def code_only(command: str) -> str:
    """The command without the words no program runs: a heredoc body that goes to cat, git or gh (a PR body, a commit
    message, a file) and a multi-line quoted argument other than -c/-e code. A line in them is no command (review of deb8395)."""
    kept = []

    def heredoc(m):  # a body a program reads is set aside, so the quote pass below cannot pair its quotes
        opener = command[command.rfind("\n", 0, m.start()) + 1:m.start()]
        if not SHELL_READS.search(opener):
            # an unquoted delimiter lets the shell run the body's $(…) before cat sees it: those lines stay.
            # ponytail: a $( whose command is on the next line is missed; keep the whole substitution if one shows up
            runs = "" if m[1] else "".join(line + "\n" for line in m[3].split("\n") if re.search(r"\$\(|`", line))
            return m[0][:m.start(3) - m.start()] + runs
        kept.append(m[0])
        return "\0%d\0" % (len(kept) - 1)
    # quotes pair from the left as the shell pairs them; only a multi-line one that is no -c/-e code goes
    out = QUOTED.sub(lambda m: m[0] if m[1] or "\n" not in m[2] else ' ""', HEREDOC.sub(heredoc, command))
    return re.sub(r"\0(\d+)\0", lambda m: kept[int(m[1])], out)


def data_files(command: str, cwd: str) -> str:
    """What a curl command sends from files (-d @f). ponytail: a path built any other way than VAR=value or cd is unread."""
    expand, out = _expand(command), []
    for m in DATA_FILE.finditer(command):
        base = cwd  # where curl runs: every cd before it, in turn
        for c in CD.finditer(command[:m.start()]):
            base = os.path.normpath(os.path.join(base, os.path.expanduser(expand(c[2]))))
        out.append(on_disk(os.path.join(base, os.path.expanduser(expand(m[1])))))
    return "\n".join(out)


def final_text(path: str, edits: list) -> str:
    """A file as an Edit/MultiEdit leaves it."""
    text = on_disk(path)
    for e in edits:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def direct_post_problems(tool: str, inp: dict, cwd: str) -> list[str]:
    """Where a scratch code file or a command that runs code posts text to Slack's API itself, with no blocks.
    Each change is (path, where the call is, what it sends): a rewrite is judged by what it leaves, not by what it removed,
    and a command's call by the words that run, its blocks by everything it sends."""
    command = inp.get("command") or ""
    changes = []
    if tool == "Write":
        content = inp.get("content") or ""
        changes = [(inp.get("file_path") or "", content, content)]
    elif tool in ("Edit", "MultiEdit"):
        path = inp.get("file_path") or ""
        final = final_text(path, inp.get("edits") or [inp])
        changes = [(path, final, final)]
    elif "*** Begin Patch" in command:
        deleted = {os.path.join(cwd, p.strip()) for p in re.findall(r"^\*\*\* Delete File: (.+)$", command, re.M)}
        for path, removed, added in patch_changes(command, cwd):
            gone = set(removed.split("\n"))
            final = "\n".join(line for line in on_disk(path).split("\n") if line not in gone) + "\n" + added
            changes += [] if path in deleted else [(path, final, final)]
    else:
        script, code = WRITES_SCRIPT.search(_expand(command)(command)), code_only(command)
        if script and not NOT_CODE.search(script[1]):
            changes = [(script[1], command, command)]
        elif RUNS_CODE.search(code) and SENDS.search(code):  # a test written with cat leaves the rest checked
            # a body sent from a variable or stdin (-d "$payload", -d @-) is in words code_only dropped: count them all
            fed = re.search(r"(?:-d|--data[\w-]*|--json)[=\s]+[\"']?(?:\$|@-)", code)
            changes = [("", code, (command if fed else code) + "\n" + data_files(command, cwd))]
    return [f"{os.path.basename(path) or 'Bash 명령'}: 블록 없이 Slack API를 직접 불러 글을 씀"
            for path, call, sent in changes
            if (not path or (SCRATCH_PATH.match(path) and not NOT_CODE.search(path)))
            and any(POST_API.search(line) for line in call.split("\n") if OPT_OUT not in line)
            and not BLOCKS.search(sent)]


def fresh_reason(payload: dict) -> str | None:
    """A check that rides on this hook, for its registration in both tools: see slack-fresh-guard.py. Its own crash passes."""
    sys.dont_write_bytecode = True
    try:
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "agent-hooks", "slack-fresh-guard.py")
        spec = importlib.util.spec_from_file_location("slack_fresh", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check(payload, dict(os.environ))
    except Exception:
        return None


def selftest():
    assert problems("• 항목") and problems("    ◦ 하위")
    assert problems("- item") and problems("1. step") and problems("  * x")
    assert problems("**bold**") and problems("# 제목") and problems("A·B")
    ok = (
        "*[ 제목 ]* <@U1>\n"
        "> *결론* 한 줄\n"
        ":yoyoo-wawa::yoyoo-wawa:\n"
        "*< 섹션 >*\n"
        "본문 `- not a list` 와 `a·b`\n"
        "```\n- fenced\n• fenced\n```\n"
        "-1도 아니고 2026-10-08 날짜"
    )
    assert problems(ok) == [], problems(ok)

    B, S = "\u2022", "\u25e6"  # escaped so editing this file doesn't trip the check below
    nowhere = "/nonexistent/"
    # 10/14 usage DM: TypeScript building text lines for chat.postMessage.
    ts = "await fetch('https://slack.com/api/chat.postMessage')\nlines.push(`" + B + " ${who} ${n}건`);"
    assert code_problems("Write", {"file_path": nowhere + "report.ts", "content": ts}, "")
    # 10/15 release post: YAML TEXT with literal \\n, the Slack token named in the same file.
    yml = 'SLACK_BOT_TOKEN: x\nTEXT="*[ a ]*\\n' + B + ' 주간\\n    ' + S + ' 기준"'
    assert code_problems("Edit", {"file_path": nowhere + "train.yml", "old_string": "", "new_string": yml}, "")
    slack_line = 'msg = "' + B + ' {}"  # chat.postMessage'
    for tool, inp in [
        ("Edit", {"file_path": nowhere + "a.py", "old_string": slack_line, "new_string": slack_line + " x"}),  # same count
        ("Edit", {"file_path": nowhere + "a.py", "old_string": slack_line, "new_string": 'msg = "{}"'}),  # removes one
        ("Write", {"file_path": nowhere + "ui.ts", "content": "label = '" + B + " item'"}),  # not Slack code
        ("Write", {"file_path": nowhere + "README.md", "content": slack_line}),  # docs
        ("Write", {"file_path": nowhere + "a.py", "content": slack_line + "  # slack-format: ok"}),
        ("Write", {"file_path": nowhere + "a.py", "content": 'msg = "A ' + B + ' B"  # chat.postMessage'}),  # mid-line
        ("Bash", {"command": 'grep -c "[' + B + S + ']" notify.py  # SLACK_BOT_TOKEN'}),
        ("Bash", {"command": "echo '" + B + " not slack'"}),
    ]:
        assert code_problems(tool, inp, "") == [], (tool, inp)
    curl = "curl https://slack.com/api/chat.postMessage -d '{\"text\":\"" + B + " a\"}'"
    assert code_problems("Bash", {"command": curl}, "")
    # 10/18 cloud-functions release post: lines piped into nightly_api's slack-post.sh wrapper,
    # which names no Slack API in the command itself
    wrapper = "printf '%s\\n' '*[ a ]*' '" + B + " item' | GCP_PROJECT_ID=p scripts/deploy/slack-post.sh C0536DQTREV"
    assert code_problems("Bash", {"command": wrapper}, "")
    patch = "*** Begin Patch\n*** Add File: n.py\n+import slack_sdk\n+t = \"" + B + " a\"\n*** End Patch"
    assert code_problems("apply_patch", {"command": patch}, nowhere)
    same = "*** Begin Patch\n*** Update File: n.py\n@@\n-" + slack_line + "\n+" + slack_line + " x\n*** End Patch"
    assert code_problems("apply_patch", {"command": same}, nowhere) == []

    # Korean @mentions: Slack's real names are English, so `@홍길동` pastes as plain text.
    members = {"홍길동": "Gildong", "gildong": "Gildong"}
    assert mention_problems("안녕 @홍길동, 확인 부탁", members) == ["1행: @홍길동 → @Gildong (Slack 이름은 영어로)"]
    assert mention_problems("x\n@홍길동님 확인", members) == ["2행: @홍길동 → @Gildong (Slack 이름은 영어로)"]
    assert mention_problems("@모르는이 확인", members) == ["1행: @모르는이 → Slack 영어 이름으로 (members.json에 없음)"]
    for fine in ["@Gildong 확인", "`@홍길동`", "```\n@홍길동\n```", "mail a@b.com", "https://x.y/@홍길동"]:
        assert mention_problems(fine, members) == [], fine
    md = "/tmp/slack-format-check-selftest.md"
    with open(md, "w", encoding="utf-8") as f:
        f.write("**[ 제목 ]** @홍길동\n")
    try:
        assert slackcopy_problems("slackcopy " + md, members), "slackcopy file with a Korean mention"
        assert slackcopy_problems("slackcopy --no-lint " + md + " 2>&1 | tail -3", members)
        assert slackcopy_problems("echo slackcopy is great", members) == []
        assert slackcopy_problems("cat " + md, members) == []  # not slackcopy
    finally:
        os.remove(md)
    assert problems("*[ a ]* @홍길동", members)  # Slack MCP text too

    # a teammate who left is never tagged (요유 2026-11-06); the list is local, so no name lives in this file
    gone = {"U9": ["Gone", "곤"]}
    for text in ("hi @Gone", "@gone님", "@곤 확인", "<@U9> 봐 주세요", "<@U9|gone>"):
        assert left_problems(text, gone) == ["@Gone: 회사를 떠난 사람이라 태그하지 않는다"], text
    for text in ("gone@munice.com", "Gone 얘기", "@Gonezo", "https://a.b/@곤", "<@U99>"):
        assert left_problems(text, gone) == [], text

    # cf#752: a session's own call to Slack's post API skips the shared client, so its list lines go out as typed.
    tmp = "/private/tmp/claude-501/x/scratchpad/"
    post = 's._do("chat.postMessage", {"channel": c, "text": t}, post=True)'
    for tool, inp in [
        ("Write", {"file_path": tmp + "bb.py", "content": "from slack import Slack\n" + post}),
        ("Edit", {"file_path": "/tmp/codex/2026-11-06-x/sl.py", "old_string": "", "new_string": post}),
        ("Write", {"file_path": tmp + "up.py", "content": "c.files_upload_v2(channel=c, file=f, initial_comment=t)"}),
        ("Bash", {"command": "curl -sS https://slack.com/api/chat.postMessage -H \"$H\" -d @body.json"}),
        ("Bash", {"command": "cd /x && python3 - <<'EOF'\nWebClient(t).chat_postMessage(channel='C1', text='- a')\nEOF"}),
        ("Bash", {"command": "uv run --with slack_sdk python3 -c 'W(t).chat_update(channel=c, ts=s, text=x)'"}),
        ("Bash", {"command": "TOKEN=$T node -e \"new WebClient(t).chat.update({channel, ts, text})\""}),
        ("Bash", {"command": "cat > /tmp/codex/x/post.py <<'EOF'\nr = s.chat_postMessage(channel=c, text=t)\nEOF"}),
        ("apply_patch", {"command": "*** Begin Patch\n*** Add File: /tmp/codex/x/p.py\n+" + post + "\n*** End Patch"}),
    ]:
        assert direct_post_problems(tool, inp, ""), (tool, inp)
    for tool, inp in [
        ("Write", {"file_path": nowhere + "functions/bot/src/slack.ts", "content": "client.chat.postMessage({channel, text})"}),
        ("Write", {"file_path": tmp + "qa.py", "content": "from slack import Slack\nSlack().post('D1', None, '- a')"}),
        ("Write", {"file_path": tmp + "qa.py", "content": post + "  # slack-format: ok"}),
        ("Bash", {"command": 'grep -rn "chat.postMessage" functions/ | head'}),
        ("Bash", {"command": "gh pr create --body \"$(cat <<'EOF'\nchat.postMessage에 블록을 보낸다\nEOF\n)\""}),
        ("Bash", {"command": 'git commit -m "fix: chat.update keeps the blocks"'}),
        ("Bash", {"command": "curl -sS 'https://slack.com/api/conversations.history?channel=C1' -H \"$H\""}),
        ("Bash", {"command": "python3 " + tmp + "qa.py"}),
        ("Bash", {"command": "python3 -c \"from slack import Slack; Slack().post('D1', None, '- a')\""}),
        ("Bash", {"command": "printf '%s' \"$t\" | GCP_PROJECT_ID=p scripts/deploy/slack-post.sh D1"}),
        # builds its own blocks, so it draws its own lists
        ("Write", {"file_path": tmp + "post_louie.py", "content": post + "\n" + 'body["blocks"] = [rich]'}),
        ("Bash", {"command": "curl -s https://slack.com/api/chat.postMessage -d \"$(jq -n '{channel: $c, blocks: $b}')\""}),
        # notes, a commit message, a test fake and a read of past sessions name the API without calling it
        ("Write", {"file_path": tmp + "verification.md", "content": post}),
        ("Bash", {"command": "cat > " + tmp + "commit1.txt <<'EOF'\nchat.postMessage's text has no list syntax\nEOF"}),
        ("Write", {"file_path": tmp + "wt/.agents/main/tests/listfake.py", "content": 'if method == "chat.postMessage":'}),
        ("Bash", {"command": "f=$(rg -l 'chat.postMessage' 10 11); python3 - \"$f\" <<'EOF'\nimport json\nEOF"}),
    ]:
        assert direct_post_problems(tool, inp, "") == [], (tool, inp)

    # Codex review of deb8395: a test written with cat passes as one written with Write; a rewrite is judged by what it
    # leaves, not by the blocks it removed; a curl body read from a file counts.
    assert direct_post_problems("Bash", {"command": "cat > /tmp/x/tests/test_slack.py <<'EOF'\n"
                                                     "return method == 'chat.postMessage'\nEOF"}, "") == []
    old, body = "/tmp/slack-format-check-selftest-old.py", "/tmp/slack-format-check-selftest-body.json"
    with open(old, "w", encoding="utf-8") as f:
        f.write('c.chat_postMessage(channel=c, blocks=b)\n')
    with open(body, "w", encoding="utf-8") as f:
        f.write('{"channel": "C1", "blocks": [{"type": "rich_text"}]}')
    try:
        text_only = "c.chat_postMessage(channel=c, text='- a')"
        assert direct_post_problems("Write", {"file_path": old, "content": text_only}, "")
        assert direct_post_problems("Edit", {"file_path": old, "old_string": "blocks=b", "new_string": "text=t"}, "")
        assert direct_post_problems("Edit", {"file_path": old, "old_string": "\n", "new_string": "\n" + text_only + "\n"}, "") == []
        curl = "curl -s https://slack.com/api/chat.postMessage -H \"$H\" "
        assert direct_post_problems("Bash", {"command": curl + "--data-binary @" + body}, "") == []
        assert direct_post_problems("Bash", {"command": "S=/tmp; " + curl + "-d @$S/" + os.path.basename(body)}, "") == []
        assert direct_post_problems("Bash", {"command": "cd /tmp && " + curl + "-d @" + os.path.basename(body)}, "") == []
        assert direct_post_problems("Bash", {"command": curl + "-d @/tmp/slack-format-check-selftest-missing.json"}, "")
    finally:
        os.remove(old)
        os.remove(body)

    # review of deb8395: a line inside a PR body, a commit message or a file written with cat is no command, while a
    # shell or an interpreter that reads a heredoc runs it; loops, env and absolute paths still name the command
    send = "curl -s https://slack.com/api/chat.postMessage -d text=x"
    for cmd in [
        "gh pr create --body \"$(cat <<'EOF'\n## 검증\n" + send + "\nEOF\n)\"",
        "git commit -F - <<'EOF'\nfix: x\n\n" + send.replace("postMessage", "update") + "\nEOF",
        "cat > /Users/x/repo/scripts/x.sh <<'EOF'\n" + send + "\nEOF",
        "cat > /tmp/x/a.py.md <<'EOF'\n" + send + "\nEOF",
        "gh pr comment 1 --body '확인:\n" + send + "'",
    ]:
        assert direct_post_problems("Bash", {"command": cmd}, "") == [], cmd
    for cmd in [
        "bash <<'EOF'\n" + send + "\nEOF",
        "for c in C1 C2; do " + send + "; done",
        "timeout 30 " + send,
        "/usr/bin/python3 -c \"import urllib.request as u\nu.urlopen('https://slack.com/api/chat.postMessage')\"",
    ]:
        assert direct_post_problems("Bash", {"command": cmd}, ""), cmd

    # Codex review of 920bbfa: quotes pair as the shell pairs them, a uv-run or unquoted heredoc still runs, a test
    # written with cat leaves the rest checked, a deleted script and a multi-line blocks body pass, curl reads from its cd
    for cmd in [
        'echo "ready"\n' + send + ' -d "a=b"',
        "uv run --with slack_sdk python3 - <<'PY'\nWebClient(t).chat_postMessage(channel='C1', text='- a')\nPY",
        "cat <<EOF\n$(" + send + ")\nEOF",
        "cat > /tmp/x/test_fixture.py <<'EOF'\nx = 1\nEOF\n" + send,
    ]:
        assert direct_post_problems("Bash", {"command": cmd}, ""), cmd
    assert direct_post_problems("Bash", {"command": send + " -d '{\n\"blocks\": [{\"type\": \"rich_text\"}]\n}'"}, "") == []
    assert direct_post_problems("Bash", {"command": "*** Begin Patch\n*** Delete File: post.py\n*** End Patch"},
                                "/tmp/x") == []
    import shutil
    import tempfile
    here, there = tempfile.mkdtemp(dir="/tmp"), tempfile.mkdtemp(dir="/tmp")
    try:
        with open(os.path.join(here, "body.json"), "w", encoding="utf-8") as f:
            f.write('{"blocks": []}')
        with open(os.path.join(there, "body.json"), "w", encoding="utf-8") as f:
            f.write('{"text": "- a"}')
        assert direct_post_problems("Bash", {"command": "cd " + there + " && " + curl + "-d @body.json"}, here)
        assert direct_post_problems("Bash", {"command": curl + "-d @body.json"}, here) == []
    finally:
        shutil.rmtree(here)
        shutil.rmtree(there)

    # Codex review of f6c7d1e: blocks count only in what is sent, an unquoted heredoc keeps only its $(…) lines,
    # cd after cd adds up
    assert direct_post_problems("Bash", {"command": "cat <<'EOF'\nblocks 설명\nEOF\n" + send}, "")
    assert direct_post_problems("Bash", {"command": "cat <<EOF\n오늘 $(date)\n" + send + "\nEOF"}, "") == []
    assert direct_post_problems("Bash", {"command": curl + "-d @- <<'EOF'\n{\"blocks\": []}\nEOF"}, "") == []
    # Codex review of 24216ce: a blocks body in a variable or piped in
    assert direct_post_problems("Bash", {"command": "payload='{\n\"blocks\": []\n}'\n" + curl + '-d "$payload"'}, "") == []
    assert direct_post_problems("Bash", {"command": "cat <<'EOF' | " + curl + "-d @-\n{\"blocks\": []}\nEOF"}, "") == []
    assert direct_post_problems("Bash", {"command": "payload='{\n\"text\": \"- a\"\n}'\n" + curl + '-d "$payload"'}, "")
    here = tempfile.mkdtemp(dir="/tmp")
    try:
        os.mkdir(os.path.join(here, "sub"))
        with open(os.path.join(here, "body.json"), "w", encoding="utf-8") as f:
            f.write('{"blocks": []}')
        assert direct_post_problems("Bash", {"command": "cd sub && cd .. && " + curl + "-d @body.json"}, here) == []
    finally:
        shutil.rmtree(here)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name") or ""
    inp = payload.get("tool_input") or {}
    if "slack" in tool.lower() and "text" in inp:  # the Slack MCP post tools
        found, fix = left_problems(inp.get("text") or ""), LEFT_FIX
        if not found:
            found, fix = problems(inp.get("text") or ""), FIX + "\n" + MENTION_FIX
    else:
        cwd = payload.get("cwd") or os.getcwd()
        try:  # its own crash must not take the older checks below with it
            found, fix = direct_post_problems(tool, inp, cwd), DIRECT_FIX
        except Exception:
            found, fix = [], DIRECT_FIX
        if not found:
            found, fix = code_problems(tool, inp, cwd), CODE_FIX
        if not found and "slackcopy" in (inp.get("command") or ""):  # a markdown file headed for the clipboard
            found, fix = slackcopy_problems(inp["command"], load_members()), MENTION_FIX
    reason = "Slack 형식 검사에 걸렸습니다.\n- " + "\n- ".join(found) + "\n" + fix if found else fresh_reason(payload)
    if reason:  # JSON deny: Claude Code and Codex both read it
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, ensure_ascii=False))
