"""Offline integration across the real registry, Git onboarding, MXC and approval."""
import json
import os
from pathlib import Path
import subprocess
import sys
from shutil import copyfile
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from devorchestra import (codex_setup, cycle_progress, game_pilot, local_folder, native_cycle, original_apply,
                           project_onboarding, registered_cycle)
from devorchestra.catalog import Catalog, new_project
from devorchestra.ledger import Ledger, digest
from devorchestra.lease import Blocked
from devorchestra.runtime import Runner
from devorchestra.native_developer_trial import _check_dedicated_config


class RegisteredTrustTests(unittest.TestCase):
    def test_fixed_criterion_routes_only_the_selected_cycle(self):
        rc = registered_cycle
        primary, other = "C1-fixture", "C2-fixture"
        criteria = {key: {"text": f"Goal for {key}", "checks": []} for key in (primary, other)}
        for target in (primary, other):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                work = Path(directory) / "feature.txt"
                work.write_text("before", encoding="utf-8")
                scope = dict(project_id="fixture", criteria=criteria, approved_criterion=primary,
                             goal="Implement the approved feature", files=["feature.txt"], cycles=1,
                             requirements=[], verifier={"mode": rc.BASELINE_VERIFIER})
                session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(),
                                          record=Mock(), source=lambda: "candidate", changes=[],
                                          check_image=Mock(), owned_verified=True, usage_complete=True,
                                          publish_cycle=Mock())

                def call(_, phase, criterion, prompt, **options):
                    if phase == "manager-planning":
                        self.assertIn(criteria[primary]["text"], prompt)
                        self.assertEqual([primary], options["response_schema"]["properties"]["criterion"]["enum"])
                        return json.dumps({"criterion": target, "plan": "Implement feature"})
                    self.assertEqual(("developer", primary), (phase, criterion))
                    work.write_text("after", encoding="utf-8")
                    session.image = {"files": {"feature.txt": {"sha256": digest("after")}}}
                    session.last_changed = ["feature.txt"]
                    return "synthetic diff"

                session.call = Mock(side_effect=call)
                checked = {"owned_tree": "DRAINED", "observations": []}
                comparison = {"criteria": {primary: "VERIFIED", other: "NOT_STARTED"},
                              "newly_verified": [primary], "regressed": []}
                with (patch.object(rc, "Catalog"), patch.object(rc, "read_frozen_requirements", return_value=[]),
                      patch.object(rc, "_review_and_verify", return_value=("ACCEPT", "ACCEPT", "", checked, comparison)) as verify,
                      patch.object(rc, "_decide", return_value="CONTINUE")):
                    result = rc._cycles(session)
                if target == other:
                    self.assertEqual(rc.CRITERION_APPROVAL_REQUIRED, result)
                    self.assertEqual(["manager-planning"], [entry.args[1] for entry in session.call.call_args_list])
                    self.assertEqual("before", work.read_text(encoding="utf-8"))
                    verify.assert_not_called()
                    session.publish_cycle.assert_not_called()
                    mismatch = next(entry.kwargs for entry in session.record.call_args_list
                                    if entry.args[0] == "native_inspection_criterion_approval_required")
                    self.assertEqual((primary, other), (mismatch["approved_criterion"], mismatch["target"]))
                else:
                    self.assertEqual("CYCLE_LIMIT", result)
                    self.assertEqual("after", work.read_text(encoding="utf-8"))
                    self.assertEqual(primary, verify.call_args.args[2])
                    self.assertEqual(primary, session.publish_cycle.call_args.args[1])

    def test_locked_checkpoint_and_reused_plan_must_match(self):
        rc = registered_cycle
        scope = {"approved_criterion": "C1", "resume": {"criterion": "C2"}}
        with patch.object(rc, "_resume_contract", return_value=(scope, "saved diff")):
            with self.assertRaises(rc.CriterionMismatch):
                rc._approved_resume_contract(None, [], "", (), False)
        scope["resume"]["criterion"] = "C1"
        with patch.object(rc, "_resume_contract", return_value=(scope, {"criterion": "C2"})):
            with self.assertRaises(rc.CriterionMismatch):
                rc._approved_resume_contract(None, [], "", (), False)
        session = SimpleNamespace(scope=scope, direct_binding=True, owned_verified=True, usage_complete=True)
        for check in (lambda: rc.RegisteredSession.call(session, 1, "developer", "C2", ""),
                      lambda: rc._verify_signoff(session, 1, "C2", {}),
                      lambda: rc.RegisteredSession.publish_cycle(session, 1, "C2", "ACCEPT", {})):
            with self.assertRaises(rc.CriterionMismatch):
                check()

    def test_locked_manager_unknown_never_reaches_developer(self):
        rc = registered_cycle
        scope = dict(project_id="fixture", approved_criterion="C1",
                     criteria={"C1": {"text": "Goal", "checks": []}}, cycles=1, goal="Goal",
                     files=["feature.txt"], requirements=[], verifier={})
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), record=Mock(),
                                  call=Mock(side_effect=Blocked("REQUEST_UNKNOWN")))
        with (patch.object(rc, "Catalog"), patch.object(rc, "read_frozen_requirements", return_value=[]),
              self.assertRaisesRegex(Blocked, "REQUEST_UNKNOWN")):
            rc._cycles(session)
        self.assertEqual(["manager-planning"], [entry.args[1] for entry in session.call.call_args_list])
        self.assertFalse(any(entry.args[0] == "native_inspection_plan_accepted"
                             for entry in session.record.call_args_list))

    def test_locked_verifier_does_not_run_or_promote_other_checks(self):
        rc = registered_cycle
        approved, other = "C1", "C2"
        scope = {"approved_criterion": approved,
                 "criteria": {key: {"text": key, "checks": [{"title": key}]} for key in (approved, other)},
                 "verifier": {"mode": rc.BASELINE_VERIFIER, "checks": {other: {}}}}
        session = SimpleNamespace(scope=scope, signoff_state={"criterion": approved, "review": "ACCEPT"}, record=Mock(), phases=[],
                                  check=Mock(), check_image=Mock(), logs=Mock(), source=lambda: "candidate")
        self.assertEqual([], rc.RegisteredSession.verify_registered(session, 1)["observations"])
        session.logs.assert_not_called()
        session.verify_registered = Mock(return_value={
            "phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED", "verdict": "PASS",
            "observations": [{"title": other, "actual": "same", "expected": "same"}],
        })
        _, comparison = rc._verify_signoff(session, 1, approved, {approved: "NOT_STARTED", other: "NOT_STARTED"})
        self.assertEqual("NOT_STARTED", comparison["criteria"][other])
        self.assertEqual([], comparison["newly_verified"])

    def test_packaged_verifier_approval_pins_inputs_and_limits_coverage(self):
        rc = registered_cycle
        with TemporaryDirectory() as folder:
            base = Path(folder)
            work = base / "project"
            work.mkdir()
            archive = base / "test.zip"
            archive.write_bytes(b"fixture archive")
            check = {"args": ["fixture.txt"], "stdout": "expected", "inputs": ["fixture.txt"],
                     "coverage": "partial", "level": "functional"}
            package = {"archive": str(archive), "entry": "bin/test.exe",
                       "sha256": rc.fingerprint(archive)["sha256"]}
            image = {"files": {"fixture.txt": {"sha256": "fixture", "bytes": 1}}}
            criteria = {"C1": {"checks": [{"title": "C1"}]}}
            config = {"package": package, "checks": {"C1": check}}
            tool = rc._tools(config, criteria, image, [], project_root=work)
            self.assertEqual(image["files"], tool["inputs"])
            with self.assertRaises(Blocked):
                rc._tools(config, criteria, image, ["fixture.txt"], project_root=work)
            with self.assertRaises(Blocked):
                rc._tools({"package": {**package, "entry": "../test.exe"}, "checks": {"C1": check}},
                          criteria, image, [], project_root=work)
            with self.assertRaises(Blocked):
                rc._tools({"package": package, "checks": {"C1": {**check, "level": "static", "coverage": "criterion"}}},
                          criteria, image, [], project_root=work)
            observation = {"title": "C1", "actual": "matching", "expected": "matching", "coverage": "partial"}
            receipt = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
                       "verdict": "PASS", "observations": [observation]}
            self.assertEqual("NOT_VERIFIED", cycle_progress.compare(criteria, {}, receipt)["criteria"]["C1"])

    def test_packaged_verifier_rejects_foreign_and_duplicate_observations(self):
        rc = registered_cycle
        key = "C1"
        spec = {"args": ["input.txt"], "inputs": ["input.txt"], "stdout": "5",
                "level": "functional", "coverage": "criterion"}
        expected = rc._expected_observation(spec)
        tool = {"checks": {key: spec}, "inputs": {"input.txt": {"sha256": "input"}},
                "package": {"identity": {"sha256": "package"}}}
        row = {"title": key, "actual": expected, "expected": expected, "check_sha256": digest(spec),
               "input_sha256": {"input.txt": "input"}, "level": "functional", "coverage": "criterion",
               "package_sha256": "package", "execution_sha256": digest(["project", "run", 1, key, "candidate", digest(spec)])}
        receipt = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
                   "verdict": "PASS", "source_sha256": "candidate", "observations": [row]}
        session = SimpleNamespace(scope={"criteria": {key: {"checks": [{"title": key}]}},
                                         "verifier": tool, "project_id": "project"},
                                  fields={"run_id": "run"}, source=lambda: "candidate",
                                  signoff_state={"review": "ACCEPT"}, verify_registered=Mock(return_value=receipt))
        status = {key: "NOT_STARTED"}
        self.assertEqual("VERIFIED", rc._verify_signoff(session, 1, key, status)[1]["criteria"][key])
        foreign = digest(["foreign", "run", 1, key, "candidate", digest(spec)])
        for rows in ([row, row], [{**row, "execution_sha256": foreign}], [{**row, "title": "C2"}],
                     [{**row, "input_sha256": {}}], [{**row, "check_sha256": digest({**spec, "args": []})}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(Blocked, "does not bind"):
                session.verify_registered.return_value = {**receipt, "observations": rows}
                rc._verify_signoff(session, 1, key, status)

    def test_windows_memory_signal_is_independent_of_turn_lifecycle(self):
        from devorchestra.native_developer_trial import _failure_evidence
        evidence = _failure_evidence('{"type":"turn.started"}', b"memory allocation of 84704 bytes failed")
        self.assertEqual(["turn.started"], evidence["cli_events"])
        self.assertEqual(["CLI_MEMORY_ALLOCATION_FAILED"], evidence["cli_failure_signals"])

    def test_one_functional_check_binds_real_fixture_output_to_only_its_criterion(self):
        rc = registered_cycle
        acceptance = ["Sum two numbers", "Paint the result in a GUI"]
        key, other = cycle_progress.freeze(acceptance, None)
        criteria = cycle_progress.freeze(acceptance, {key: [{"title": key}]})
        with TemporaryDirectory() as folder:
            work = Path(folder)
            fixture = work / "sum_fixture.py"
            fixture.write_text("import sys\nprint(sum(map(int, sys.argv[1:])))\n", encoding="ascii")
            check = {"args": ["-B", fixture.name, "2", "3"], "inputs": [fixture.name],
                     "stdout": "5", "level": "functional"}
            image = {"files": {fixture.name: rc.fingerprint(fixture)}}
            with patch.object(rc, "_signed_offline", return_value=True):
                spec = rc._tools({"executable": sys.executable, "checks": {key: check}}, criteria, image, [])
            session = Mock()
            session.scope = {"verifier": spec, "criteria": criteria}
            session.signoff_state = {}
            session.image, session.work = image, work
            session.source.return_value = digest(image)
            session.owned.side_effect = lambda operation: operation()
            session.logs.return_value = work

            def observe(_launcher, argv, cwd, *_args, **_kwargs):
                self.assertEqual(check["args"], argv[-len(check["args"]):])
                result = subprocess.run([sys.executable, *check["args"]], cwd=cwd, capture_output=True,
                                        text=True, check=False, timeout=10)
                return {"stdout": result.stdout, "stderr": result.stderr, "exitCode": result.returncode,
                        "job_active": 0}

            with patch.object(rc, "_observe", side_effect=observe) as launch:
                receipt = rc.RegisteredSession.verify_registered(session, 1)
                fixture.write_text("print(6)\n", encoding="ascii")
                image["files"][fixture.name] = rc.fingerprint(fixture)
                failed = rc.RegisteredSession.verify_registered(session, 2)
                self.assertEqual(2, launch.call_count)
            self.assertEqual({key: "VERIFIED", other: "NOT_VERIFIED"},
                             cycle_progress.compare(criteria, {}, receipt)["criteria"])
            self.assertEqual("FAILED", cycle_progress.compare(criteria, {}, failed)["criterion_evidence"][key])
            self.assertNotEqual(receipt["observations"][0]["input_sha256"][fixture.name],
                                failed["observations"][0]["input_sha256"][fixture.name])

    def test_controller_distinguishes_failed_missing_and_unclassified_evidence(self):
        classify = registered_cycle._decision_cause
        self.assertEqual("VERIFICATION_FAILED", classify("C1", "REWORK",
                         {"criterion_evidence": {"C1": "FAILED"}}, "REWORK"))
        self.assertEqual("VERIFICATION_MISSING", classify("C1", "ACCEPT",
                         {"criterion_evidence": {"C1": "UNCONFIGURED"}}, "USER_TEST_REQUIRED"))
        self.assertEqual("REVIEW_FINDING_UNCLASSIFIED", classify("C1", "REWORK",
                         {"criterion_evidence": {"C1": "MISSING"}}, "REWORK"))
        self.assertEqual("ENVIRONMENT_BLOCKED", classify("C1", "ACCEPT",
                         {"criterion_environment_blocked": True}, "USER_TEST_REQUIRED"))

    def test_preview_size_evidence_identifies_boundary_without_execution(self):
        from devorchestra.replica_evidence import EvidenceSizeLimit, snapshot

        with TemporaryDirectory() as folder:
            path = Path(folder) / "preview.dat"
            path.write_bytes(b"12345")
            with self.assertRaises(EvidenceSizeLimit) as raised:
                snapshot(path.parent, max_file_bytes=4)
            self.assertEqual({"relative_path": "preview.dat", "file_bytes": 5, "prior_bytes": 0,
                              "effective_file_limit": 4, "total_limit": 256 * 1024**2}, raised.exception.bundle)

    def test_direct_postprocess_publishes_saved_paths_before_next_cycle(self):
        rc = registered_cycle
        comparison = {"criteria": {"first": "USER_TEST_REQUIRED", "second": "NOT_STARTED"}}
        session = Mock()
        session.stop_reason = None
        session.image = {"files": {}}
        session.scope = {"direct_git": {"head": "approved"}, "criteria": comparison["criteria"], "files": [],
                         "resume": {"cycle": 3, "criterion": "first", "mode": "postprocess",
                                    "criteria_status": comparison["criteria"], "changed": ["game.txt"],
                                    "changed_total": ["game.txt"]}}
        with (patch.object(rc, "_review_and_verify", return_value=(None, "ACCEPT", None, {}, comparison)),
              patch.object(rc, "_cycle_stagnation", return_value=0),
              patch.object(rc, "_expected_final", return_value="USER_TEST_REQUIRED"),
              patch.object(rc, "_decide", return_value="USER_TEST_REQUIRED"),
              patch.object(rc, "_cycles", return_value="next") as cycles):
            def continue_after_publish(_, *, continuation):
                session.publish_cycle.assert_called_once_with(
                    3, "first", "USER_TEST_REQUIRED", comparison, recovered_paths=["game.txt"])
                self.assertEqual("USER_VERIFICATION", continuation["deferred_criteria"]["first"]["kind"])
                return "next"

            cycles.side_effect = continue_after_publish
            self.assertEqual("next", rc._resume_remaining(session, "saved diff"))
            session.publish_cycle.side_effect = Blocked("remote conflict")
            with self.assertRaisesRegex(Blocked, "remote conflict"):
                rc._resume_remaining(session, "saved diff")
            cycles.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows extended paths")
    def test_preview_inventory_reads_deep_paths_and_rejects_missing_files(self):
        from tempfile import TemporaryDirectory
        from devorchestra.replica_evidence import snapshot

        with TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            item = root / ("a" * 100) / ("b" * 100) / ("c" * 50 + ".cache")
            native = Path("\\\\?\\" + str(item))
            native.parent.mkdir(parents=True)
            try:
                native.write_bytes(b"fixture")
                image = snapshot(root, long_local=True)
                self.assertEqual(7, image["files"][item.relative_to(root).as_posix()]["bytes"])
                with patch("devorchestra.replica_evidence.fingerprint", side_effect=FileNotFoundError):
                    with self.assertRaises(FileNotFoundError):
                        snapshot(root, long_local=True)
            finally:
                native.unlink(missing_ok=True)
                native.parent.rmdir()
                native.parent.parent.rmdir()

    def test_project_work_contract_and_document_only_implementation(self):
        from devorchestra import cycle_progress
        from devorchestra.project_evidence import discovery, preview_entry

        plan = {"criterion": "C1", "mode": "edit", "plan": "Show a result after validation",
                "deliverable": "implementation", "components": "UI and validation",
                "verification": "Run the project test", "evidence": "Result appears for accepted input"}
        self.assertEqual(plan, registered_cycle._parse_plan(json.dumps(plan), ["C1"], evidence_contract=True))
        with self.assertRaises(registered_cycle.PlanFormatError):
            registered_cycle._parse_plan(json.dumps({**plan, "deliverable": "documentation"}), ["C1"],
                                         evidence_contract=True)
        self.assertEqual(len(discovery({"src/Tool.csproj": {}, "project.godot": {}, "pyproject.toml": {}})), 3)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Example.exe").write_bytes(b"MZ" + bytes(58) + (64).to_bytes(4, "little") + b"PE\0\0")
            manifest = root / "preview.json"
            files = {"Example.exe": {}, "preview.json": {}}
            manifest.write_text('{"kind":"windows-gui","entry":"Example.exe"}', encoding="ascii")
            self.assertEqual("Example.exe", preview_entry(root, files))
            manifest.write_text('{"kind":"windows-gui","entry":"../outside.exe"}', encoding="ascii")
            with self.assertRaises(Blocked):
                preview_entry(root, files)
        criteria = {"C1": {"checks": []}, "C2": {"checks": [{"title": "C2"}]},
                    "C3": {"checks": [{"title": "C3"}]}}
        checked = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
                   "runtime": registered_cycle.BASELINE_VERIFIER, "integrity": "PASS", "verdict": "FAIL",
                   "observations": [{"title": "C2", "expected": True, "actual": False},
                                    {"title": "C3", "status": "NOT_VERIFIED"}]}
        comparison = cycle_progress.compare(criteria, {}, checked)
        self.assertEqual({"C1": "UNCONFIGURED", "C2": "FAILED", "C3": "MISSING"}, comparison["criterion_evidence"])
        comparison["criteria"]["C1"] = "USER_TEST_REQUIRED"
        scope = {"criteria": criteria, "verifier": {"mode": registered_cycle.BASELINE_VERIFIER},
                 "project_evidence_contract": 1}
        self.assertEqual(1, registered_cycle._cycle_stagnation(
            scope, "C1", "ACCEPT", checked, comparison, set(), ["README.md"], 0))
        self.assertTrue(comparison["delivery"]["document_only"])
        self.assertFalse(comparison["delivery"]["independent_criterion_improvement"])

    def test_subscription_admission_and_signoff_without_token_reservation(self):
        from devorchestra.ai_usage import SUBSCRIPTION_MODE, subscription_quota

        def snapshot(remaining):
            bucket = {"primary": {"usedPercent": 100 - remaining},
                      "credits": {"hasCredits": False, "unlimited": False}}
            return subscription_quota({"rateLimits": bucket, "ordinaryUsageAllowed": True})

        for remaining in (11, 10, 9, None):
            with self.subTest(remaining=remaining):
                session = object.__new__(registered_cycle.RegisteredSession)
                session.scope = {"token_mode": SUBSCRIPTION_MODE, "tokens": None, "model_calls_max": None,
                                 "seconds": 3600, "quota_stop_percent": 10,
                                 "graceful_stop": {"signoff_seconds": 1800}}
                session.started, session.usage, session.calls = monotonic(), 20000000, 8
                session.stop_reason = session.signoff_started = None
                session.owned = lambda invoke: invoke()
                session.binary = session.lab = session.env = None
                session.check, session.record = Mock(), Mock()
                reading = snapshot(remaining) if remaining is not None else subscription_quota(None)
                with patch.object(registered_cycle.codex_setup, "read_subscription_quota", return_value=reading):
                    if remaining == 11:
                        self.assertFalse(session.admit_graceful("developer"))
                        self.assertIsNone(session.stop_reason)
                    else:
                        with self.assertRaisesRegex(Blocked, "GRACEFUL_STOP"):
                            session.admit_graceful("developer")
                        if remaining is not None:
                            self.assertEqual("QUOTA_LOW", session.stop_reason)
                            self.assertTrue(session.admit_graceful("reviewer"))
                        else:
                            with self.assertRaisesRegex(Blocked, "SIGNOFF_PENDING"):
                                session.admit_graceful("reviewer")
                        self.assertTrue(session.admit_graceful("verifier"))
                    self.assertFalse(session.model_calls_exhausted())
                if remaining == 11:
                    with patch.object(registered_cycle.codex_setup, "read_subscription_quota", return_value=snapshot(10)):
                        session.after_model("developer", None)
                    self.assertEqual("QUOTA_LOW", session.stop_reason)
    def test_unsettled_candidate_requires_same_git_protected_files_and_cleanup(self):
        project, run = "fixture", "run"
        replica = Path("fixture-replica")
        onboarding = {"revision": 1}
        frozen = {"head": "original", "files": {"checks.py": {"sha256": "check"}}}
        before = {"directory_id": [1, 2], "git": {"head": "head", "index": "index"}, "files": frozen["files"]}
        after = {**before, "files": {**before["files"], "game.gd": {"sha256": "game"}}}
        scope = {"project_edit": True, "replica": str(replica), "onboarding_sha256": digest(onboarding),
                 "replica_sha256": digest(before), "original_head": frozen["head"],
                 "protected_edit_paths": ["checks.py"]}
        fields = {"project_id": project, "run_id": run, "probe_kind": registered_cycle.KIND,
                  "approval_sha256": digest(scope)}
        role = {**fields, "phase": "developer", "cycle": 1, "target": "C1"}
        rows = [
            {**fields, "kind": "native_inspection_intent", "scope": scope, "workspace": "temp"},
            {**role, "kind": "native_inspection_role_intent", "source_sha256": digest(before)},
            {**role, "kind": "native_inspection_role_result", "outcome": "REQUEST_UNKNOWN", "owned_tree": "DRAINED"},
            {**fields, "kind": "native_inspection_result", "outcome": "REQUEST_UNKNOWN", "owned_tree": "DRAINED",
             "usage_complete": False, "replica_sha256": digest(before),
             "staging_cleanup": {"status": "RETAINED_FOR_RECOVERY", "workspace": "temp"}},
        ]

        def recover(image):
            return registered_cycle._settled_integrity_candidate(rows, project, replica, onboarding, image,
                                                                 frozen=frozen)

        receipt = recover(after)
        self.assertTrue(receipt["unsettled_candidate"])
        self.assertEqual(receipt["source_sha256"], digest(after))
        self.assertEqual(receipt["changed"], ["game.gd"])
        with self.assertRaises(Blocked):
            recover({**after, "git": {**after["git"], "index": "altered"}})
        with self.assertRaises(Blocked):
            recover({**after, "files": {**after["files"], "checks.py": {"sha256": "altered"}}})
        rows.insert(-1, {**fields, "kind": "native_inspection_project_tools_intent", "directory": "tool"})
        with self.assertRaises(Blocked):
            recover(after)

    def test_progress_stop_preserves_rework_without_granting_new_scope(self):
        fixture = SimpleNamespace(scope={"criteria": {"C1": {}}}, owned_verified=True, usage_complete=True,
                                  check=Mock(), check_image=Mock(), source=lambda: "source",
                                  changes=["Fixture.cs"], record=Mock())
        feedback = {"next": "REWORK", "target": "C1", "review": "V1: clipped label", "scope": "ignored"}
        result = registered_cycle._save_progress_stop(fixture, 2, set(), outcome="GRACEFUL_STOP", feedback=feedback)
        self.assertEqual("GRACEFUL_STOP", result)
        self.assertEqual({"next": "REWORK", "target": "C1", "review": "V1: clipped label"},
                         fixture.record.call_args.kwargs["rework"])

    def test_final_schema_only_offers_evidence_compatible_verdicts(self):
        session = Mock()
        session.scope = {"verifier": {}, "graceful_stop": {}}
        session.signoff_state = {"changed": []}
        comparison = {"criteria": {"C1": "VERIFIED"}, "newly_verified": ["C1"], "regressed": []}
        session.call.return_value = '{"verdict":"COMPLETED","reason":"The independent check passed."}'
        self.assertEqual(registered_cycle._decide(session, 1, "C1", "ACCEPT", comparison, "COMPLETED"),
                         "COMPLETED")
        schema = session.call.call_args.kwargs["response_schema"]
        self.assertEqual(schema["properties"]["verdict"]["enum"], ["COMPLETED"])
        self.assertEqual(set(schema["required"]), {"verdict", "reason"})

    def test_structured_plan_cannot_widen_the_frozen_criterion_or_file_scope(self):
        parse = registered_cycle._parse_plan
        paths = ["docs/DESIGN.md", "src/main.py"]
        detail = {"mode": "documentation", "artifact": paths[0], "change": "Describe the design",
                  "acceptance_checks": ["Compare against the goal"], "test_targets": ["Review the diff"],
                  "tools_versions": "Installed tools", "outputs_evidence": "Project design"}
        payload = {"criterion": "C1", "plan": detail}
        plan = parse(json.dumps(payload), ["C1"], approved_paths=paths)
        self.assertEqual((plan["criterion"], plan["mode"], plan["artifact"]), ("C1", "documentation", paths[0]))
        self.assertEqual(json.loads(plan["plan"])["test_targets"], detail["test_targets"])
        for invalid in ({**payload, "criterion": "C2"}, {**payload, "mode": "edit"},
                        {**payload, "mode": []},
                        {**payload, "plan": {**detail, "artifact": "../DESIGN.md"}},
                        {**payload, "plan": {**detail, "artifact": paths[1]}}):
            with self.subTest(invalid=invalid), self.assertRaises(Blocked) as failure:
                parse(json.dumps(invalid), ["C1"], approved_paths=paths)
            self.assertNotIsInstance(failure.exception, registered_cycle.PlanFormatError)
        with self.assertRaises(registered_cycle.PlanFormatError):
            parse('{"criterion":"C1","criterion":"C2","plan":"Edit"}', ["C1"], approved_paths=paths)
        with self.assertRaises(registered_cycle.PlanFormatError):
            parse(json.dumps({**payload, "plan": {**detail, "test_targets": [False]}}), ["C1"], approved_paths=paths)

    def test_recovered_second_plan_starts_developer_with_prior_review_and_exact_file(self):
        paths = ["docs/DESIGN.md", "docs/OTHER.md"]
        scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Design", "checks": []}}, "goal": "Work",
                 "files": paths, "cycles": 2, "requirements": [], "verifier": {"mode": registered_cycle.BASELINE_VERIFIER},
                 "resume": {"parent_run_id": "previous"}, "graceful_stop": {}, "model_calls_max": 3}
        session = SimpleNamespace(scope=scope, image={"files": {"prior": 1}}, runner=Mock(), check=Mock(),
                                  record=Mock(), source=lambda: digest(session.image), calls=0, stop_reason=None,
                                  check_image=Mock(), owned_verified=True, usage_complete=True, publish_cycle=Mock())
        plan = {"criterion": "C1", "plan": "Resolve the missing evidence", "mode": "documentation",
                "artifact": paths[0]}
        continuation = {"cycle": 1, "stagnant": 1, "criteria_status": {"C1": "USER_TEST_REQUIRED"},
                        "feedback": {"target": "C1", "next": "REWORK", "review": "REWORK · Add test evidence"},
                        "known": [digest(session.image["files"])], "reviewed": [], "failures": [], "changed": paths[:1]}

        def develop(*_args, **_options):
            session.image = {"files": {"prior": 2}}
            session.last_changed = paths[:1]
            return "new diff"

        session.call = Mock(side_effect=develop)
        receipt = {"outcome": "COMPLETED", "verdict": "NOT_VALIDATED", "owned_tree": "DRAINED", "observations": []}
        comparison = {"criteria": {"C1": "USER_TEST_REQUIRED"}, "newly_verified": [], "regressed": []}
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[]),
              patch.object(registered_cycle, "_review_and_verify", return_value=("ACCEPT", "ACCEPT", "", receipt, comparison)),
              patch.object(registered_cycle, "_decide", return_value="CONTINUE")):
            self.assertEqual(registered_cycle._cycles(session, plan, continuation=continuation), "CYCLE_LIMIT")
        session.call.assert_called_once()
        self.assertEqual(session.call.call_args.args[:3], (2, "developer", "C1"))
        self.assertIn("Add test evidence", session.call.call_args.args[3])
        self.assertEqual(session.call.call_args.kwargs["allowed_paths"], paths[:1])
        self.assertEqual(session.cycles_checked, 2)
        session.publish_cycle.assert_called_once_with(2, "C1", "CONTINUE", comparison)

    def test_unreadable_plan_saves_prior_candidate_and_call_exhaustion_stops_safely(self):
        scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Design", "checks": []}}, "goal": "Work",
                 "files": ["docs/DESIGN.md"], "cycles": None, "requirements": [], "verifier": {},
                 "graceful_stop": {}, "model_calls_max": 1}
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), check_image=Mock(),
                                  record=Mock(), source=lambda: "verified", owned_verified=True, usage_complete=True,
                                  admit_graceful=Mock(), request_stop=Mock(), calls=0, call=Mock(return_value="not JSON"))
        continuation = {"cycle": 1, "stagnant": 1, "criteria_status": {"C1": "USER_TEST_REQUIRED"},
                        "feedback": {"target": "C1", "next": "REWORK", "review": "REWORK · Evidence missing"},
                        "known": [], "reviewed": [], "failures": [], "changed": scope["files"]}
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[])):
            self.assertEqual(registered_cycle._cycles(session, continuation=continuation),
                             registered_cycle.MANUAL_CHECKPOINT)
        self.assertEqual(session.record.call_args.args[0], "native_inspection_manual_checkpoint")
        self.assertEqual(session.record.call_args.kwargs["cycle"], 1)
        self.assertEqual(session.record.call_args.kwargs["feedback"]["review"], continuation["feedback"]["review"])
        self.assertEqual(session.record.call_args.kwargs["changed"], scope["files"])
        session.calls = 1
        session.call.reset_mock()
        self.assertEqual(registered_cycle._cycles(session, continuation=continuation), "GRACEFUL_STOP")
        session.request_stop.assert_called_once_with("MODEL_CALL_LIMIT", "manager-planning")
        session.call.assert_not_called()

    def test_accepted_planning_rework_advances_without_functional_pass(self):
        first, second = "C1", "C2"
        scope = {"project_id": "fixture", "criteria": {k: {"text": k, "checks": []} for k in (first, second)},
                 "goal": "Plan the implementation", "files": ["docs/PROJECT-PLAN.md"], "cycles": 3,
                 "requirements": [], "visual_preview": True, "verifier": {"mode": registered_cycle.BASELINE_VERIFIER}}
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), record=Mock(),
                                  source=lambda: digest(session.image), changes=[], cycles_checked=0,
                                  check_image=Mock(), owned_verified=True, usage_complete=True, publish_cycle=Mock())

        def call(number, phase, target, prompt, **_options):
            if phase == "manager-planning":
                return json.dumps({"criterion": second if number == 3 else first, "plan": "Plan"})
            session.image = {"files": {"docs/PROJECT-PLAN.md": {"revision": number}}}
            session.last_changed = ["docs/PROJECT-PLAN.md"]
            return "diff"

        def verify(_, number, key, diff, status):
            verdict = "REWORK" if number == 1 else "ACCEPT"
            checked = {"runtime": registered_cycle.BASELINE_VERIFIER, "integrity": "PASS", "owned_tree": "DRAINED",
                       "observations": []}
            return verdict, verdict, "Reason", checked, {"criteria": dict.fromkeys(scope["criteria"], "USER_TEST_REQUIRED"),
                                                         "newly_verified": [], "regressed": []}

        session.call = Mock(side_effect=call)
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[]),
              patch.object(registered_cycle, "_review_and_verify", side_effect=verify),
              patch.object(registered_cycle, "_decide", side_effect=lambda *args: args[-1]) as decide):
            self.assertEqual(registered_cycle._cycles(session), "CYCLE_LIMIT")
        self.assertEqual([item.args[-1] for item in decide.call_args_list], ["REWORK", "CONTINUE", "CONTINUE"])
        self.assertEqual(decide.call_args_list[1].args[4]["newly_reviewed_without_functional_check"], [first])
        self.assertEqual(decide.call_args_list[1].args[4]["criteria"][first], "USER_TEST_REQUIRED")
        self.assertEqual([item.args[2] for item in session.call.call_args_list if item.args[1] == "developer"],
                         [first, first, second])

    def test_rejected_changed_drafts_do_not_pin_the_next_criterion(self):
        scope = {"project_id": "fixture", "criteria": {key: {"text": key, "checks": []} for key in ("C1", "C2")},
                 "goal": "Plan and implement", "files": ["docs/DESIGN.md", "src/main.py"], "cycles": 3,
                 "requirements": [], "verifier": {"mode": registered_cycle.BASELINE_VERIFIER}}
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), record=Mock(),
                                  source=lambda: digest(session.image), changes=["docs/DESIGN.md"],
                                  check_image=Mock(), owned_verified=True, usage_complete=True, publish_cycle=Mock())

        def call(number, phase, target, prompt, **_options):
            if phase == "manager-planning":
                return json.dumps({"criterion": "C2" if number == 3 else "C1", "plan": "Bounded work"})
            session.image = {"files": {"docs/DESIGN.md": {"revision": number}}}
            session.last_changed = ["docs/DESIGN.md"]
            return "diff"

        def verify(_, number, key, diff, status):
            verdict = "REWORK" if key == "C1" else "ACCEPT"
            return verdict, verdict, "Missing approved comparison material", {"observations": []}, {
                "criteria": {"C1": "BLOCKED", "C2": "VERIFIED" if key == "C2" else "NOT_STARTED"},
                "newly_verified": ["C2"] if key == "C2" else [], "regressed": []}

        session.call = Mock(side_effect=call)
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[]),
              patch.object(registered_cycle, "_review_and_verify", side_effect=verify),
              patch.object(registered_cycle, "_decide", side_effect=lambda *args: args[-1])):
            self.assertEqual("USER_TEST_REQUIRED", registered_cycle._cycles(session))
        self.assertEqual(["C1", "C1", "C2"], [item.args[2] for item in session.call.call_args_list
                                               if item.args[1] == "developer"])
        self.assertNotIn("Prior Reviewer findings", session.call.call_args_list[-1].args[3])
        self.assertEqual("C1", next(item.kwargs["target"] for item in session.record.call_args_list
                                   if item.args[0] == "native_inspection_criterion_deferred"))
        transition = next(item.kwargs for item in session.record.call_args_list
                          if item.args[0] == "native_inspection_goal_transition")
        self.assertEqual(("C1", "C2", "REPEATED_NO_PROGRESS"),
                         (transition["previous_target"], transition["target"], transition["reason"]))

    def test_missing_prerequisite_saves_checkpoint_without_dispatching_developer(self):
        scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Compare sources", "checks": []}},
                 "goal": "Plan", "files": ["docs/DESIGN.md"], "cycles": 1,
                 "requirements": [], "verifier": {}}
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), record=Mock(),
                                  source=lambda: "source", changes=[], check_image=Mock(), owned_verified=True,
                                  usage_complete=True, call=Mock(return_value=json.dumps({
                                      "criterion": "C1", "mode": "needs-input", "plan": "Supply comparison notes"})))
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[])):
            self.assertEqual(registered_cycle.NEEDS_INPUT, registered_cycle._cycles(session))
        self.assertEqual(["manager-planning"], [item.args[1] for item in session.call.call_args_list])
        self.assertEqual({"C1": {"kind": "PREREQUISITE", "reason": "Supply comparison notes"}},
                         session.record.call_args.kwargs["deferred_criteria"])

    def test_planned_documentation_counts_without_satisfying_red_functional_check(self):
        key = "C1"
        scope = {"project_id": "fixture", "criteria": {key: {"text": "Implement a feature", "checks": [{"title": key}]}},
                 "goal": "Work", "files": ["docs/DESIGN.md", "src/main.py"], "cycles": 1, "requirements": [],
                 "verifier": {"checks": {key: {"args": []}}}}
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), check_image=Mock(),
                                  record=Mock(), source=lambda: digest(session.image), changes=["docs/DESIGN.md"],
                                  owned_verified=True, usage_complete=True, publish_cycle=Mock())

        def call(number, phase, target, prompt, **options):
            if phase == "manager-planning":
                return json.dumps({"criterion": key, "plan": "Write design", "mode": "documentation"})
            session.image = {"files": {"docs/DESIGN.md": {"sha256": "updated"}}}
            session.last_changed = ["docs/DESIGN.md"]
            return "diff"

        session.call = Mock(side_effect=call)
        receipt = {"outcome": "COMPLETED", "verdict": "FAIL", "owned_tree": "DRAINED", "observations": []}
        comparison = {"criteria": {key: "BLOCKED"}, "newly_verified": [], "regressed": []}
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[]),
              patch.object(registered_cycle, "_review_and_verify",
                           return_value=("ACCEPT", "ACCEPT", "", receipt, comparison)),
              patch.object(registered_cycle, "_decide", side_effect=lambda *args: args[-1]) as decide):
            self.assertEqual(registered_cycle._cycles(session), "CYCLE_LIMIT")
        self.assertEqual(session.call.call_args.kwargs["allowed_paths"], ["docs/DESIGN.md"])
        self.assertEqual(decide.call_args.args[-1], "CONTINUE")
        self.assertEqual(comparison["progress_basis"], "PLANNED_DOCUMENTATION")
        self.assertEqual(comparison["criteria"][key], "BLOCKED")
        self.assertEqual(session.record.call_args.args[0], "native_inspection_progress_checkpoint")

    def test_product_approval_stops_documentation_and_unverifiable_qa_before_developer(self):
        for path, mode in (("docs/DESIGN.md", "documentation"), ("src/qa.gd", "edit")):
            with self.subTest(path=path):
                scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Work", "checks": []}},
                         "allow_documentation": False, "goal": "Work", "files": [path, "src/game.gd"], "cycles": 1,
                         "requirements": [], "verifier": {}}
                session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(),
                                          check_image=Mock(), record=Mock(), changes=[], source=lambda: "source",
                                          owned_verified=True, usage_complete=True, publish_cycle=Mock(),
                                          call=Mock(return_value=json.dumps({"criterion": "C1", "plan": json.dumps({
                                              "artifact": path}),
                                                                            "mode": mode})))
                with (patch.object(registered_cycle, "Catalog"),
                      patch.object(registered_cycle, "read_frozen_requirements", return_value=[])):
                    self.assertEqual(registered_cycle.NEEDS_INPUT, registered_cycle._cycles(session))
                self.assertEqual(["manager-planning"], [call.args[1] for call in session.call.call_args_list])
                self.assertFalse(session.publish_cycle.called)
                self.assertEqual("PREREQUISITE", session.record.call_args.kwargs["deferred_criteria"]["C1"]["kind"])

    def test_test_only_delivery_does_not_count_as_reviewed_implementation(self):
        scope = {"criteria": {"C1": {"checks": []}}, "verifier": {"mode": "replica-integrity"}}
        checked = {"runtime": "replica-integrity", "integrity": "PASS", "owned_tree": "DRAINED"}
        comparison = {"criteria": {"C1": "USER_TEST_REQUIRED"}, "newly_verified": [], "regressed": []}
        accepted = set()
        stagnant = registered_cycle._cycle_stagnation(
            scope, "C1", "ACCEPT", checked, comparison, accepted, ["src/qa.gd", "docs/notes.md"], 1)
        self.assertEqual(2, stagnant)
        self.assertEqual("TEST_CHANGES_ONLY", comparison["progress_basis"])
        self.assertEqual(set(), accepted)

    def test_render_gate_blocks_code_only_gui_acceptance(self):
        scope = {"render_review_gate": 1, "criteria": {"C1": {"text": "Tutorial"}}}
        session = SimpleNamespace(scope=scope, signoff_state={"changed": ["scenes/tutorial.tscn"]},
                                  source=lambda: "candidate", call=Mock(), record=Mock(),
                                  fields={"run_id": "test"})
        with patch.object(registered_cycle, "_verify_signoff", return_value=({}, {})):
            self.assertEqual("REWORK", registered_cycle._review_and_verify(session, 1, "C1", "diff", {})[1])
        session.call.assert_not_called()
        self.assertIn("native_inspection_render_gate_blocked", [row.args[0] for row in session.record.call_args_list])

    def test_render_comparison_and_product_completion(self):
        before = {"id": "F1", "source_sha256": "baseline", "scenario": "hud", "step": "startup", "sha256": "a"}
        after = {**before, "source_sha256": "candidate", "sha256": "b"}
        pair = registered_cycle._render_pair
        self.assertEqual([{**before, "id": "BASELINE"}, after], pair([before], [after], "candidate"))
        self.assertEqual([], pair([before], [{**after, "scenario": "result"}], "candidate"))
        self.assertEqual([], pair([after], [after], "candidate"))
        self.assertEqual([], pair([before], [{**after, "uniform_client": True}], "candidate"))
        for flag in ("document_only", "test_changes_only"):
            comparison = {"criteria": {"C1": "VERIFIED"}, "regressed": [], "delivery": {flag: True}}
            self.assertEqual("USER_TEST_REQUIRED", registered_cycle._expected_final(
                {"render_review_gate": 1}, "C1", comparison, "ACCEPT"))

    def test_graceful_budget_stop_saves_settled_candidate_before_new_role(self):
        scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Work", "checks": []}},
                 "cycles": None, "graceful_stop": {}, "verifier": {}}
        session = SimpleNamespace(scope=scope, image={"files": {}}, check=Mock(), check_image=Mock(), record=Mock(),
                                  source=lambda: "verified-snapshot", changes=[], owned_verified=True,
                                  usage_complete=True, admit_graceful=Mock(side_effect=Blocked("GRACEFUL_STOP")))
        self.assertEqual(registered_cycle._cycles(session), "GRACEFUL_STOP")
        self.assertEqual(session.record.call_args.args[0], "native_inspection_progress_checkpoint")
        self.assertEqual(session.record.call_args.kwargs["cycle"], 0)

    def test_first_empty_developer_turn_replans_and_second_saves_candidate(self):
        scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Work", "checks": []}},
                 "goal": "Work", "files": ["work.txt"], "cycles": None, "requirements": [], "verifier": {}}
        session = SimpleNamespace(scope=scope, image={"files": {}}, runner=Mock(), check=Mock(), check_image=Mock(),
                                  record=Mock(), source=lambda: "verified-snapshot", changes=[], owned_verified=True,
                                  usage_complete=True)
        session.call = Mock(side_effect=[json.dumps({"criterion": "C1", "plan": "First"}),
                                         registered_cycle.NoDeveloperChange("no files"),
                                         json.dumps({"criterion": "C1", "plan": "Different approach"}),
                                         registered_cycle.NoDeveloperChange("no files")])
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(registered_cycle, "read_frozen_requirements", return_value=[])):
            self.assertEqual(registered_cycle._cycles(session), "NO_PROGRESS")
        self.assertIn("Choose a different bounded approach", session.call.call_args_list[2].args[-1])
        self.assertEqual(session.record.call_args.args[0], "native_inspection_progress_checkpoint")
        self.assertEqual(session.record.call_args.kwargs["source_sha256"], "verified-snapshot")

    def test_ui_distinguishes_developer_response_and_missing_candidate(self):
        from devorchestra.gui import CommandCenter
        from devorchestra.projection import registered_progress

        common = {"run_id": "fixture-run", "project_id": "fixture", "probe_kind": registered_cycle.KIND}
        rows = [{**common, "kind": "native_inspection_intent", "scope": {}, "sequence": 1},
                {**common, "kind": "native_inspection_role_result", "cycle": 2, "phase": "developer",
                 "outcome": "COMPLETED", "provider_usage": {"input_tokens": 3, "output_tokens": 2}}]
        roles = registered_progress(rows, "fixture")["roles"]
        self.assertEqual("RESPONSE_COMPLETE", CommandCenter.registered_role_state(roles["developer"], None, False))
        rows.append({**common, "kind": "native_inspection_replan", "cycle": 2, "reason": "NO_FILE_CHANGE"})
        view = registered_progress(rows, "fixture")
        self.assertEqual("NO_FILE_CHANGE", view["roles"]["developer"]["outcome"])
        for phase in ("reviewer", "verifier", "manager-final"):
            self.assertEqual("SKIPPED_NO_CANDIDATE", view["roles"][phase]["outcome"])
        self.assertEqual(5, view["tokens"])

    def test_pre_developer_recovery_requires_clean_stage_and_no_mutating_role(self):
        scope = {"prior_replica_candidate": {"changed": ["design.md"]}, "replica_sha256": "same",
                 "portable_source_policy": "GITHUB_PUBLIC_ZIP_V1"}
        common = {"run_id": "fixture", "approval_sha256": digest(scope)}
        rows = [
            {**common, "kind": "native_inspection_intent", "scope": scope, "workspace": "fixture-stage"},
            {**common, "kind": "native_inspection_role_intent", "phase": "manager-planning",
             "cycle": 1, "source_sha256": "same"},
            {**common, "kind": "native_inspection_role_result", "phase": "manager-planning", "cycle": 1,
             "outcome": "COMPLETED", "owned_tree": "DRAINED", "provider_usage": {"input_tokens": 3, "output_tokens": 2}},
            {**common, "kind": "native_inspection_project_tools_intent", "directory": "fixture-tools"},
            {**common, "kind": "native_inspection_project_tools_cleanup", "directory": "fixture-tools", "result": "REMOVED"},
            {**common, "kind": "native_inspection_result", "outcome": "BLOCKED", "owned_tree": "DRAINED",
             "reason": "arbitrary preparation failure", "cycle_count": 0, "model_turns_dispatched": 1,
             "usage_complete": True, "known_reported_tokens": 5, "changed": ["design.md"], "replica_sha256": "same",
             "staging_cleanup": {"status": "RETAINED_FOR_RECOVERY", "workspace": "fixture-stage"}},
        ]
        self.assertTrue(registered_cycle._recoverable_pre_developer_stop(rows))
        for index, patch_values in ((1, {"phase": "developer"}), (4, {"result": "UNCONFIRMED"}),
                                    (-1, {"replica_sha256": "different"}), (-1, {"known_reported_tokens": 0})):
            changed = rows.copy()
            changed[index] = {**changed[index], **patch_values}
            self.assertFalse(registered_cycle._recoverable_pre_developer_stop(changed))

    def test_read_only_planner_cannot_defer_approved_developer_work(self):
        from devorchestra.replica_evidence import snapshot

        with TemporaryDirectory() as folder:
            work = Path(folder)
            (work / "DESIGN.md").write_text("Existing design to preserve", encoding="utf-8")
            scope = {"project_id": "fixture", "criteria": {"C1": {"text": "Create a runnable app", "checks": []}},
                     "goal": "Build an app", "files": [], "project_edit": True, "protected_edit_paths": [],
                     "planning_protocol": registered_cycle.PLANNING_PROTOCOL, "cycles": 1, "requirements": [],
                     "permissions": list(registered_cycle.PERMISSIONS), "shell_roles": ["developer"],
                     "research": "CODEX_LIVE", "portable_source_policy": "GITHUB_PUBLIC_ZIP_V1",
                     "verifier": {"mode": registered_cycle.BASELINE_VERIFIER}}
            blocked = {"criterion": "C1", "mode": "needs-input", "prerequisite": "PLANNER_TOOLS",
                       "tools": [], "plan": "I cannot edit from the planning role."}
            session = SimpleNamespace(scope=scope, work=work, image=snapshot(work), runner=Mock(),
                                      check=Mock(), check_image=Mock(), record=Mock(), source=lambda: "source",
                                      changes=[], owned_verified=True, usage_complete=True)
            session.call = Mock(side_effect=[json.dumps(blocked), registered_cycle.NoDeveloperChange("fixture")])
            with (patch.object(registered_cycle, "Catalog"),
                  patch.object(registered_cycle, "read_frozen_requirements", return_value=[])):
                self.assertEqual("CYCLE_LIMIT", registered_cycle._cycles(session))
            self.assertEqual(["manager-planning", "developer"], [call.args[1] for call in session.call.call_args_list])
            self.assertIn("Existing design to preserve", session.call.call_args_list[0].args[3])
            self.assertIn("Controller checked", session.call.call_args_list[1].args[3])
            self.assertTrue(any(call.args[0] == "native_inspection_planning_handoff_corrected"
                                for call in session.record.call_args_list))
            capabilities = registered_cycle._developer_capabilities(scope)
            for prerequisite in ("REPLICA_WRITE", "OFFLINE_BUILD", "PUBLIC_RESEARCH", "PORTABLE_TOOL",
                                 "FUNCTIONAL_PROOF"):
                result = registered_cycle._repair_planning_handoff({**blocked, "prerequisite": prerequisite}, capabilities)
                self.assertEqual("edit", result["mode"])
            genuine = {**blocked, "prerequisite": "EXTERNAL_EVIDENCE"}
            self.assertIs(genuine, registered_cycle._repair_planning_handoff(genuine, capabilities))

    def test_whole_replica_can_add_remove_and_review_binary_assets_but_protects_inputs(self):
        from devorchestra.replica_evidence import snapshot

        with TemporaryDirectory() as folder:
            work = Path(folder)
            (work / "old.txt").write_text("remove this", encoding="utf-8")
            (work / "fixed.txt").write_text("expected output", encoding="utf-8")
            session = SimpleNamespace(work=work, original=work, original_image={}, image=snapshot(work),
                                      scope={"project_edit": True, "protected_edit_paths": ["fixed.txt"]}, changes=[])
            before = {"old.txt": "remove this", "fixed.txt": "expected output"}
            (work / "old.txt").unlink()
            (work / "game.gd").write_text("extends Node\n", encoding="utf-8")
            (work / "asset.dat").write_bytes(b"\0asset")
            with patch.object(registered_cycle, "original_matches", return_value=True):
                diff = registered_cycle.RegisteredSession._accept_developer(session, before)
                self.assertEqual(["asset.dat", "game.gd", "old.txt"], session.last_changed)
                self.assertIn("+++ /dev/null", diff)
                self.assertIn("+extends Node", diff)
                self.assertIn("sha256", diff)
                (work / "fixed.txt").write_text("changed expectation", encoding="utf-8")
                with self.assertRaises(Blocked):
                    registered_cycle.RegisteredSession._accept_developer(session, {})

    def test_explicit_verdicts_and_bounded_multiline_reason(self):
        parse = registered_cycle._parse_verdict
        options = registered_cycle.REVIEW_VERDICTS
        self.assertEqual(parse('{"verdict":"REWORK","reason":"Fix spacing.\\nCheck layout."}', options),
                         ("REWORK", "Fix spacing.\nCheck layout.", False))
        self.assertEqual(parse('{"verdict":"REWORK","reason":"ACCEPT: only the layout check passed."}', options),
                         ("REWORK", "ACCEPT: only the layout check passed.", False))
        for invalid in ('{"verdict":"REWORK","verdict":"ACCEPT","reason":""}',
                        '{"verdict":"ACCEPT","reason":[]}'):
            with self.assertRaises(registered_cycle.VerdictError):
                parse(invalid, options)
        for message, verdict, reason in [
            ("ACCEPT", "ACCEPT", ""), ("REWORK", "REWORK", ""),
            ("REWORK: fix labels", "REWORK", "fix labels"),
            ("REWORK\r\n1. Fix labels.\r\n\r\n- ACCEPT was considered.", "REWORK",
             "1. Fix labels.\n\n- ACCEPT was considered."),
            ("REWORK\n\nFirst paragraph.\n\nSecond paragraph.", "REWORK",
             "First paragraph.\n\nSecond paragraph."),
        ]:
            with self.subTest(message=message):
                self.assertEqual(parse(message, options), (verdict, reason, False))
        for message in ("I recommend REWORK", "ACCEPT or REWORK", "ACCEPT\nREWORK: change it",
                        "ACCEPT/REWORK", "COMPLETED", "ACCEPTED"):
            with self.subTest(message=message), self.assertRaises(registered_cycle.VerdictError):
                parse(message, options)
        for verdict in registered_cycle.FINAL_VERDICTS:
            self.assertEqual(parse(verdict + "\nGrounded reason.", registered_cycle.FINAL_VERDICTS)[0], verdict)
        self.assertEqual(parse("REWORK\n" + "a" * 2000, options),
                         ("REWORK", "a" * registered_cycle.MAX_VERDICT_REASON, True))

    def test_shutdown_reservation_can_be_cancelled_until_checkpoint_is_claimed(self):
        pause = registered_cycle.PauseControl()
        pause.change("request")
        self.assertEqual(pause.state, "SCHEDULED")
        pause.change("cancel")
        self.assertFalse(pause.claim())
        pause.change("request")
        self.assertTrue(pause.claim())
        pause.change("cancel")
        self.assertEqual(pause.state, "SAVING")

    def test_recovered_cycle_can_reserve_shutdown_in_command_center(self):
        from devorchestra.gui import CommandCenter

        for mode in ("partial-candidate", "postprocess"):
            with self.subTest(mode=mode):
                pause = registered_cycle.PauseControl()
                center = Mock()
                center.view = {"project_id": "fixture"}
                center.registered_job = {"future": Mock(), "cancelled": Event(), "pause": pause,
                                         "scope": {"project_id": "fixture", "resume": {"mode": mode}}}
                center.registered_job["future"].done.return_value = False
                center.shutdown_buttons = {key: Mock() for key in ("request", "cancel", "finalize", "resume")}
                CommandCenter.render_shutdown(center)
                center.operations_pause.configure.assert_called_with(
                    state="normal", text="종료 예약", command=unittest.mock.ANY)
                center.operations_pause.configure.call_args.kwargs["command"]()
                center.shutdown_command.assert_called_once_with("request")
                pause.change("request")
                CommandCenter.render_shutdown(center)
                center.operations_pause.configure.assert_called_with(
                    state="normal", text="예약 취소", command=unittest.mock.ANY)

    def test_feedback_is_linked_and_credential_like_findings_are_omitted(self):
        session = Mock()
        session.source.return_value = "source"
        message = "REWORK — The HUD clips at narrow widths."
        verdict, finding = registered_cycle._verdict(message, {"REWORK"})
        registered_cycle._record_feedback(session, 1, "C4", "reviewer", verdict, message, finding, ["hud.gd"])
        event = session.record.call_args.kwargs
        self.assertEqual((event["reason"], event["target"], event["files"]),
                         (finding, "C4", ["hud.gd"]))
        self.assertEqual(event["message_sha256"], digest(message))
        registered_cycle._record_feedback(session, 1, "C4", "manager-final", "REWORK",
                                          "REWORK — password fixture", "password fixture")
        self.assertEqual(session.record.call_args.kwargs["reason"], "[omitted: possible sensitive text]")

    def test_player_report_is_bound_to_candidate_and_is_not_a_verifier_result(self):
        candidate = {"source_sha256": "source", "run_id": "export", "identity": {"sha256": "exe"}}
        runner = Mock()
        runner._idle_diagnostic.return_value = ([], "head")
        ratings = dict.fromkeys(game_pilot.PLAYER_FEEDBACK_FIELDS, "OK")
        with patch.object(game_pilot, "player_candidate", return_value=candidate):
            game_pilot.save_player_feedback(runner, candidate, ratings, "test note", True)
        _, _, event = runner.ledger.append.call_args.args
        self.assertEqual(event["origin"], "USER_REPORTED")
        self.assertNotIn("verdict", event)
        self.assertIs(game_pilot.latest_player_feedback([event], "source", "export"), event)
        self.assertIsNone(game_pilot.latest_player_feedback([event], "source", "other-export"))

    def test_review_only_game_plan_reviews_diff_without_dispatching_developer(self):
        key = game_pilot.CRITERIA[1]
        session = Mock()
        session.image = {"files": {"game.gd": {}}}
        session.scope = {"project_id": game_pilot.PROJECT, "criteria": {key: {"text": "movement"}},
                         "goal": "Review play", "files": ["game.gd"], "cycles": None, "requirements": [],
                         "verifier": {"manager_brief": "game", "executable": "godot"},
                         "candidate_base": {"changed": ["game.gd"], "existing_plan": "review",
                                            "design_present": True, "parent_outcome": "REWORK"}}
        session.call.return_value = json.dumps({"criterion": key, "plan": "Review movement", "mode": "verify"})
        comparison = {"criteria": {key: "USER_TEST_REQUIRED"}, "newly_verified": [], "regressed": []}
        with patch.object(registered_cycle, "Catalog"), \
                patch.object(registered_cycle, "read_frozen_requirements", return_value=[]), \
                patch.object(registered_cycle, "_read_source", side_effect=["old\n", "new\n"]), \
                patch.object(registered_cycle, "_review_and_verify",
                             return_value=("ACCEPT", "ACCEPT", "", {}, comparison)) as verify, \
                patch.object(registered_cycle, "_decide", return_value="USER_TEST_REQUIRED"):
            self.assertEqual(registered_cycle._cycles(session), "USER_TEST_REQUIRED")
        self.assertEqual([call.args[1] for call in session.call.call_args_list], ["manager-planning"])
        self.assertIn("+new", verify.call_args.args[3])

    def test_only_approved_registered_replica_trust_is_accepted(self):
        with TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            replica = root / "old-replica"
            config = root / "config.toml"
            config.write_text(f"[projects.{json.dumps(str(replica))}]\ntrust_level = \"trusted\"\n",
                              encoding="utf-8")
            with self.assertRaises(Blocked):
                _check_dedicated_config(root, root / "temp")
            _check_dedicated_config(root, root / "temp", approved_replicas={replica})
            config.write_text(config.read_text(encoding="utf-8") + "[mcp_servers.example]\ncommand = \"ignored\"\n",
                              encoding="utf-8")
            with self.assertRaises(Blocked):
                _check_dedicated_config(root, root / "temp", approved_replicas={replica})


@unittest.skipUnless(os.environ.get("DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"), "Requires signed local Windows tools")
class RegisteredCycleTests(unittest.TestCase):

    def test_local_folder_cycle_and_interrupted_original_apply(self):
        self._local_folder_cycle(auto_apply=False)

    def test_local_folder_auto_applies_only_after_verified_cycle(self):
        self._local_folder_cycle(auto_apply=True)

    def _local_folder_cycle(self, *, auto_apply):
        class SimulatedExit(BaseException):
            pass

        with TemporaryDirectory(prefix="devorchestra-local-") as folder:
            directory = Path(folder)
            original, replica, state = [directory / name for name in ("source", "replica", ".ai-local")]
            original.mkdir()
            (original / "greeting.txt").write_bytes(b"draft\n")
            if not auto_apply:
                (original / "other.txt").write_bytes(b"old\n")
            (original / ".env").write_bytes(b"synthetic secret fixture")
            ledger = Ledger(state / "state" / "p1.sqlite3", local_root=state)
            ledger.initialize()
            (ledger.local_directory("state") / "codex-developer-home").mkdir()
            archive = codex_setup.archive_path(ledger)
            copyfile(os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"], archive)
            project = new_project("folder-fixture", "Local greeting", "Make the greeting read hello.")
            project.update(local_path=str(original), acceptance=["Greeting says hello"])
            catalog = Catalog(ledger)
            catalog.save_project(project)
            catalog.write_local_requirement("folder-fixture", "requirements/greeting.md", "Say hello.", "FEATURE")
            runner = Runner(ledger)
            with (patch.object(project_onboarding, "_git", side_effect=AssertionError("Git used")),
                  patch("devorchestra.direct_git._command", side_effect=AssertionError("Git publication used"))):
                frozen = local_folder.review(runner, "folder-fixture", replica)
                self.assertNotIn(".env", frozen["baseline"]["files"])
                (original / "extra.txt").write_bytes(b"owner")
                with self.assertRaisesRegex(Blocked, "변경"):
                    local_folder.create(runner, "folder-fixture", replica, digest(frozen))
                (original / "extra.txt").unlink()
                local_folder.create(runner, "folder-fixture", replica, digest(frozen))
                key = next(iter(cycle_progress.freeze(project["acceptance"], None)))
                verifier = {"executable": str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                            "checks": {key: {"args": ["/d", "/q", "/c", "type", "greeting.txt"],
                                             "stdout": "hello", "inputs": []}}}
                limits = registered_cycle.recommended_limits()
                limits.update(cycles=1, tokens=None, role_tokens=dict.fromkeys(limits["role_tokens"]),
                              token_mode=registered_cycle.SUBSCRIPTION_MODE)
                args = ("folder-fixture", str(archive), int(time()) + limits["seconds"] + 2940, limits["seconds"],
                        None, 1, ["greeting.txt", "other.txt"], list(registered_cycle.PERMISSIONS), verifier, None)
                calls = []

                def transport(binary, argv, work, logs, env, **kwargs):
                    (logs / "probe.stderr").write_bytes(b"")
                    if "login" in argv:
                        return {"stdout": "Logged in using ChatGPT\n", "exitCode": 0, "job_active": 0}
                    phase = logs.name.split("-", 1)[1]
                    calls.append(phase)
                    self.assertIn("permissions.developer-trial.network.enabled=false", argv)
                    if phase == "developer":
                        (work / "greeting.txt").write_bytes(b"hello\n")
                        (work / "other.txt").write_bytes(b"new\n")
                    plan = {"criterion": key, "mode": "edit", "plan": "Update local output",
                            "deliverable": "implementation", "components": "Text output",
                            "verification": "Check greeting", "evidence": "Exact output"}
                    message = {"manager-planning": json.dumps(plan), "developer": "Edited",
                               "reviewer": "ACCEPT — the diff meets the criterion.",
                               "manager-final": "COMPLETED — the independent check passed."}[phase]
                    rows = [{"type": "thread.started", "thread_id": "offline"}, {"type": "turn.started"},
                            {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5}}]
                    return {"stdout": "\n".join(map(json.dumps, rows)), "exitCode": 0, "job_active": 0}

                quota = {"status": "AVAILABLE", "remaining": 60, "checked_at": time(), "windows": []}
                with (patch.object(native_cycle, "_observe", side_effect=transport),
                      patch.object(registered_cycle, "_diagnostic", return_value={"request_id": "offline"}),
                      patch.object(codex_setup, "read_subscription_quota", return_value=quota)):
                    scope = registered_cycle.review(runner, *args, soft_limits=limits, auto_apply=auto_apply)
                    self.assertEqual("LOCAL_FOLDER", scope["development_mode"])
                    with self.assertRaisesRegex(Blocked, "직접 Git"):
                        registered_cycle.review(runner, *args, soft_limits=limits, direct_branch="main")
                    result = registered_cycle.run(runner, *args, soft_limits=limits, approval=digest(scope),
                                                  auto_apply=auto_apply)
                    self.assertEqual("COMPLETED", result["outcome"], result)
                self.assertEqual(["manager-planning", "developer", "reviewer", "manager-final"], calls)
                self.assertEqual({key: "VERIFIED"}, result["criteria_status"])
                self.assertEqual(b"hello\n" if auto_apply else b"draft\n",
                                 (original / "greeting.txt").read_bytes())
                self.assertEqual(b"hello\n", (replica / "greeting.txt").read_bytes())
                self.assertFalse((original / ".git").exists())
                self.assertFalse((replica / ".git").exists())
                events = ledger.read()
                report = next(row for row in events if row["kind"] == "native_inspection_development_record")
                self.assertIn("Verifier PASS", report["current"])
                self.assertIn("Target: Greeting says hello", report["current"])
                self.assertEqual("LOCAL_ONLY", report["publication"])
                self.assertFalse(any(row["kind"].startswith("direct_git") for row in events))
                expires = int(time()) + 200
                if auto_apply:
                    self.assertEqual("APPLIED", result["original_apply"]["result"])
                    self.assertEqual("original_apply_result", events[-1]["kind"])
                    self.assertIsNone(events[-1]["paths"]["other.txt"]["before"])
                    self.assertTrue((ledger.local_directory("recovery") / events[-1]["previous_bytes_ref"]).is_file())
                    self.assertEqual(b"new\n", (original / "other.txt").read_bytes())
                    with self.assertRaises(Blocked):
                        original_apply.review(runner, "folder-fixture", expires)
                    return
                (original / "unrelated.txt").write_bytes(b"owner edit")
                with self.assertRaises(Blocked):
                    original_apply.review(runner, "folder-fixture", expires)
                (original / "unrelated.txt").unlink()
                write_scope, _ = original_apply.review(runner, "folder-fixture", expires)

                def partial(source, stage, before, after, *checks, prepared):
                    name = next(iter(after))
                    os.rename(source / name, stage / "old-0")
                    os.rename(stage / "next-0", source / name)
                    raise SimulatedExit()

                with patch.object(original_apply, "_write", side_effect=partial), self.assertRaises(SimulatedExit):
                    original_apply.run(runner, "folder-fixture", expires, digest(write_scope))
                runner = Runner(Ledger(ledger.path, local_root=state))
                plan = original_apply.review_recovery(runner, "folder-fixture")
                self.assertEqual("ROLL_BACK", plan["action"])
                modified = next(name for name, status in plan["states"].items() if status == "NEW")
                target = original / modified
                observed = target.read_bytes()
                target.write_bytes(b"user edit")
                with self.assertRaisesRegex(Blocked, "사용자가 수정"):
                    original_apply.review_recovery(runner, "folder-fixture")
                target.write_bytes(observed)
                with self.assertRaisesRegex(Blocked, "검토가 변경"):
                    original_apply.recover(runner, "folder-fixture", "stale")
                applied = original_apply.recover(runner, "folder-fixture", digest(plan))
                self.assertEqual("ROLLED_BACK", applied["result"])
                self.assertEqual(b"draft\n", (original / "greeting.txt").read_bytes())
                with self.assertRaises(Blocked):
                    original_apply.recover(runner, "folder-fixture", digest(plan))
                write_scope, _ = original_apply.review(runner, "folder-fixture", expires)
                self.assertEqual("APPLIED", original_apply.run(
                    runner, "folder-fixture", expires, digest(write_scope))["result"])
                self.assertEqual(b"hello\n", (original / "greeting.txt").read_bytes())
                self.assertEqual(b"new\n", (original / "other.txt").read_bytes())

    def test_onboard_run_verify_and_review_original_write(self):
        self._exercise(False)

    def test_resume_settled_developer_without_repeating_model_calls(self):
        self._exercise(True)

    def test_windows_postprocess_reuses_reviewer_then_reworks_without_replay(self):
        self._exercise(False, recover=True)

    def test_windows_postprocess_reuses_final_manager_and_verifier_without_model_calls(self):
        self._exercise(False, recover="manager")

    def test_windows_final_decision_conflict_recovers_without_replaying_developer(self):
        self._exercise(False, recover="invalid-manager")

    def test_windows_large_source_and_diff_reach_reviewer_without_argument_limit(self):
        self._exercise(False, large=True)

    def _exercise(self, resume, *, recover=False, large=False):
        manager_recovery = recover in {"manager", "invalid-manager"}
        retry_decision = recover == "invalid-manager"
        approved = Path(__file__).resolve().parents[1] / ".ai-local" / "temp"
        with TemporaryDirectory(prefix="registered-test-", dir=approved) as folder:
            base = Path(folder)
            original, replica = base / "original", base / "replica"
            original.mkdir()
            runtime = base / ".ai-local"
            ledger = Ledger(runtime / "state" / "p1.sqlite3", local_root=runtime)
            ledger.initialize()
            (ledger.local_directory("state") / "codex-developer-home").mkdir()
            archive = codex_setup.archive_path(ledger)
            archive.parent.mkdir(parents=True, exist_ok=True)
            copyfile(os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"], archive)
            runner = Runner(ledger)
            (original / "formatting.py").write_text('GREETING = "old"\nSIGNOFF = "old"\n', encoding="utf-8")
            (original / "check.py").write_text(
                ('import sys\nimport formatting, closing\n'
                 'print(formatting.GREETING if sys.argv[1] == "GREETING" else closing.SIGNOFF)\n'
                 if resume else 'import sys\nimport formatting\nprint(getattr(formatting, sys.argv[1]))\n'),
                encoding="utf-8")
            if resume:
                (original / "closing.py").write_text('SIGNOFF = "old"\n', encoding="utf-8")
            else:
                (original / "greeting.txt").write_text("old\n", encoding="ascii")
                (original / "closing.txt").write_text("old\n", encoding="ascii")
            (original / "README.md").write_text("Text formatting utility\n", encoding="utf-8")
            if large:
                (original / "design.md").write_text("Initial design\n", encoding="utf-8")
            def git(*args):
                subprocess.run(["git", *args], cwd=original, check=True, timeout=15, capture_output=True)

            git("init", "--template=")
            git("config", "core.autocrlf", "false")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "--no-gpg-sign", "-m", "Existing formatting utility")
            (original / "README.md").write_text("User working change\n", encoding="utf-8")
            source = new_project("registered-test", "Formatting", "Change the greeting and signoff")
            source.update(local_path=str(original), acceptance=["Greeting displays hello", "Signoff displays goodbye"])
            Catalog(ledger).save_project(source)
            onboarding = project_onboarding.review(runner, "registered-test", str(replica), [])
            self.assertIn("README.md", onboarding["baseline"]["raw_worktree_changed"])
            receipt = project_onboarding.create(runner, "registered-test", str(replica), [], digest(onboarding))
            self.assertEqual(receipt["status"], "REPLICA_READY")
            keys = list(cycle_progress.freeze(source["acceptance"], None))
            tests = {"executable": sys.executable, "checks": {
                keys[0]: {"args": ["-B", "check.py", "GREETING"], "stdout": "hello", "inputs": ["check.py"]},
                keys[1]: {"args": ["-B", "check.py", "SIGNOFF"], "stdout": "goodbye", "inputs": ["check.py"]}}}
            if not resume:
                from devorchestra.mxc_launcher import _known_folder

                tests = {"executable": str(_known_folder(0x24) / "System32" / "cmd.exe"), "checks": {
                    keys[0]: {"args": ["/d", "/q", "/c", "type", "greeting.txt"], "stdout": "hello", "inputs": []},
                    keys[1]: {"args": ["/d", "/q", "/c", "type", "closing.txt"], "stdout": "goodbye", "inputs": []}}}
            params = ("registered-test", os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"], int(time()) + 600,
                      300, 270000, 1 if manager_recovery and not retry_decision else 2,
                      ["formatting.py", "closing.py"] if resume else
                      ["greeting.txt", "closing.txt"] + (["design.md"] if large else []),
                      list(registered_cycle.PERMISSIONS), tests)
            calls = []
            large_text = "Documented fixture behavior with UTF-8 입력.\n" * 900
            developer_observed = Event() if not resume and not recover and os.environ.get("DEVORCHESTRA_REGISTERED_GUI_QA") else None

            def transport(binary, argv, work, logs, env, **options):
                (logs / "probe.stderr").write_bytes(b"")
                if "login" in argv:
                    return {"stdout": "Logged in using ChatGPT\n", "exitCode": 0, "job_active": 0}
                cycle, phase = logs.name.split("-", 1)
                cycle = int(cycle)
                calls.append((cycle, phase))
                shell = "true" if phase in {"developer", "reviewer"} else "false"
                self.assertIn(f"features.shell_tool={shell}", argv)
                if phase != "developer":
                    schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8"))
                    self.assertFalse(schema["additionalProperties"])
                    self.assertIn("plan" if phase == "manager-planning" else "verdict", schema["required"])
                command_events = []
                if phase == "developer":
                    if cycle == 1 and developer_observed is not None:
                        command = {"id": "fixture-command", "type": "command_execution",
                                   "command": "python -m fixture PRIVATE_FIXTURE_ARGUMENT"}
                        command_events.append({"type": "item.started", "item": command})
                        partial = [{"type": "thread.started", "thread_id": "offline"},
                                   {"type": "turn.started"}, *command_events]
                        (logs / "probe.stdout").write_text("\n".join(map(json.dumps, partial)) + "\n", encoding="utf-8")
                        options["check"]()
                        self.assertTrue(developer_observed.wait(15), "Main window never showed the running command")
                        command_events.append({"type": "item.completed", "item": {**command, "exit_code": 0}})
                    if manager_recovery:
                        (work / "greeting.txt").write_text("hello\n", encoding="ascii")
                        (work / "closing.txt").write_text("goodbye\n", encoding="ascii")
                    elif recover:
                        if cycle == 1:
                            (work / "greeting.txt").write_text("candidate\n", encoding="ascii")
                        else:
                            self.assertIn("Fix the placeholder", argv[-1])
                            (work / "greeting.txt").write_text("hello\n", encoding="ascii")
                            (work / "closing.txt").write_text("goodbye\n", encoding="ascii")
                    elif not resume:
                        filename, value = ("greeting.txt", "hello") if cycle == 1 else ("closing.txt", "goodbye")
                        (work / filename).write_text(value + "\n", encoding="ascii")
                        if large and cycle == 1:
                            (work / "design.md").write_text(large_text, encoding="utf-8")
                    elif cycle == 2:
                        (work / "closing.py").write_text('SIGNOFF = "goodbye"\n', encoding="utf-8")
                    else:
                        code = 'GREETING = "hello"\nSIGNOFF = "old"\n' if cycle == 1 else (
                            'GREETING = "hello"\nSIGNOFF = "goodbye"\n')
                        (work / "formatting.py").write_text(code, encoding="utf-8")
                if large and (cycle, phase) == (1, "reviewer"):
                    self.assertEqual(argv[-1], "-")
                    self.assertIn("+Documented fixture behavior with UTF-8 입력.", options["stdin_bytes"].decode("utf-8"))
                    self.assertGreater(len(options["stdin_bytes"]), 32767)
                message = {"manager-planning": json.dumps({"criterion": keys[0 if recover else cycle - 1],
                                                           "mode": "edit", "plan": "Update the text",
                                                           "deliverable": "implementation", "components": "Text output",
                                                           "verification": "Run the approved exact-output check",
                                                           "evidence": "Expected greeting or signoff"}),
                           "developer": "Edited",
                           "reviewer": ("REWORK\n1. Fix the placeholder.\n\nThe display still needs the greeting."
                                        if recover and not manager_recovery and cycle == 1 else "ACCEPT — the diff meets the criterion."),
                           "manager-final": ("COMPLETED\nBoth independent checks passed." if manager_recovery
                                             else "REWORK\nThe review requires a fix." if recover and cycle == 1
                                             else "CONTINUE — one check remains." if cycle == 1
                                             else "COMPLETED\nBoth independent checks passed.")}[phase]
                if retry_decision and phase == "manager-final":
                    verdict = "CONTINUE" if calls.count((1, "manager-final")) == 1 else "COMPLETED"
                    message = json.dumps({"verdict": verdict, "reason": "Compare the independent checks."})
                events = [{"type": "thread.started", "thread_id": "offline"}, {"type": "turn.started"},
                          *command_events,
                          {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                          {"type": "turn.completed", "usage": {
                              "input_tokens": 60000 if resume and (cycle, phase) == (2, "developer") else 100,
                              "output_tokens": 5}}]
                stdout = "\n".join(map(json.dumps, events))
                if recover:
                    (logs / "probe.stdout").write_text(stdout, encoding="utf-8")
                return {"stdout": stdout, "exitCode": 0, "job_active": 0}

            with patch.object(native_cycle, "_observe", side_effect=transport), patch.object(
                    registered_cycle, "_diagnostic", return_value={"request_id": "offline"}):
                scope = registered_cycle.review(runner, *params)
                preview = registered_cycle.review(runner, *params[:-1],
                                                  {"mode": registered_cycle.BASELINE_VERIFIER},
                                                  visual_review=True)
                self.assertEqual(preview["visual_preview"], "scratch-windows-exe")
                self.assertEqual(calls, [])
                if recover:
                    parse = registered_cycle._parse_verdict

                    def legacy_format(message, options):
                        failing_role = (message.startswith("COMPLETED\n") and options == registered_cycle.FINAL_VERDICTS
                                        if manager_recovery else
                                        message.startswith("REWORK\n") and options == registered_cycle.REVIEW_VERDICTS)
                        if failing_role:
                            raise registered_cycle.VerdictError("Simulated previous parser failure")
                        return parse(message, options)

                    with patch.object(registered_cycle, "_parse_verdict", side_effect=parse if retry_decision else legacy_format):
                        stopped = registered_cycle.run(runner, *params, approval=digest(scope))
                    self.assertEqual(stopped["outcome"], registered_cycle.POSTPROCESS_PENDING)
                    self.assertEqual(stopped["model_turns_dispatched"], 4 if manager_recovery else 3)
                    self.assertEqual(stopped["known_reported_tokens"], 420 if manager_recovery else 315)
                    seconds, tokens, _ = registered_cycle.manual_remaining(scope, stopped)
                    args = ("registered-test", int(time()) + seconds + 2940, seconds, tokens)
                    approved = registered_cycle.review_resume(runner, *args, graceful=True)
                    ref = approved["resume"]["saved_response"]
                    self.assertEqual(ref["phase"], "manager-final" if manager_recovery else "reviewer")
                    self.assertEqual(approved["resume"].get("rerun_phase"), "manager-final" if retry_decision else None)
                    expected_calls = [(1, "manager-planning"), (1, "developer"), (1, "reviewer")]
                    if manager_recovery:
                        expected_calls.append((1, "manager-final"))
                    self.assertEqual(calls, expected_calls)
                    saved_log = ledger.local_directory("temp") / ref["workspace"] / "tmp" / f"1-{ref['phase']}" / "probe.stdout"
                    saved_bytes = saved_log.read_bytes()
                    saved_log.write_bytes(saved_bytes.replace(b"independent", b"changed").replace(b"placeholder", b"substituted"))
                    try:
                        with self.assertRaisesRegex(Blocked, "영수증"):
                            registered_cycle._read_settled_response(ledger, ref, approved)
                    finally:
                        saved_log.write_bytes(saved_bytes)
                    result = registered_cycle.run_resume(runner, *args, approval=digest(approved), graceful=True)
                    self.assertEqual((result["model_turns_dispatched"], result["known_reported_tokens"]),
                                     (1, 105) if retry_decision else (0, 0) if manager_recovery else (5, 525))
                    rows = registered_cycle.history(ledger, "registered-test")
                    self.assertEqual(any(row["kind"] == "native_inspection_role_reused" for row in rows), not retry_decision)
                    self.assertTrue(any(row["kind"] == "native_inspection_verifier_reused" for row in rows)
                                    if manager_recovery else True)
                    with self.assertRaises(Blocked):
                        registered_cycle.run_resume(runner, *args, approval=digest(approved), graceful=True)
                elif resume:
                    stopped = registered_cycle.run(runner, *params, approval=digest(scope))
                    self.assertEqual(stopped["outcome"], "TOKEN_LIMIT")
                    args = ("registered-test", int(time()) + 600, 300, 110000)
                    with self.assertRaisesRegex(Blocked, "No independent"):
                        registered_cycle.review_resume(runner, *args)
                    self.assertEqual(registered_cycle.verify_resume_candidate(runner, "registered-test")["verdict"],
                                     "PASS")
                    approval = registered_cycle.review_resume(runner, *args)
                    self.assertEqual(approval["resume"]["changed"], ["closing.py"])
                    candidate = replica / "closing.py"
                    original_bytes = candidate.read_bytes()
                    candidate.write_bytes(b'SIGNOFF = "tampered"\n')
                    with self.assertRaises(Blocked):
                        registered_cycle.run_resume(runner, *args, approval=digest(approval))
                    candidate.write_bytes(original_bytes)
                    result = registered_cycle.run_resume(runner, *args, approval=digest(approval))
                    self.assertEqual(calls[-2:], [(2, "reviewer"), (2, "manager-final")])
                    with self.assertRaises(Blocked):
                        registered_cycle.run_resume(runner, *args, approval=digest(approval))
                elif os.environ.get("DEVORCHESTRA_REGISTERED_GUI_QA"):
                    result = self._drive_gui(ledger, params, developer_observed)
                else:
                    result = registered_cycle.run(runner, *params, approval=digest(scope))
                self.assertEqual(result["outcome"], "COMPLETED", result)
                with self.assertRaises(Blocked):
                    registered_cycle.run(runner, *params, approval=digest(scope))
            self.assertEqual(len(calls), 5 if retry_decision else 4 if manager_recovery else 8)
            self.assertEqual(result["cycle_count"], 1 if manager_recovery else 2)
            self.assertEqual(result["criteria_status"], dict.fromkeys(keys, "VERIFIED"))
            self.assertEqual(result["staging_cleanup"]["status"], "REMOVED")
            self.assertIn('"old"', (original / "formatting.py").read_text())
            if not resume:
                self.assertEqual((original / "greeting.txt").read_text(), "old\n")
                self.assertEqual((original / "closing.txt").read_text(), "old\n")
            self.assertEqual((original / "README.md").read_text(), "User working change\n")
            if large:
                from hashlib import sha256
                from devorchestra.mxc_host_probe import _observe

                stdin = large_text.encode("utf-8")
                io = base / "stdin-check"
                io.mkdir()
                receipt = _observe(Path(sys.executable), ["-I", "-c",
                    "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"],
                    base, io, os.environ.copy(), stdin_bytes=stdin)
                self.assertEqual((receipt["exitCode"], receipt["job_active"]), (0, 0))
                self.assertEqual(receipt["stdout"].strip(), sha256(stdin).hexdigest())
            expires = int(time()) + 120
            apply, patch_text = original_apply.review(runner, "registered-test", expires)
            self.assertEqual(set(apply["paths"]), set(params[6]))
            self.assertIn('SIGNOFF = "goodbye"' if resume else "+goodbye", patch_text)
            if large:
                self.assertEqual((original / "design.md").read_text(encoding="utf-8"), "Initial design\n")
                applied = original_apply.run(runner, "registered-test", expires, digest(apply))
                self.assertEqual(applied["result"], "APPLIED")
                self.assertEqual((original / "design.md").read_text(encoding="utf-8"), large_text)
                self.assertEqual((original / "README.md").read_text(encoding="utf-8"), "User working change\n")

    def _drive_gui(self, ledger, params, developer_observed):
        import tkinter as tk
        from tkinter import ttk

        from devorchestra.gui import ACTIVE_AGENT_BORDER, TILE, CommandCenter
        from devorchestra.projection import ProjectionReader

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def button(widget, label):
            return next(child for child in descendants(widget)
                        if isinstance(child, ttk.Button) and child["text"] == label)

        root = tk.Tk()
        root.withdraw()
        try:
            center = CommandCenter(root, ProjectionReader(ledger))
            root.deiconify()
            center.project.current(center.project_ids.index("registered-test"))
            center.select_project(None)
            center.development_start()
            button(root, "등록 프로젝트 Codex 자동 개발").invoke()
            panel = next(widget for widget in root.winfo_children()
                         if isinstance(widget, tk.Toplevel) and "Codex 개발 세션" in widget.title())
            children = list(descendants(panel))
            whole = next(widget for widget in children if isinstance(widget, ttk.Checkbutton)
                         and widget["text"].startswith("복제본 전체 개발"))
            self.assertTrue(whole.instate(["selected"]))
            whole.invoke()
            entries = [widget for widget in children if isinstance(widget, ttk.Entry)]
            for entry, value in zip(entries, (params[1], ",".join(params[6]), "300", "270000", "2", "45000",
                                              "60000", "55000", "45000"),
                                    strict=True):
                entry.delete(0, "end")
                entry.insert(0, value)
            text = next(widget for widget in children if isinstance(widget, tk.Text)
                        and registered_cycle.BASELINE_VERIFIER in widget.get("1.0", "end"))
            text.delete("1.0", "end")
            text.insert("1.0", json.dumps(params[-1]))
            from devorchestra.gui_registered_cycle import LABELS

            for checkbox in (widget for widget in children
                             if isinstance(widget, ttk.Checkbutton)
                             and widget["text"] in {LABELS[key] for key in registered_cycle.PERMISSIONS}):
                if not checkbox.instate(["selected"]):
                    checkbox.invoke()
            confirmations = []
            settled = []
            deadline = monotonic() + 120
            paused = []

            def approve():
                dialog = next((widget for widget in descendants(root) if isinstance(widget, tk.Toplevel)
                               and widget.title() in {"등록 프로젝트 Codex 세션 승인", "체크포인트에서 이어서 개발"}), None)
                if dialog is not None:
                    confirmations.append(dialog.title())
                    button(dialog, "확인").invoke()
                if len(confirmations) < 2 and monotonic() < deadline:
                    root.after(30, approve)

            def inspect():
                heading = center.registered_status.cget("text")
                detail = center.registered_detail.cget("text")
                if (not developer_observed.is_set() and not panel.winfo_exists() and center.page == "operations"
                        and "1사이클 · Developer" in heading
                        and "실행 중" in center.phases[1][1].cget("text")
                        and center.card_frames["Developer"].cget("highlightbackground") == ACTIVE_AGENT_BORDER
                        and "도구 명령 실행 중 · python" in center.cards["Developer"][1].cget("text")
                        and "Manager가 전달한 계획 · Update the text" in center.cards["Developer"][1].cget("text")
                        and "확정 사용량 105" in detail and center.registered_cancel.winfo_ismapped()):
                    center.registered_history_button.invoke()
                    history = center.registered_history[2].get("1.0", "end")
                    if "도구 명령 실행 중 · python" in history and "PRIVATE_FIXTURE_ARGUMENT" not in history:
                        controls = center.shutdown_buttons
                        controls["request"].invoke()
                        if controls["cancel"].instate(["!disabled"]):
                            controls["cancel"].invoke()
                            if controls["request"].instate(["!disabled"]):
                                controls["request"].invoke()
                                controls["finalize"].invoke()
                                if "체크포인트를 저장합니다" in center.shutdown_label.cget("text"):
                                    developer_observed.set()
                progress = center.view.get("registered_progress") if center.view else None
                if progress and progress["end"] and center.registered_job["settled"]:
                    if (not paused and progress["end"]["outcome"] == registered_cycle.MANUAL_CHECKPOINT
                            and center.shutdown_buttons["resume"].instate(["!disabled"])):
                        paused.append(progress["end"])
                        center.shutdown_buttons["resume"].invoke()
                    if "완료 · 2사이클" in heading and "closing.txt" in detail:
                        settled.append(progress["end"])
                        root.quit()
                        return
                if monotonic() < deadline:
                    root.after(50, inspect)
                else:
                    root.quit()

            root.after(30, approve)
            root.after(30, button(panel, "세션 검토 및 시작").invoke)
            root.after(60, inspect)
            root.mainloop()
            if settled:
                self.assertEqual(confirmations, ["등록 프로젝트 Codex 세션 승인", "체크포인트에서 이어서 개발"])
                self.assertEqual(paused[0]["cycle_count"], 1)
                self.assertEqual(paused[0]["owned_tree"], "DRAINED")
                self.assertTrue(developer_observed.is_set())
                self.assertFalse(panel.winfo_exists())
                self.assertTrue(all(frame.cget("highlightbackground") == TILE for frame in center.card_frames.values()))
                history = center.registered_history[2].get("1.0", "end")
                self.assertIn("변경 파일 · closing.txt", history)
                self.assertIn("세션 종료 · 완료", history)
                center.original_apply_panel("registered-test")
                apply_window = next(widget for widget in root.winfo_children()
                                    if isinstance(widget, tk.Toplevel) and "검증된 변경 원본 반영" in widget.title())
                patch_view = next(widget for widget in descendants(apply_window) if isinstance(widget, tk.Text))
                self.assertIn("+goodbye", patch_view.get("1.0", "end"))
                return settled[0]
            self.fail(f"Command Center did not render a terminal registered result: "
                      f"{center.registered_status.cget('text')}; confirmations={confirmations}")
        finally:
            developer_observed.set()
            if center.registered_job and not center.registered_job["future"].done():
                center.cancel_registered_run()
                try:
                    center.registered_job["future"].result(timeout=15)
                except (RuntimeError, OSError, ValueError):
                    pass
            root.destroy()
