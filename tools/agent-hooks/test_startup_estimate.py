"""Exercise startup ETA validation through the real pane-tell CLI."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
KST = dt.timezone(dt.timedelta(hours=9))
spec = importlib.util.spec_from_file_location("estimate", ROOT / "pane-tell-estimate.py")
estimate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(estimate)


class EstimateClock(unittest.TestCase):
    def test_six_hour_boundary_current_minute_and_past(self):
        now = dt.datetime(2026, 11, 12, 1, 9, 30, tzinfo=KST)
        for time, expected in [("01:08", True), ("01:09", False), ("07:09", False),
                               ("07:10", True), ("17:10", True), ("16:40", True),
                               ("11:40", True), ("25:00", True)]:
            with self.subTest(time=time):
                self.assertEqual(estimate.suspicious("끝날 예상: " + time + " KST", now), expected)

    def test_nearby_midnight_is_tomorrow(self):
        now = dt.datetime(2026, 11, 12, 23, 50, tzinfo=KST)
        self.assertFalse(estimate.suspicious("끝날 예상: 00:10 KST", now))
        self.assertFalse(estimate.suspicious("끝날 예상: 05:50 KST", now))
        self.assertTrue(estimate.suspicious("끝날 예상: 05:51 KST", now))
        self.assertTrue(estimate.suspicious("끝날 예상: 23:40 KST", now))


class EstimateRelay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("HERDR", "PANE_TELL", "MUNICE"))}
        self.env.update(HOME=str(self.root), XDG_STATE_HOME=str(self.root / "state"),
                        HERDR_PANE_ID="w1:p2", HERDR_BIN_PATH=str(self.root / "herdr"),
                        PANE_TELL_BATCH="0", PANE_TELL_SETTLE="0", FIXTURE=str(self.root))
        main = self.root / "state/orchestrator/main"
        main.parent.mkdir(parents=True)
        main.write_text("term_main")
        self.panes = [dict(pane_id="w1:p2", label="🟢 🫡 worker", terminal_id="term_worker",
                           agent="codex", agent_session={"value": "worker-session"}, tab_id="t"),
                      dict(pane_id="w1:p1", label="🟢 👑 main", terminal_id="term_main",
                           agent="claude", agent_session={"value": "main-session"}, tab_id="t")]
        self.save_panes()
        (self.root / "herdr").write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE'])
panes = json.loads((root / 'panes').read_text())
args = sys.argv[1:]
if args[:2] == ['pane', 'list']:
    print(json.dumps({'result': {'panes': panes}}))
elif args[:2] == ['session', 'list']:
    print(json.dumps({'sessions': [dict(name=name, running=True,
                      socket_path=str(root / 'sessions' / name / 'herdr.sock'))
                      for name in ['default', 'workers']]}))
elif args[:2] == ['pane', 'get']:
    pane = next(p for p in panes if p['pane_id'] == args[2])
    print(json.dumps({'result': {'pane': dict(pane, scroll={'offset_from_bottom': 0})}}))
elif args[:2] == ['tab', 'list']:
    print('{"result":{"tabs":[]}}')
elif args[:2] == ['pane', 'read']:
    print('────────────────────────────────────────\\n❯ \\n────────────────────────────────────────\\n  footer')
elif args[:2] == ['agent', 'prompt']:
    with (root / 'prompts').open('a') as stream:
        stream.write(json.dumps(args[2:], ensure_ascii=False) + '\\n')
else:
    sys.exit(1)
''')
        (self.root / "herdr").chmod(0o755)
        now = dt.datetime.now(KST)
        self.suspect = "task — 끝날 예상: " + (now + dt.timedelta(hours=12)).strftime("%H:%M KST")

    def save_panes(self):
        (self.root / "panes").write_text(json.dumps(self.panes))

    def tell(self, text, target="w1:p1"):
        return subprocess.run(["/bin/bash", str(ROOT / "pane-tell"), target, text],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def prompts(self):
        path = self.root / "prompts"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_suspicious_estimate_asks_sender_before_reaching_main(self):
        result = self.tell(self.suspect)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [], "unconfirmed ETA reached main")
        self.assertIn("다시", result.stdout)
        self.assertIn("KST", result.stdout)

    def test_repeated_estimate_is_confirmation_not_a_permanent_block(self):
        self.tell(self.suspect)
        self.assertEqual(self.prompts(), [])
        result = self.tell(self.suspect)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.prompts()), 1)
        self.assertIn(self.suspect, self.prompts()[0][1])

    def test_new_sender_session_must_confirm_again(self):
        self.tell(self.suspect)
        self.panes[0]["agent_session"]["value"] = "new-session"
        self.save_panes()
        self.tell(self.suspect)
        self.assertEqual(self.prompts(), [])

    def test_changed_but_still_long_estimate_is_asked_only_once(self):
        self.tell(self.suspect)
        result = self.tell("revised " + self.suspect)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.prompts()), 1)

    def test_near_future_estimate_and_normal_report_pass_through(self):
        eta = (dt.datetime.now(KST) + dt.timedelta(minutes=20)).strftime("%H:%M KST")
        for text in ["task — 끝날 예상: " + eta, "작업을 마쳤습니다."]:
            result = self.tell(text)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.prompts()), 2)

    def test_non_main_receiver_does_not_gate_estimates(self):
        (self.root / "state/orchestrator/main").write_text("another-terminal")
        result = self.tell(self.suspect)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.prompts()), 1)

    def test_workers_to_default_main_asks_before_batching(self):
        self.env.update(HERDR_SOCKET_PATH=str(self.root / "sessions/workers/herdr.sock"),
                        PANE_TELL_BATCH="120")
        result = self.tell(self.suspect, "default/w1:p1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [])
        self.assertIn("다시", result.stdout)
        self.assertFalse((self.root / "state/pane-tell/batch").exists())

    def test_workers_receiver_with_same_terminal_id_is_not_main(self):
        self.env["HERDR_SOCKET_PATH"] = str(self.root / "sessions/workers/herdr.sock")
        result = self.tell(self.suspect)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.prompts()), 1)


if __name__ == "__main__":
    unittest.main()
