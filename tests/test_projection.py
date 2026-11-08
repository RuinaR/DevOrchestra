"""Exercise the GUI data and rendering paths without creating a desktop window."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from xml.etree.ElementTree import fromstring

from devorchestra.gui import CommandCenter, audit_rows, evidence_text
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner, Scenario
from devorchestra.sandbox_profile import restricted_profile


@unittest.skipUnless(os.name == "nt", "Windows P1 ledger integration")
class ProjectionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(Path(tmp.name) / "projection.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.reader = ProjectionReader(self.ledger)

    def start(self, project="fixture-a", scenario=Scenario.NORMAL):
        self.run_id = self.runner.command("start", project=project, scenario=scenario)["run_id"]

    def command(self, action):
        return self.runner.command(action, expected_run=self.run_id)

    def view(self, project="fixture-a"):
        return self.reader.read(project)

    def approve(self):
        self.command("step")
        self.command("approve")

    def test_empty_and_verified_pipeline(self):
        empty = self.view()
        self.assertEqual((1, (0, ""), "IDLE"), (empty["schema_version"],
                         empty["snapshot_version"], empty["project_state"]))
        self.assertIsNone(empty["resources"]["elapsed"])
        self.assertEqual([], empty["events"])
        self.assertEqual("NOT_VERIFIED", empty["safety"]["noninterference"])
        self.assertEqual("BLOCKED", empty["safety"]["real_execution"])
        self.assertTrue(empty["safety"]["isolation_mode"].startswith("Windows Native Codex CANDIDATE"))
        self.assertEqual(["BLOCKED"] * 3, [empty["safety"][name]
                         for name in ("files", "network", "credentials")])
        self.assertEqual("24fc3e7", empty["safety"]["security_baseline"]["git_sha"][:7])
        self.assertTrue(all(card["start"] is None for card in empty["role_cards"].values()))
        self.assertEqual("0ebe8db", empty["milestone_gates"][0]["git_sha"][:7])
        self.start()
        self.command("step")
        self.assertEqual("DENY", self.view()["safety"]["approval"]["decision"])
        self.command("approve")
        self.command("run")
        view = self.view()
        self.assertEqual("COMPLETED", view["project_state"])
        self.assertEqual("PASS", view["milestone_gates"][0]["state"])
        self.assertEqual(empty["milestone_gates"], view["milestone_gates"])
        self.assertTrue(all(phase["completed_with_evidence"] for phase in view["pipeline_phases"]))
        self.assertEqual(5, view["resources"]["synthetic_tokens"])
        self.assertEqual("NOT_APPLICABLE", view["resources"]["measured_tokens"])
        self.assertIsNone(view["active_role"])
        self.assertEqual(view["snapshot_version"], self.view()["snapshot_version"])
        self.assertEqual(view["snapshot_version"][0], len(self.ledger.read()))

    def test_filters_preserve_project_run_and_role_boundaries(self):
        self.start()
        self.approve()
        self.command("run")
        first = self.run_id
        self.start(scenario=Scenario.REVIEW_FAIL)
        self.approve()
        self.command("run")
        failure = self.view()
        self.assertEqual("FAILED", failure["pipeline_phases"][2]["state"])
        self.assertFalse(failure["pipeline_phases"][2]["completed_with_evidence"])
        self.assertEqual("PASS", failure["milestone_gates"][0]["state"])
        records = audit_rows(failure, first, "Verifier")
        self.assertEqual(["role_finished", "role_started"], [e["kind"] for e in records])
        self.assertIn('"test": "fixture-transition"', evidence_text(records[0]))
        self.start("fixture-b", Scenario.TIME_LIMIT)
        self.command("step")
        other = self.view("fixture-b")
        self.assertEqual("BLOCKED", other["project_state"])
        self.assertEqual("TIME_LIMIT", other["safety"]["latest"]["reason"])
        self.assertEqual(0, other["resources"]["time_budget"])
        self.assertEqual(failure["milestone_gates"], other["milestone_gates"])
        self.assertEqual([], audit_rows(other, first, "Verifier"))
        self.assertEqual("FAILED", self.view()["project_state"])
        self.assertEqual("BLOCKED", self.view()["global_state"])

    def test_reopen_unfinished_role_and_explicit_recovery(self):
        self.start()
        self.approve()
        self.command("step")
        with patch("devorchestra.runtime.mock_role", side_effect=RuntimeError("aborted fixture")):
            with self.assertRaises(RuntimeError):
                self.command("step")
        reopened = ProjectionReader(self.ledger).read("fixture-a")
        self.assertIsNone(reopened["active_role"])
        self.assertEqual("Manager", reopened["unconfirmed_role"])
        self.assertEqual("UNKNOWN", reopened["pipeline_phases"][0]["state"])
        self.command("recover")
        recovered = self.view()
        self.assertIsNone(recovered["unconfirmed_role"])
        self.assertEqual("RECOVERING", recovered["pipeline_phases"][0]["state"])
        self.assertEqual("UNKNOWN", recovered["role_cards"]["Manager"]["latest"]["result"])
        self.command("cancel")
        self.assertEqual("CANCELLED", self.view()["project_state"])

    def test_gui_refresh_clears_invalid_snapshot(self):
        self.start()
        center = CommandCenter.__new__(CommandCenter)
        center.reader = self.reader
        center.project_ids = ["fixture-a"]
        center.project = Mock()
        center.project.current.return_value = 0
        center.view = None
        center.observation = Mock()
        center.render = Mock(wraps=center.render)
        center.clear = Mock(wraps=center.clear)
        for attr in ("global_badge", "mission", "goal", "project_states", "gates",
                     "safety", "resources", "run_filter", "role_filter", "render_audit"):
            setattr(center, attr, Mock())
        center.phases = [(name, Mock()) for name in ("PLAN", "DEV", "REV", "VFY", "DEC")]
        center.cards = {"Manager": (Mock(), Mock())}
        center.refresh()
        center.render.assert_called_once()
        center.cards["Manager"][0].configure.assert_called_with(text="NO EXECUTION", fg="#b9cde4")
        center.refresh()
        center.render.assert_called_once()
        self.approve()
        self.command("run")
        center.refresh()
        center.phases[0][1].configure.assert_called_with(text="PLAN\nRECORDED OK", fg="#78efca")
        center.cards["Manager"][0].configure.assert_called_with(text="PASS · CHECKPOINTING", fg="#78efca")
        center.project_ids.append("fixture-b")
        center.project.current.return_value = 1
        center.select_project(None)
        center.cards["Manager"][0].configure.assert_called_with(text="NO EXECUTION", fg="#b9cde4")
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("DELETE FROM events WHERE seq=1")
        center.refresh()
        self.assertIsNone(center.view)
        center.global_badge.configure.assert_called_with(text="GLOBAL / BLOCKED", fg="#ff9da9")

    def test_reader_rejects_replaced_verified_history(self):
        self.start()
        self.view()
        other = Ledger(self.ledger.path.with_name("second.sqlite3"))
        runner = Runner(other)
        runner.command("init")
        runner.command("start", project="fixture-b")
        self.reader.ledger = other
        with self.assertRaisesRegex(Blocked, "same sequence"):
            self.view()
        runner.command("step", expected_run=other.read()[0]["run_id"])
        with self.assertRaisesRegex(Blocked, "replaced"):
            self.view()


class SandboxProfileTests(unittest.TestCase):
    def test_profile_denies_redirection_and_has_no_host_paths_or_command(self):
        root = fromstring(restricted_profile())
        self.assertEqual("Configuration", root.tag)
        self.assertEqual(
            {"Networking": "Disable", "vGPU": "Disable", "AudioInput": "Disable",
             "VideoInput": "Disable", "ProtectedClient": "Enable",
             "ClipboardRedirection": "Disable", "PrinterRedirection": "Disable"},
            {node.tag: node.text for node in root},
        )
