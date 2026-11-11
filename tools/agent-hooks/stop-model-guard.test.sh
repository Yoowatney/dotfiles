#!/usr/bin/env bash
set -euo pipefail
export STOP_GUARD_ROOT="${STOP_GUARD_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
python3 - <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ['STOP_GUARD_ROOT'])

# Native stand-ins exercise the real macOS process reader. Detach their root
# so the agent running this test cannot count as another ancestor. Every Stop
# endpoint is a local file writer; no real agent binary or API is used.
DRIVER = r'''
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern char **environ;
int finish(pid_t pid) { int s; waitpid(pid, &s, 0); return WIFEXITED(s) ? WEXITSTATUS(s) : 99; }
int run(char **a) {
    if (!strcmp(a[1], "nested")) {
        pid_t pid = fork();
        if (!pid) {
            for (char **e = environ; *e;) {
                if (!strncmp(*e, "HERDR_", 6) || !strncmp(*e, "MUNICE_", 7)) {
                    char *key = strdup(*e); *strchr(key, '=') = 0;
                    unsetenv(key); free(key); e = environ;
                } else e++;
            }
            char *args[] = {a[2], "stop", a[2], a[3], NULL};
            execv(a[2], args); _exit(98);
        }
        return finish(pid);
    }
    int fds[2]; pipe(fds);
    pid_t pid = fork();
    if (!pid) {
        dup2(fds[0], 0); close(fds[0]); close(fds[1]);
        execl("/bin/bash", "bash", a[3], NULL); _exit(97);
    }
    close(fds[0]);
    const char *payload = getenv("FIXTURE_PAYLOAD");
    if (!payload) payload = "{\"session_id\":\"fixture-session\",\"cwd\":\"fixture\"}";
    write(fds[1], payload, strlen(payload)); close(fds[1]);
    return finish(pid);
}
int main(int argc, char **argv) {
    if (argc == 4) return run(argv);
    if (argc != 5) return 96;
    pid_t pid = fork();
    if (pid) return 0;
    while (getppid() != 1) usleep(1000);
    int result = run(argv + 1);
    char path[4096]; snprintf(path, sizeof(path), "%s/done", getenv("HOME"));
    FILE *out = fopen(path, "w"); fprintf(out, "%d", result); fclose(out);
    return result;
}
'''


class StopGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binaries = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.binaries.cleanup)
        cls.bin = Path(cls.binaries.name)
        source = cls.bin / 'fixture.c'
        source.write_text(DRIVER)
        subprocess.run(['cc', str(source), '-o', str(cls.bin / 'codex')], check=True, capture_output=True)
        os.link(cls.bin / 'codex', cls.bin / 'claude')
        os.link(cls.bin / 'codex', cls.bin / 'python3')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.calls = self.home / 'calls'
        hooks = self.home / 'hooks'
        hooks.mkdir()
        for name in ('codex-work-log-guard.sh', 'run-llm-wiki-ingest.sh'):
            path = hooks / name
            path.write_text('#!/bin/bash\necho call >> "$HOME/calls"\n')
            path.chmod(0o755)
        ingest = self.home / 'ingest'
        ingest.write_text('#!/bin/bash\necho call >> "$HOME/calls"\n')
        ingest.chmod(0o755)
        herdr = self.home / 'herdr'
        # Routing replies are constants; avoid Python startup within the real
        # two-second herdr deadline while keeping the native process reader.
        herdr.write_text('''#!/bin/bash
case "${1:-} ${2:-}" in
    'pane get')
        printf '%s\\n' '{"result":{"pane":{"pane_id":"resolved","terminal_id":"fixture-terminal"}}}' ;;
    'agent get')
        case "${FIXTURE_AGENT_NAME:-}" in
            spawn-parent-fixture) printf '%s\\n' '{"result":{"agent":{"name":"spawn-parent-fixture"}}}' ;;
            '') printf '%s\\n' '{"result":{"agent":{"name":""}}}' ;;
            *) exit 2 ;;
        esac ;;
    *) printf '%s\\n' '{}' ;;
esac
''')
        herdr.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('HERDR_', 'CODEX_', 'CLAUDE_', 'MUNICE_'))}
        self.env.update(HOME=str(self.home), XDG_STATE_HOME=str(self.home / 'state'),
                        HERDR_PANE_ID='stale', HERDR_SESSION='workers',
                        HERDR_BIN_PATH=str(herdr), FIXTURE_AGENT_NAME='spawn-parent-fixture',
                        AGENT_HOOKS_ROOT=str(hooks), CODEX_THREAD_ID='fixture-session',
                        CLAUDE_BIN='/usr/bin/false',
                        LLM_WIKI_STATE_DIR=str(self.home / 'wiki'),
                        LLM_WIKI_INGEST_SCRIPT=str(ingest), LLM_WIKI_COOLDOWN_SEC='0')

    def run_case(self, nested=False, parent='codex', child='codex', claude_stop=False, remember=False):
        hook = ROOT / ('session-health.sh' if remember else
                       ('run-llm-wiki-ingest.sh' if claude_stop else 'codex-stop-ingest.sh'))
        result = subprocess.run([str(self.bin / parent), 'launch', 'nested' if nested else 'stop',
                                 str(self.bin / child), str(hook)], env=self.env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / 'done').read_text(), '0', result.stderr)
        self.assertEqual(result.stdout, '')
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def test_worker_nested_codex_skips_both_stop_endpoints(self):
        self.assertEqual(self.run_case(nested=True), 0)

    def test_worker_nested_claude_skips_ingest(self):
        self.assertEqual(self.run_case(nested=True, child='claude', claude_stop=True), 0)

    def test_native_herdr_fixture_preserves_routing_replies(self):
        herdr = str(self.home / 'herdr')
        cases = [
            (['pane', 'get', 'stale'], 'spawn-parent-fixture',
             {'result': {'pane': {'pane_id': 'resolved', 'terminal_id': 'fixture-terminal'}}}),
            (['agent', 'get', 'resolved'], 'spawn-parent-fixture',
             {'result': {'agent': {'name': 'spawn-parent-fixture'}}}),
            (['agent', 'get', 'resolved'], '', {'result': {'agent': {'name': ''}}}),
            (['session', 'list'], 'spawn-parent-fixture', {}),
        ]
        for args, name, expected in cases:
            with self.subTest(args=args, name=name):
                reply = subprocess.run([herdr, *args], env={**self.env, 'FIXTURE_AGENT_NAME': name},
                                       capture_output=True, text=True, timeout=2)
                self.assertEqual(reply.returncode, 0, reply.stderr)
                self.assertEqual(reply.stderr, '')
                self.assertEqual(json.loads(reply.stdout), expected)

    def test_herdr_timeout_preserves_normal_stop(self):
        def load(name, filename):
            spec = importlib.util.spec_from_file_location(name, ROOT / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        peers = load('stop_fixture_peers', 'herdr_peers.py')
        guard = load('stop_fixture_guard', 'stop-model-guard.py')
        with mock.patch.object(peers.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('fixture-herdr', 2)) as call:
            self.assertFalse(guard.worker(peers, self.env))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['timeout'], 2)

    def test_claude_worker_nested_codex_skips(self):
        self.assertEqual(self.run_case(nested=True, parent='claude'), 0)

    def test_claude_worker_nested_claude_skips(self):
        self.assertEqual(self.run_case(nested=True, parent='claude', child='claude', claude_stop=True), 0)

    def test_worker_own_codex_stop_preserves_work_log(self):
        self.assertEqual(self.run_case(), 2)

    def test_worker_own_claude_stop_preserves_ingest(self):
        self.assertEqual(self.run_case(parent='claude', claude_stop=True), 1)

    def test_manual_parent_nested_codex_keeps_stop(self):
        self.env['FIXTURE_AGENT_NAME'] = ''
        self.assertEqual(self.run_case(nested=True), 2)

    def test_main_parent_nested_codex_keeps_stop(self):
        state = self.home / 'state/orchestrator'
        state.mkdir(parents=True)
        (state / 'main').write_text('fixture-terminal')
        self.assertEqual(self.run_case(nested=True), 2)

    def test_default_server_worker_is_also_recognized(self):
        self.env['HERDR_SESSION'] = 'default'
        self.assertEqual(self.run_case(nested=True), 0)

    def test_unavailable_herdr_preserves_normal_stop(self):
        self.env['HERDR_BIN_PATH'] = '/usr/bin/false'
        self.assertEqual(self.run_case(nested=True), 2)

    def test_existing_model_free_marker_still_skips(self):
        marker = self.home / '.local/state/agent-model-free/fixture-session'
        marker.parent.mkdir(parents=True)
        marker.touch()
        self.assertEqual(self.run_case(), 0)

    def test_launchd_style_stop_keeps_ingest(self):
        self.assertEqual(self.run_case(parent='python3', claude_stop=True), 1)

    def test_nested_codex_remembers_before_ancestors_exit(self):
        self.env['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
        self.assertEqual(self.run_case(nested=True, remember=True), 0)
        marker = self.home / '.local/state/agent-model-free/fixture-session'
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        # A later detached Stop has no worker ancestor anymore.
        self.assertEqual(self.run_case(), 0)

    def test_nested_claude_remembers_json_session_id(self):
        self.env.pop('CODEX_THREAD_ID')
        self.env['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
        self.assertEqual(self.run_case(nested=True, parent='claude', child='claude', remember=True), 0)
        self.assertTrue((self.home / '.local/state/agent-model-free/fixture-session').is_file())
        self.assertEqual(self.run_case(parent='claude', claude_stop=True), 0)

    def test_worker_own_start_does_not_mark_itself(self):
        self.env['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
        self.assertEqual(self.run_case(remember=True), 0)
        self.assertFalse((self.home / '.local/state/agent-model-free/fixture-session').exists())
        self.assertEqual(self.run_case(), 2)

    def test_main_nested_start_does_not_mark_child(self):
        self.env['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
        state = self.home / 'state/orchestrator'
        state.mkdir(parents=True)
        (state / 'main').write_text('fixture-terminal')
        self.assertEqual(self.run_case(nested=True, remember=True), 0)
        self.assertFalse((self.home / '.local/state/agent-model-free/fixture-session').exists())

    def test_malformed_start_does_not_mark_inherited_parent_id(self):
        self.env['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
        self.env['FIXTURE_PAYLOAD'] = 'not-json'
        self.assertEqual(self.run_case(nested=True, remember=True), 0)
        self.assertFalse((self.home / '.local/state/agent-model-free/fixture-session').exists())

    def test_invalid_start_session_id_cannot_escape_marker_store(self):
        self.env['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
        self.env['FIXTURE_PAYLOAD'] = json.dumps({'session_id': '../escaped'})
        self.assertEqual(self.run_case(nested=True, remember=True), 0)
        self.assertFalse((self.home / '.local/state/agent-model-free').exists())


unittest.main(argv=['stop-model-guard'], verbosity=2)
PY
