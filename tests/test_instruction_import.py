from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.instruction_import import ImportSelection, _publish_verified, prepare_import
from devorchestra.instructions import InstructionStore
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger


class InstructionImportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = InstructionStore(Ledger(root / "legacy" / "runtime.sqlite3"))
        self.destination = InstructionStore(Ledger(root / "new" / "runtime.sqlite3"))
        self.destination.ledger.path.parent.mkdir(parents=True)
        original = root / "AGENTS.md"
        original.write_bytes(b"Owner fixture\r\n")
        backup = self.source.backup(original)
        candidate = self.source.draft_reviewed_candidate(backup)
        self.selection = ImportSelection(
            sha256(self.source.ledger.path.read_bytes()).hexdigest(), backup,
            sha256(original.read_bytes()).hexdigest(), candidate,
            self.source.compare(candidate)["sha256"],
        )

    def publish(self, acl=lambda _path: None):
        return _publish_verified(self.source, self.destination, self.selection, verify_destination_acl=acl)

    def test_preserves_records_and_idempotently_refuses_duplicate_import(self):
        self.assertEqual("IMPORTED", self.publish())
        self.assertEqual("ALREADY_IMPORTED", self.publish())
        self.assertEqual(([self.selection.backup_version, self.selection.candidate_version], None),
                         self.destination.versions())
        self.assertEqual(self.selection.database_sha256,
                         sha256(self.source.ledger.path.read_bytes()).hexdigest())

    def test_wrong_hash_and_acl_never_create_destination(self):
        with self.assertRaisesRegex(Blocked, "content hash"):
            prepare_import(self.source, replace(self.selection, candidate_sha256="wrong"))

        def unsafe(_path):
            raise Blocked("ACL fixture denied")

        with self.assertRaisesRegex(Blocked, "ACL"):
            self.publish(unsafe)
        self.assertFalse(self.destination.ledger.path.exists())

    def test_interrupted_staging_leaves_no_published_database(self):
        with patch.object(Ledger, "append", side_effect=OSError("fixture interruption")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertFalse(self.destination.ledger.path.exists())
        self.assertEqual([], list(self.destination.ledger.path.parent.iterdir()))

    def test_destination_collision_is_preserved(self):
        path = self.destination.ledger.path
        self.destination.ledger.initialize()
        before = path.read_bytes()
        with self.assertRaisesRegex(Blocked, "different data"):
            self.publish()
        self.assertEqual(before, path.read_bytes())

    def test_destination_sidecar_blocks_publication(self):
        path = self.destination.ledger.path
        sidecar = path.with_name(path.name + "-wal")
        sidecar.write_bytes(b"stale fixture")
        with self.assertRaisesRegex(Blocked, "sidecar"):
            self.publish()
        self.assertFalse(path.exists())
        self.assertEqual(b"stale fixture", sidecar.read_bytes())

    def test_destination_created_at_publish_is_not_overwritten(self):
        rename = Path.rename

        def concurrent_destination(staged, target):
            target.write_bytes(b"concurrent fixture")
            return rename(staged, target)

        with patch.object(Path, "rename", concurrent_destination), self.assertRaises(OSError):
            self.publish()
        self.assertEqual(b"concurrent fixture", self.destination.ledger.path.read_bytes())
