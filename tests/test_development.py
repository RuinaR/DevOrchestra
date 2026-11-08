from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.catalog import Catalog, new_project
from devorchestra.development import DECISIONS, session_view, validate_proposal
from devorchestra.event_audit import Binding, Packet, normalize_stream
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner, Scenario


class WireAuditTests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding("alpha", "session", "run", 3, "agent", "context", "thread", "turn")

    def packet(self, seq, method, params, **fields):
        return Packet(self.binding, seq, str(seq), {"method": method, "params": params, **fields})

    def stream(self):
        context = {"threadId": "thread", "turnId": "turn"}
        item = {"type": "commandExecution", "id": "command"}
        return (
            self.packet(1, "turn/started", {"turn": {"id": "turn", "status": "inProgress"}}),
            self.packet(2, "item/started", {**context, "item": item}),
            self.packet(3, "item/commandExecution/requestApproval", {**context, "itemId": "command"}, id=9),
            self.packet(4, "item/completed", {**context, "item": {**item, "exitCode": 0, "command": "private"}}),
            self.packet(5, "thread/tokenUsage/updated", context),
            self.packet(6, "turn/completed", {"turn": {"id": "turn", "status": "completed"}}),
        )

    def test_reported_success_is_not_handoff_or_usage(self):
        events = self.stream()
        summary = normalize_stream(self.binding, events)
        self.assertEqual(("completed", "UNKNOWN", "UNKNOWN", "UNKNOWN"),
                         tuple(summary[key] for key in ("provider_status", "tokens", "cost", "handoff")))
        self.assertEqual("NOT_GRANTED", summary["events"][2]["decision"])
        self.assertEqual(0, summary["events"][3]["exit_code"])
        self.assertNotIn("private", str(summary))
        self.assertEqual("UNKNOWN", normalize_stream(self.binding, events[:-1])["provider_status"])

    def test_rejects_duplicate_gap_and_cross_session(self):
        start, item, approval, finish, usage, terminal = self.stream()
        duplicate_usage = replace(usage, sequence=6)
        replay_tool = replace(item, sequence=3, event_id="new")
        wrong_turn = replace(terminal, message={"method": "turn/completed", "params": {
            "turn": {"id": "wrong", "status": "completed"}}})
        for events in (
            (start, approval, item),
            (start, item, finish),
            (start, item, replay_tool),
            (start, item, approval, finish, usage, duplicate_usage),
            (start, item, approval, finish, usage, wrong_turn),
            (start, replace(item, binding=replace(self.binding, project_id="other"))),
            (start, replace(item, binding=replace(self.binding, development_id="old"))),
        ):
            with self.subTest(events=events), self.assertRaises(Blocked):
                normalize_stream(self.binding, events)


@unittest.skipUnless(os.name == "nt", "Windows session controller contract")
class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temp.cleanup)
        self.ledger = Ledger(Path(temp.name) / "test.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.catalog = Catalog(self.ledger)
        self.alpha = new_project("alpha", "Alpha", "Frozen synthetic goal")
        self.revision = self.catalog.save_project(self.alpha)

    def start(self, *, tokens=10, cycles=3, retries=0):
        start = self.runner.development("start", project="alpha", cycles=cycles, retries=retries,
                                       limits={"seconds": 120, "tokens": tokens, "cost_microunits": 0})
        self.identity = start["development_id"]
        return start

    def next(self, scenario=Scenario.NORMAL):
        return self.runner.development("next", development_id=self.identity, scenario=scenario)

    def run_task(self, scenario=Scenario.NORMAL):
        task = self.next(scenario)
        self.run_id = task["run_id"]
        self.assertEqual("NEED_APPROVAL", self.command("step")["state"])
        self.command("approve")
        return self.command("run")

    def command(self, action):
        return self.runner.command(action, expected_run=self.run_id)

    def test_two_tasks_keep_usage_and_frozen_revision_across_reopen(self):
        self.start()
        self.assertEqual("COMPLETED", self.run_task()["state"])
        with self.assertRaises(Blocked):
            self.runner.command("start", project="fixture-b")
        self.catalog.save_project({**self.alpha, "goal": "New goal"}, self.revision)
        self.runner = Runner(Ledger(self.ledger.path))
        self.assertEqual("COMPLETED", self.run_task()["state"])
        view = ProjectionReader(self.ledger).read("alpha")
        self.assertEqual("Frozen synthetic goal", view["run"]["goal"])
        self.assertEqual(self.revision, view["run"]["project"]["revision"])
        self.assertEqual((2, 10, 0), tuple(view["development"][key] for key in ("completed", "tokens", "cost")))
        self.assertEqual([5, 5], [run["tokens"] for run in view["development"]["runs"]])
        self.assertEqual("NEXT", view["run"]["selection"])
        self.assertIsNone(ProjectionReader(self.ledger).read("fixture-b")["development"])
        self.assertEqual("SESSION_BUDGET_LIMIT", self.next()["reason"])
        self.runner = Runner(Ledger(self.ledger.path))
        self.assertEqual(10, self.next()["synthetic_tokens"])
        self.assertEqual(10, session_view(self.ledger.read())["tokens"])

    def test_review_failure_and_invalid_manager_cannot_advance(self):
        self.start()
        self.assertEqual("FAILED", self.run_task(Scenario.REVIEW_FAIL)["state"])
        self.assertEqual("BLOCKED", self.next()["state"])
        self.assertEqual(1, session_view(self.ledger.read())["cycles"])
        self.start()
        with patch("devorchestra.runtime.mock_next", return_value={
                "decision": "NEXT", "goal_hash": "wrong", "evidence_ref": "bogus"}):
            self.assertEqual("INVALID_MANAGER_PROPOSAL", self.run_task()["reason"])
        self.assertEqual("BLOCKED", self.next()["state"])

    def test_rework_respects_retry_limit(self):
        self.start(tokens=20, retries=1)
        with patch("devorchestra.runtime.mock_next",
                   side_effect=lambda goal, proof: {"decision": "REWORK", "goal_hash": goal, "evidence_ref": proof}):
            self.assertEqual("COMPLETED", self.run_task()["state"])
            self.assertEqual("COMPLETED", self.run_task()["state"])
        self.assertEqual("RETRY_LIMIT", self.next()["reason"])
        self.assertEqual(1, session_view(self.ledger.read())["retries"])

    def test_expiry_or_cancel_prevents_cycle_and_corruption_blocks_controller(self):
        self.start()
        self.run_id = self.next()["run_id"]
        self.command("step")
        approval = self.command("approve")
        self.command("step")
        with patch("devorchestra.runtime.time", return_value=approval["expires"] + 1):
            reason = self.command("step")["reason"]
            self.assertIn(reason, ("APPROVAL_EXPIRED", "TIME_LIMIT"))
        with self.assertRaises(Blocked):
            self.command("cancel")
        if reason == "TIME_LIMIT":
            with self.assertRaisesRegex(Blocked, "Shutdown"):
                self.next()
            key = self.runner.shutdown("request")["request_id"]
            self.runner.shutdown("finalize", key)
            self.runner.shutdown("resume", key)
        self.assertEqual("BLOCKED", self.next()["state"])
        self.start()
        self.run_id = self.next()["run_id"]
        self.assertEqual("CANCELLED", self.command("cancel")["state"])
        with self.assertRaises(Blocked):
            self.command("approve")
        self.assertEqual("BLOCKED", self.next()["state"])
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("DELETE FROM events WHERE seq=1")
        with self.assertRaises(Blocked):
            self.next()

    def test_all_manager_decisions_require_matching_evidence(self):
        for decision, state in DECISIONS.items():
            proposal = {"decision": decision, "goal_hash": "goal", "evidence_ref": "proof"}
            self.assertEqual(state, validate_proposal(proposal, "goal", "proof"))
            with self.assertRaises(Blocked):
                validate_proposal(proposal, "goal", None)
