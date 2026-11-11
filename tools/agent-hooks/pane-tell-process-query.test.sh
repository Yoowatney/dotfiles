#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
mkdir "$SANDBOX/bin" "$SANDBOX/tmp"
printf '#!/usr/bin/env bash\necho "pgrep: Cannot get process list" >&2\nexit 2\n' > "$SANDBOX/bin/pgrep"
chmod +x "$SANDBOX/bin/pgrep"

# Stop a regressed suite at its first fixture rather than running its delivery
# cases with a broken process barrier. The real suite must reject the query
# failure before it starts any delivery fixture.
python3 -I -c '
import os
from pathlib import Path
import signal
import subprocess
import sys

root, sandbox = map(Path, sys.argv[1:])
env = dict(os.environ)
env["PATH"] = str(sandbox / "bin") + os.pathsep + env["PATH"]
env["TMPDIR"] = str(sandbox / "tmp")
proc = subprocess.Popen(
    ["bash", str(root / "pane-tell.test.sh")], env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    start_new_session=True,
)
output = []
continued = False
try:
    for line in proc.stdout:
        output.append(line)
        if line.startswith("ok "):
            continued = True
            break
finally:
    if proc.poll() is None and continued:
        os.killpg(proc.pid, signal.SIGKILL)
    status = proc.wait()
text = "".join(output)
if continued or status == 0 or "FAIL process query unavailable" not in text:
    print("FAIL process-query denial must abort before delivery fixtures", file=sys.stderr)
    print(f"exit={status}, continued={continued}", file=sys.stderr)
    print(text, end="", file=sys.stderr)
    sys.exit(1)
print("ok   process-query denial aborts before delivery fixtures")
' "$ROOT" "$SANDBOX"
