from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.ledger import Ledger
from devorchestra.owned_artifacts import cleanup, review, workspace
from devorchestra.runtime import native_unresolved


class OwnedArtifactsTests(unittest.TestCase):
    def test_open_file_and_wrong_owner_preserve_disposable_workspace(self):
        with TemporaryDirectory() as temp:
            ledger = Ledger.for_project(Path(temp))
            ledger.local_directory("temp", create=True)
            run_id = "a" * 32
            root = workspace(ledger, run_id)
            root.mkdir()
            identity = root.stat().st_ino
            payload = root / "payload"
            payload.write_bytes(b"data")
            self.assertEqual("UNCONFIRMED", cleanup(ledger, run_id, root, identity + 1)["status"])
            with payload.open("rb"):
                self.assertEqual("UNCONFIRMED", cleanup(ledger, run_id, root, identity)["status"])
                self.assertTrue(payload.is_file())
            self.assertEqual("REMOVED", cleanup(ledger, run_id, root, identity)["status"])
            self.assertFalse(root.exists())

    def test_interruption_preserves_the_pending_workspace_and_ledger(self):
        with TemporaryDirectory() as temp:
            ledger = Ledger.for_project(Path(temp))
            ledger.initialize()
            ledger.local_directory("temp", create=True)
            run_id = "b" * 32
            root = workspace(ledger, run_id)
            root.mkdir()
            (root / "remaining").write_bytes(b"data")
            intent = {"kind": "build_verifier_intent", "run_id": run_id,
                      "cleanup_plan": {"workspace": root.name}}
            ledger.append(0, "", intent)
            with patch("devorchestra.owned_artifacts.rmtree", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    cleanup(ledger, run_id, root, root.stat().st_ino)
            self.assertTrue(native_unresolved(ledger.read()))
            self.assertEqual("PROTECTED", review(ledger, 2)["entries"][0]["classification"])
            self.assertTrue((root / "remaining").exists())
