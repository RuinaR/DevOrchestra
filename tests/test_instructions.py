from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch

from devorchestra.instructions import InstructionStore, selected_source, source_ref
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.runtime import Runner


class InstructionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.ledger = Ledger(root / "run.sqlite3")
        self.store = InstructionStore(self.ledger)
        self.source = root / "AGENTS.md"
        self.source.write_bytes(b"\xef\xbb\xbfOriginal\r\n")
        self.replica = root / "replica"
        self.replica.mkdir()
        self.project = self.replica / "AGENTS.md"
        self.project.write_bytes(b"Project contract\n")
        self.version = self.store.backup(self.source)
        self.sha256 = self.store.compare(self.version)["sha256"]

    def approve(self, version=None, *, expires=None):
        version = version or self.version
        path, data = selected_source(self.replica)
        return self.store.approve(version, self.store.compare(version)["sha256"],
                                  "fixture-a", self.replica, source_ref(path, data),
                                  time() + 60 if expires is None else expires)

    def test_backup_preserves_bytes_and_reopens_after_source_change(self):
        pin = self.approve()
        original = self.source.read_bytes()
        self.source.write_bytes(b"Edited after approval\n")
        reopened = InstructionStore(self.ledger)
        self.assertTrue(reopened.compare(self.version)["source_changed"])
        self.assertEqual(original, reopened.preview(self.version)[1])
        self.assertTrue(reopened.verify(pin, self.replica).startswith(original))
        self.assertEqual(b"Edited after approval\n", self.source.read_bytes())

    def test_approval_expires_and_project_change_blocks_pin(self):
        with self.assertRaises(Blocked):
            self.approve(expires=time() - 1)
        pin = self.approve()
        with patch("devorchestra.instructions.time", return_value=pin["expires"]):
            with self.assertRaisesRegex(Blocked, "expired"):
                self.store.verify(pin, self.replica)
        (self.replica / "AGENTS.override.md").write_text("Conflicting override")
        with self.assertRaisesRegex(Blocked, "changed"):
            self.store.verify(pin, self.replica)
        with self.assertRaisesRegex(Blocked, "changed"):
            self.store.approve(self.version, self.sha256, "fixture-a", self.replica,
                               pin["project"], time() + 60)

    def test_damaged_backup_is_not_accepted(self):
        pin = self.approve()
        with self.store.ledger.connect("rw") as db:
            db.execute("UPDATE events SET hash = 'corrupt' WHERE seq = 1")
        with self.assertRaisesRegex(Blocked, "verification failed"):
            self.store.verify(pin, self.replica)

    def test_controller_stages_frozen_version_and_restore_requires_approval(self):
        old_pin = self.approve()
        runner = Runner(self.ledger)
        runner.command("init")
        run = runner.command("start")["run_id"]
        for action in ("step", "approve", "step"):
            runner.command(action, expected_run=run)
        catalog = {"setups": {"fixture-a": {"replica": str(self.replica)}}}
        with patch("devorchestra.runtime.Catalog.read", return_value=catalog):
            self.assertEqual(old_pin, runner.pin_instruction_baseline(run)["instruction_pin"])
            self.source.write_bytes(b"New version\n")
            new_version = self.store.backup(self.source)
            self.assertEqual(self.version, self.store.active("fixture-a")["version"])
            self.approve(new_version)
            env = Runner(Ledger(self.ledger.path)).stage_instruction_home(run)
            self.assertEqual("INSTRUCTION_LOAD_UNVERIFIED", env["instruction_loading"])
            self.assertEqual(self.store.preview(self.version)[2],
                             (Path(env["CODEX_HOME"]) / "AGENTS.md").read_bytes())
            restored = self.approve()
            self.assertNotEqual(old_pin["approval_id"], restored["approval_id"])
            self.assertEqual(self.version, self.store.active("fixture-a")["version"])
