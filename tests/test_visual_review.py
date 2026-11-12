from json import dumps
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from devorchestra import native_cycle, registered_cycle, visual_review
from devorchestra.default_instructions import public_instruction
from devorchestra.lease import Blocked
from devorchestra.native_developer_trial import _exec_argv


class VisualReviewTests(unittest.TestCase):
    def test_fresh_resume_approval_updates_instructions_without_changing_checkpoint(self):
        saved = {"mode": "postprocess", "saved_response": {"message_sha256": "old-response"}}
        scope = {"instructions": {role: public_instruction(role)["sha256"] for role in registered_cycle.ROLES},
                 "resume": saved, "permissions": ["replica-read"]}
        scope["instructions"]["reviewer"] = "old-instruction"
        with patch.object(registered_cycle, "_resume_contract", return_value=(scope, "verified diff")):
            approved, diff = registered_cycle._approved_resume_contract(None, [], "head", (), True)
        self.assertEqual("old-instruction", scope["instructions"]["reviewer"])
        self.assertEqual(public_instruction("reviewer")["sha256"], approved["instructions"]["reviewer"])
        self.assertEqual(["reviewer"], approved["resume"]["updated_role_instructions"])
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

    def test_runtime_freezes_reviewer_document_and_passes_real_attachment_arguments(self):
        instruction = public_instruction("reviewer")
        session = SimpleNamespace(check=Mock(), check_image=Mock(), calls=0, usage=0, record=Mock(),
                                  source=lambda: "source", logs=lambda _: Path.cwd(), seconds=lambda _: 10,
                                  scope={"role_tokens": {"reviewer": 100}, "model_calls_max": 1,
                                         "tokens": 100, "model": "test", "visual_review": True,
                                         "instructions": {"reviewer": instruction["sha256"]}},
                                  binary=Path.cwd() / "codex.exe", work=Path.cwd())
        image = Path.cwd() / "F1.png"
        with (TemporaryDirectory() as folder,
              patch.object(native_cycle, "_observe") as launch,
              patch.object(native_cycle, "_exec_argv", side_effect=Blocked("fixture boundary")) as argv):
            session.logs = lambda _: Path(folder)
            with self.assertRaisesRegex(Blocked, "fixture boundary"):
                native_cycle._FixtureSession.dispatch(
                    session, 1, "reviewer", "C1", "Inspect the image", shell_tools=False,
                    images=(image,), image_evidence=({"id": "F1"},))
            self.assertTrue(argv.call_args.kwargs["output_schema"].is_file())
        self.assertIn(instruction["text"], argv.call_args.args[2])
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
