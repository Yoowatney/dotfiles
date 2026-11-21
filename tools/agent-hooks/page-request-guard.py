#!/usr/bin/env python3
"""PreToolUse rider: an explainer page is written only when 요유's latest message asks for one.

gcp-account-guard.py runs it for Claude and Codex alike. 요유 2026-11-18: "html은 내가 html 만들라고할때만 해주면 좋겠음". Until
then yoyoo-share-guard.py sent a long reply to a page at Stop and this refused only a short question (#60).
Cases: page-request-guard.test.py.
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import importlib.util
import json
import os
import re
import shlex
import sys
import threading
import time
import unicodedata

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
REASON = ("page-on-request: 요유가 이번 메시지에서 HTML이나 설명 페이지를 요청하지 않았습니다. 페이지를 만들지 말고 터미널에서 답하세요. "
          "페이지는 요유가 그 메시지에서 HTML이나 설명 페이지를 직접 요청했을 때만 만듭니다.")


# api#1373: a Slack relay (#0, 요유's DM, a thread call) or a main pane's pane-tell with a Slack link is no typed message, so
# its words count only once Slack itself says 요유 wrote them. The relayed text is never read: a session can type anything.
SLACK = os.path.expanduser('~/work/munice/.agents/skills/agent-board/main/slack_approval.py')
PASTED = re.compile(r'^<pasted_content[^>]*>\s*')
RELAY = ('[Slack #0-yoyooyo 요유]', '[Slack DM 요유]', '[Slack 스레드 요유 부름]', '[pane-tell: ')
PLACE = re.compile(r'글 ([CDG][A-Z0-9]{6,})/(\d{10}\.\d{6})|slack\.com/archives/([CDG][A-Z0-9]{6,})/p(\d{10})(\d{6})')
WINDOW = 6 * 3600  # from the post; ponytail: a longer job whose page comes later is refused once and 요유 says 페이지
READ_SECONDS = 4  # the hook has 10 s and a late answer would let the write through


ID, STAMP = r'[CDG][A-Z0-9]{6,}', r'\d{10}\.\d{6}'
CALL = '[Slack 스레드 요유 부름]'
CALL_SHAPE = re.compile(r'\(스레드 %s/%s, 글 (%s)/(%s)\) 요유 글:' % (ID, STAMP, ID, STAMP))
# The tails main-channel.py puts after 요유's words. Words and quoted parents are free text and may hold a look-alike, so
# every candidate is a claim that slack_posts checks against the post itself.
TAILS = (re.compile(r'(?=\(ts %s, 글 (%s)/(%s)\)\s*$)' % (STAMP, ID, STAMP)),
         re.compile(r"(?=\(글 (%s)/(%s), 스레드 %s: '[^\n]*'\)\s*$)" % (ID, STAMP, STAMP)))
MARK = re.compile(r'\[pane-tell: [^\n]*?확인하세요\]\s*')  # the mark pane-tell puts before every message it types


def places(text: str) -> list:
    """One list of Slack places (channel, ts, claim) per post a prompt names, oldest first. pane-tell may carry several
    messages in one prompt, and one message several links; only the latest post Slack confirms counts. A link claims nothing.
    A Slack relay claims that its text before the tail is its label and the post's words."""
    groups = []
    for part in re.split(r'\n\n(?=\[pane-tell: )', PASTED.sub('', text, 1)):
        mark = MARK.match(part)
        body = part[mark.end():] if mark else part
        if body.startswith('[Slack') and not body.startswith(CALL):
            found = [(m[1], m[2], body[:m.start()].rstrip()) for shape in TAILS for m in shape.finditer(body)]
            groups += [sorted(set(found), key=lambda place: place[1])] if found else []  # one relay: the candidates are one group
            continue
        if body.startswith(CALL):
            m = CALL_SHAPE.search(body)
            found = [(m[1], m[2], None)] if m else []
        elif mark:
            found = [(m[1] or m[3], m[2] or '%s.%s' % (m[4], m[5]), None) for m in PLACE.finditer(body)]
        else:
            found = []
        groups += [[place] for place in sorted(set(found), key=lambda place: place[1])]  # one post each, the newest last
    return groups


def slack_posts(found: list, wait: float = READ_SECONDS) -> list:
    """The words of the posts at `found` (channel, ts, claim) that 요유 typed in the Slack app within WINDOW, as 요유 reads them.
    Anything else, an unreadable Slack included, gives no words. slack_approval.py is the module the GCP guard trusts."""
    box = []

    def read():
        try:
            module = load(SLACK)
            slack, now = module.slack_mod.Slack(), time.time()
            for channel, ts, claim in found:
                try:
                    m = module.read_one(slack, channel, ts)
                except Exception:
                    continue  # one post the bot cannot read does not hide the others
                # A look-alike tail inside the words or the quoted parent names a post whose words the relay does not start with.
                # ponytail: a post edited after it was relayed fails this too, and an earlier typed request then stands.
                if claim is not None and m and not claim.startswith(claim.split('] ', 1)[0] + '] ' + module.relay_body(dict(m, files=None))):
                    continue  # (attachments add lines after the words, so the claim only has to start with them)
                if (m and m.get('user') == module.YOYOO and not m.get('bot_id') and m.get('subtype') in (None, 'thread_broadcast', 'file_share')
                        and m.get('client_msg_id') and -module.SKEW <= now - float(m['ts']) <= WINDOW):
                    box.append(module.displayed(m.get('text') or ''))
        except BaseException:
            pass
    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(wait)
    return list(box)


def load(name: str):
    spec = importlib.util.spec_from_file_location(name[:-3].replace('-', '_'), os.path.join(HERE, name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unrequested(payload: dict) -> bool:
    """The latest human turn asks for no page. Use that turn, never a tool argument or an assistant's summary.

    ponytail: the latest turn alone, so a follow-up that names no page ("색만 바꿔줘") is refused once and 요유 says 페이지.
    Missing/unreadable context is unknown and passes. Do not reuse the GCP grant
    parser: notifications terminate grants there but must not erase this request.
    """
    records = load('yoyoo-share-guard.py').records  # only once a page write is seen: most calls never load it
    latest, chain = '', []  # chain: the places each relay since the last typed message names, oldest first
    try:
        with open(payload.get('transcript_path') or '', encoding='utf-8') as stream:
            for raw in stream:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get('isMeta') or entry.get('isSidechain') or entry.get('isCompactSummary'):
                    continue
                origin = entry.get('sourceToolAssistantUUID') or entry.get('sourceToolUseID')
                if origin:
                    continue
                attachment = entry.get('attachment') if isinstance(entry.get('attachment'), dict) else {}
                provenance = attachment if entry.get('type') == 'attachment' else entry
                source = provenance.get('origin')
                if isinstance(source, dict) and source.get('kind') not in (None, 'human'):
                    continue
                if attachment.get('commandMode') == 'bash':
                    continue
                item = next(records([raw]), None)
                if not item or item[0] != 'in':
                    continue
                text = item[1].strip()
                relay = PASTED.sub('', text, 1)
                if relay.startswith(RELAY):  # a relay with no place too: a pasted_content wrapper hid its '[' from the skip below
                    chain += places(relay)
                    continue
                if text.startswith(('<task-notification', '<system-reminder', '[', '# AGENTS.md', '<environment_context')):
                    continue
                latest, chain = unicodedata.normalize('NFC', text), []  # words 요유 typed count alone, whatever links they hold
    except (OSError, TypeError):
        return False
    deadline = time.monotonic() + READ_SECONDS
    for found in reversed(chain[-8:]):  # the latest relay Slack confirms wins; a report that links a bot's post does not erase an earlier request
        # ponytail: eight reports after the request push it out, and the shared 4 s read budget ends first anyway
        posts = slack_posts(found, max(deadline - time.monotonic(), 0))
        if posts:
            latest = unicodedata.normalize('NFC', '\n'.join(posts))
            break
    else:
        if chain and not latest:
            return True  # relays Slack does not confirm, and no typed message before them: no request
    # Quoting a request or saying not to write HTML is not a direct request.
    request = re.sub(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’', '', latest)
    page = r'(?:html|설명\s*페이지|웹\s*페이지|explainer\s*page|페이지)'
    verb = r'(?:만들어|써\s*줘|작성해|설명해|정리해|보여\s*(?:줘|주)|답해|답하|해\s*(?:줘|주)|수정해|고쳐|부탁|주세요|표현해|그려\s*(?:줘|주|줄)|보여\s*줄|줄래|줄\s*수|주실)'  # 2026-11-21: 표현해줄래 was a request the list missed
    # A refusal of the page itself, close to the page word and on the next line too ("HTML 페이지\n만들지 말고", "HTML로 자세히
    # 정리하지 말고", "HTML 말고"). A limit on the page ("페이지는 공개하지 말고", "고객 식별자를 포함하지 말아") is no refusal.
    refuse = r'(?:(?:만들|쓰|작성하|정리하|보여\s*주|답하|해\s*주)지\s*(?:말|마|않)|필요\s*없)'
    negative = (re.search(page + r'[^.!?]{0,12}' + refuse, request, re.I)
                or re.search(page + r'\s*(?:은|는|으로|로)?\s*말고', request, re.I)
                or re.search(r"\b(?:do\s+not|don't|no|without)\b[^.!?]{0,24}" + page, request, re.I))
    # The page word, then a request verb before the sentence ends (. ! ?), across line breaks and at any length: since 2026-11-18
    # no length rule lets a long request through. ponytail: wording, not meaning, after seven Codex review rounds of new phrasings.
    # A wrong refusal blocks a page 요유 asked for, while the rules already keep an unasked page out, so this errs toward letting
    # through: HTML named as the topic ("HTML과 CSS의 차이를 설명해줘", "write a test for HTML parsing") passes too.
    # The first page word of a sentence stands for all of them, so one scan per sentence: "HTML " * 20000 stays linear.
    def then(first, second):
        return any((m := re.search(first, s, re.I)) and re.search(second, s[m.end():], re.I) for s in re.split(r'[.!?]', request))
    positive = then(page, verb)
    english = then(r'\b(?:create|write|make|build|show)\b', r'\b(?:html|explainer)\b')
    english = english or re.search(r'\b(?:explain|summarize)\s+in\s+' + page, request, re.I)
    return bool(latest) and (bool(negative) or not (positive or english))


def writes_explainer(payload: dict) -> bool:
    """Recognize the existing page CLI and ordinary direct writers, not arbitrary
    hidden scripts. Inspect destinations, not quoted documentation or source code.
    """
    inp = payload.get('tool_input') or {}
    tool = payload.get('tool_name')
    cwd = payload.get('cwd') or os.getcwd()
    home = os.path.expanduser('~')
    root = os.path.realpath(os.path.join(home, 'explain'))

    def page_path(path, base=cwd):
        path = path.replace('${HOME}', home).replace('$HOME', home)
        path = os.path.realpath(os.path.join(base, os.path.expanduser(path)))
        return os.path.dirname(path) == root and path.lower().endswith('.html')

    if tool in ('Write', 'Edit', 'MultiEdit'):
        return page_path(str(inp.get('file_path') or ''))
    if tool == 'apply_patch':
        patch = str(inp.get('input') or inp.get('patch') or inp.get('command') or '')
        return any(page_path(path) for path in re.findall(r'^\*\*\* (?:Add File|Update File|Move to): (.+)$', patch, re.M))
    if tool != 'Bash':
        return False
    raw = str(inp.get('command') or '')
    if not any(word in raw for word in ('explain', '.html')) and os.path.realpath(cwd) != root:
        return False

    # Reuse the Actions guard's substitution reader so quoted documentation
    # is not mistaken for a command. Heredoc data is removed below.
    parser = load('actions-run-guard.py')

    def shell(command, base, depth=0):
        if depth > 6:
            return False
        if any(shell(inner, base, depth + 1) for inner in parser.substitutions(command)):
            return True
        heredoc = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[\t ]*\2\b", re.S)
        for match in heredoc.finditer(command):
            head = command[:match.start()].rsplit('\n', 1)[-1]
            if re.search(r'\b(?:bash|sh|zsh)\s*$', head) and shell(match[4], base, depth + 1):
                return True
            if re.search(r'\b(?:python3?|node|ruby|perl)\b', head) and script(match[4], base):
                return True
        command = heredoc.sub(lambda match: match[3] + '\n', command)
        lexer = shlex.shlex(command.replace('\\\n', ''), posix=True, punctuation_chars=';|&()\n<>')
        lexer.whitespace = ' \t\r'
        lexer.wordchars += '{}'
        try:
            words = list(lexer)
        except ValueError:
            return False
        segments, current = [], []
        for word in words + [';']:
            if word and set(word) <= set(';|&()\n'):
                segments.append(current)
                current = []
            else:
                current.append(word)
        for words in segments:
            if not words:
                continue
            while words and (re.match(r'^\w+=', words[0]) or words[0] in ('env', 'command', 'exec')):
                words = words[1:]
            if not words:
                continue
            if words[:2] == ['uv', 'run']:
                words = words[2:]
                while words and words[0].startswith('-'):
                    option = words.pop(0)
                    if option in ('--project', '--directory', '--with', '--python') and words:
                        words.pop(0)
                if not words:
                    continue
            name = os.path.basename(words[0])
            if name in ('bash', 'sh', 'zsh'):
                for i, word in enumerate(words[:-1]):
                    if word.startswith('-') and not word.startswith('--') and 'c' in word:
                        if shell(words[i + 1], base, depth + 1):
                            return True
            if name == 'cd' and len(words) > 1:
                base = os.path.realpath(os.path.join(base, os.path.expanduser(words[1].replace('$HOME', home))))
            if name == 'explain-page' or name in ('python', 'python3'):
                if any(os.path.basename(w) == 'explain-page' and i + 1 < len(words) and words[i + 1] in ('write', 'publish')
                       for i, w in enumerate(words)):
                    return True
            if any(w in ('>', '>>') and i + 1 < len(words) and page_path(words[i + 1], base) for i, w in enumerate(words)):
                return True
            if name in ('tee', 'cp', 'mv', 'install', 'rsync', 'ln') and len(words) > 1:
                targets = words[1:] if name == 'tee' else words[-1:]
                if any(page_path(w, base) for w in targets):
                    return True
                if name != 'tee':
                    destination = words[-1]
                    for flag in ('-t', '--target-directory'):
                        if flag in words and words.index(flag) + 1 < len(words):
                            destination = words[words.index(flag) + 1]
                    destination = destination.replace('${HOME}', home).replace('$HOME', home)
                    if (os.path.realpath(os.path.join(base, os.path.expanduser(destination))) == root
                            and any(w.lower().endswith('.html') for w in words[1:])):
                        return True
            if name in ('python', 'python3', 'node', 'ruby', 'perl') and script(' '.join(words[1:]), base):
                return True
        return False

    def script(code, base):
        return (bool(re.search(r'write_text|write_bytes|writeFile|open\([^\n]*[\'\"][wax][bt+]*[\'\"]', code))
                and '.html' in code and ('explain/' in code or os.path.realpath(base) == root))

    return shell(raw, cwd)


def check(hook_input: dict, env=None) -> str | None:
    return REASON if writes_explainer(hook_input) and unrequested(hook_input) else None
