from contextlib import closing
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from devorchestra.catalog import Catalog, new_project
from devorchestra.gui import CommandCenter
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner, Scenario
from devorchestra.setup import environment_status, validate_setup


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(root.cleanup)
        self.ledger = Ledger(Path(root.name) / "runtime.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")

    def reserve(self):
        return self.runner.shutdown("request")["request_id"]

    def start(self, scenario=Scenario.NORMAL):
        return self.runner.command("start", scenario=scenario)["run_id"]

    def step(self, run):
        return self.runner.command("step", expected_run=run)

    def test_idle_duplicate_finalize_and_restart(self):
        key = self.reserve()
        self.assertEqual(key, self.reserve())
        ready = self.runner.shutdown("finalize", key)
        self.assertEqual("IDLE", ready["checkpoint"]["state"])
        self.assertEqual(ready, self.runner.shutdown("finalize", key))
        with self.assertRaises(Blocked):
            self.runner.shutdown("cancel", key)
        reopened = Runner(Ledger(self.ledger.path))
        self.assertEqual("RESUMED", reopened.shutdown("resume", key)["state"])
        with self.assertRaises(Blocked):
            reopened.shutdown("resume", key)
        self.start()

    def test_cancel_wins_before_checkpoint_and_stale_request_cannot_finalize(self):
        key = self.reserve()
        self.assertEqual("CANCELLED", self.runner.shutdown("cancel", key)["state"])
        next_key = self.reserve()
        self.assertNotEqual(key, next_key)
        with self.assertRaises(Blocked):
            self.runner.shutdown("finalize", key)

    def test_role_boundary_is_preserved_and_other_project_cannot_start(self):
        run = self.start()
        self.step(run)
        self.runner.command("approve", expected_run=run)
        self.step(run)
        first = self.reserve()
        with self.assertRaises(Blocked):
            self.step(run)
        self.runner.shutdown("cancel", first)
        manager = self.step(run)
        key = self.reserve()
        with self.assertRaises(Blocked):
            self.runner.command("start", project="fixture-b")
        with self.assertRaises(Blocked):
            self.runner.development("next", development_id="other")
        checkpoint = self.runner.shutdown("finalize", key)["checkpoint"]
        self.assertEqual((run, manager["generation"], "Manager", "DEVELOPING", 1),
                         (checkpoint["run_id"], checkpoint["generation"], checkpoint["last_completed_role"],
                          checkpoint["current_phase"], checkpoint["tokens_used"]))
        Runner(self.ledger).shutdown("resume", key)
        self.assertEqual("Developer", self.step(run)["role"])

    def test_approval_wait_and_budget_stop_remain_recorded(self):
        run = self.start()
        self.step(run)
        key = self.reserve()
        self.assertEqual("NEED_APPROVAL", self.runner.shutdown("finalize", key)["checkpoint"]["state"])
        self.runner.shutdown("resume", key)
        self.runner.command("cancel", expected_run=run)
        run = self.start(Scenario.TOKEN_LIMIT)
        self.step(run)
        self.runner.command("approve", expected_run=run)
        self.step(run)
        self.assertEqual("BLOCKED", self.step(run)["state"])
        key = self.reserve()
        self.assertEqual("BLOCKED", self.runner.shutdown("finalize", key)["checkpoint"]["state"])

    def test_interrupted_role_never_claims_safe_exit(self):
        run = self.start()
        self.step(run)
        self.runner.command("approve", expected_run=run)
        self.step(run)
        with patch("devorchestra.runtime.mock_role", side_effect=RuntimeError("synthetic interruption")):
            with self.assertRaises(RuntimeError):
                self.step(run)
        key = self.reserve()
        reopened = Runner(Ledger(self.ledger.path))
        record = reopened.shutdown("finalize", key)
        self.assertEqual(("RECOVERY_REQUIRED", "UNKNOWN"), (record["state"], record["child_drain"]))
        with self.assertRaises(Blocked):
            reopened.shutdown("resume", key)

    def test_failed_checkpoint_write_keeps_admission_closed(self):
        key = self.reserve()
        with patch.object(self.ledger, "append", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                self.runner.shutdown("finalize", key)
        with self.assertRaises(Blocked):
            self.start()
        self.assertEqual("READY", Runner(self.ledger).shutdown("finalize", key)["state"])

    def test_busy_role_acknowledges_intent_without_releasing_execution_slot(self):
        run = self.start()
        self.step(run)
        self.runner.command("approve", expected_run=run)
        self.step(run)
        from devorchestra.runtime import mock_role

        def attempt_reserve(*args):
            with ThreadPoolExecutor(max_workers=1) as pool:
                key = pool.submit(self.reserve).result(timeout=3)
            self.assertEqual(key, self.reserve())
            with self.assertRaisesRegex(Blocked, "Execution slot busy"):
                self.runner.shutdown("finalize", key)
            return mock_role(*args)

        with patch("devorchestra.runtime.mock_role", side_effect=attempt_reserve):
            self.step(run)
        key = self.reserve()
        with self.assertRaisesRegex(Blocked, "closed admission"):
            self.step(run)
        self.assertEqual("READY", self.runner.shutdown("finalize", key)["state"])

    def test_role_admission_rechecks_intent_inside_write_transaction(self):
        run = self.start()
        self.step(run)
        self.runner.command("approve", expected_run=run)
        self.step(run)
        append = self.ledger.append

        def race(count, tail, event, **options):
            if event["kind"] == "role_started":
                self.reserve()
            return append(count, tail, event, **options)

        with patch.object(self.ledger, "append", side_effect=race), patch("devorchestra.runtime.mock_role") as role:
            with self.assertRaisesRegex(Blocked, "closed admission"):
                self.step(run)
            role.assert_not_called()

    def test_request_commit_failure_rolls_back_and_generation_replay_is_rejected(self):
        key = "b" * 32
        with closing(self.ledger.connect("rw")) as db, db:
            db.execute("CREATE TRIGGER fail_anchor BEFORE UPDATE ON anchor "
                       "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.runner.shutdown("request", key, expected_generation=0)
        self.assertEqual([], self.ledger.read())
        with closing(self.ledger.connect("rw")) as db, db:
            db.execute("DROP TRIGGER fail_anchor")
        reopened = Runner(Ledger(self.ledger.path))
        self.assertEqual(key, reopened.shutdown("request", key, expected_generation=0)["request_id"])
        reopened.shutdown("cancel", key)
        with self.assertRaisesRegex(Blocked, "replayed"):
            reopened.shutdown("request", key, expected_generation=0)
        self.start()
        with self.assertRaisesRegex(Blocked, "generation"):
            reopened.shutdown("request", key, expected_generation=0)

    def test_expired_wait_never_becomes_ready(self):
        with patch("devorchestra.runtime.time", return_value=1):
            key = self.runner.shutdown("request", wait_seconds=1)["request_id"]
        with patch("devorchestra.runtime.time", return_value=2):
            result = self.runner.shutdown("finalize", key)
        self.assertEqual(("RECOVERY_REQUIRED", "WAIT_LIMIT"), (result["state"], result["reason"]))

    def test_expired_checkpoint_is_blocked_in_projection_and_controller(self):
        with patch("devorchestra.runtime.time", return_value=1):
            run = self.start()
            self.step(run)
            self.runner.command("approve", expected_run=run)
            key = self.reserve()
            self.runner.shutdown("finalize", key)
        view = ProjectionReader(self.ledger).read("fixture-b")
        self.assertEqual("fixture-a", view["shutdown"]["checkpoint"]["project_id"])
        self.assertIn("expired", view["shutdown"]["resume_problem"])
        with self.assertRaisesRegex(Blocked, "expired"):
            self.runner.shutdown("resume", key)

    def test_changed_git_base_and_corrupt_ledger_block_resume(self):
        catalog = Catalog(self.ledger)
        project = new_project("registered", "Registered", "Goal")
        revision = catalog.save_project(project)
        key = self.reserve()
        self.runner.shutdown("finalize", key)
        catalog.save_project({**project, "git_sha": "a" * 40}, revision)
        with self.assertRaisesRegex(Blocked, "Checkpoint"):
            self.runner.shutdown("resume", key)
        with closing(self.ledger.connect("rw")) as db, db:
            db.execute("UPDATE events SET hash = 'corrupt' WHERE seq = 1")
        with self.assertRaisesRegex(Blocked, "Audit"):
            self.runner.shutdown("resume", key)


class SetupTests(unittest.TestCase):
    def test_rejects_partial_colliding_or_privileged_inputs(self):
        project = new_project("portable", "Portable", "Goal")
        project["local_path"] = r"C:\source\repo"
        valid = {"project": project, "replica": r"C:\replicas\repo", "wait_seconds": 10}
        validate_setup(valid, r"C:\local\state")
        for data in ({}, {"project": project}, {**valid, "replica": r"c:\SOURCE\repo\nested"},
                     {**valid, "replica": r"C:\local\state\nested"}, {**valid, "token": "fake"},
                     {**valid, "project": {**project, "goal": "sk-synthetic0123456789"}},
                     {**valid, "project": {**project, "git_policy": {"commit": "owner_approval", "push": "disabled"}}}):
            with self.subTest(fields=list(data)), self.assertRaises(ValueError):
                validate_setup(data, r"C:\local\state")

    def test_environment_facts_never_unlock_execution(self):
        with patch("devorchestra.setup.platform.machine", return_value="ARM64"), patch(
                "devorchestra.setup.which", return_value=None):
            self.assertEqual({"windows_x64": False, "codex_found": False, "cli_signature_version": "NOT_VERIFIED",
                              "mxc": "NOT_VERIFIED", "backend": "windows-native-codex",
                              "backend_label": "Windows Native Codex",
                              "live_execution": "BLOCKED"}, environment_status())
        with patch("devorchestra.setup.which", return_value=r"C:\tools\codex.exe"):
            self.assertEqual("BLOCKED", environment_status()["live_execution"])

    def test_setup_persists_without_overwriting_registration(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            catalog = Catalog(Ledger(Path(root) / "runtime.sqlite3"))
            project = new_project("portable", "Portable", "Goal")
            project["local_path"] = r"C:\source\repo"
            setup = {"project": project, "replica": r"C:\replicas\repo", "wait_seconds": 20}
            with patch("devorchestra.setup.environment_status", return_value={"windows_x64": True}):
                revision = catalog.save_setup(setup)
                with self.assertRaises(Blocked):
                    catalog.save_setup(setup)
            with self.assertRaises(ValueError):
                catalog.save_project({**project, "local_path": setup["replica"]}, revision)
            self.assertEqual(20, catalog.read()["setups"]["portable"]["wait_seconds"])


class ShutdownWindowTests(unittest.TestCase):
    def test_close_with_no_loaded_view_or_idle_work(self):
        for view in (None, {"shutdown": {"state": "RUNNING"}, "global_run": None}):
            center = CommandCenter.__new__(CommandCenter)
            center.root = Mock()
            center.view = view
            center.gui_verification_cancel = None
            center.shutdown_future = None
            center.shutdown_command = Mock()
            center.close_requested()
            center.root.destroy.assert_called_once()
            center.shutdown_command.assert_not_called()

    def test_window_close_waits_for_checkpoint_and_never_closes_on_error(self):
        center = CommandCenter.__new__(CommandCenter)
        center.root = Mock()
        center.refresh = Mock()
        center.view = {"shutdown": {"state": "SCHEDULED", "request_id": "key"}}
        center.gui_verification_cancel = None
        center.shutdown_command = Mock()
        pending = center.shutdown_future = Future()
        center.close_requested()
        center.shutdown_command.assert_not_called()
        center.finish_shutdown(pending, "finalize", "key", 0)
        center.root.destroy.assert_not_called()
        pending.set_exception(OSError("checkpoint write failed"))
        center.finish_shutdown(pending, "finalize", "key", 0)
        center.root.destroy.assert_not_called()
        ready = Future()
        ready.set_result({"state": "READY", "request_id": "key"})
        center.view["shutdown"]["state"] = "READY"
        center.finish_shutdown(ready, "finalize", "key", 0)
        center.root.destroy.assert_called_once()
