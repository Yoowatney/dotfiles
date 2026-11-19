#!/usr/bin/env python3
"""Settings verify.sh checks against this checkout: one "ok<TAB>name" or "fail<TAB>name" line per check.

Prints names only, never values: settings-drift.py relays the failed names off this Mac.
"""
import os
import plistlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = re.compile(r'^[ \t]*defaults[ \t]+(-currentHost[ \t]+)?write[ \t]+(\S+)[ \t]+(\S+)[ \t]+'
                      r'-(bool|int|integer|float|string)[ \t]+("[^"\n]*"|\'[^\'\n]*\'|\S+)', re.M)
BREW = re.compile(r'^[ \t]*(cask|mas)[ \t]+"([^"]+)"(?:,[ \t]*id:[ \t]*(\d+))?', re.M)


def parse_defaults(text):
    """Typed `defaults write` lines; values decided at run time ($...) are left out."""
    rows = []
    for host, domain, key, kind, value in DEFAULTS.findall(text):
        value = value.strip('"\'')
        if '$' not in value:
            rows.append((bool(host), domain, key, 'int' if kind == 'integer' else kind, value))
    return rows


def same_value(kind, want, have):
    if have is None:
        return False
    try:
        if kind == 'bool':
            return (want.lower() in ('true', 'yes', '1')) == (have in ('1', 'true', 'YES'))
        if kind == 'int':
            return int(want) == int(have)
        if kind == 'float':
            return float(want) == float(have)
    except ValueError:
        return False
    return want == have


def parse_brewfile(text):
    casks, mas = [], []
    for kind, name, app_id in BREW.findall(text):
        if kind == 'cask':
            casks.append(name.split('/')[-1])
        elif app_id:
            mas.append((name, app_id))
    return casks, mas


def home_app(tile):
    """Apps in ~/Applications belong to one Mac: dock.xml leaves them out and the check ignores them."""
    return tile.get('tile-data', {}).get('file-data', {}).get('_CFURLString', '').startswith('file:///Users/')


def dock_ids(prefs):
    return [t['tile-data']['bundle-identifier'] for t in prefs.get('persistent-apps', [])
            if t.get('tile-data', {}).get('bundle-identifier') and not home_app(t)]


def run(*cmd):
    try:
        env = {**os.environ, 'PATH': os.environ.get('PATH', '/usr/bin:/bin') + ':/opt/homebrew/bin'}  # ssh without a login shell
        r = subprocess.run(cmd, capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode('utf-8', 'replace').strip() if r.returncode == 0 else None


def export(domain):
    try:
        return plistlib.loads(subprocess.run(['defaults', 'export', domain, '-'], capture_output=True, timeout=60).stdout)
    except Exception:
        return {}


def main():
    def emit(ok, name):
        print(('ok' if ok else 'fail') + '\t' + name)

    for host, domain, key, kind, want in parse_defaults((ROOT / 'macos/defaults.sh').read_text(encoding='utf-8')):
        emit(same_value(kind, want, run('defaults', *(['-currentHost'] if host else []), 'read', domain, key)),
             f'defaults {domain} {key}')
    hotkey = export('com.apple.symbolichotkeys').get('AppleSymbolicHotKeys', {}).get('60', {})
    emit(hotkey.get('enabled') and hotkey.get('value', {}).get('parameters', [])[1:2] == [79], 'hotkey 60 F18')

    casks, mas = parse_brewfile((ROOT / 'Brewfile').read_text(encoding='utf-8'))
    installed = set((run('brew', 'list', '--cask', '-1') or '').split())
    for cask in casks:
        emit(cask in installed, f'brew cask {cask}')
    owned = {line.split()[0] for line in (run('mas', 'list') or '').splitlines() if line.strip()}
    for name, app_id in mas:  # an app installed outside the App Store still counts
        emit(app_id in owned or (Path('/Applications') / f'{name}.app').exists(), f'mas {name}')

    def xml(path):
        with open(ROOT / path, 'rb') as f:
            return plistlib.load(f)
    emit(dock_ids(export('com.apple.dock')) == dock_ids(xml('macos/dock/dock.xml')), 'dock persistent-apps')
    emit(export('com.iktm.snap').get('apps') == xml('macos/snap/snap.xml').get('apps'), 'snap apps')


if __name__ == '__main__':
    main()
