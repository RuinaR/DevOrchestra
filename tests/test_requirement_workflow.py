import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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

    def supervised_result(self, **changes):
        self.catalog.register_requirement("sample", "goal.md", "GOAL")
        prompt = self.runner.supervised_handoff("start", "sample", paths=["src/main.py"])
        envelope = json.loads(prompt.split("\n\n", 1)[1])["response_envelope"]
        envelope["result"] = dict(
            disposition="PROPOSED", priority_reason="Fixture priority", task_spec="Implement sample",
            proposed_paths=["src/main.py"], acceptance_checks=self.project["acceptance"], stop_conditions=[],
            work_kind="PRODUCT", product_outcome="Sample is available", validation_reason="Check sample",
            approach="Implement the approved fixture",
        )
        envelope["result"].update(changes)
        return envelope

    def test_supervised_manager_import_and_developer_handoff_after_restart(self):
        envelope = self.supervised_result()
        request = envelope["request_id"]
        def submit(value):
            return self.runner.supervised_handoff("submit", "sample", expected_request=request,
                                                  raw=json.dumps(value).encode())

        with self.assertRaisesRegex(Blocked, "awaiting a result"):
            self.runner.command("start", project="fixture-a")
        for change in ({"generation": True}, {"baseline_sha256": "wrong"},
                       {"result": {**envelope["result"], "proposed_paths": ["outside.py"]}}):
            with self.assertRaises(Blocked):
                submit({**envelope, **change})
        self.assertEqual("PROPOSED", submit(envelope))
        with self.assertRaises(Blocked):
            submit(envelope)
        self.runner = Runner(Ledger(self.ledger.path))
        replica = self.source.with_name("replica")
        replica.mkdir()
        draft = self.runner.supervised_handoff("developer", "sample", expected_request=request,
                                               raw=json.dumps(envelope).encode(), replica_path=str(replica))
        self.assertIn("src/main.py", draft)
        self.assertIn("separate owner approval", draft)
        self.assertNotIn("Implement sample", str(self.ledger.read()))
        view = ProjectionReader(self.ledger).read("sample")
        self.assertEqual("NOT_VERIFIED", view["supervised_handoff"]["execution_evidence"])
        self.assertEqual("NOT_AVAILABLE", view["workflow"]["user_test_status"])
        self.assertIsNone(view["development"])
        def role(action, prompt, result):
            bound = json.loads(prompt.split("\n\n", 1)[1])["response_envelope"]
            bound["result"] = result
            return self.runner.supervised_handoff(action, "sample", expected_request=request,
                                                  raw=json.dumps(bound).encode())

        change_report = {"disposition": "READY_FOR_REVIEW", "summary": "Fixture implementation",
                         "changes": [{"path": "src/main.py", "reason": "Implement acceptance criterion"}],
                         "validation_attempts": [], "unfinished": []}
        with self.assertRaisesRegex(Blocked, "No observed change"):
            role("developer_submit", draft, change_report)
        (replica / "src").mkdir()
        (replica / "src" / "main.py").write_bytes(b"print('fixture')")
        outside = replica / "outside.txt"
        outside.write_bytes(b"Unexpected")
        with self.assertRaisesRegex(Blocked, "outside the approved scope"):
            role("developer_submit", draft, change_report)
        outside.unlink()
        reviewer = role("developer_submit", draft, change_report)
        self.assertIn("observed_changes", reviewer)
        self.assertEqual("ACCEPT_FOR_VERIFICATION", role(
            "reviewer_submit", reviewer,
            {"decision": "ACCEPT_FOR_VERIFICATION", "findings": [], "coverage_gaps": []},
        ))
        plan = {"commands": ["fixture package"], "environment": "Synthetic Windows fixture",
                "output_folder": "dist", "artifacts": ["dist/app.zip"], "type": "PACKAGE"}
        verifier = self.runner.supervised_handoff("verifier", "sample", expected_request=request,
                                                 raw=json.dumps(plan).encode())
        (replica / "dist").mkdir()
        artifact = replica / "dist" / "app.zip"
        artifact.write_bytes(b"Synthetic output")
        from hashlib import sha256
        receipt = {"environment": plan["environment"], "commands": plan["commands"], "exit_codes": [0],
                   "artifacts": {"dist/app.zip": sha256(artifact.read_bytes()).hexdigest()},
                   "criteria": self.project["acceptance"]}
        with self.assertRaisesRegex(Blocked, "does not match"):
            role("verifier_submit", verifier, {**receipt, "artifacts": {"dist/app.zip": "bad"}})
        self.assertEqual("OWNER_REPORTED", role("verifier_submit", verifier, receipt))
        view = ProjectionReader(self.ledger).read("sample")
        self.assertEqual("NOT_AVAILABLE", view["supervised_handoff"]["user_test_status"])
        self.assertNotIn("print('fixture')", str(self.ledger.read()))

    def test_supervised_question_expiry_and_changed_requirements(self):
        envelope = self.supervised_result(disposition="CLARIFICATION", work_kind="NONE",
                                          task_spec="Which behavior?", proposed_paths=[], acceptance_checks=[])
        request = envelope["request_id"]
        raw = json.dumps(envelope).encode()
        with patch("devorchestra.supervised.time", return_value=10**12), self.assertRaisesRegex(Blocked, "expired"):
            self.runner.supervised_handoff("submit", "sample", raw=raw, expected_request=request)
        self.assertEqual("CLARIFICATION", self.runner.supervised_handoff(
            "submit", "sample", raw=raw, expected_request=request))
        pending = ProjectionReader(self.ledger).read("sample")["user_question"]
        self.runner.respond_question(pending["question"]["id"], pending["run_id"], pending["generation"],
                                     "ANSWER", "Use default behavior")
        prompt = self.runner.supervised_handoff("start", "sample", paths=["src/main.py"])
        self.assertIn("Use default behavior", prompt)
        new_request = json.loads(prompt.split("\n\n", 1)[1])["response_envelope"]["request_id"]
        self.assertEqual(prompt, self.runner.supervised_handoff("prompt", "sample", expected_request=new_request))
        self.document.write_bytes(b"Changed baseline")
        with self.assertRaisesRegex(Blocked, "Frozen requirement changed"):
            self.runner.supervised_handoff("prompt", "sample", expected_request=new_request)

    def test_revisions_are_frozen_in_session_and_visible_in_projection(self):
        reader = ProjectionReader(self.ledger)
        self.assertEqual("REGISTER_REQUIREMENTS", reader.read("sample")["workflow"]["next_action"])
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
        self.assertEqual((1, "REVIEW_NEXT_REQUIREMENTS", "NO_RECORDED_ATTEMPT"),
                         (view["workflow"]["pending_requirements_count"],
                          view["workflow"]["next_action"], view["workflow"]["manager_status"]))
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
        workflow = reader.read("sample")["workflow"]
        self.assertEqual((0, "OPEN_SUPERVISED_HANDOFF", "BLOCKED", "NOT_AVAILABLE"),
                         (workflow["pending_requirements_count"], workflow["next_action"],
                          workflow["development_admission"], workflow["user_test_status"]))
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
