#!/usr/bin/env python3
"""Mask secret-shaped values in this Mac's agent transcripts and their copies, once a day.

wow 11/06: three secrets reached session transcripts in one day (tf#1047, api#1035, api#1038). Each was found by
another pane after the fact, and each fix closed only its own path, so a new path is found only after the next
incident. This finds them whatever the path took: it reads every file changed since the last run, overwrites each
match in place with '*' of the same byte length, and DMs 요유 through backend-bot what it masked, never the values.

Usage: transcript-secret-scan.py [--dry-run] [--full] [--no-slack] | --selftest
"""
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
ROOTS = [
    "~/.claude/projects",                                 # Claude sessions
    "~/.local/share/claude-transcript-archive/projects",  # their hourly archive (claude-transcript-archive.sh)
    "~/.codex/sessions", "~/.codex/archived_sessions",    # Codex sessions
    "~/.claude/history.jsonl", "~/.codex/history.jsonl",  # typed prompts
    "~/.local/share/llm-wiki/raw", "~/.local/share/llm-wiki/codex-turns", "~/.local/share/llm-wiki/pending-wiki",
    "~/.local/state/pane-reports",                        # whole pane replies (auto-tab-close.py)
]
SUFFIXES = (".jsonl", ".json", ".md", ".txt")
ENV_DIR = "~/.config"  # values of secret-named keys in env files here are masked wherever they show up
STATE = "~/.local/state/transcript-secret-scan/state.json"
DM = "U091WR1BYD7"  # 요유, through backend-bot (alerts go to the DM)
SLACK_PY = "/Users/yoyoo/work/munice/munice-claude/.agents/skills/agent-board/ambient/slack.py"
OVERLAP = 3600  # rescan an hour before the last run: a file written while it ran is read again
# Google API keys committed in these repos ship inside the apps (Firebase config), so they are public: masking them
# would only send a DM each time a session reads a client's config (5 of 31 seen on 11/06).
PUBLIC_KEY_REPOS = ["/Users/yoyoo/work/munice/" + r for r in
                    ("nightly_ios", "nightly_and", "nightly.so", "munice.com", "nightly_api", "cloud-functions", "terraform")]

# ponytail: prefix-shaped secrets only, so a mask never lands on a commit hash or an id. A secret with no shape (a
# password) is caught only when an env file under ENV_DIR holds it; add a pattern when a new kind leaks.
# Each regex starts with a literal and runs on its own, only on files that hold that literal: one alternation of them
# all loses re's literal-prefix search and took over 9 CPU minutes without finishing 13GB (11/06).
PATTERNS = [  # (kind, regex)
    ("slack_token", rb"xox[abeoprs]-[0-9A-Za-z][0-9A-Za-z-]{20,}"),
    ("slack_app_token", rb"xapp-\d-[A-Z0-9]{8,}-\d{6,}-[0-9a-f]{20,}"),
    ("slack_webhook", rb"hooks\.slack\.com/(?:services|workflows|triggers)/T[A-Z0-9]{6,}/[A-Z0-9]{6,}/[A-Za-z0-9]{16,}"),
    ("github_token", rb"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("github_token", rb"github_pat_[A-Za-z0-9_]{60,}"),
    ("google_api_key", rb"AIza[0-9A-Za-z_-]{35}"),
    ("google_oauth_token", rb"ya29\.[0-9A-Za-z_-]{30,}"),
    # body: base64 and newlines, raw or JSON-escaped once or more (a service account JSON a tool printed), so a match
    # never runs across JSON fields; 40 characters at least, as an Ed25519 key has 64
    ("private_key", rb"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----(?:[A-Za-z0-9+/=\s]|\\+[nr]){40,}?-----END (?:[A-Z]+ )*PRIVATE KEY-----"),
    ("anthropic_key", rb"sk-ant-[A-Za-z0-9_-]{30,}"),
    ("openai_key", rb"sk-(?:proj|svcacct)-[A-Za-z0-9_-]{30,}"),
    ("stripe_key", rb"sk_live_[0-9A-Za-z]{20,}"),
    ("stripe_key", rb"rk_live_[0-9A-Za-z]{20,}"),
    ("aws_key", rb"AKIA[0-9A-Z]{16}"),
    ("notion_token", rb"ntn_[A-Za-z0-9]{40,}"),
    ("notion_token", rb"secret_[A-Za-z0-9]{43}"),
    ("npm_token", rb"npm_[A-Za-z0-9]{36}"),
]
SECRET_KEY = re.compile(r"TOKEN|SECRET|PASSW|API_?KEY|PRIVATE|CREDENTIAL|AUTH", re.I)
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
MASKABLE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")


def env_values(root):
    """Values of secret-named keys in env files under root: long, not a URL or a path, mixed characters."""
    out = set()
    for d, _, files in os.walk(root):
        for f in files:
            if not (f == ".env" or f.endswith(".env") or f.startswith(".env.")):
                continue
            try:
                lines = open(os.path.join(d, f), encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for line in lines:
                m = ENV_LINE.match(line)
                if not m or not SECRET_KEY.search(m.group(1)):
                    continue
                v = m.group(2)
                if v[:1] in ("'", '"'):  # quoted: up to the closing quote, a '#' inside stays
                    close = v.find(v[0], 1)
                    v = v[1:close] if close > 0 else v[1:]
                else:  # unquoted: an inline comment starts at whitespace and '#'
                    v = re.split(r"\s+#", v, maxsplit=1)[0]
                kinds = sum(any(t(c) for c in v) for t in (str.islower, str.isupper, str.isdigit))
                if len(v) >= 12 and kinds >= 2 and "://" not in v and not v.startswith(("/", "~", "$")):
                    out.add(v)
    return out


def public_keys(repos):
    """Google API keys in the tracked files of repos (git grep HEAD); a repo that is missing gives none."""
    import subprocess
    out = set()
    for r in repos:
        try:
            out |= set(subprocess.run(["git", "-C", r, "grep", "-ohE", "AIza[0-9A-Za-z_-]{35}", "HEAD"],
                                      capture_output=True, timeout=120).stdout.split())
        except (OSError, subprocess.SubprocessError):
            pass
    return out


def matcher(values, public=()):
    """What find_all searches: each pattern with its leading literal, every known value raw and as JSON escapes it
    (a '"' shows as '\\"'), and the public keys to leave alone."""
    pats = [(k, re.match(rb"(?:[^\\[(.?*+{]|\\\.)+", p).group().replace(b"\\.", b"."), re.compile(p)) for k, p in PATTERNS]
    forms = {v.encode() for v in values} | {json.dumps(v, ensure_ascii=False)[1:-1].encode() for v in values}
    return pats, sorted(forms, key=len, reverse=True), frozenset(public)


def placeholder(kind, s):
    """A documentation or test value: under 8 distinct characters past the prefix (ghp_xxxx…, AKIAAAAA…) or EXAMPLE."""
    return b"EXAMPLE" in s or len(set(s[keep(kind, s):])) < 8


def find_all(data, rx):
    """(offset, kind, bytes) of every match, left to right; a match inside an earlier one is dropped, and so are
    placeholders and public keys."""
    pats, forms, public = rx
    hits = []
    for kind, lit, p in pats:
        if lit in data:
            hits += [(m.start(), kind, m.group()) for m in p.finditer(data)
                     if not placeholder(kind, m.group()) and m.group() not in public]
    for f in forms:
        i = data.find(f)
        while i >= 0:
            hits.append((i, "env_value", f))
            i = data.find(f, i + len(f))
    hits.sort(key=lambda h: (h[0], -len(h[2])))
    out, end = [], 0
    for h in hits:
        if h[0] >= end:
            out.append(h)
            end = h[0] + len(h[2])
    return out


PREFIX = re.compile(rb"xox[a-z]-|xapp-|gh[pousr]_|github_pat_|AIza|ya29\.|sk-ant-|sk-(?:proj|svcacct)-|[sr]k_live_|AKIA|ntn_|secret_|npm_")


def keep(kind, s):
    """How many leading bytes stay readable: the type prefix (xoxb-, ghp_, AIza…), a key's header, a webhook's ids
    (workspace and app, not secret), nothing of a value from an env file."""
    if kind == "private_key":
        return s.index(b"-----", 5) + 5
    if kind == "slack_webhook":
        return s.rindex(b"/") + 1
    m = PREFIX.match(s) if kind != "env_value" else None
    return m.end() if m else 0


def mask(kind, s):
    """'*' over letters, digits and base64 marks, past the kept prefix; a backslash escape and any other byte stay, so
    JSON stays valid and the length does not change. A private key keeps its footer."""
    out, i = bytearray(s), keep(kind, s)
    end = s.rindex(b"-----END") if kind == "private_key" else len(s)
    while i < end:
        if s[i] == 0x5C:  # backslashes: keep the run and what it escapes (\n, \\n, \", \uXXXX), so nested JSON parses
            while i < end and s[i] == 0x5C:
                i += 1
            i += 5 if s[i:i + 1] == b"u" else 1
            continue
        if s[i] in MASKABLE:
            out[i] = 0x2A
        i += 1
    return bytes(out)


def scan_file(path, rx, dry):
    """Mask every match in path in place; returns {kind: count}. Writes only the matched bytes, after checking they
    are still there, so a session appending to the file loses nothing; its mtime is put back."""
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return {}
    hits = find_all(data, rx)
    found = {}
    if not hits:
        return found
    with open(path, "r+b") if not dry else open(os.devnull, "rb") as f:
        for off, kind, s in hits:
            masked = mask(kind, s)
            if masked == s:
                continue
            found[kind] = found.get(kind, 0) + 1
            if dry:
                continue
            f.seek(off)
            if f.read(len(s)) != s:  # changed under us: leave it for the next run
                continue
            f.seek(off)
            f.write(masked)
    if not dry and found:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    return found


def files(roots, since):
    for r in roots:
        r = os.path.expanduser(r)
        walk = [(os.path.dirname(r), [], [os.path.basename(r)])] if os.path.isfile(r) else os.walk(r)
        for d, _, names in walk:
            for n in names:
                p = os.path.join(d, n)
                if not n.endswith(SUFFIXES):
                    continue
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if os.path.islink(p) or max(st.st_mtime, st.st_ctime) < since:
                    continue
                yield p


def report(result, nfiles, dry):
    """The DM: what was masked, by kind and by root, never a value. File paths stay in the log (stdout): they name
    personal projects, and this goes to the company Slack."""
    total = sum(sum(k.values()) for k in result.values())
    kinds, roots = {}, {}
    for p, k in result.items():
        for kind, n in k.items():
            kinds[kind] = kinds.get(kind, 0) + n
        r = next((r for r in ROOTS if p.startswith(os.path.expanduser(r))), "?")
        roots[r] = roots.get(r, 0) + 1
    lines = ["[세션 기록 비밀값 검사] 비밀값 모양 %d곳을 %s (파일 %d개 중 %d개)" % (
        total, "찾았습니다(가리지 않음)" if dry else "가렸습니다", nfiles, len(result))]
    lines += ["- %s: %d곳" % (k, n) for k, n in sorted(kinds.items(), key=lambda x: -x[1])]
    lines.append("위치:")
    lines += ["- `%s`: 파일 %d개" % (r, n) for r, n in sorted(roots.items(), key=lambda x: -x[1])]
    lines.append("값과 파일 이름은 이 글에 없습니다(회사맥 `~/Library/Logs/transcript-secret-scan.log`). "
                 "새로 나온 토큰이면 재발급할지 요유가 정합니다.")
    return "\n".join(lines)


def run(argv):
    dry, full, slack = "--dry-run" in argv, "--full" in argv, "--no-slack" not in argv and "--dry-run" not in argv
    state = os.path.expanduser(STATE)
    try:
        last = json.load(open(state)).get("last_run", 0)
    except (OSError, ValueError):
        last = 0
    started = time.time()
    rx = matcher(env_values(os.path.expanduser(ENV_DIR)), public_keys(PUBLIC_KEY_REPOS))
    result, n = {}, 0
    for p in files(ROOTS, 0 if full else last - OVERLAP):
        n += 1
        found = scan_file(p, rx, dry)
        if found:
            result[p] = found
    text = report(result, n, dry)
    print(time.strftime("%F %T"), text if result else "[세션 기록 비밀값 검사] 파일 %d개, 비밀값 모양 없음" % n)
    for p in sorted(result):
        print("  %s (%s)" % (p, ", ".join("%s %d" % kv for kv in sorted(result[p].items()))))
    # The files are masked by now, so a failed post would never be found again: the text waits in a file until a post
    # gets through (the next run, or the one after).
    pending = os.path.join(os.path.dirname(state), "pending.txt")
    if slack:
        if result:
            os.makedirs(os.path.dirname(state), exist_ok=True)
            with open(pending, "a", encoding="utf-8") as f:
                f.write(text + "\n\n")
        if os.path.exists(pending):
            import importlib.util
            spec = importlib.util.spec_from_file_location("slack", SLACK_PY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.Slack().post(DM, None, open(pending, encoding="utf-8").read().strip())  # raises: launchd logs it
            os.remove(pending)
    if not dry:
        os.makedirs(os.path.dirname(state), exist_ok=True)
        json.dump({"last_run": started, "files": n, "masked": sum(sum(k.values()) for k in result.values())},
                  open(state, "w"))
    return 0


def selftest():
    import tempfile
    tok = "xoxb-" + "1234567890-" * 3 + "abcdefABCDEF"
    gh = "ghp_" + "aB3dE5fG7hJ9" + "kL1mN2pQ4rS6" + "tU8vW0xY2zA4"
    top, foot = "-----BEGIN " + "PRIVATE KEY-----", "-----END " + "PRIVATE KEY-----"  # split: no key shape in this file
    key = top + "\\n" + "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 8 + "\\n" + foot
    pw = 'Pa55"word-Zx9q'
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "cfg", "svc"))
        open(os.path.join(d, "cfg", "svc", ".env"), "w").write(
            # values go in through %s, so the push guard's generic-key rule sees no key=value here
            "export API_TOKEN='%s'\nPROJECT_ID=miraclenight-f06c6-staging\nSHORT_SECRET=abc123\n"
            "DB_PASSWORD=%s\nWEBHOOK_SECRET=https://example.com/x/Y1z2\nAUTH_MODE=production\n" % ("Qw3rty-Uiop-9876", pw))
        vals = env_values(os.path.join(d, "cfg"))
        assert vals == {pw, "Qw3rty-Uiop-9876"}, "secret-named, long, mixed, quotes off; not a URL, short, a word or an id: %r" % vals
        rx = matcher(vals)
        line = {"type": "tool_result", "content": "token %s and %s\nkey \"%s\" pw %s commit 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
                % (tok, gh, key.replace("\\n", "\n"), pw)}
        raw = (json.dumps(line, ensure_ascii=False) + "\n").encode()
        p = os.path.join(d, "s.jsonl")
        open(p, "wb").write(raw + b'{"type":"user","content":"clean"}\n')
        os.utime(p, (1_000_000_000, 1_000_000_000))
        assert scan_file(p, rx, True) == {"slack_token": 1, "github_token": 1, "private_key": 1, "env_value": 1}
        assert open(p, "rb").read().startswith(raw), "a dry run writes nothing"
        found = scan_file(p, rx, False)
        assert found == {"slack_token": 1, "github_token": 1, "private_key": 1, "env_value": 1}, found
        out = open(p, "rb").read()
        assert len(out) == len(raw) + len(b'{"type":"user","content":"clean"}\n'), "same length"
        rows = [json.loads(l) for l in out.decode().splitlines()]
        c = rows[0]["content"]
        assert tok not in c and gh not in c and pw not in c and "MIIEvQ" not in c, c
        assert "xoxb-****" in c and "ghp_****" in c, "the type prefix stays"
        assert top in c and foot in c, "a key keeps its header and footer"
        assert 'pw ****"*********' in c, "a value is masked whole but for its escaped quote, so the JSON parses"
        assert "commit 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" in c, "a hash is no secret"
        assert rows[1] == {"type": "user", "content": "clean"}
        assert os.stat(p).st_mtime == 1_000_000_000, "mtime is put back"
        assert scan_file(p, rx, False) == {}, "a masked file has nothing left"
        # webhook keeps its ids; Google key and OAuth token keep only their prefix; text beside them is untouched
        wh = "https://hooks.slack.com/services/T02KL7BB1NG/B0ABCDEF12/" + "aBcD1234" * 3
        q2 = os.path.join(d, "r.md")
        open(q2, "w").write("hook %s key AIzaSyB-%s tok ya29.%s end" % (wh, "x1Y2" * 8, "Ab1_Cd2-Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv"))
        assert scan_file(q2, rx, False) == {"slack_webhook": 1, "google_api_key": 1, "google_oauth_token": 1}
        t = open(q2).read()
        assert "hooks.slack.com/services/T02KL7BB1NG/B0ABCDEF12/****" in t and "aBcD1234" not in t, t
        assert "AIza****" in t and "SyB" not in t and "ya29.****" in t and "Ab1" not in t, t
        assert t.startswith("hook https://") and t.endswith(" end"), t
        # Codex review 11/06: a service account JSON printed by a tool escapes its key twice in the transcript; an
        # Ed25519 key is short; an env value can carry an inline comment
        body = "MC4CAQAwBQYDK2VwBCIEIG" + "x7Qk9pL2mN4bV8cZ1sR3tY6uW0eA5dF7hJ9kL2pQ"  # split: this file holds no key
        pem = top + "\n" + body + "\n" + foot + "\n"
        sa = json.dumps({"type": "service_account", "private_key": pem})
        q4 = os.path.join(d, "sa.jsonl")
        open(q4, "w").write(json.dumps({"content": sa}) + "\n" + json.dumps({"content": pem}) + "\n")
        assert scan_file(q4, rx, False) == {"private_key": 2}
        a, b = [json.loads(l)["content"] for l in open(q4)]
        assert "MC4CAQ" not in a + b and json.loads(a)["private_key"].startswith(top + "\n"), a
        open(os.path.join(d, "cfg", "svc", "b.env"), "w").write(
            "CUSTOM_PASSWORD=%s # service password\nQUOTED_SECRET='%s' # keep the # inside\n" % ("Zx8-Cv7Bn6Mq5", "Ab#12cd-EF34gh"))
        v2 = env_values(os.path.join(d, "cfg"))
        assert {"Zx8-Cv7Bn6Mq5", "Ab#12cd-EF34gh"} <= v2 and not any("#" in v and " " in v for v in v2), v2
        # a key committed in a client repo is public, and a documentation value is no secret: both stay
        pub = "AIza" + "SyCPq7rS8tU9vW0xY1zA2bC3dE4fG5hI6jK"  # 35 characters after AIza, as Google's keys
        repo = os.path.join(d, "app")
        os.makedirs(repo)
        open(os.path.join(repo, "GoogleService-Info.plist"), "w").write("<string>%s</string>" % pub)
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        import subprocess
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True)
        assert public_keys([repo, os.path.join(d, "missing")]) == {pub.encode()}
        q3 = os.path.join(d, "p.md")
        doc = "key %s aws %s gh ghp_%s real AKIA%s" % (pub, "AKIA" + "IOSFODNN7EXAMPLE", "x" * 36, "Q7W3E9R2T5Y8U1I4")
        open(q3, "w").write(doc)
        assert scan_file(q3, matcher(vals, {pub.encode()}), False) == {"aws_key": 1}
        assert open(q3).read() == doc.replace("Q7W3E9R2T5Y8U1I4", "*" * 16)
        # incremental: only files changed since the last run, symlinks skipped, suffixes only
        old, new = os.path.join(d, "old.jsonl"), os.path.join(d, "new.jsonl")
        open(old, "w").write("a"); open(new, "w").write("b"); open(os.path.join(d, "bin.sqlite"), "w").write("c")
        os.symlink(new, os.path.join(d, "link.jsonl"))
        os.utime(old, (100, 100))
        picked = set(files([d], time.time() - 60))
        assert new in picked and os.path.join(d, "link.jsonl") not in picked and not any(x.endswith(".sqlite") for x in picked)
        assert old in picked, "ctime counts too: a copy made today of an old file is read (rsync keeps the old mtime)"
        assert set(files([p], 0)) == {p}, "a root may be one file"
        # the report names kinds and files, never a value
        text = report({p: found}, 3, False)
        assert "4곳" in text and "가렸습니다" in text and "slack_token" in text and tok not in text and pw not in text, text
        assert d not in text and "s.jsonl" not in text, "no file path in the DM: %s" % text
        g0 = globals()["ROOTS"]
        globals()["ROOTS"] = [d]
        assert "- `%s`: 파일 1개" % d in report({p: found}, 3, False), "counted by root"
        globals()["ROOTS"] = g0
        assert "찾았습니다(가리지 않음)" in report({p: found}, 3, True)
        # run(): a failed Slack post keeps the DM for the next run, which sends it although it masks nothing new
        g = globals()
        saved = {k: g[k] for k in ("ROOTS", "STATE", "SLACK_PY", "ENV_DIR", "PUBLIC_KEY_REPOS")}
        src, sent = os.path.join(d, "src"), os.path.join(d, "sent.txt")
        os.makedirs(src)
        open(os.path.join(src, "a.jsonl"), "w").write('{"c":"%s"}\n' % tok)
        fake = os.path.join(d, "fake_slack.py")
        open(fake, "w").write(
            "import os\nclass Slack:\n    def post(self, ch, ts, text):\n"
            "        if os.path.exists(%r): raise RuntimeError('channel_not_found')\n"
            "        open(%r, 'a').write(ch + '|' + text + '\\n=\\n')\n" % (os.path.join(d, "fail"), sent))
        g.update(ROOTS=[src], STATE=os.path.join(d, "st", "state.json"), SLACK_PY=fake, ENV_DIR=os.path.join(d, "none"),
                 PUBLIC_KEY_REPOS=[])
        try:
            open(os.path.join(d, "fail"), "w").close()
            try:
                run([])
                raise AssertionError("a failed post must fail the run")
            except RuntimeError:
                pass
            assert not os.path.exists(STATE), "no state: the window is read again"
            assert "slack_token" in open(os.path.join(d, "st", "pending.txt")).read() and tok not in open(os.path.join(d, "st", "pending.txt")).read()
            assert tok not in open(os.path.join(src, "a.jsonl")).read(), "masked before the post"
            os.remove(os.path.join(d, "fail"))
            assert run([]) == 0
            got = open(sent).read()
            assert got.startswith(DM + "|[세션 기록 비밀값 검사] 비밀값 모양 1곳을 가렸습니다") and got.count("\n=\n") == 1, got
            assert not os.path.exists(os.path.join(d, "st", "pending.txt")) and os.path.exists(STATE)
            assert run([]) == 0 and open(sent).read() == got, "nothing new, nothing sent"
        finally:
            g.update(saved)
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        sys.exit(run(sys.argv[1:]))
