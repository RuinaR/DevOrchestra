import os
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.dashboard import project_view, render
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.policy import Capability, Grant, Request
from devorchestra.runtime import Runner, Scenario


@unittest.skipUnless(os.name == "nt", "Windows execution contract")
class P1Tests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger = Ledger(self.root / "p1.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")

    def start(self, scenario=Scenario.NORMAL):
        self.run_id = self.runner.command("start", scenario=scenario)["run_id"]

    def command(self, action):
        return self.runner.command(action, expected_run=self.run_id)

    def plan(self):
        self.assertEqual("NEED_APPROVAL", self.command("step")["state"])
        self.command("approve")
        self.assertEqual("PLANNING", self.command("step")["state"])

    def dashboard(self):
        return project_view(self.ledger.read())

    def test_sequence_and_read_model(self):
        self.assertIn("No recorded run", render(self.dashboard()))
        self.start()
        self.assertEqual("NEED_APPROVAL", self.command("step")["state"])
        self.assertIn("[NEED_APPROVAL]", render(self.dashboard()))
        self.command("approve")
        self.command("step")
        self.assertIn("No execution recorded", render(self.dashboard()))
        for phase, next_state in zip(
            ("PLANNING", "DEVELOPING", "REVIEWING", "VERIFYING", "CHECKPOINTING"),
            ("DEVELOPING", "REVIEWING", "VERIFYING", "CHECKPOINTING", "COMPLETED"),
        ):
            self.assertEqual(next_state, self.command("step")["state"])
            view = self.dashboard()
            self.assertEqual(next_state, view["state"])
            self.assertIn(phase, view["completed_phases"])
            self.assertEqual(view, self.dashboard())
            self.assertIn(f"[done {phase}]", render(view))
        view = self.dashboard()
        self.assertIn("[COMPLETED]", render(view))
        roles = [e for e in view["events"] if e["kind"].startswith("role_")]
        self.assertEqual(10, len(roles))
        for started, finished in zip(roles[::2], roles[1::2]):
            self.assertEqual("role_started", started["kind"])
            self.assertEqual("role_finished", finished["kind"])
            self.assertEqual(started["agent_run_id"], finished["agent_run_id"])
            self.assertEqual([], finished["owned_children"])
        self.assertEqual(["Manager", "Developer", "Reviewer", "Verifier", "Manager"],
                         [e["role"] for e in roles[::2]])
        self.assertEqual(5, view["synthetic_tokens"])
        self.runner.command("start", project="fixture-b")
        self.assertEqual("COMPLETED", project_view(self.ledger.read(), "fixture-a")["state"])
        self.assertEqual("PRECHECK", self.dashboard()["state"])
        self.assertNotIn("draft", render(self.dashboard()))

    def test_role_failures(self):
        for scenario, role in ((Scenario.AGENT_ERROR, "Developer"),
                               (Scenario.REVIEW_FAIL, "Reviewer"),
                               (Scenario.VERIFY_FAIL, "Verifier")):
            with self.subTest(scenario=scenario):
                self.start(scenario)
                self.plan()
                for _ in range(4):
                    event = self.command("step")
                    if event["state"] == "FAILED":
                        break
                self.assertEqual(("FAILED", role, "FAIL"),
                                 (event["state"], event["role"], event["result"]))
                self.assertIn("[FAILED]", render(self.dashboard()))
                with self.assertRaises(Blocked):
                    self.command("step")

    def test_pause_resume_cancel_and_admission(self):
        self.start()
        self.plan()
        self.assertEqual("PAUSED", self.command("pause")["state"])
        with self.assertRaises(Blocked):
            self.runner.command("start", project="fixture-b")
        with self.assertRaises(Blocked):
            self.command("step")
        self.assertEqual("PLANNING", self.command("resume")["state"])
        self.assertEqual("CANCELLED", self.command("cancel")["state"])
        self.runner.command("start", project="fixture-b")
        with self.assertRaises(Blocked):
            self.command("step")  # stale run ID

    def test_budgets(self):
        for scenario, reason in ((Scenario.TIME_LIMIT, "TIME_LIMIT"),
                                 (Scenario.TOKEN_LIMIT, "TOKEN_LIMIT")):
            with self.subTest(scenario=scenario):
                self.start(scenario)
                if scenario is Scenario.TOKEN_LIMIT:
                    self.plan()
                result = self.command("step")
                self.assertEqual(("BLOCKED", reason), (result["state"], result["reason"]))
                self.assertIn("[BLOCKED]", render(self.dashboard()))
                self.assertEqual([], self.dashboard()["completed_phases"])
                key = self.runner.shutdown("request")["request_id"]
                self.runner.shutdown("finalize", key)
                self.runner.shutdown("resume", key)

    def test_permission_scope(self):
        request = Request("fixture-a", "task", "run", "Manager", Capability.READ,
                          "mock.inspect", "mock://fixture-a/fixture.txt")
        grant = Grant(request, 1, 2)
        self.assertTrue(grant.permits(request, 1))
        self.assertFalse(grant.permits(request, 2))
        for changes in ({"target": "../fixture.txt"}, {"tool": "shell"}, {"role": "Developer"},
                        {"project": "fixture-b"}, {"run": "other"}, {"task": "other"}):
            self.assertFalse(grant.permits(replace(request, **changes), 1))
        for capability in set(Capability) - {Capability.READ}:
            forbidden = replace(request, capability=capability)
            self.assertFalse(Grant(forbidden, 1, 2).permits(forbidden, 1))

    def test_audit_write_failure_blocks_execution(self):
        self.start()
        self.plan()
        with patch.object(self.ledger, "append", side_effect=sqlite3.OperationalError("fixture I/O failure")), \
                patch("devorchestra.runtime.mock_role") as role:
            with self.assertRaises(sqlite3.OperationalError):
                self.command("step")
            role.assert_not_called()
        self.assertEqual("PLANNING", self.dashboard()["state"])
        original_append = self.ledger.append

        def fail_result(count, tail, event, **options):
            if event["kind"] == "role_finished":
                raise sqlite3.OperationalError("fixture result I/O failure")
            return original_append(count, tail, event, **options)

        with patch.object(self.ledger, "append", side_effect=fail_result):
            with self.assertRaises(sqlite3.OperationalError):
                self.command("step")
        with self.assertRaises(Blocked):
            self.command("step")
        self.assertIn("LIVE STATUS UNKNOWN", render(self.dashboard()))

    def test_audit_tail_deletion_detected(self):
        self.start()
        from contextlib import closing
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("DELETE FROM events WHERE seq=1")
        with self.assertRaises(Blocked):
            self.command("step")
        with self.assertRaises(Blocked):
            self.dashboard()

    def test_owned_process_crash_and_global_slot(self):
        self.start()
        self.plan()
        code = """
import os
import sys
from pathlib import Path
from unittest.mock import patch
from devorchestra.ledger import Ledger
from devorchestra.runtime import Runner

def abort_role(*args):
    print("ROLE_ENTERED", flush=True)
    sys.stdin.readline()
    os._exit(91)

with patch("devorchestra.runtime.mock_role", abort_role):
    Runner(Ledger(Path(sys.argv[1]))).command("step", expected_run=sys.argv[2])
"""
        with subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(self.ledger.path), self.run_id],
            text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        ) as child:
            try:
                self.assertEqual("ROLE_ENTERED", child.stdout.readline().strip())
                self.assertIn("LIVE STATUS UNKNOWN", render(self.dashboard()))
                with self.assertRaisesRegex(Blocked, "slot busy"):
                    Runner(Ledger(self.root / "other.sqlite3")).command("init")
            finally:
                try:
                    child.communicate("exit\n", timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
            self.assertEqual(91, child.returncode)
        fresh = Runner(Ledger(self.ledger.path))
        with self.assertRaises(Blocked):
            fresh.command("start", project="fixture-b")
        with self.assertRaises(Blocked):
            fresh.command("step", expected_run=self.run_id)
        recovered = fresh.command("recover", expected_run=self.run_id)
        self.assertEqual(("RECOVERING", "UNKNOWN"), (recovered["state"], recovered["result"]))
        self.assertIn("UNKNOWN", render(self.dashboard()))
        with self.assertRaises(Blocked):
            fresh.command("resume", expected_run=self.run_id)
        self.assertEqual("CANCELLED", fresh.command("cancel", expected_run=self.run_id)["state"])
        fresh.command("start", project="fixture-b")
