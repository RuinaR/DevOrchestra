import json
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
from shutil import copytree
import stat
import subprocess
from tempfile import TemporaryDirectory
from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devorchestra.__main__ import main
from devorchestra.catalog import Catalog, new_project
from devorchestra.instructions import InstructionStore, selected_source, source_ref
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.runtime import Runner
from devorchestra.setup import validate_setup
from tools.check_public_index import check_index


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_default_entry_does_not_follow_environment_or_cwd(self):
        expected = Ledger.for_project().path
        original = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "legacy")}):
                self.assertEqual(expected, Ledger.for_project().path)
        finally:
            os.chdir(original)

    def test_new_layout_preserves_ledger_instructions_and_shutdown(self):
        ledger = Ledger.for_project(self.root)
        self.assertFalse(ledger.state_boundary.exists())
        Runner(ledger).command("init")
        self.assertEqual({"state", "instructions", "logs", "recovery", "temp"},
                         {p.name for p in ledger.state_boundary.iterdir()})
        self.assertEqual(ledger.path.parent, Catalog(ledger).ledger.path.parent)
        self.assertGreater(Catalog(ledger).save_project(new_project("local-test", "Local test", "Fixture")), 0)
        source = self.root / "AGENTS.md"
        source.write_bytes(b"Global fixture\r\n")
        replica = self.root / "replica"
        replica.mkdir()
        (replica / "AGENTS.md").write_bytes(b"Project fixture\n")
        store = InstructionStore(ledger)
        version = store.backup(source)
        project_file, data = selected_source(replica)
        pin = store.approve(version, store.compare(version)["sha256"], "fixture-a", replica,
                            source_ref(project_file, data), time() + 60)
        scheduled = Runner(ledger).shutdown("request")
        reopened = Ledger.for_project(self.root)
        self.assertEqual(source.read_bytes(), InstructionStore(reopened).preview(version)[1])
        self.assertTrue(InstructionStore(reopened).verify(pin, replica).startswith(source.read_bytes()))
        self.assertEqual("READY", Runner(reopened).shutdown("finalize", scheduled["request_id"])["state"])

    def test_empty_store_never_adopts_legacy(self):
        legacy = self.root / "legacy" / "DevOrchestra"
        legacy.mkdir(parents=True)
        (legacy / "p1.sqlite3").write_bytes(b"unverified legacy fixture")
        ledger = Ledger.for_project(self.root)
        output = StringIO()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(legacy.parent)}), \
                patch("sys.argv", ["devorchestra", "local-status"]), \
                patch.object(Ledger, "for_project", return_value=ledger), redirect_stdout(output):
            main()
        self.assertEqual({"storage": ".ai-local", "runtime_present": False,
                          "instructions_present": False, "legacy_import": "NOT_ATTEMPTED",
                          "status": "NOT_INITIALIZED"},
                         json.loads(output.getvalue()))
        self.assertFalse(ledger.state_boundary.exists())
        self.assertEqual(b"unverified legacy fixture", (legacy / "p1.sqlite3").read_bytes())

    def test_initialization_is_idempotent_and_state_opens_at_relocated_checkout(self):
        original = self.root / "checkout"
        original.mkdir()
        ledger = Ledger.for_project(original)
        self.assertEqual("NOT_INITIALIZED", ledger.storage_status()["status"])
        self.assertEqual([], list(original.iterdir()))
        runner = Runner(ledger)
        runner.command("init")
        run = runner.command("start")["run_id"]
        before = ledger.path.read_bytes()
        runner.command("init")
        self.assertEqual(before, ledger.path.read_bytes())
        moved = Path(copytree(original, self.root / "moved"))
        reopened = Ledger.for_project(moved)
        self.assertEqual("READY", reopened.storage_status()["status"])
        self.assertEqual(run, reopened.read()[0]["run_id"])

    def test_partial_initialization_and_corruption_stay_blocked(self):
        ledger = Ledger.for_project(self.root)
        with patch.object(ledger, "connect", side_effect=OSError("fixture initialization error")):
            with self.assertRaises(OSError):
                Runner(ledger).command("init")
        self.assertEqual("BLOCKED", ledger.storage_status()["status"])
        self.assertEqual("CORRUPT", ledger.storage_status()["reason"])
        before = ledger.path.read_bytes()
        with self.assertRaises(Blocked):
            Runner(ledger).command("init")
        self.assertEqual(before, ledger.path.read_bytes())

    def test_storage_access_denial_is_distinct(self):
        ledger = Ledger.for_project(self.root)
        with patch("devorchestra.ledger.check_path", side_effect=PermissionError):
            self.assertEqual("ACCESS_DENIED", ledger.storage_status()["reason"])

    def test_reparse_metadata_and_file_collision_are_rejected(self):
        project = self.root / "project"
        project.mkdir()
        lstat = Path.lstat

        def reparse(path):
            if path == project / ".ai-local":
                return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return lstat(path)

        with patch.object(Path, "lstat", reparse), self.assertRaisesRegex(Blocked, "reparse"):
            Ledger.for_project(project)
        (self.root / ".ai-local").write_bytes(b"conflict")
        with self.assertRaisesRegex(Blocked, "conflicts"):
            Ledger.for_project(self.root)

    def test_replica_cannot_overlap_private_state(self):
        project = new_project("sample", "Sample", "Goal")
        project["local_path"] = str(self.root)
        boundary = self.root / ".ai-local"
        setup = {"project": project, "replica": str(self.root.with_name("synthetic-replica")), "wait_seconds": 10}
        validate_setup(setup, boundary)
        with self.assertRaises(ValueError):
            validate_setup({**setup, "replica": str(boundary / "temp")}, boundary)

    def test_git_ignores_local_data_and_gate_rejects_forced_staging(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)

        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

        git("init", "--quiet")
        (root / ".gitignore").write_bytes((Path(__file__).resolve().parents[1] / ".gitignore").read_bytes())
        private = root / ".ai-local"
        private.mkdir()
        (private / "synthetic.txt").write_text("private fixture")
        git("check-ignore", ".ai-local/synthetic.txt")
        self.assertTrue(check_index(root))
        git("add", "-f", "--", ".ai-local/synthetic.txt")
        self.assertFalse(check_index(root))
