"""Ask a sender once before relaying an implausible startup ETA to main."""
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from herdr_peers import main_terminal

KST = dt.timezone(dt.timedelta(hours=9))
ETA = re.compile(r"끝날 예상:\s*([0-9]{2}):([0-9]{2})\s*KST")


def suspicious(message, now):
    match = ETA.search(message)
    if not match:
        return False
    try:
        candidate = now.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
    except ValueError:
        return True
    now = now.replace(second=0, microsecond=0)
    # HH:MM has no date: a nearby time after midnight means tomorrow.
    if candidate < now and candidate + dt.timedelta(days=1) <= now + dt.timedelta(hours=6):
        candidate += dt.timedelta(days=1)
    return candidate < now or candidate > now + dt.timedelta(hours=6)


def main():
    terminal, message = sys.argv[1:]
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    now = dt.datetime.now(KST)
    if terminal != main_terminal(os.environ) or not suspicious(message, now):
        return 0
    identity = [os.environ.get(key, "unknown") for key in
                ("PANE_TELL_SOURCE_SERVER", "PANE_TELL_SOURCE_PANE", "PANE_TELL_SOURCE_SESSION", "HERDR_TASK_ID")]
    identity += [terminal]
    key = hashlib.sha256("\0".join(identity).encode()).hexdigest()
    pending = state / "pane-tell/estimate-confirmations" / key
    pending.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with pending.open("x") as stream:
            stream.write(now.isoformat() + "\n")
    except FileExistsError:
        return 0
    print(f"pane-tell: 현재 KST 시각은 {now:%Y-%m-%d %H:%M:%S} KST입니다. "
          "받은 끝날 예상이 과거이거나 6시간보다 뒤여서 아직 전달하지 않았습니다. "
          "날짜와 시각을 다시 확인하고, 고친 예상 또는 확인한 같은 예상으로 pane-tell을 한 번 더 실행하세요. "
          "긴 작업의 예상도 확인 후에는 전달합니다.")
    return 10


if __name__ == "__main__":
    sys.exit(main())
