from json import dumps, loads
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from devorchestra import adaptive_play, native_cycle, registered_cycle, visual_review
from devorchestra.default_instructions import public_instruction
from devorchestra.lease import Blocked
from devorchestra.native_developer_trial import _exec_argv


class VisualReviewTests(unittest.TestCase):
    def test_image_dispatch_keeps_settled_response_after_owned_job_recovery(self):
        from devorchestra.mxc_host_probe import OwnedMxcStopped

        instruction = public_instruction("reviewer")
        with TemporaryDirectory() as directory:
            work = Path(directory)

            def logs(label):
                path = work / label
                path.mkdir()
                return path

            session = SimpleNamespace(check=Mock(), check_image=Mock(), calls=0, usage=0, record=Mock(), phases=[],
                                      source=lambda: "source", logs=logs, seconds=lambda _: 60, env={},
                                      progress_check=Mock(),
                                      scope={"role_tokens": {"reviewer": 100}, "model_calls_max": 1,
                                             "tokens": 100, "model": "test", "visual_review": True,
                                             "instructions": {"reviewer": instruction["sha256"]}},
                                      binary=work / "codex.exe", work=work)

            def recovered(_binary, _argv, _work, logs, _env, **_options):
                rows = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"},
                        {"type": "item.completed", "item": {"type": "agent_message", "text": "settled"}},
                        {"type": "turn.completed", "usage": {"input_tokens": 15, "output_tokens": 2}}]
                (logs / "probe.stdout").write_text("\n".join(map(dumps, rows)) + "\n", encoding="utf-8")
                raise OwnedMxcStopped("MODEL_SETTLED_EXIT_DELAY")

            with patch.object(native_cycle, "_observe", side_effect=recovered):
                response, _ = native_cycle._FixtureSession.dispatch(
                    session, 1, "reviewer", "C1", "Inspect the image", shell_tools=False,
                    images=(work / "F1.png",), image_evidence=({"id": "F1"},))
        self.assertEqual("settled", response["message"])
        self.assertEqual(17, session.usage)
        self.assertTrue(session.owned_verified)
        self.assertEqual("TERMINATED_AND_DRAINED", session.phases[0]["process_result"])
        self.assertEqual("MODEL_SETTLED_EXIT_DELAY", session.phases[0]["process_stop_reason"])

    def test_image_turn_reclaims_slow_exit_only_with_settled_usage_and_response(self):
        with TemporaryDirectory() as directory:
            log = Path(directory)
            stream = log / "probe.stdout"
            session = SimpleNamespace(scope={}, check=Mock())
            check = native_cycle._FixtureSession.progress_check(session, "reviewer", 0, log, 4096,
                                                               settle_image_exit=True)
            events = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"},
                      {"type": "item.completed", "item": {"type": "agent_message", "text": "settled"}},
                      {"type": "turn.completed", "usage": {"input_tokens": 15, "output_tokens": 2}}]
            stream.write_text("\n".join(map(dumps, events[:-1])) + "\n", encoding="utf-8")
            with patch.object(native_cycle, "monotonic", return_value=100):
                check()
            stream.write_text("\n".join(map(dumps, events)) + "\n", encoding="utf-8")
            with patch.object(native_cycle, "monotonic", side_effect=[101, 115, 116]):
                check()
                check()
                with self.assertRaisesRegex(Blocked, "MODEL_SETTLED_EXIT_DELAY"):
                    check()
            stream.write_text("\n".join(map(dumps, events + [{"type": "error"}])) + "\n", encoding="utf-8")
            with patch.object(native_cycle, "monotonic", side_effect=[120, 150]):
                check()
                check()

    def test_fresh_resume_approval_updates_instructions_without_changing_checkpoint(self):
        saved = {"mode": "postprocess", "saved_response": {"message_sha256": "old-response"}}
        scope = {"instructions": {role: public_instruction(role)["sha256"] for role in registered_cycle.ROLES},
                 "resume": saved, "permissions": ["replica-read"]}
        scope["instructions"]["reviewer"] = "old-instruction"
        with patch.object(registered_cycle, "_resume_contract", return_value=(scope, "verified diff")):
            approved, diff = registered_cycle._approved_resume_contract(None, [], "head", (), True)
        self.assertEqual("old-instruction", scope["instructions"]["reviewer"])
        self.assertEqual(public_instruction("reviewer")["sha256"], approved["instructions"]["reviewer"])
        self.assertEqual(public_instruction("global")["sha256"], approved["instructions"]["global"])
        self.assertEqual(["global", "reviewer"], approved["resume"]["updated_role_instructions"])
        self.assertEqual(saved["saved_response"], approved["resume"]["saved_response"])
        self.assertEqual(scope["permissions"], approved["permissions"])
        self.assertEqual("verified diff", diff)

    def test_exec_delimits_the_prompt_from_variadic_image_arguments(self):
        root = Path.cwd()
        images = (root / "F1.png", root / "F2.png")
        args = _exec_argv(root, "test", "Inspect both frames", writable=False, shell_tools=False, images=images)
        self.assertEqual(["--image", str(images[0]), "--image", str(images[1]), "--", "Inspect both frames"],
                         args[-6:])
        self.assertIn("features.computer_use=false", args)
        self.assertIn("features.shell_tool=false", args)

    def report(self, verdict="REWORK", kind="defect", uncertainty="low"):
        return dumps({"verdict": verdict, "observations": [{"frame": "F1", "visible": "Label clips at right edge"}],
                      "not_verified": ["Animation timing"], "code_finding": None,
                      "findings": [{"id": "V1", "kind": kind, "severity": "high", "frame": "F1",
                                    "observed": "Label clips at right edge", "expected": "Complete label",
                                    "reproduce": "Open the initial window", "fix": "Allow the label to wrap",
                                    "basis": "The last glyph is truncated", "uncertainty": uncertainty}]})

    def test_suggestions_and_uncertain_defects_cannot_require_rework(self):
        _, blockers = visual_review.parse(self.report(), ["F1"])
        self.assertEqual(["V1"], [item["id"] for item in blockers])
        for override in ({"kind": "suggestion"}, {"uncertainty": "high"}):
            with self.subTest(override=override), self.assertRaises(Blocked):
                visual_review.parse(self.report(**override), ["F1"])
            _, blockers = visual_review.parse(self.report(verdict="ACCEPT", **override), ["F1"])
            self.assertFalse(blockers)
        with self.assertRaises(Blocked):
            visual_review.parse(self.report(), ["F2"])

    def test_bounded_uncertainty_notes_do_not_discard_valid_visual_observations(self):
        report = loads(self.report(verdict="ACCEPT", kind="suggestion"))
        report["not_verified"] = [f"Unverified observation {number}" for number in range(5)]
        result, blockers = visual_review.parse(dumps(report), ["F1"])
        self.assertEqual(report["not_verified"], result["not_verified"])
        self.assertEqual([], blockers)
        report["not_verified"] *= 2
        with self.assertRaises(Blocked):
            visual_review.parse(dumps(report), ["F1"])

    def test_runtime_freezes_reviewer_document_and_passes_real_attachment_arguments(self):
        instruction = public_instruction("reviewer")
        common = public_instruction("global")
        session = SimpleNamespace(check=Mock(), check_image=Mock(), calls=0, usage=0, record=Mock(),
                                  source=lambda: "source", logs=lambda _: Path.cwd(), seconds=lambda _: 10,
                                  progress_check=Mock(),
                                  scope={"role_tokens": {"reviewer": 100}, "model_calls_max": 1,
                                         "tokens": 100, "model": "test", "visual_review": True,
                                         "instructions": {"reviewer": instruction["sha256"], "global": common["sha256"]}},
                                  binary=Path.cwd() / "codex.exe", work=Path.cwd())
        image = Path.cwd() / "F1.png"
        with (TemporaryDirectory() as folder,
              patch.object(native_cycle, "_observe") as launch,
              patch.object(native_cycle, "_exec_argv", side_effect=Blocked("fixture boundary")) as argv):
            session.logs = lambda _: Path(folder)
            session.scope["instructions"]["global"] = "stale"
            with self.assertRaisesRegex(Blocked, "global instructions changed"):
                native_cycle._FixtureSession.dispatch(session, 1, "reviewer", "C1", "Inspect the image")
            argv.assert_not_called()
            session.scope["instructions"]["global"] = common["sha256"]
            with self.assertRaisesRegex(Blocked, "fixture boundary"):
                native_cycle._FixtureSession.dispatch(
                    session, 1, "reviewer", "C1", "Inspect the image", shell_tools=False,
                    images=(image,), image_evidence=({"id": "F1"},))
            self.assertTrue(argv.call_args.kwargs["output_schema"].is_file())
        self.assertIn(instruction["text"], argv.call_args.args[2])
        self.assertTrue(argv.call_args.args[2].startswith(common["text"] + "\n\n"))
        self.assertIn("NOT_VERIFIED", argv.call_args.args[2])
        self.assertEqual((image,), argv.call_args.kwargs["images"])
        self.assertFalse(argv.call_args.kwargs["writable"])
        launch.assert_not_called()

    def test_review_uses_capture_receipt_then_checks_evidence_again(self):
        criteria = {"C1": {"text": "Show complete labels", "checks": []}}
        evidence = [{"id": "F1", "step": "before-input", "scenario": "C1", "source_sha256": "source"}]
        receipt = {"verdict": "NOT_VALIDATED", "phase": "verifier", "outcome": "COMPLETED",
                   "observations": [], "user_test_required": ["C1"], "source_sha256": "source"}
        ledger = SimpleNamespace(state_boundary=Path.cwd())
        session = SimpleNamespace(scope={"criteria": criteria, "visual_review": True,
                                         "project_id": "fixture", "requirements": []},
                                  check=Mock(), check_image=Mock(), record=Mock(), phases=[],
                                  verify_registered=Mock(return_value=receipt), call=Mock(return_value=self.report()),
                                  fields={"run_id": "a" * 32}, source=lambda: "source",
                                  runner=SimpleNamespace(ledger=ledger), signoff_state={}, logs=lambda _: Path.cwd())
        image = Path.cwd() / "F1.png"
        with (
            patch.object(registered_cycle, "Catalog"),
            patch.object(visual_review, "frames", return_value=evidence),
            patch.object(visual_review, "stage", return_value=(image,)),
            patch.object(visual_review, "read_frames") as recheck,
        ):
            decision, review, finding, _, comparison = registered_cycle._review_and_verify(
                session, 1, "C1", "bounded diff", {"C1": "NOT_STARTED"})
        self.assertEqual("REWORK", review)
        self.assertIn("Label clips", finding)
        self.assertIn("Allow the label to wrap", decision)
        self.assertEqual({"C1": "USER_TEST_REQUIRED"}, comparison["criteria"])
        self.assertEqual((image,), session.call.call_args.kwargs["images"])
        self.assertIn("Do not claim to have played", session.call.call_args.args[3])
        review_record = next(call.kwargs for call in session.record.call_args_list
                             if call.args[0] == "native_inspection_visual_review")
        self.assertEqual("CODEX_CLI_IMAGE", review_record["review_backend"])
        self.assertEqual("NOT_VERIFIED", review_record["autonomous_play"])
        self.assertEqual("CONTROLLER_SCENARIO",
                         visual_review.observation_provenance([{"step": "after-input"}])["input_source"])
        session.verify_registered.assert_called_once_with(1)
        recheck.assert_called_once_with(ledger, evidence)

    def test_missing_preview_still_runs_code_review(self):
        criteria = {"C1": {"text": "Show complete labels", "checks": []}}
        receipt = {"verdict": "NOT_VALIDATED", "phase": "verifier", "outcome": "COMPLETED",
                   "owned_tree": "DRAINED", "observations": [], "user_test_required": ["C1"],
                   "visual_preview": {"reason": "NO_RUNNABLE_GUI"}, "source_sha256": "source"}
        session = SimpleNamespace(scope={"criteria": criteria, "visual_review": True},
                                  fields={"run_id": "a" * 32}, source=lambda: "source",
                                  runner=SimpleNamespace(ledger=None), verify_registered=Mock(return_value=receipt),
                                  record=Mock(), phases=[], signoff_state={}, call=Mock(return_value="ACCEPT"))
        _, review, _, _, comparison = registered_cycle._review_and_verify(
            session, 1, "C1", "bounded diff", {"C1": "NOT_STARTED"})
        self.assertEqual(review, "ACCEPT")
        self.assertIn("Visual quality is NOT_VERIFIED", session.call.call_args.args[3])
        review_record = next(call.kwargs for call in session.record.call_args_list
                             if call.args[0] == "native_inspection_visual_review")
        self.assertEqual(("NO_CAPTURE", "NOT_VERIFIED"),
                         (review_record["observation_source"], review_record["autonomous_play"]))
        self.assertEqual(comparison["criteria"], {"C1": "USER_TEST_REQUIRED"})
        session.verify_registered.assert_called_once_with(1)

    def test_saved_play_evidence_is_reused_and_actions_reach_rework(self):
        criteria = {"C1": {"text": "Show complete labels", "checks": []}}
        evidence = [{"id": f"P{index}", "step": "play-observation", "scenario": "C1", "source_sha256": "source",
                     "executable_sha256": "exe", "sha256": f"hash-{index}"} for index in range(2)]
        receipt = {"verdict": "NOT_VALIDATED", "phase": "verifier", "outcome": "COMPLETED",
                   "observations": [], "user_test_required": ["C1"], "source_sha256": "source"}
        actions = [{"step": 1, "action": {"action": "click", "x": 10, "y": 12}, "result": "SCREEN_CHANGED",
                    "before": "hash-0", "after": "hash-1"}]
        ledger = SimpleNamespace(state_boundary=Path.cwd())
        session = SimpleNamespace(scope={"criteria": criteria, "visual_review": True,
                                         "adaptive_play": adaptive_play.approval(),
                                         "project_id": "fixture", "requirements": []},
                                  check=Mock(), check_image=Mock(), record=Mock(), phases=[],
                                  verify_registered=Mock(), call=Mock(return_value=dumps({
                                      **loads(self.report().replace('"F1"', '"P0"')),
                                      "observations": [{"frame": frame["id"], "visible": "Observed"} for frame in evidence]})),
                                  fields={"run_id": "a" * 32}, source=lambda: "source",
                                  runner=SimpleNamespace(ledger=ledger), logs=lambda _: Path.cwd(),
                                  signoff_state={"pending_visual": {"receipt": receipt, "frames": evidence,
                                     "play_report": {"actions": actions, "outcome": "STEP_LIMIT", "has_frames": True}}})
        with (patch.object(registered_cycle, "Catalog"),
              patch.object(visual_review, "stage", return_value=(Path.cwd() / "F1.png",)),
              patch.object(visual_review, "read_frames"),
              patch.object(registered_cycle.adaptive_play, "run") as replay):
            decision, review, _, _, comparison = registered_cycle._review_and_verify(
                session, 1, "C1", "bounded diff", {"C1": "NOT_STARTED"})
        replay.assert_not_called()
        session.verify_registered.assert_not_called()
        self.assertEqual("REWORK", review)
        self.assertIn('"reproduce_actions"', decision)
        self.assertEqual(1, comparison["adaptive_play_actions"])
        self.assertEqual("USER_TEST_REQUIRED", comparison["criteria"]["C1"])
