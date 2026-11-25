#!/usr/bin/env python3
"""Translate AGY lifecycle events to the existing local worker safeguards."""
from __future__ import annotations

import fnmatch
import concurrent.futures
import configparser
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

HOME = Path.home()
CLAUDE = HOME / '.claude'
REQUIRED = ('secret-print-guard.py', 'gcp-account-guard.py', 'no-sed-inplace.py',
            'secret-scan-push.sh', 'slack-format-check.py', 'host_guard.py',
            'dispatch/guard.sh', 'harness-guards/guard.py')
SLACK_WRITE = re.compile(r'(?:chat[._](?:post|update|delete|schedule)|slack[\w./-]*(?:send|post|reply|edit)|'
                         r'hooks\.slack\.com|main-channel\.py\s+(?:post|reply|decide)|'
                         r'(?:reply|post)\.sh)', re.I)
AUTH_OVERRIDES = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'AGY_ADC_AUTH', 'GOOGLE_APPLICATION_CREDENTIALS',
                  'GOOGLE_CLOUD_PROJECT', 'GOOGLE_CLOUD_PROJECT_ID', 'GOOGLE_CLOUD_QUOTA_PROJECT',
                  'GOOGLE_GENAI_USE_VERTEXAI')


def deny(reason):
    return {'decision': 'deny', 'reason': reason}


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('required string is missing')
    return value


def translate(event):
    call = event['toolCall']
    name, args = nonempty(call['name']), call['args']
    if not isinstance(args, dict):
        raise ValueError('invalid tool arguments')
    cwd = args.get('Cwd') or event.get('cwd') or (event.get('workspacePaths') or [None])[0]
    cwd = nonempty(cwd)
    base = {'session_id': nonempty(event.get('conversationId')), 'cwd': cwd,
            'transcript_path': event.get('transcriptPath') or '', 'hook_event_name': 'PreToolUse'}
    if name == 'run_command':
        if 'BypassSandbox' in args and not (args['BypassSandbox'] is False or args['BypassSandbox'] == 'false'):
            raise ValueError('sandbox bypass is not supported')
        if args.get('RequestedTerminalID'):
            raise ValueError('cross-terminal execution is not supported')
        tool, inp = 'Bash', {'command': nonempty(args.get('CommandLine')),
                             'run_in_background': bool(args.get('RunPersistent'))}
    elif name in ('view_file', 'read_file'):
        tool, inp = 'Read', {'file_path': nonempty(args.get('AbsolutePath') or args.get('Path'))}
    elif name in ('list_dir', 'find_by_name', 'grep_search'):
        key = {'list_dir': 'DirectoryPath', 'find_by_name': 'SearchDirectory', 'grep_search': 'SearchPath'}[name]
        tool, inp = 'Grep', {'path': nonempty(args.get(key)), 'pattern': args.get('Query', args.get('Pattern', '')),
                             'output_mode': 'content' if name == 'grep_search' else 'files_with_matches'}
    elif name == 'write_file':
        if not isinstance(args.get('Content'), str):
            raise ValueError('invalid file content')
        tool, inp = 'Write', {'file_path': nonempty(args.get('Path')), 'content': args['Content']}
    elif name == 'edit_file':
        if not isinstance(args.get('Target'), str) or not isinstance(args.get('Replacement'), str):
            raise ValueError('invalid file replacement')
        tool, inp = 'Edit', {'file_path': nonempty(args.get('Path')),
                             'old_string': args['Target'], 'new_string': args['Replacement']}
    elif name == 'write_to_file':
        tool, inp = 'Write', {'file_path': nonempty(args.get('TargetFile')), 'content': args.get('CodeContent', '')}
    elif name == 'replace_file_content':
        tool, inp = 'Edit', {'file_path': nonempty(args.get('TargetFile')),
                             'old_string': args.get('TargetContent', ''), 'new_string': args.get('ReplacementContent', '')}
    elif name == 'multi_replace_file_content':
        chunks = args.get('ReplacementChunks')
        if not isinstance(chunks, list) or not all(isinstance(c, dict) for c in chunks):
            raise ValueError('invalid replacement chunks')
        tool, inp = 'MultiEdit', {'file_path': nonempty(args.get('TargetFile')),
            'edits': [{'old_string': c.get('TargetContent', ''), 'new_string': c.get('ReplacementContent', '')} for c in chunks]}
    elif name == 'read_url_content':
        tool, inp = 'WebFetch', {'url': nonempty(args.get('Url'))}
    elif name == 'search_web':
        tool, inp = 'WebSearch', {'query': nonempty(args.get('query'))}
    elif name in ('command_status', 'wait', 'wait_5_seconds', 'list_permissions', 'finish'):
        tool, inp = 'Status', args
    elif name == 'manage_task' and args.get('Action') in ('list', 'status'):
        tool, inp = 'Status', args
    else:
        raise ValueError('unsupported tool; no unguarded fallback')
    base.update(tool_name=tool, tool_input=inp)
    return base


def settings():
    data = json.loads((CLAUDE / 'settings.json').read_text())
    rules = data.get('permissions', {}).get('deny')
    groups = data.get('hooks', {}).get('PreToolUse')
    if not isinstance(rules, list) or not isinstance(groups, list):
        raise ValueError('shared safety configuration is missing')
    configured = '\n'.join(h.get('command', '') for g in groups for h in g.get('hooks', []))
    if any(name not in configured for name in REQUIRED):
        raise ValueError('a required shared safeguard is missing')
    return rules, groups


def expanded_path(raw, cwd):
    raw = raw.replace('${HOME}', str(HOME)).replace('$HOME', str(HOME))
    if raw == '~' or raw.startswith('~/'):
        raw = str(HOME) + raw[1:]
    return Path(cwd, raw).resolve()


def shell_paths(command, cwd):
    """Inspect literal shell paths; this is not an arbitrary-code sandbox."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = ''
    segments, segment = [], []
    for word in lexer:
        if word and all(c in ';&|()\n' for c in word):
            if segment:
                segments.append(segment)
            segment = []
        else:
            segment.append(word)
    if segment:
        segments.append(segment)
    previous = cwd
    for words in segments:
        # Redirection operands are paths even for echo/printf.
        args, redirections = [], []
        redirect = False
        for word in words:
            if word and all(c in '<>' for c in word):
                redirect = True
            elif redirect:
                redirections.append(word)
                redirect = False
            else:
                args.append(word)
        for word in redirections:
            yield word, cwd, False
        while args and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', args[0]):
            args.pop(0)
        if not args:
            continue
        name = Path(args[0]).name
        operands = [word for word in args[1:] if not word.startswith('-')]
        if name == 'cd':
            target = operands[0] if operands else str(HOME)
            target = previous if target == '-' else str(expanded_path(target, cwd))
            yield target, cwd, False
            previous, cwd = cwd, target
            continue
        if name in ('echo', 'printf', 'pwd'):
            continue
        recursive = name in ('rg', 'find', 'tar', 'du') or (
            name in ('grep', 'ls', 'cp') and any(
                word in ('--recursive', '--dereference-recursive') or
                (word.startswith('-') and not word.startswith('--') and
                 any(c in word[1:] for c in ('rR' if name != 'ls' else 'R')))
                for word in args[1:]))
        if name in ('rg', 'grep'):
            # -e/--regexp makes its following value the query instead of the
            # first positional operand. Drop option values before path checks.
            values, skip = [], False
            query_option = False
            for word in args[1:]:
                if skip:
                    skip = False
                    continue
                if word in ('-e', '--regexp', '-f', '--file', '-g', '--glob', '-t', '--type'):
                    skip = True
                    query_option |= word in ('-e', '--regexp', '-f', '--file')
                elif not word.startswith('-'):
                    values.append(word)
            operands = values if query_option else values[1:]
            if not operands and recursive:
                operands = ['.']
        elif name == 'ls' and not operands:
            operands = ['.']
        for word in operands:
            if '://' not in word and not any(c.isspace() for c in word):
                yield word, cwd, recursive


def policy_problem(payload, rules):
    tool, inp = payload['tool_name'], payload['tool_input']
    # The session Mac may run without a terminal sandbox. Keep explicit source
    # credential reads, glob spellings and recursive parent searches denied in
    # the native hook as well. This is command policy, not filesystem isolation.
    protected = (HOME / '.config/gcloud', HOME / '.gemini/oauth_creds.json',
                 HOME / '.local/state/antigravity-worker/gcp-auth',
                 HOME / '.ssh', HOME / '.aws', HOME / '.gnupg',
                 HOME / '.config/gh/hosts.yml')
    def private_path(raw, recursive=False, cwd=None):
        candidate = str(expanded_path(raw, cwd or payload['cwd'])).casefold()
        wildcard = any(c in candidate for c in '*?[')
        for source in protected:
            source = str(source.resolve()).casefold()
            if (candidate == source or candidate.startswith(source + '/')
                    or (recursive and source.startswith(candidate.rstrip('/') + '/'))
                    or (wildcard and any(fnmatch.fnmatchcase(str(parent), candidate)
                                         for parent in (Path(source), *Path(source).parents)))
                    or (wildcard and fnmatch.fnmatchcase(source, '/'.join(
                        candidate.split('/')[:len(source.split('/'))])))):
                return True
        return False
    if tool == 'Bash':
        command = inp['command']
        if any(private_path(path, recursive=recursive, cwd=cwd)
               for path, cwd, recursive in shell_paths(command, payload['cwd'])):
            return 'Source authentication files cannot be accessed by worker tools.'
    elif tool in ('Read', 'Grep', 'Write', 'Edit', 'MultiEdit'):
        if private_path(inp.get('file_path', inp.get('path', '')), recursive=tool == 'Grep'):
            return 'Source authentication files cannot be accessed by worker tools.'
    # OAuth belongs to the CLI authentication layer, never model tools.
    # The terminal sandbox separately excludes this file from its mounts.
    if tool == 'Bash' and re.search(r'oauth_creds(?:\.json)?', inp['command'], re.I):
        return 'Subscription OAuth credentials cannot be accessed by worker tools.'
    if tool in ('Read', 'Grep', 'Write', 'Edit', 'MultiEdit'):
        target = inp.get('file_path', inp.get('path', ''))
        resolved = str(expanded_path(target, payload['cwd'])).casefold()
        credential = str((HOME / '.gemini/oauth_creds.json').resolve()).casefold()
        if (resolved == credential or Path(resolved).name == 'oauth_creds.json'
                or (tool == 'Grep' and credential.startswith(resolved.rstrip('/') + '/'))):
            return 'Subscription OAuth credentials cannot be accessed by worker tools.'
    # Only the SDK may consume the short-lived token. The model must not print it
    # or alter it; recursive searches of parent directories are denied too.
    token = (HOME / '.local/state/antigravity-worker/gcp-auth/access-token').resolve()
    if tool == 'Bash' and re.search(r'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE|gcp-auth', inp['command']):
        return 'Agent authentication files cannot be accessed by worker tools.'
    if tool in ('Read', 'Grep', 'Write', 'Edit', 'MultiEdit'):
        target = inp.get('file_path', inp.get('path', ''))
        resolved = Path(payload['cwd'], os.path.expanduser(target)).resolve()
        if resolved == token or token.parent in resolved.parents or resolved == token.parent or (tool == 'Grep' and resolved in token.parents):
            return 'Agent authentication files cannot be accessed by worker tools.'
    if tool == 'Bash' and SLACK_WRITE.search(inp['command']):
        return 'Worker Slack publication must go through the lead.'
    if tool == 'Bash' and re.search(r'\bgcloud\b[^\n;&|]*\bconfig\b', inp['command']) and re.search(
            r'\b(?:set|unset|activate|create|delete|rename)\b', inp['command']):
        return 'Worker tools cannot change persistent GCP account or project configuration.'
    if tool == 'Bash':
        source = CLAUDE / 'hooks/no-sed-inplace.py'
        spec = importlib.util.spec_from_file_location('agy_shell_policy', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.CLAUDE = {'claude', 'cl', 'cla', 'codex', 'agy', 'agy-personal', 'gemini'}
        if module.claude_model_command(inp['command']):
            return 'Nested model execution must go through the lead and guarded pane-spawn.'
    for rule in rules:
        if not isinstance(rule, str):
            raise ValueError('invalid deny rule')
        match = re.fullmatch(r'([^()]+)\((.*)\)', rule)
        if not match:
            if rule == tool or fnmatch.fnmatchcase(tool, rule + '*') or (
                    rule == 'Edit' and tool in ('Write', 'MultiEdit')):
                return 'Shared deny policy blocks this tool.'
            continue
        target, pattern = match.groups()
        if target != tool and not (target == 'Read' and tool == 'Grep') and not (
                target == 'Edit' and tool in ('Write', 'MultiEdit')):
            continue
        if tool == 'Bash':
            # Native hooks below also inspect nested shells and tokenized git options.
            value = inp['command']
            pattern = pattern.replace(':*', '*')
            if fnmatch.fnmatchcase(value, pattern):
                return 'Shared deny policy blocks this command.'
        else:
            value = inp.get('file_path', inp.get('path', inp.get('url', '')))
            if value and not value.startswith(('http://', 'https://')):
                value = str(expanded_path(value, payload['cwd'])).casefold()
                pattern = str(expanded_path(pattern, payload['cwd'])).casefold()
            if fnmatch.fnmatchcase(value, pattern):
                return 'Shared deny policy blocks this target.'
    return None


def run_guard(command, payload, timeout=10):
    child_env = dict(os.environ)
    if 'gcp-account-guard.py' in command:
        command = f'{sys.executable} -I {CLAUDE / "hooks/gcp-account-guard.py"} --judge'
        child_env.pop('MUNICE_GCP_ADMIN', None)
        child_env['MUNICE_AUTO_DISPATCH'] = '1'
        child_env.pop('MUNICE_AMBIENT', None)
    try:
        result = subprocess.run(command, shell=True, input=json.dumps(payload), text=True,
                                capture_output=True, cwd=payload['cwd'], env=child_env, timeout=timeout)
        if result.returncode:
            return 'Shared safety hook failed; the tool remains blocked.'
        for line in result.stdout.splitlines():
            output = json.loads(line)
            if not isinstance(output, dict) or output.get('continue') is False:
                return 'Shared safety hook rejected the operation.'
            if output and not set(output).intersection(('hookSpecificOutput', 'decision', 'continue', 'systemMessage')):
                return 'Shared safety hook returned an invalid response.'
            decision = output.get('hookSpecificOutput', {}).get('permissionDecision')
            if decision == 'deny' or output.get('decision') in ('deny', 'block'):
                # Existing guards intentionally report categories, never secret values.
                return output.get('hookSpecificOutput', {}).get('permissionDecisionReason') or 'Shared safety hook denied the tool.'
            if decision not in (None, 'allow', 'ask'):
                return 'Shared safety hook returned an unsupported decision.'
            if decision == 'ask':
                return 'Shared safety hook requires lead approval.'
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return 'Shared safety hook could not be checked; the tool remains blocked.'
    return None


def ponytail(event, state_dir=None):
    conversation = nonempty(event.get('conversationId'))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', conversation):
        raise ValueError('invalid conversation id')
    directory = Path(state_dir) if state_dir else HOME / '.local/state/antigravity-worker/ponytail'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (conversation + '.json')
    previous = json.loads(path.read_text()).get('mode') if path.exists() else None
    plugin = CLAUDE / 'plugins/marketplaces/ponytail'
    if not (plugin / 'skills/ponytail/SKILL.md').is_file():
        raise ValueError('Ponytail source is missing')
    # Reuse the plugin's canonical instruction builder and mode normalization.
    script = r'''
const fs=require('fs'), p=JSON.parse(fs.readFileSync(0,'utf8'));
const c=require(p.plugin+'/hooks/ponytail-config');
const builder=require(p.plugin+'/hooks/ponytail-instructions');
let mode=c.normalizePersistedMode(p.previous)||c.getDefaultMode();
const prompt=String(p.prompt||'').trim().toLowerCase();
if(c.isDeactivationCommand(prompt)) mode='off';
const words=prompt.split(/\s+/), command=words[0].replace(/^[@$]/,'/');
if(command==='/ponytail-review'||command==='/ponytail:ponytail-review') mode='review';
if(command==='/ponytail'||command==='/ponytail:ponytail') mode=c.normalizeMode(words[1])||c.getDefaultMode();
console.log(JSON.stringify({mode,text:mode==='off'?'PONYTAIL MODE OFF':builder.getPonytailInstructions(mode)}));
'''
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True, timeout=5,
                            input=json.dumps({'plugin': str(plugin), 'previous': previous,
                                              'prompt': event.get('lastUserInput', '')}))
    if result.returncode:
        raise ValueError('Ponytail instruction generation failed')
    built = json.loads(result.stdout)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'mode': built['mode']}))
    temp.chmod(0o600)
    temp.replace(path)
    if event.get('hookEventName') == 'PreInvocation' and previous == built['mode']:
        return {'injectSteps': []}
    return {'injectSteps': [{'systemMessage': {'systemMessage': nonempty(built['text'])}}]}


def control_command(command, env=None):
    """Run only fixed harness operations outside the model's terminal sandbox."""
    env = os.environ if env is None else env
    argv = shlex.split(command)
    batch = bool(argv and argv[0] == 'PANE_TELL_BATCH=0')
    if batch:
        argv = argv[1:]
    if not argv or Path(argv[0]).name not in ('pane-tell', 'pane-label'):
        return None
    if env.get('HERDR_AGY_STARTUP_ROLE') != 'worker':
        raise ValueError('harness bridge is limited to workers')
    tool = Path(argv[0]).name
    if tool == 'pane-label':
        if batch or len(argv) != 2 or argv[1] not in ('--hold', '--free'):
            raise ValueError('only the own-pane hold flag may be changed')
    elif len(argv) != 3 or not env.get('HERDR_AGY_LEAD') or argv[1] != env['HERDR_AGY_LEAD']:
        raise ValueError('progress messages may only reach the launching lead')
    child_env = dict(env)
    if batch:
        child_env['PANE_TELL_BATCH'] = '0'
    result = subprocess.run([str(Path(__file__).with_name(tool)), *argv[1:]],
                            env=child_env, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=30)
    if result.returncode:
        raise ValueError('harness operation failed')
    return {'decision': 'allow', 'overwrite': {'CommandLine':
            shlex.join(['/usr/bin/printf', '%s\n', tool + ': completed by guarded harness bridge'])}}


def gcloud_environment():
    """Expose a short-lived agent token, never the source OAuth/ADC credentials."""
    directory = HOME / '.config/gcloud'
    active_path = directory / 'active_config'
    if not active_path.exists():
        return []
    active = active_path.read_text().strip()
    if active != 'munice':
        raise ValueError('the active GCP configuration is not the agent configuration')
    config = configparser.ConfigParser()
    config.read(directory / 'configurations' / ('config_' + active))
    principal = config.get('auth', 'impersonate_service_account')
    if not re.fullmatch(r'ai-agent-[A-Za-z0-9-]+@[A-Za-z0-9-]+\.iam\.gserviceaccount\.com', principal):
        raise ValueError('the GCP configuration does not select an agent account')
    adc_path = directory / 'application_default_credentials.json'
    adc = json.loads(adc_path.read_text())
    expected = 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' + principal + ':generateAccessToken'
    if adc.get('type') != 'impersonated_service_account' or adc.get('service_account_impersonation_url') != expected:
        raise ValueError('the existing ADC does not match the configured agent account')
    # A separate config root keeps all other accounts outside the sandbox.
    # Account and project files stay read-only; SDK cache writes stay isolated.
    worker = HOME / '.local/state/antigravity-worker/gcloud'
    (worker / 'configurations').mkdir(parents=True, exist_ok=True, mode=0o700)
    for target, content in ((worker / 'active_config', active),
                            (worker / 'configurations' / ('config_' + active),
                             (directory / 'configurations' / ('config_' + active)).read_text())):
        if not target.exists() or target.read_text() != content:
            temp = target.with_suffix('.tmp')
            temp.write_text(content)
            temp.chmod(0o400)
            temp.replace(target)
    auth = worker.parent / 'gcp-auth'
    auth.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = auth / 'access-token'
    metadata = auth / 'metadata.json'
    with (auth / 'refresh.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(metadata.read_text())
        except (OSError, ValueError):
            cached = {}
        if not (cached.get('principal') == principal and cached.get('expires_at', 0) > time.time() + 300
                and token.is_file() and 0 < token.stat().st_size < 8192):
            child_env = {key: value for key, value in os.environ.items()
                         if not key.startswith('CLOUDSDK_') and key not in AUTH_OVERRIDES}
            child_env['CLOUDSDK_CONFIG'] = str(directory)
            result = subprocess.run(['gcloud', '--configuration=munice',
                                     '--impersonate-service-account=' + principal,
                                     'auth', 'print-access-token', '--quiet'],
                                    capture_output=True, text=True, env=child_env, timeout=20)
            value = result.stdout.strip()
            if result.returncode or not re.fullmatch(r'[A-Za-z0-9._~+/-]{20,8192}', value):
                raise ValueError('agent access token refresh failed')
            for target, content in ((token, value), (metadata, json.dumps({
                    'principal': principal, 'expires_at': time.time() + 2700}))):
                temp = target.with_suffix('.tmp')
                temp.write_text(content)
                temp.chmod(0o400)
                temp.replace(target)
    return ['CLOUDSDK_CONFIG=' + str(worker),
            'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=',
            'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE=' + str(token),
            'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=']


def worker_gcloud_environment():
    """Hosts without the agent GCP setup (집맥) still run tools; gcloud gets an empty config and no account."""
    try:
        return gcloud_environment()
    except ValueError:
        empty = HOME / '.local/state/antigravity-worker/no-gcloud'
        empty.mkdir(parents=True, exist_ok=True, mode=0o700)
        return ['CLOUDSDK_CONFIG=' + str(empty), 'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=',
                'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE=', 'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=']


def pre_tool(event):
    try:
        payload = translate(event)
        rules, groups = settings()
        reason = policy_problem(payload, rules)
        if reason:
            return deny(reason)
        handlers = []
        for group in groups:
            matcher = group.get('matcher') or '*'
            if matcher != '*' and not re.search(matcher, payload['tool_name']):
                continue
            for handler in group.get('hooks', []):
                if handler.get('type') != 'command':
                    return deny('Unsupported shared safety hook type.')
                handlers.append((nonempty(handler.get('command')), min(float(handler.get('timeout', 10)), 30)))
        # The guards are separate processes; running them together costs the slowest one, not their sum,
        # on every AGY tool call (about 1.2 s with ten guards one after another). The first deny in order wins.
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(handlers), 1)) as pool:
            reasons = list(pool.map(lambda h: run_guard(h[0], payload, timeout=h[1]), handlers))
        reason = next((r for r in reasons if r), None)
        if reason:
            return deny(reason)
        result = {'decision': 'allow'}  # Delegate to AGY policy; never auto-approve.
        if payload['tool_name'] == 'Bash':
            control = control_command(payload['tool_input']['command'])
            if control:
                return control
            # AGY may inject ADC variables into tool children after pane startup.
            # Check the original command first, then keep the execution shell clean.
            result['overwrite'] = {'CommandLine': shlex.join(
                ['/usr/bin/env', *[part for key in (*AUTH_OVERRIDES,
                     'CLOUDSDK_CORE_ACCOUNT', 'CLOUDSDK_ACTIVE_CONFIG_NAME',
                     'CLOUDSDK_AUTH_ACCESS_TOKEN', 'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE') for part in ('-u', key)],
                 *worker_gcloud_environment(),
                 '/bin/zsh', '-f', '-c', payload['tool_input']['command']])}
        return result
    except Exception:
        return deny('AGY safety input or configuration is invalid; the tool remains blocked.')


def load_completion():
    spec = importlib.util.spec_from_file_location('agy_completion', Path(__file__).with_name('auto-tab-close.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completion(event, env=None):
    env = os.environ if env is None else env
    if env.get('HERDR_AGY_STARTUP_ROLE') == 'worker':
        load_completion().handle_stop(event, env)
    return {'decision': 'allow'}


def main():
    event_name = sys.argv[1] if len(sys.argv) > 1 else 'PreToolUse'
    try:
        event = json.load(sys.stdin)
        if event_name == 'PreToolUse':
            result = pre_tool(event)
        elif event_name in ('SessionStart', 'PreInvocation'):
            result = ponytail(event)
        elif event_name == 'Stop':
            result = completion(event)
        else:
            result = deny('Unsupported lifecycle event.')
    except Exception:
        if event_name in ('SessionStart', 'PreInvocation'):
            result = {'terminationBehavior': 'terminate', 'injectSteps': [{'errorMessage': {
                'userMessage': 'AGY shared instructions could not be loaded.'}}]}
        else:
            result = deny('AGY safety event could not be read; the tool remains blocked.')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
