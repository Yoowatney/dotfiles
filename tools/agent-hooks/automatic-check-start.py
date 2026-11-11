"""Bounded check startup with one retry and explicit failed-pane cleanup receipts."""
import json
import os
import re
import signal
import subprocess
import time

ADDRESS = re.compile(r"^(?:\w+/)?w\w+:p\w+$", re.M)
ATTEMPT_TIMEOUT = 45  # Two starts plus cancellation (5s each) leave time for immediate error delivery within 120s.


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def start(args, env, receipt_prefix, context, save, *, attempt_timeout=ATTEMPT_TIMEOUT):
    """Return (confirmed address, error). Persist attempts before starting, so a repeated Stop never duplicates a start."""
    attempts = context.setdefault("start_attempts", [])
    if attempts and attempts[-1].get("address"):
        return attempts[-1]["address"], ""
    if attempts and "error" not in attempts[-1] and "address" not in attempts[-1]:
        return "", "previous check start interrupted; cleanup unconfirmed"
    if attempts and attempts[-1].get("cleanup") not in ("closed", "not-opened"):
        return "", attempts[-1]["error"]
    for number in range(len(attempts), 2):
        receipt = f"{receipt_prefix}.{number + 1}.json"
        attempt = {"started_at": time.time()}
        attempts.append(attempt)
        save()
        output = error = ""
        try:
            process = subprocess.Popen(args, env={**env, "PANE_SPAWN_CHECK_RECEIPT": receipt},
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            try:
                output, error = process.communicate(timeout=min(attempt_timeout, ATTEMPT_TIMEOUT))
            except subprocess.TimeoutExpired:
                output, error = stop_group(process)
                error = "check start timed out; " + error
            match = ADDRESS.search(output or "")
            if process.returncode == 0 and match:
                attempt.update(address=match[0], finished_at=time.time())
                save()
                return match[0], ""
            error = (error or output or f"exit {process.returncode}").strip()[:1200]
        except OSError as exc:
            error = str(exc)[:1200]
        attempt.update(error=error, finished_at=time.time())
        # pane-spawn closes an unsuccessful attempt before writing 'closed'. An in-flight create or
        # failed cleanup cannot be safely retried: the original report must reach main immediately.
        try:
            with open(receipt, encoding="utf-8") as stream:
                cleanup = json.load(stream).get("status")
        except FileNotFoundError:
            cleanup = "not-opened"  # launcher did not reach pane creation
        except (OSError, ValueError, TypeError, AttributeError):
            cleanup = "unknown"
        attempt["cleanup"] = cleanup
        if cleanup not in ("closed", "not-opened"):
            attempt["error"] += "; cleanup unconfirmed (no duplicate check started)"
            save()
            break
        save()
    return "", " | ".join(f"attempt {i + 1}: {a['error']}" for i, a in enumerate(attempts))
