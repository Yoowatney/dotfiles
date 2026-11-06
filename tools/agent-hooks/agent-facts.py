#!/usr/bin/env python3
"""Writes ~/.local/state/agent-facts.md: facts sessions looked up again and again, each section with the time it was read, so
a session reads one file instead of running the same lookups (api#1051). launchd runs it hourly on 회사맥 and 집맥
(com.yoyoo.agent-facts), where the sessions run; 회사서버맥 runs none. ~/.codex/AGENTS.md tells sessions to read it first.

In the 3 days to 11/07, watch job and code reads left out: GCP account checks in 44 sessions, herdr usage 38, pane-report
files 34, release tags 27. Nothing secret or about customers goes in: account names, tags and commands only, never a token,
key or secret value. A lookup that fails says so in its section, and the file is written anyway.

    agent-facts.py              write the file
    agent-facts.py --selftest
Env: AGENT_FACTS_OUT (the file).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
OUT = os.path.expanduser("~/.local/state/agent-facts.md")
REPOS = (("nightly_api", 10), ("cloud-functions", 100), ("monaural_beat_recommend", 5), ("nightly_ios", 3), ("nightly_and", 3))
HERDR = """- 밑 칸 목록: `herdr --session workers agent list` · 기본 서버 칸 목록: `env -u HERDR_SOCKET_PATH -u HERDR_SESSION herdr pane list`
- 이 칸: `herdr pane get "$HERDR_PANE_ID"` · 밑 칸 화면 읽기: `herdr --session workers agent read <id>`
- 다른 칸에 글 보내기: `pane-tell <id|이름 일부> "<글>"`, 다른 서버의 칸은 `pane-tell workers/<id> "<글>"`, 칸 고르기: `pane-tell --list`
- 새 칸: `pane-spawn "<일 이름>" "<지시>"`(인자 없이 실행하면 사용법) · 칸 이름: `pane-label "<이슈> <2-4 단어>"`, `pane-label --hold`·`--free`"""
REPORTS = """- 하루 파일: `~/.local/state/pane-reports/YYYY-MM-DD.md`, 항목 머리는 `## HH:MM:SS <칸 주소> <칸 이름>`입니다.
- 항목 하나만 읽기: `grep -n '^## HH:MM:SS' <파일>`로 줄 번호를 찾고 그 줄부터 읽습니다. 파일 전체를 읽지 않습니다."""


def run(*cmd):
    """(stdout, "") or ("", why it failed). Only the exit code or error kind: a tool's error text can quote anything."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", type(e).__name__
    return (r.stdout, "") if r.returncode == 0 else ("", "exit %d" % r.returncode)


def gcp():
    out, why = run("gcloud", "config", "configurations", "list", "--format=json")
    try:
        rows = json.loads(out) if not why else None
    except ValueError:
        rows, why = None, "출력이 JSON이 아님"
    if rows is None:
        return "- 조회 실패(%s): `gcloud config configurations list`로 직접 보세요." % why
    def munice(account):  # gomgom and other accounts of 요유's stay out of a munice session's file
        return account.endswith(("@munice.com", ".iam.gserviceaccount.com"))
    def one(c):
        core = (c.get("properties") or {}).get("core") or {}
        if not munice(core.get("account") or ""):
            return None
        # `configurations list` leaves out auth/, the one setting that says who a command really runs as (the agent SA, tf#1005)
        try:
            props = json.loads(run("gcloud", "config", "configurations", "describe", c.get("name") or "", "--format=json")[0] or "{}")
            runs_as = ((props.get("properties") or {}).get("auth") or {}).get("impersonate_service_account") or ""
        except ValueError:
            runs_as = ""
        return "`%s`(로그인 %s, %s프로젝트 %s)" % (c.get("name"), core["account"], "명령은 %s으로 실행, " % runs_as if runs_as else "",
                                            core.get("project") or "없음")
    active = [one(c) or "`%s`(munice 밖 계정이라 적지 않음)" % c.get("name") for c in rows if c.get("is_active")]
    rest = [x for x in (one(c) for c in rows if not c.get("is_active")) if x]
    lines = ["- 활성 구성: %s" % (active[0] if active else "없음")]
    if rest:
        lines.append("- 다른 구성: " + ", ".join(rest))
    lines.append("- gcloud 설정 파일 기준입니다. 한 명령에만 다른 계정을 쓰는지(`--configuration`, `CLOUDSDK_*`)는 그 명령에서 봅니다.")
    return "\n".join(lines)


def releases():
    lines = []
    for repo, n in REPOS:
        out, why = run("gh", "release", "list", "-R", "munice-dev/" + repo, "-L", str(n), "--json", "tagName,isPrerelease,publishedAt")
        try:
            rows = json.loads(out) if not why else None
        except ValueError:
            rows, why = None, "출력이 JSON이 아님"
        if rows is None:
            lines.append("- %s: 조회 실패(%s)" % (repo, why))
            continue
        groups = {}  # a cloud-functions tag is <service>-v<version>; one group elsewhere
        for r in rows:  # newest first, as gh lists them
            tag = r.get("tagName") or ""
            m = re.match(r"(.+)-v\d", tag)
            g = groups.setdefault(m.group(1) if m else "", {})
            g.setdefault("prerelease" if r.get("isPrerelease") else "release",
                         "%s (%s)" % (tag[m.end(1) + 1:] if m else tag, (r.get("publishedAt") or "")[:10]))
        parts = []
        for name, g in groups.items():
            shown = " · ".join("%s %s" % (k, g[k]) for k in ("release", "prerelease") if k in g)
            parts.append("%s%s" % ("`%s` " % name if name else "", shown))
        lines.append("- %s: %s" % (repo, "; ".join(parts) or "릴리스 없음"))
    lines.append("- gh가 준 prerelease 표시를 그대로 적었습니다. 어느 버전이 배포돼 있는지는 각 리포 AGENTS.md의 배포 방식으로 확인합니다.")
    return "\n".join(lines)


def write(path=OUT, now=None):
    """Each section's time is when it was read; one file replaces the last whole, so a reader never sees half of it."""
    stamp = lambda: time.strftime("%Y-%m-%d %H:%M", time.localtime(now or time.time()))
    herdr = (run("herdr", "--version")[0].strip() or "버전 조회 실패")
    sections = [("GCP 계정", gcp()), ("최근 릴리스 태그(gh release list)", releases()), ("herdr 자주 쓰는 명령(%s)" % herdr, HERDR),
                ("칸 보고 파일", REPORTS)]
    text = ["# 에이전트 사실 파일 (확인 %s, agent-facts.py가 1시간마다 다시 씀)" % stamp(), "",
            "여러 세션이 되풀이해 조회하던 값입니다. 확인 시각이 질문에 비해 오래됐거나 '조회 실패'면 직접 조회하세요.", ""]
    for title, body in sections:
        text += ["## %s (확인 %s)" % (title, stamp()), body, ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".agent-facts.")  # mode 0600: account names
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(text))
    os.replace(tmp, path)


def selftest():
    with tempfile.TemporaryDirectory() as d:
        bin_, log = os.path.join(d, "bin"), os.path.join(d, "calls")
        os.makedirs(bin_)
        configs = [{"name": "munice", "is_active": True, "properties": {"core": {"account": "y@munice.com", "project": "p"}}},
                   {"name": "munice-admin", "is_active": False, "properties": {"core": {"account": "y@munice.com"}}},
                   {"name": "home", "is_active": False, "properties": {"core": {"account": "someone@gmail.com", "project": "p"}}}]
        described = {"name": "munice", "properties": {"core": {"account": "y@munice.com"}, "auth": {"impersonate_service_account": "agent@p.iam.gserviceaccount.com"}}}
        cf = [{"tagName": "slack-admin-bot-v1.31.0", "isPrerelease": False, "publishedAt": "2026-11-06T06:33:22Z"},
              {"tagName": "stripe-webhook-v1.2.1", "isPrerelease": True, "publishedAt": "2026-11-06T14:02:04Z"},
              {"tagName": "slack-admin-bot-v1.30.0", "isPrerelease": False, "publishedAt": "2026-11-06T06:01:09Z"}]
        api = [{"tagName": "v1.26.0-rc.3", "isPrerelease": True, "publishedAt": "2026-11-06T01:00:00Z"},
               {"tagName": "v1.25.1", "isPrerelease": False, "publishedAt": "2026-11-05T01:00:00Z"}]
        for name, body in (("gcloud", 'case "$3 $4" in "list "*) echo \'%s\' ;; "describe munice") echo \'%s\' ;; *) echo "{}" ;; esac'
                            % (json.dumps(configs), json.dumps(described))),
                           ("gh", 'case "$4" in munice-dev/cloud-functions) echo \'%s\' ;; munice-dev/nightly_api) echo \'%s\' ;; '
                                  'munice-dev/nightly_ios) exit 1 ;; *) echo "[]" ;; esac' % (json.dumps(cf), json.dumps(api))),
                           ("herdr", "echo 'herdr 0.9.1'")):
            with open(os.path.join(bin_, name), "w") as f:
                f.write('#!/bin/sh\necho "%s $*" >> %s\n%s\n' % (name, log, body))
            os.chmod(os.path.join(bin_, name), 0o755)
        os.environ["PATH"] = bin_ + ":/usr/bin:/bin"
        out = os.path.join(d, "state", "agent-facts.md")
        write(out, now=1793851200)  # 2026-11-05 13:00 KST
        text = open(out).read()
        assert "확인 2026-11-05 13:00" in text, text
        assert "- 활성 구성: `munice`(로그인 y@munice.com, 명령은 agent@p.iam.gserviceaccount.com으로 실행, 프로젝트 p)" in text, text
        assert "- 다른 구성: `munice-admin`(로그인 y@munice.com, 프로젝트 없음)" in text, "the other configurations too"
        assert "gmail" not in text and "`home`" not in text, "an account outside munice stays out"
        # 집맥: the active configuration is not munice's, and munice's still says whom it runs as
        configs[0]["is_active"], configs[2]["is_active"] = False, True
        with open(os.path.join(bin_, "gcloud"), "w") as f:
            f.write('#!/bin/sh\ncase "$3 $4" in "list "*) echo \'%s\' ;; "describe munice") echo \'%s\' ;; *) echo "{}" ;; esac\n'
                    % (json.dumps(configs), json.dumps(described)))
        write(out, now=1793851200)
        text = open(out).read()
        assert "- 활성 구성: `home`(munice 밖 계정이라 적지 않음)" in text and "gmail" not in text, text
        assert "`munice`(로그인 y@munice.com, 명령은 agent@p.iam.gserviceaccount.com으로 실행, 프로젝트 p)" in text, text
        assert "- nightly_api: release v1.25.1 (2026-11-05) · prerelease v1.26.0-rc.3 (2026-11-06)" in text, text
        assert "`slack-admin-bot` release v1.31.0 (2026-11-06); `stripe-webhook` prerelease v1.2.1 (2026-11-06)" in text, "newest per service"
        assert "- nightly_ios: 조회 실패(exit 1)" in text and "- monaural_beat_recommend: 릴리스 없음" in text, "a failed lookup is said, the rest still written"
        assert "herdr 0.9.1" in text and "agent read <id>" in text and "grep -n '^## HH:MM:SS'" in text, text
        calls = open(log).read()
        assert not re.search(r"secrets|print-access-token|auth (?:login|activate)|config set", calls), "nothing secret is ever read: " + calls
        # Failure flow: gcloud missing, gh down. The file is still written, with each failure in its section.
        for name in ("gcloud", "gh"):
            os.unlink(os.path.join(bin_, name))
        write(out, now=1793854800)
        text = open(out).read()
        assert "확인 2026-11-05 14:00" in text and "- 조회 실패(FileNotFoundError)" in text and "- nightly_api: 조회 실패(FileNotFoundError)" in text, text
        assert oct(os.stat(out).st_mode & 0o777) == "0o600", "only 요유 reads it: account names"
        assert not [p for p in os.listdir(os.path.dirname(out)) if p != "agent-facts.md"], "no temp file left"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        write(os.environ.get("AGENT_FACTS_OUT") or OUT)
