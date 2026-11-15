#!/usr/bin/env python3
"""Record each scheduled invocation and enqueue one alert per bad streak."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import uuid


def load_inbox():
    spec = importlib.util.spec_from_file_location('job_health_inbox', Path(__file__).with_name('main-alert-inbox.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_root():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'scheduled-job-health'


def job_key(label):
    return hashlib.sha256(label.encode()).hexdigest()[:24]


def save(path, data):
    fd, name = tempfile.mkstemp(prefix='.health-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_state(path):
    if not path.exists():
        return {'version': 1, 'streak': 0, 'episode': None, 'active': None, 'outbox': [], 'recent': []}
    data = json.loads(path.read_text())
    valid = isinstance(data, dict) and set(data) == {
        'version', 'streak', 'episode', 'active', 'outbox', 'recent'}
    valid = valid and data['version'] == 1 and type(data['streak']) is int and data['streak'] >= 0
    valid = valid and ((data['streak'] == 0 and data['episode'] is None) or
                      (data['streak'] > 0 and isinstance(data['episode'], str) and
                       re.fullmatch(r'[0-9a-f]{32}', data['episode']) is not None))
    valid = valid and (data['active'] is None or
                      (isinstance(data['active'], str) and
                       re.fullmatch(r'outcome-[0-9a-f]{32}', data['active']) is not None))
    valid = valid and isinstance(data['recent'], list) and len(data['recent']) <= 2
    valid = valid and all(isinstance(item, dict) and set(item) == {'outcome', 'exit_code'} and
                         item['outcome'] in ('success', 'failed', 'skipped', 'interrupted') and
                         (item['exit_code'] is None or type(item['exit_code']) is int)
                         for item in data['recent'])
    valid = valid and isinstance(data['outbox'], list)
    valid = valid and all(isinstance(item, dict) and set(item) == {'event_id', 'title', 'body'} and
                         all(isinstance(value, str) and value for value in item.values())
                         for item in data['outbox'])
    if not valid:
        raise ValueError('invalid job state; refusing overwrite')
    return data


def finish(data, label, outcome, code):
    data['active'] = None
    data['recent'] = (data['recent'] + [{'outcome': outcome, 'exit_code': code}])[-2:]
    if outcome == 'success':
        data['streak'], data['episode'] = 0, None
        return
    if data['streak'] == 0:
        data['episode'] = uuid.uuid4().hex
    data['streak'] += 1
    if data['streak'] == 2:
        data['outbox'].append({
            'event_id': 'job-health-' + job_key(label) + '-' + data['episode'],
            'title': '정기 작업이 두 번 연속 실패하거나 건너뜀',
            'body': json.dumps({'job': label, 'consecutive_bad_runs': 2, 'results': data['recent']}, ensure_ascii=True)})


def flush(path, data):
    while data['outbox']:
        alert = data['outbox'][0]
        try:
            load_inbox().enqueue(os.environ, **alert)
        except (OSError, ValueError, KeyError, TypeError):
            print('scheduled-job-health: inbox unavailable; alert retained for retry', file=sys.stderr)
            return
        data['outbox'].pop(0)
        save(path, data)


def snapshot(logs):
    result = []
    for log in logs:
        path = Path(log['path']).expanduser()
        try:
            info = path.stat()
            with path.open('rb') as stream:
                stream.seek(max(0, info.st_size - 256))
                tail = stream.read(256)
            mark = (info.st_dev, info.st_ino, info.st_size, tail)
        except FileNotFoundError:
            mark = None
        result.append((log, path, mark))
    return result


def log_outcome(logs):
    outcome = 'success'
    for config, path, mark in logs:
        try:
            with path.open('rb') as stream:
                info = os.fstat(stream.fileno())
                offset = mark[2] if mark and (info.st_dev, info.st_ino) == mark[:2] and info.st_size >= mark[2] else 0
                if offset:
                    stream.seek(max(0, offset - 256))
                    if stream.read(min(offset, 256)) != mark[3]:
                        offset = 0  # Truncation followed by regrowth on the same inode.
                stream.seek(offset)
                # Bound work; only complete trailing lines can describe the terminal result.
                if info.st_size - offset > 1024 * 1024:
                    stream.seek(-1024 * 1024, os.SEEK_END)
                    stream.readline()
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            if config.get('skip_pattern') and re.search(config['skip_pattern'], line):
                outcome = 'skipped'
            if config.get('failure_pattern') and re.search(config['failure_pattern'], line):
                outcome = 'failed'
            if config.get('success_pattern') and re.search(config['success_pattern'], line):
                outcome = 'success'
    return outcome


def child(command, marker=None):
    env = dict(os.environ)
    if marker:
        env['SCHEDULED_JOB_OUTCOME_FILE'] = str(marker)
    try:
        proc = subprocess.Popen(command, env=env)
    except OSError:
        print('scheduled-job-health: original command could not start', file=sys.stderr)
        return 127
    previous = {}
    def forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass
    try:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[signum] = signal.signal(signum, forward)
        code = proc.wait()
        return code if code >= 0 else 128 - code
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def locked(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def run(config):
    label, command = config['label'], config['command']
    if not isinstance(label, str) or not label or len(label) > 256 or not isinstance(command, list) \
            or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError('invalid job specification')
    root = state_root() / 'runs' / job_key(label)
    code = None
    try:
        with locked(root):
            path = root / 'state.json'
            data = read_state(path)
            if data['active'] is not None:
                finish(data, label, 'interrupted', None)
                save(path, data)
            flush(path, data)  # An outstanding alert must be attempted before the next invocation.
            marker = root / ('outcome-' + uuid.uuid4().hex)
            data['active'] = marker.name
            save(path, data)
            logs = snapshot(config.get('logs', []))
            code = child(command, marker)
            try:
                with marker.open() as stream:
                    explicit = stream.read(64).strip()
            except FileNotFoundError:
                explicit = None
            finally:
                marker.unlink(missing_ok=True)
            outcome = 'failed' if code else (explicit if explicit in ('skipped', 'failed') else log_outcome(logs))
            finish(data, label, outcome, code)
            save(path, data)
            flush(path, data)
            return code
    except BlockingIOError:
        # A concurrent wrapper still owns this attempt; never run a duplicate command.
        print('scheduled-job-health: invocation already active', file=sys.stderr)
        return 75
    except (OSError, ValueError, KeyError, TypeError):
        print('scheduled-job-health: monitoring unavailable; original exit status preserved', file=sys.stderr)
        return child(command) if code is None else code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run'])
    parser.add_argument('--spec', required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.spec.read_text()))


if __name__ == '__main__':
    sys.exit(main())
