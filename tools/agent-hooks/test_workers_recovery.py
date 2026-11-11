"""Recovery contracts with temporary state and mocked process boundaries only."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(Path(__file__).with_name(filename)))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


watch = load("watch_recovery", "workers-watch.py")
auto = load("auto_recovery", "auto-tab-close.py")
move = load("move_recovery", "workers-move")
DONE = "Full test evidence and decisions.\n" * 20 + "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(HOME=str(self.root), XDG_STATE_HOME=str(self.root / "state"),
                        HERDR_PANE_ID="w1:p2", HERDR_SOCKET_PATH="/sessions/default/herdr.sock",
                        PANE_REPORT_DIR=str(self.root / "reports"))
        self.state = self.root / "state/orchestrator"
        self.state.mkdir(parents=True)
        (self.state / "reported").mkdir()
        self.pane = dict(pane_id="w1:p2", terminal_id="term_W", label="🟢 🫡 worker", agent_session={"value": "sid-W"})
        self.agent = dict(self.pane, name="spawn-M-1", agent_status="idle", state_change_seq=2)
        self.closed = []

    def herdr(self, env, *args, socket=""):
        if args == ("agent", "list"):
            return {"agents": [self.agent] if "default" in socket else []}
        if args == ("pane", "list"):
            return {"panes": [self.pane] if "default" in socket else []}
        if args[:2] == ("pane", "close"):
            self.closed.append((socket, args[2]))
            return {}  # every failure must remain eligible for another attempt
        raise AssertionError(args)

    def test_default_close_failure_retries_and_guards_pending_or_unsent(self):
        (self.state / "reported/sid-W").touch()
        with patch.object(watch.herdr_peers, "socket_of", side_effect=lambda env, n: f"/sessions/{n}/herdr.sock"), \
             patch.object(watch.herdr_peers, "herdr", self.herdr), \
             patch.object(watch.herdr_peers, "main_terminal", return_value="term_M"), \
             patch.object(watch.atc, "last_reply", return_value=DONE), \
             patch.object(watch, "pending_messages", return_value=False):
            watch.cleanup_default(self.env, 1000)
            watch.cleanup_default(self.env, 1901)
            watch.cleanup_default(self.env, 1961)
            self.assertEqual(len(self.closed), 2)
            self.assertTrue(all("default" in sock for sock, _ in self.closed))
            with patch.object(watch, "pending_messages", return_value=True):
                watch.cleanup_default(self.env, 2021)
            self.assertEqual(len(self.closed), 2)
            self.pane["label"] = "🔴 worker"
            watch.cleanup_default(self.env, 2081)
            self.assertEqual(len(self.closed), 2)
            self.pane["label"] = "🟢 worker"
            (self.state / "reported/sid-W").unlink()
            watch.cleanup_default(self.env, 2141)
            self.assertEqual(len(self.closed), 2)

    def test_default_inventory_failure_is_not_a_stopped_server(self):
        with patch.object(watch.herdr_peers, "socket_of", return_value=None):
            with self.assertRaises(watch.WatchReadError):
                watch.cleanup_default(self.env, 1)
        with patch.object(watch.herdr_peers, "socket_of", return_value=""):
            watch.cleanup_default(self.env, 1)

    def test_tick_failure_is_classified_and_success_resets_streak(self):
        with patch.object(watch, "tick", side_effect=OSError("never persist arbitrary exception text")), \
             patch.object(watch.herdr_peers, "all_panes", return_value={}):
            watch.run_tick(self.env, 1)
            watch.run_tick(self.env, 2)
        record = json.loads((self.state / "watch-errors.json").read_text())
        self.assertEqual(record["consecutive"], 2)
        self.assertEqual(record["category"], "io")
        self.assertNotIn("never persist", (self.state / "watch-errors.log").read_text())
        with patch.object(watch, "tick", return_value=[]):
            watch.run_tick(self.env, 3)
        self.assertEqual(json.loads((self.state / "watch-errors.json").read_text())["consecutive"], 0)

    def test_default_cleanup_failure_does_not_block_workers_watch(self):
        with patch.object(watch, "cleanup_default", side_effect=OSError("cleanup unavailable")), \
             patch.object(watch.herdr_peers, "all_panes", return_value={}), \
             patch.object(watch.herdr_peers, "main_terminal", return_value="term_M"), \
             patch.object(watch.herdr_peers, "socket_of", return_value=""), \
             patch.object(watch, "refresh_main") as refresh:
            watch.run_tick(self.env, 1)
        refresh.assert_called_once()
        self.assertTrue((self.state / "watch.json").exists())
        self.assertEqual(json.loads((self.state / "watch-errors.json").read_text())["category"], "io")

    def test_repeated_failure_alert_retries_then_sends_once_per_streak(self):
        from types import SimpleNamespace
        with patch.object(watch, "tick", side_effect=watch.WatchReadError()), \
             patch.object(watch.herdr_peers, "all_panes", return_value={"term_M": ("w1:pM", {})}), \
             patch.object(watch.herdr_peers, "main_terminal", return_value="term_M"), \
             patch.object(watch.subprocess, "run", side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]) as send:
            for now in range(1, 6):
                watch.run_tick(self.env, now)
            self.assertEqual(send.call_count, 2)
        record = json.loads((self.state / "watch-errors.json").read_text())
        self.assertEqual(record["category"], "inventory")
        self.assertTrue(record["alerted"])

    def test_default_reaper_protects_main_lead_children_and_user_panes(self):
        (self.state / "reported/sid-W").touch()
        with patch.object(watch.herdr_peers, "socket_of", side_effect=lambda env, n: f"/sessions/{n}/herdr.sock"), \
             patch.object(watch.herdr_peers, "herdr", self.herdr), \
             patch.object(watch.herdr_peers, "main_terminal", return_value="term_M"), \
             patch.object(watch.atc, "last_reply", return_value=DONE), \
             patch.object(watch, "pending_messages", return_value=False):
            watch.cleanup_default(self.env, 1000)
            for label in ("👑 lead", "🔴 working"):
                self.pane["label"] = label
                watch.cleanup_default(self.env, 1901)
            self.pane["label"] = "🟢 worker"
            with patch.object(watch.herdr_peers, "main_terminal", return_value="term_W"):
                watch.cleanup_default(self.env, 1961)
            with patch.object(watch.atc, "held", return_value=True):
                watch.cleanup_default(self.env, 2021)
            self.agent["name"] = ""
            watch.cleanup_default(self.env, 2081)
        self.assertEqual(self.closed, [])

    def test_default_pending_queue_uses_receiver_socket(self):
        root = self.root / "state/pane-tell"
        (root / "waiting").mkdir(parents=True)
        (root / "receivers").mkdir()
        (root / "waiting/msg").touch()
        (root / "receivers/msg").write_text("term_W\n/sessions/default/herdr.sock\n")
        self.assertTrue(watch.pending_messages(self.env, "term_W", "/sessions/default/herdr.sock"))
        self.assertFalse(watch.pending_messages(self.env, "term_W", "/sessions/workers/herdr.sock"))
        (root / "receivers/msg").write_text("term_W\ndefault\n")
        self.assertTrue(watch.pending_messages(self.env, "term_W", "/sessions/default/herdr.sock"))
        self.assertTrue(watch.pending_messages(self.env, "term_W", "/tmp/herdr.sock"))
        self.assertFalse(watch.pending_messages(self.env, "term_W", "/sessions/personal/herdr.sock"))

    def test_archive_failure_still_starts_latest_report_delivery(self):
        with patch.object(auto.subprocess, "Popen") as spawn, patch.object(auto, "here", return_value=self.pane):
            auto.defer(DONE, self.env)
            spawn.reset_mock()
            with patch.object(auto, "save", side_effect=OSError("archive unavailable")):
                with self.assertRaises(OSError):
                    auto.defer("Monitor ended.", self.env)
            spawn.assert_called_once()
            self.assertEqual(json.loads(Path(auto.pending_file(self.env)).read_text())["reply"], "Monitor ended.")

    def test_superseded_long_report_is_archived_without_old_close_decision(self):
        with patch.object(auto.subprocess, "Popen"), patch.object(auto, "here", return_value=self.pane):
            auto.defer(DONE, self.env)
            auto.defer("Monitor ended.", self.env)
            reports = "".join(p.read_text() for p in (self.root / "reports").glob("*.md"))
            self.assertIn(DONE, reports)
            pending = json.loads(Path(auto.pending_file(self.env)).read_text())
            self.assertEqual(pending["reply"], "Monitor ended.")
            self.assertFalse(auto.should_close(pending["reply"]))
            auto.defer(None, self.env)
            self.assertFalse(Path(auto.pending_file(self.env)).exists())

    def test_remote_restart_tracks_session_and_ignores_reused_address(self):
        active = {"job": {"remote": "workers/w1:p2", "remote_session": "sid-R", "remote_name": "spawn-M-r1"}}
        tracking = {}
        got = {"panes": ["w1:p2", "w9:p8"], "agents": [
            {"pane_id": "w1:p2", "name": "unrelated", "agent_session": {"value": "sid-other"}},
            {"pane_id": "w9:p8", "name": "spawn-M-r1", "agent_session": {"value": "sid-R"}}]}
        addresses, sessions = move.bridge_targets(active, got, tracking)
        self.assertEqual(active["job"]["remote"], "workers/w9:p8")
        self.assertEqual(addresses.get("workers/w9:p8"), "job")
        self.assertNotIn("workers/w1:p2", addresses)
        self.assertEqual(sessions["sid-R"], "job")
        # A report written at an intermediate address remains attributable after the pane closes.
        active["job"]["remote"] = "workers/w1:p2"
        addresses, sessions = move.bridge_targets(active, {"panes": [], "agents": []}, tracking)
        self.assertEqual(active["job"]["remote"], "workers/w9:p8")
        self.assertEqual(sessions["sid-R"], "job")

    def test_bridge_restart_routes_identity_and_skips_other_sessions_or_archive(self):
        day = move.datetime.date.today().isoformat() + ".md"
        move.save(self.env, "moves.json", {"job": {"step": "moved", "remote": "workers/w1:p2",
                  "remote_session": "sid-R", "remote_name": "spawn-M-r1", "start": [day[:-3], 0]}})
        def entry(addr, sid, body):
            return f"\n## 11:00:00 {addr} worker\n\n세션 {sid}, 폴더 /tmp/example\n\n{body}\n"
        text = entry("workers/w9:p8", "sid-R", "correct")
        text += entry("workers/w9:p8", "sid-other", "wrong-session")
        text += entry("workers/w1:p2", "sid-other", "reused-address")
        text += entry("workers/w9:p8", "sid-R", auto.ARCHIVE_ONLY + "\n\nold-close")
        got = {"panes": ["w1:p2", "w9:p8"], "agents": [
            {"pane_id": "w1:p2", "name": "unrelated", "agent_session": {"value": "sid-other"}},
            {"pane_id": "w9:p8", "name": "spawn-M-r1", "agent_session": {"value": "sid-R"}}],
            "chunks": {day: {"text": text, "bytes": len(text.encode())}}}
        with patch.object(move, "remote", return_value=got), patch.object(move, "running", return_value=False), \
             patch.object(move, "locked", return_value=True), \
             patch.object(move, "main_pane", return_value=("", {})):
            move.bridge(self.env)
        state = move.load(self.env, "bridge.json", {})
        self.assertEqual(len(state["queue"]), 1)
        self.assertIn("correct", state["queue"][0]["text"])
        self.assertEqual(state["offsets"][day], len(text.encode()))

    def test_bridge_retries_chunks_after_inventory_failure(self):
        day = move.datetime.date.today().isoformat() + ".md"
        move.save(self.env, "moves.json", {"job": {"step": "moved", "remote": "workers/w1:p2",
                  "remote_session": "sid-old", "remote_name": "spawn-M-r1", "start": [day[:-3], 0]}})
        text = "\n## 11:00:00 workers/w9:p8 worker\n\n세션 sid-new, 폴더 /tmp/example\n\nnew report\n"
        got = {"panes": ["w9:p8"], "agents": None,
               "chunks": {day: {"text": text, "bytes": len(text.encode())}}}
        with patch.object(move, "remote", return_value=got), patch.object(move, "running", return_value=False), \
             patch.object(move, "locked", return_value=True), patch.object(move, "main_pane", return_value=("", {})):
            move.bridge(self.env)
            state = move.load(self.env, "bridge.json", {})
            self.assertEqual(state["offsets"][day], 0)
            self.assertEqual(state["queue"], [])
            self.assertEqual(state["gone"], {})
            got["agents"] = [{"pane_id": "w9:p8", "name": "spawn-M-r1", "agent_session": {"value": "sid-new"}}]
            move.bridge(self.env)
        state = move.load(self.env, "bridge.json", {})
        self.assertEqual(len(state["queue"]), 1)
        self.assertEqual(state["offsets"][day], len(text.encode()))

    def test_dirty_worktree_is_kept_then_committed_wip_pushes_before_move(self):
        import subprocess
        origin, repo, work = (self.root / n for n in ("origin.git", "repo", "work"))
        def git(cwd, *args):
            return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Recovery Test",
                                   "-c", "user.email=recovery@example.invalid", *args], cwd=cwd,
                                  check=True, text=True, capture_output=True).stdout.strip()
        git(self.root, "init", "--bare", "-b", "main", str(origin))
        git(self.root, "clone", str(origin), str(repo))
        git(repo, "commit", "--allow-empty", "-m", "init")
        git(repo, "push", "origin", "main")
        git(repo, "worktree", "add", "-b", "work-item", str(work))
        (work / "item.txt").write_text("unfinished work\n")
        rec = dict(terminal="term_W", session="sid-W", agent="spawn-M-1", pane="w1:p2", cwd=str(work), label="worker")
        moves = {"term_W": rec}
        memo = f"[인계 메모] 옮김\n폴더: {work}\n목표: finish item"
        with patch.object(move, "remote") as remote:
            move.move_one(self.env, moves, rec, memo, [])
            self.assertEqual(rec["step"], "kept")
            self.assertIn("커밋하지 않은", rec["reason"])
            remote.assert_not_called()
        git(work, "add", "item.txt")
        git(work, "commit", "-m", "wip")
        def remote(env, op, **kwargs):
            self.assertEqual(git(repo, "show", "origin/work-item:item.txt"), "unfinished work")
            return {"ok": True, "address": "workers/w9:p8", "session": "sid-R", "name": "spawn-M-r1"}
        with patch.object(move, "remote", side_effect=remote), patch.object(move, "close_local") as close:
            move.move_one(self.env, moves, rec, memo, [])
            close.assert_called_once()
        self.assertEqual(rec["remote_session"], "sid-R")


if __name__ == "__main__":
    unittest.main()
