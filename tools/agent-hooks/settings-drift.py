#!/usr/bin/env python3
"""Run verify.sh on every Mac once a day and alert the main inbox about checks that newly fail.

Targets: this Mac, plus the first line of ~/.config/herdr/remote-host and ~/.config/munice/portable-host.
A failure is alerted once, when it first appears; it alerts again only after a run where it passed.
Runs only on 세션맥, the Mac with portable-host: init.sh links this job's plist on every Mac.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

COMMAND = 'scutil --get ComputerName; bash ~/.dotfiles/verify.sh'
HOST_FILES = ('.config/herdr/remote-host', '.config/munice/portable-host')
STALE_SECONDS = 7 * 86400
RUN_FAILED = '점검 실행 실패'
BODY_BYTES = 2500  # main-alert-inbox.py takes at most 3000


def load_inbox():
    spec = importlib.util.spec_from_file_location('settings_drift_inbox', Path(__file__).with_name('main-alert-inbox.py'))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def targets():
    hosts = ['local']
    for rel in HOST_FILES:
        try:
            host = (Path.home() / rel).read_text().splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def run(host):
    """Exit code and stdout; 255 means ssh could not reach the host (asleep, offline)."""
    if host == 'local':
        cmd = [os.environ.get('SETTINGS_DRIFT_BASH', '/bin/bash'), '-c', COMMAND]
    else:
        cmd = [os.environ.get('SETTINGS_DRIFT_SSH', 'ssh'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, COMMAND]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=900)
    except subprocess.TimeoutExpired:
        return 124, ''
    return r.returncode, r.stdout


def check(host, target, now):
    """Update target in place and return the alert lines for what is new."""
    code, out = run(host)
    lines = out.splitlines()
    if code == 255:
        since = target['last_ok'] or target['first_seen']
        if now - since > STALE_SECONDS and not target['stale_alerted']:
            target['stale_alerted'] = True
            return [f"{target['name']}: {(now - since) // 86400}일째 점검하지 못함"]
        return []
    if lines and lines[0].strip() and not lines[0].startswith('VERIFY-'):
        target['name'] = lines[0].strip()
    if 'VERIFY-DONE' not in lines:  # verify.sh stopped early: its fail list is incomplete, keep the old one
        if RUN_FAILED in target['fails']:
            return []
        target['fails'].append(RUN_FAILED)
        return [f"{target['name']}: {RUN_FAILED} (exit {code})"]
    fails = sorted({line[len('VERIFY-FAIL '):] for line in lines if line.startswith('VERIFY-FAIL ')})
    new = [f"{target['name']}: {name}" for name in fails if name not in target['fails']]
    target.update(fails=fails, last_ok=now, stale_alerted=False)
    return new


def alert(new, now, prior):
    body = ''
    for i, line in enumerate(new):
        if len((body + line).encode()) > BODY_BYTES:
            body += f'외 {len(new) - i}개\n'
            break
        body += line + '\n'
    body += '\n그 맥에서 bash ~/.dotfiles/verify.sh 로 확인합니다.'
    # The saved state is part of the ID: a retry after an unsaved run reuses it, a failure back after a fix gets a new one.
    digest = hashlib.sha256((prior + '\n'.join(sorted(new))).encode()).hexdigest()[:16]
    load_inbox().enqueue(os.environ, event_id=f"settings-drift-{digest}-{time.strftime('%Y%m%d', time.localtime(now))}",
                         title=f'맥 설정 차이 {len(new)}개', body=body)


def main():
    if not (Path.home() / HOST_FILES[1]).exists():
        print('skip: no ~/' + HOST_FILES[1] + ', so this is not 세션맥')
        return 0
    path = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'settings-drift/state.json'
    prior = path.read_text() if path.exists() else ''
    state = json.loads(prior) if prior else {}
    now = int(time.time())
    new = []
    for host in targets():
        target = state.setdefault(host, {'name': host, 'first_seen': now, 'last_ok': None, 'fails': [], 'stale_alerted': False})
        found = check(host, target, now)
        print(f"{host}\t{target['name']}\tfails={len(target['fails'])}\tnew={len(found)}")
        new += found
    if new:
        try:
            alert(new, now, prior)
        except Exception as error:  # leave the state unsaved so the next run alerts again
            print(f'alert inbox write failed: {error}', file=sys.stderr)
            return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
