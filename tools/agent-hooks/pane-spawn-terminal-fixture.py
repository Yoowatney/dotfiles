#!/usr/bin/env python3
"""Replay a real launch through a cold PTY without starting an agent."""
import json
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time


def check_launch(launch, helper):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output = root / 'captured.json'
        binary = root / 'capture'
        binary.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                          'p=' + repr(str(output)) + '\n'
                          'open(p+".tmp","w").write(json.dumps({"args":sys.argv[1:],"env":dict(os.environ)}))\n'
                          'os.replace(p+".tmp",p)\n')
        binary.chmod(0o700)
        (root / '.zshrc').write_text('codex() { ' + shlex.join([sys.executable, str(helper), str(binary)]) + ' "$@"; }\n')
        command = shlex.join(['codex', *launch['args']])
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
        env.update(launch['env'], ZDOTDIR=tmp, TERM='xterm-256color', HERDR_CODEX_STARTUP_PROFILE='full')
        master, slave = pty.openpty()
        # Queue before the shell exists: its initial canonical mode is the
        # failure boundary, independent of shell startup speed or sleeps.
        try:
            os.write(master, (command + '\r').encode())
            process = subprocess.Popen(['/bin/zsh', '-di'], stdin=slave, stdout=slave, stderr=slave,
                                       env=env, start_new_session=True)
            try:
                end = time.monotonic() + 5
                while not output.exists() and time.monotonic() < end:
                    if select.select([master], [], [], .05)[0]:
                        os.read(master, 65536)
                assert output.exists(), 'the initial terminal command was lost or truncated'
                result = json.loads(output.read_text())
                args = result['args']
                assert args == json.loads(launch['env']['HERDR_CODEX_LAUNCH_ARGS']), args
                assert args[:4] == ['--sandbox', 'workspace-write', '--ask-for-approval', 'never'], args
                assert args.count('--sandbox') == 1, args
                assert args[-2:] == ['--model', 'example'], args
                for n in range(1, 25):
                    assert 'mcp_servers.example_' + str(n) + '.enabled=false' in args, args
                assert '--disable' in args and 'apps' in args and 'plugins' in args, args
                assert not any(k.startswith('HERDR_CODEX_LAUNCH') for k in result['env'])
                assert len(command.encode()) < 1024, len(command.encode())
            finally:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        finally:
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    check_launch(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
