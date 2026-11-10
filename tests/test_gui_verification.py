from pathlib import Path
import os
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from unittest.mock import patch

from devorchestra.catalog import Catalog, DEFAULT_BUDGET, new_project
from devorchestra.gui_verification import verified_image
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.runtime import Runner, native_unresolved


class GuiVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = Ledger.for_project(Path(temporary.name))
        self.runner = Runner(self.ledger)
        self.runner.command("init")

    def register_form(self):
        root = self.ledger.state_boundary.parent
        original, replica = root / "original", root / "replica"
        original.mkdir()
        replica.mkdir()
        descriptor = replica / "gui-test.json"
        example = Path(__file__).resolve().parents[1] / "examples" / "native-form" / descriptor.name
        descriptor.write_bytes(example.read_bytes())
        Catalog(self.ledger).save_setup({
            "project": {**new_project("inventory", "Inventory", "Select an item"), "local_path": str(original)},
            "replica": str(replica), "wait_seconds": 20,
        })
        return descriptor

    def test_registered_scope_denies_manager_stale_descriptor_and_unsupported_exe(self):
        descriptor = self.register_form()
        with self.assertRaisesRegex(Blocked, "denied for this role"):
            self.runner.gui_verification_scope("inventory", registered=True, role="Manager")
        scope = self.runner.gui_verification_scope("inventory", registered=True)
        original = descriptor.read_bytes()
        descriptor.write_bytes(original + b" ")
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.verify_gui_fixture("inventory", digest(scope), registered=True)
        descriptor.write_text('{"format":"existing-exe","exe":"app.exe"}', encoding="utf-8")
        with self.assertRaisesRegex(Blocked, "NOT_VERIFIED"):
            self.runner.gui_verification_scope("inventory", registered=True)
        self.assertEqual([], self.ledger.read())

    def test_wrong_approval_and_unresolved_job_block_admission(self):
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.verify_gui_fixture("fixture-a", "invalid")
        self.assertEqual([], self.ledger.read())
        approval = digest(self.runner.gui_verification_scope("fixture-a"))
        self.ledger.update(lambda _events, _tail: {
            "kind": "gui_validation_intent", "approval_id": "lost", "project_id": "fixture-a",
        })
        with self.assertRaisesRegex(Blocked, "ownership requires recovery"):
            self.runner.verify_gui_fixture("fixture-a", approval)
        self.assertTrue(native_unresolved(self.ledger.read()))

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Interactive Windows GUI opt-in")
    def test_registered_project_runs_independent_role_scopes(self):
        descriptor = self.register_form()
        original = descriptor.read_bytes()
        for role in ("Developer", "Reviewer", "Verifier"):
            with self.subTest(role=role):
                options = {"registered": True, "role": role}
                scope = self.runner.gui_verification_scope("inventory", **options)
                receipt = self.runner.verify_gui_fixture("inventory", digest(scope), **options)
                self.assertEqual("PASS", receipt["result"], receipt["reason"])
                self.assertEqual("Selected: QA-024", receipt["observed"]["Selection confirmed"])
                self.assertEqual((24, 0, role), (receipt["observed"]["list_top_index"],
                                                  receipt["job_active"], receipt["role"]))
                self.assertEqual(3, len(receipt["scenes"]))
                self.assertEqual(receipt, self.runner.gui_verification_receipt("inventory", role="Manager"))
                self.assertFalse(native_unresolved(self.ledger.read()))
        self.assertEqual(original, descriptor.read_bytes())
        self.assertEqual([descriptor], list(descriptor.parent.iterdir()))
        self.assertFalse(list(self.ledger.local_directory("temp").iterdir()))

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Interactive Windows GUI opt-in")
    def test_actual_gui_receipt_and_cancellation(self):
        approval = digest(self.runner.gui_verification_scope("fixture-a"))
        receipt = self.runner.verify_gui_fixture("fixture-a", approval)
        self.assertEqual("PASS", receipt["result"], receipt["reason"])
        self.assertEqual(("CLOSED", 0, 0, True), (receipt["job_handles"], receipt["job_active"],
                                                    receipt["exit_code"], receipt["graceful_close"]))
        self.assertEqual(3, len(receipt["scenes"]))
        self.assertEqual(30, receipt["observed"]["list_top_index"])
        directory = self.ledger.local_directory("logs") / receipt["artifact_dir"]
        for item in [receipt["comparison"], *(scene[key] for scene in receipt["scenes"]
                                               for key in ("original", "processed"))]:
            self.assertEqual(item, verified_image(directory / item["file"], item["sha256"])[1])
        events = self.ledger.read()
        self.assertEqual(receipt["approval_id"], events[-2]["approval_id"])
        self.assertEqual(receipt, events[-1])
        self.assertFalse(native_unresolved(events))

        def cancel_after_launch():
            return any(self.ledger.local_directory("temp").glob("gui-verification-*/windows.json"))

        stopped = self.runner.verify_gui_fixture("fixture-a", approval, cancel_after_launch)
        self.assertEqual(("NOT_VERIFIED", "CANCELLED", 0, "CLOSED"),
                         (stopped["result"], stopped["reason"], stopped["job_active"], stopped["job_handles"]))
        self.assertFalse(native_unresolved(self.ledger.read()))

        with patch("devorchestra.gui_verification._exercise", side_effect=Blocked("QA_INJECTED_UI_FAILURE")):
            failed = self.runner.verify_gui_fixture("fixture-a", approval)
        self.assertEqual(("FAIL", "QA_INJECTED_UI_FAILURE", 0, True),
                         (failed["result"], failed["reason"], failed["job_active"], failed["graceful_close"]))
        self.assertFalse(native_unresolved(self.ledger.read()))

        Catalog(self.ledger).save_limits({**DEFAULT_BUDGET, "seconds": 1}, 0)
        short_approval = digest(self.runner.gui_verification_scope("fixture-a"))

        def stall_after_launch():
            if any(self.ledger.local_directory("temp").glob("gui-verification-*/windows.json")):
                sleep(1.05)
            return False

        timeout = self.runner.verify_gui_fixture("fixture-a", short_approval, stall_after_launch)
        self.assertEqual(("NOT_VERIFIED", "TIME_LIMIT", 0, "CLOSED"),
                         (timeout["result"], timeout["reason"], timeout["job_active"], timeout["job_handles"]))
        self.assertFalse(native_unresolved(self.ledger.read()))
        self.assertFalse(list(self.ledger.local_directory("temp").iterdir()))
