from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from devorchestra.catalog import Catalog, new_project
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner, Scenario


class RequirementWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "source"
        self.source.mkdir()
        self.document = self.source / "goal.md"
        self.document.write_bytes(b"Synthetic requirement text")
        self.ledger = Ledger(root / "state" / "runtime.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.catalog = Catalog(self.ledger)
        self.project = {**new_project("sample", "Sample", "Fixture goal"), "local_path": str(self.source)}
        self.revision = self.catalog.save_project(self.project)

    def test_revisions_are_frozen_in_session_and_visible_in_projection(self):
        first = self.catalog.register_requirement("sample", "goal.md", "GOAL")
        self.assertEqual(first, self.catalog.register_requirement("sample", "goal.md", "GOAL"))
        session = self.runner.development("start", project="sample",
                                          limits={"seconds": 60, "tokens": 10, "cost_microunits": 0})
        context = self.runner.manager_context("sample", session["development_id"])
        self.assertEqual([{"ref": first, "text": "Synthetic requirement text"}], context["requirements"])
        self.assertNotIn("Synthetic requirement text", str(self.ledger.read()))
        self.document.write_bytes(b"Synthetic improvement text")
        second = self.catalog.register_requirement("sample", "goal.md", "IMPROVEMENT", related=first["id"])
        self.assertEqual((2, first["id"], first["id"]),
                         (second["version"], second["supersedes"], second["related"]))
        with self.assertRaisesRegex(Blocked, "Frozen requirement changed"):
            self.runner.manager_context("sample", session["development_id"])
        run = self.runner.development("next", development_id=session["development_id"])
        view = ProjectionReader(self.ledger).read("sample")
        self.assertEqual([first], run["requirements"])
        self.assertEqual([first], view["workflow"]["cycle_requirements"])
        self.assertEqual([second], view["requirements"])
        self.assertEqual("NOT_AVAILABLE", view["workflow"]["user_test_status"])
        self.runner.command("step", expected_run=run["run_id"])
        self.runner.command("approve", expected_run=run["run_id"])
        self.runner.command("run", expected_run=run["run_id"])
        finished = ProjectionReader(self.ledger).read("sample")["workflow"]
        self.assertEqual(("COMPLETED", "SYNTHETIC", "NOT_AVAILABLE"),
                         (finished["latest_cycle_state"], finished["latest_cycle_evidence"],
                          finished["user_test_status"]))
        self.runner.development("stop", development_id=session["development_id"])
        successor = self.runner.development("start", project="sample",
                                            limits={"seconds": 60, "tokens": 10, "cost_microunits": 0})
        self.assertEqual([second], successor["frozen"]["requirements"])
        context = self.runner.manager_context("sample", successor["development_id"])
        self.assertEqual("SYNTHETIC", context["previous_cycle"]["evidence"])
        self.assertEqual(run["run_id"], context["previous_cycle"]["run_id"])
        self.assertNotIn("Synthetic improvement text", str(self.catalog.ledger.read()))
        self.assertEqual(b"Synthetic improvement text", self.document.read_bytes())

    def test_changed_unregistered_document_blocks_session_start(self):
        self.catalog.register_requirement("sample", "goal.md", "GOAL")
        self.document.write_bytes(b"Changed without registration")
        with self.assertRaisesRegex(Blocked, "Frozen requirement changed"):
            self.runner.development("start", project="sample",
                                    limits={"seconds": 60, "tokens": 10, "cost_microunits": 0})
        self.assertEqual([], self.ledger.read())

    def test_reviewer_rework_uses_new_cycle_and_stops_repeated_finding(self):
        session = self.runner.development("start", project="sample",
                                          limits={"seconds": 60, "tokens": 10, "cost_microunits": 0},
                                          cycles=4, retries=2)
        identity = session["development_id"]
        for selection in ("INITIAL", "REWORK"):
            run = self.runner.development("next", development_id=identity, scenario=Scenario.REVIEW_REWORK)
            self.assertEqual(selection, run["selection"])
            self.runner.command("step", expected_run=run["run_id"])
            self.runner.command("approve", expected_run=run["run_id"])
            self.runner.command("run", expected_run=run["run_id"])
            roles = [event for event in self.ledger.read()
                     if event["kind"] == "role_finished" and event["run_id"] == run["run_id"]]
            self.assertEqual(["Manager", "Developer", "Reviewer"], [event["role"] for event in roles])
            self.assertEqual(["fixture.txt"], roles[0]["selected_task"]["fixture_contract"]["paths"])
            self.assertEqual("NEW_REWORK_CYCLE", roles[-1]["handoff"])
        result = self.runner.development("next", development_id=identity)
        self.assertEqual(("NO_PROGRESS", "REPEATED_REVIEW_FINDING"), (result["state"], result["reason"]))

    def test_rejects_unsafe_paths_foreign_relations_and_root_rebinding(self):
        first = self.catalog.register_requirement("sample", "goal.md", "GOAL")
        for path in ("../goal.md", ".ai-local/private.md", ".git/file.txt", "goal.md:stream"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.catalog.register_requirement("sample", path, "GOAL")
        self.catalog.save_project({**self.project, "project_id": "other"})
        with self.assertRaisesRegex(Blocked, "does not belong"):
            self.catalog.register_requirement("other", "goal.md", "IMPROVEMENT", related=first["id"])
        with self.assertRaisesRegex(Blocked, "original project folder"):
            self.catalog.save_project({**self.project, "local_path": str(self.source.parent)}, self.revision)
