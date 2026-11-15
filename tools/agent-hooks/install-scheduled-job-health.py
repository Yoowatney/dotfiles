#!/usr/bin/python3
"""Inventory, wrap or restore explicitly selected user launchd schedules.

No launchctl calls: review the inventory, apply, then reload the listed jobs.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import stat
import sys
import tempfile

RUNNER = Path(__file__).resolve().with_name('scheduled-job-health.py')


def root():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'scheduled-job-health'


def key(label):
    return hashlib.sha256(label.encode()).hexdigest()[:24]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def profile(plist):
    """Only whole-invocation evidence; ordinary item-level no-ops are successful."""
    args = plist['ProgramArguments']
    paths = [Path(arg) for arg in args if arg.startswith('/')]
    log = plist.get('StandardOutPath') or plist.get('StandardErrorPath')
    names = {path.name for path in paths}
    if 'main-refresh' in names:
        return [{'path': str(Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'orchestrator/refresh.log'),
                 'skip_pattern': r'건너뜀$|메인 칸 파일 없음$|메인 칸을 찾지 못함',
                 'failure_pattern': r'못|실패|인계를 보존함|전달 보류를 유지함',
                 'success_pattern': r'메인 칸 .* (?:새로 시작함|다시 켬)(?: \([^\n]*\))?$'}]
    if 'sync-home.sh' in names and log:
        return [{'path': log.replace('.launchd.log', '.log'), 'skip_pattern': r'\bskip:', 'success_pattern': r'\bok$'}]
    if any(path.name == 'run.sh' and path.parent.name == 'watch' for path in paths) and log:
        return [{'path': log.replace('.launchd.log', '.log'), 'skip_pattern': r'(?:^|\s)offline, skipped$'}]
    if any(path.name == 'run.sh' and path.parent.name == 'dispatch' for path in paths) and log:
        return [{'path': log.replace('.launchd.log', '.log'),
                 'skip_pattern': r'(?:late slot \([^\n]*\)|another dispatch run is going), skipped$'}]
    if 'graphify-refresh.sh' in names and log:
        return [{'path': log, 'failure_pattern': r' update failed:'}]
    return []


def inventory(directory, prefixes):
    jobs, excluded = [], []
    for path in sorted(directory.glob('*.plist')):
        try:
            plist = plistlib.loads(path.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            excluded.append({'plist': str(path), 'reason': 'unreadable plist'})
            continue
        label = plist.get('Label', '')
        if not any(label.startswith(prefix) for prefix in prefixes):
            continue
        if plist.get('Disabled') or isinstance(plist.get('KeepAlive'), dict) or plist.get('KeepAlive') \
                or not (plist.get('StartInterval') or plist.get('StartCalendarInterval')):
            excluded.append({'label': label, 'reason': 'disabled, persistent, or not scheduled'})
            continue
        backup = root() / 'installed' / (key(label) + '.json')
        if backup.exists():
            original = json.loads(backup.read_text())
            source = plistlib.loads(base64.b64decode(original['original']))
        else:
            source = plist
        args = source.get('ProgramArguments')
        if source.get('Program') or not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
            excluded.append({'label': label, 'reason': 'unsupported Program/ProgramArguments; manual review required'})
            continue
        logs = profile(source)
        jobs.append({'label': label, 'plist': str(path), 'command': args, 'logs': logs,
                     'failure_evidence': 'original process exit status',
                     'skip_evidence': 'new whole-job log lines and explicit outcome marker' if logs else 'explicit outcome marker only; silent exit 0 is not distinguishable',
                     'stdout': source.get('StandardOutPath'), 'stderr': source.get('StandardErrorPath'),
                     'schedule': source.get('StartInterval', source.get('StartCalendarInterval'))})
    return jobs, excluded


def reload_commands(label, path):
    domain = 'gui/' + str(os.getuid())
    return [shlex.join(['launchctl', 'bootout', domain + '/' + label]),
            shlex.join(['launchctl', 'bootstrap', domain, str(path)])]


def apply(job):
    path = Path(job['plist'])
    saved = root() / 'installed' / (key(job['label']) + '.json')
    spec = root() / 'specs' / (key(job['label']) + '.json')
    config = {name: job[name] for name in ['label', 'command', 'logs']}
    original = path.read_bytes()
    if saved.exists():
        backup = json.loads(saved.read_text())
        already_wrapped = original == base64.b64decode(backup['wrapped']) and not path.is_symlink()
        unfinished = original == base64.b64decode(backup['original']) and \
            (str(path.readlink()) if path.is_symlink() else None) == backup['symlink']
        if not (already_wrapped or unfinished):
            raise ValueError('installed plist changed; refusing overwrite: ' + str(path))
    else:
        plist = plistlib.loads(original)
        plist['ProgramArguments'] = [sys.executable, '-I', str(RUNNER), 'run', '--spec', str(spec)]
        wrapped = plistlib.dumps(plist)
        backup = {'plist': str(path), 'original': base64.b64encode(original).decode(),
                  'wrapped': base64.b64encode(wrapped).decode(),
                  'mode': stat.S_IMODE(path.stat().st_mode),
                  'symlink': str(path.readlink()) if path.is_symlink() else None}
        write(saved, (json.dumps(backup) + '\n').encode())
    # Freeze original args separately; never interpolate them into a shell command.
    write(spec, (json.dumps(config) + '\n').encode())
    write(path, base64.b64decode(backup['wrapped']))
    return reload_commands(job['label'], path)


def restore(prefixes, directory):
    commands = []
    for saved in sorted((root() / 'installed').glob('*.json')):
        backup = json.loads(saved.read_text())
        path = Path(backup['plist'])
        original = base64.b64decode(backup['original'])
        label = plistlib.loads(original)['Label']
        if path.parent != directory or not any(label.startswith(prefix) for prefix in prefixes):
            continue
        already_restored = (path.is_symlink() and os.readlink(path) == backup['symlink']) \
            if backup['symlink'] is not None else (not path.is_symlink() and path.read_bytes() == original)
        if not already_restored and (path.is_symlink() or path.read_bytes() != base64.b64decode(backup['wrapped'])):
            raise ValueError('installed plist changed; refusing restore: ' + str(path))
        if backup['symlink'] is None:
            write(path, original)
            path.chmod(backup['mode'])
        elif not already_restored:
            temporary = path.with_name('.' + path.name + '.restore-' + str(os.getpid()))
            try:
                temporary.symlink_to(backup['symlink'])
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        saved.unlink()
        (root() / 'specs' / (key(label) + '.json')).unlink(missing_ok=True)
        commands.extend(reload_commands(label, path))
    return commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents-dir', type=Path, default=Path.home() / 'Library/LaunchAgents')
    parser.add_argument('--prefix', action='append', required=True, help='explicit owned label prefix; repeatable')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if any(not prefix or prefix == '.' for prefix in args.prefix):
        parser.error('provide a nonempty owned label prefix')
    lock = None
    try:
        if args.apply or args.restore:
            root().mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = (root() / 'install.lock').open('a')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.restore:
            result = {'reload_commands': restore(args.prefix, args.agents_dir)}
        else:
            jobs, excluded = inventory(args.agents_dir, args.prefix)
            commands = []
            if args.apply:
                for job in jobs:
                    commands.extend(apply(job))
            # Specs retain the complete argv privately; inventory never prints argument values.
            public_jobs = [{**{k: v for k, v in job.items() if k != 'command'},
                            'program': job['command'][0]} for job in jobs]
            result = {'jobs': public_jobs, 'excluded': excluded, 'reload_commands': commands}
    finally:
        if lock:
            lock.close()
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('install-scheduled-job-health: ' + str(error), file=sys.stderr)
        sys.exit(1)
