from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner


class QuestionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "probe.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.session = self.runner.development("start", limits={
            "seconds": 60, "tokens": 10, "cost_microunits": 0,
        })
        run = self.runner.development("next", development_id=self.session["development_id"])
        self.run_id = run["run_id"]
        self.runner.command("step", expected_run=self.run_id)
        self.runner.command("approve", expected_run=self.run_id)
        self.runner.command("step", expected_run=self.run_id)

    def ask(self, kind="CLARIFICATION", **options):
        return self.runner.ask_user(self.run_id, kind, "Which behavior?", "Ambiguous fixture",
                                    "Current task", "Choose the intended behavior",
                                    "Stop this task", **options)

    def respond(self, pending, decision, answer=None):
        return self.runner.respond_question(pending["question"]["id"], self.run_id,
                                            pending["generation"], decision, answer)

    def test_restart_pending_and_answer_continues_existing_task_once(self):
        pending = self.ask()
        self.runner = Runner(Ledger(self.ledger.path))
        self.assertEqual(pending, ProjectionReader(self.ledger).read("fixture-a")["user_question"])
        self.assertIsNone(ProjectionReader(self.ledger).read("fixture-b")["user_question"])
        with self.assertRaises(Blocked):
            self.runner.command("step", expected_run=self.run_id)
        with self.assertRaisesRegex(Blocked, "no longer current"):
            self.runner.respond_question(pending["question"]["id"], self.run_id,
                                         pending["generation"] + 1, "ANSWER", "Wrong generation")
        outcome = self.respond(pending, "ANSWER", "Use the existing fixture behavior")
        self.assertEqual("COMPLETED", outcome["state"])
        with self.assertRaisesRegex(Blocked, "no longer current"):
            self.respond(pending, "ANSWER", "Replay")
        context = self.runner.manager_context("fixture-a", self.session["development_id"])
        self.assertEqual("Use the existing fixture behavior", context["clarifications"][0]["answer"])
        self.assertEqual(5, sum(event["kind"] == "role_started" for event in self.ledger.read()))

    def test_declined_design_question_stops_without_role_execution(self):
        pending = self.ask("DESIGN_DECISION")
        self.assertEqual("BLOCKED", self.respond(pending, "DECLINE")["state"])
        self.assertNotIn("role_started", [event["kind"] for event in self.ledger.read()])

    def test_expiry_cannot_extend_approval_or_continue(self):
        pending = self.ask(wait_seconds=1)
        with patch("devorchestra.runtime.time", return_value=pending["question"]["expires"] + 1):
            result = self.respond(pending, "ANSWER", "Late response")
        self.assertEqual(("question_expired", "EXPIRED", "BLOCKED"),
                         (result["kind"], result["decision"], result["state"]))
        self.assertIsNone(result["answer"])

    def test_forbidden_scope_and_approval_does_not_grant_execution(self):
        with self.assertRaisesRegex(Blocked, "Unsupported approval scope"):
            self.ask("PERMISSION", scope={"capability": "screen-capture", "target": "desktop"})
        pending = self.ask("DEPENDENCY", scope={"capability": "dependency-install", "target": "fixture-package"})
        self.assertEqual("CAPABILITY_EXECUTOR_UNVERIFIED", self.respond(pending, "APPROVE")["continuation"])
        self.assertNotIn("role_started", [event["kind"] for event in self.ledger.read()])
