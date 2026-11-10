from pathlib import Path
import os
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from unittest.mock import patch

from devorchestra.catalog import Catalog, DEFAULT_BUDGET
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
