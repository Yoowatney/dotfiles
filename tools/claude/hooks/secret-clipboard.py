#!/usr/bin/env python3
"""Copy one GCP secret without a shell, inherited logging or visible payloads.

No Keychain shortcut: a generic-password item can hold an ordinary draft.
Run only through the clipboard guard's exact absolute-interpreter command.
This script does not grant access or change the account/security policy.
"""
import json
import configparser
import os
from pathlib import Path
import re
import subprocess
import sys

GCLOUD = '/opt/homebrew/share/google-cloud-sdk/bin/gcloud'
INSTALLATION_PROPERTIES = Path(GCLOUD).parent.parent / 'properties'


def configuration():
    """Keep the selected identity; refuse unsafe/ambiguous SDK configuration."""
    sdk = {k: v for k, v in os.environ.items() if k.startswith('CLOUDSDK_')}
    allowed = {'CLOUDSDK_ACTIVE_CONFIG_NAME', 'CLOUDSDK_CORE_LOG_HTTP',
               'CLOUDSDK_CORE_DISABLE_FILE_LOGGING', 'CLOUDSDK_CORE_VERBOSITY'}
    if set(sdk) - allowed or sdk.get('CLOUDSDK_CORE_LOG_HTTP', 'false').lower() not in ('false', '0'):
        return None
    folder = Path.home() / '.config/gcloud'
    selected = sdk.get('CLOUDSDK_ACTIVE_CONFIG_NAME')
    if selected is None:
        active = folder / 'active_config'
        selected = active.read_text().strip() if active.exists() else 'default'
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,62}', selected):
        return None
    path = folder / 'configurations' / ('config_' + selected)
    if not path.is_file():
        return None
    cfg = configparser.ConfigParser(interpolation=None)
    # The SDK merges installation properties before the selected named config.
    # Inspect both sources, including inherited endpoint/logging properties.
    cfg.read([INSTALLATION_PROPERTIES, path])
    if cfg.get('core', 'log_http', fallback='false').lower() not in ('false', '0'):
        return None
    # Alternate endpoints can fabricate a secret; proxy/auth overrides can
    # redirect credentials or silently change identity. No automatic exception.
    if cfg.has_section('api_endpoint_overrides') or cfg.has_section('proxy'):
        return None
    if (cfg.has_section('auth') and set(cfg['auth']) - {'impersonate_service_account'}) or \
            cfg.get('core', 'custom_ca_certs_file', fallback='') or \
            cfg.get('core', 'universe_domain', fallback='googleapis.com') != 'googleapis.com':
        return None
    return selected


def arguments(args):
    text = ' '.join(args)
    match = re.fullmatch(r'gcloud --project ([a-z][a-z0-9-]{4,62}) --secret ([A-Za-z0-9_-]{1,255})'
                         r'(?: --version (latest|[0-9]+))?', text)
    if not match or args != text.split(' '):
        return None
    project, secret, version = match.groups()
    return [GCLOUD, 'secrets', 'versions', 'access', version or 'latest',
            '--project=' + project, '--secret=' + secret, '--no-log-http', '--verbosity=none', '--quiet']


def account_allowed(command):
    """Judge the real source command, rather than hiding it inside a helper."""
    payload = dict(tool_name='Bash', tool_input=dict(command=' '.join(command)))
    try:
        result = subprocess.run(['/usr/bin/python3', '-I', str(Path(__file__).resolve().with_name('gcp-account-guard.py'))],
                                input=json.dumps(payload).encode(), capture_output=True, timeout=10)
        # An empty success is the guard's only allowed response. Errors/denials
        # are not printed: downstream diagnostics can contain sensitive data.
        return result.returncode == 0 and not result.stdout.strip() and not result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False


def transfer(args):
    command = arguments(args)
    selected = configuration() if command is not None else None
    if selected is None:
        return 1
    command.append('--configuration=' + selected)
    if not account_allowed(command):
        return 1
    # Do not inherit CLOUDSDK_*, Python injection, proxy, endpoint or shell state.
    # CLI flags override persisted core/log_http, and these SDK environment
    # properties also disable HTTP/file logging before transport initialization.
    env = {'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin',
           'CLOUDSDK_ACTIVE_CONFIG_NAME': selected,
           'CLOUDSDK_PYTHON': '/usr/bin/python3', 'CLOUDSDK_PYTHON_ARGS': '-I',
           'CLOUDSDK_CORE_LOG_HTTP': 'false', 'CLOUDSDK_CORE_DISABLE_FILE_LOGGING': 'true',
           'CLOUDSDK_CORE_VERBOSITY': 'none', 'CLOUDSDK_CORE_DISABLE_PROMPTS': 'true'}
    try:
        source = subprocess.run(command, env=env, capture_output=True, timeout=30)
        if source.returncode != 0 or not source.stdout:
            return 1
        sink = subprocess.run(['/usr/bin/pbcopy'], input=source.stdout,
                              env={'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin'},
                              capture_output=True, timeout=10)
        return 0 if sink.returncode == 0 else 1
    except (OSError, subprocess.TimeoutExpired):
        return 1


if __name__ == '__main__':
    # Never echo exceptions, SDK stderr, secret bytes or a traceback.
    try:
        result = transfer(sys.argv[1:])
    except Exception:
        result = 1
    print('Secret copied.' if result == 0 else 'Secret copy refused or failed; no payload was displayed.')
    sys.exit(result)
