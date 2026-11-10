from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra import developer_batch
from devorchestra.lease import Blocked
from devorchestra.replica_evidence import snapshot


class DeveloperBatchTests(unittest.TestCase):
    def test_existing_and_explicit_creation_apply_together(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            replica = root / "replica"
            replica.mkdir()
            (replica / "src").mkdir()
            (replica / "tests").mkdir()
            original = {"src/first.py": b"value = 1\n", "tests/second.py": None}
            (replica / "src/first.py").write_bytes(original["src/first.py"])
            developer_batch.check_names(list(original), ["tests/second.py"])
            replacement = developer_batch.replacement(
                b'{"src/first.py":"value = 2","tests/second.py":"value = 3"}', original,
            )
            before = snapshot(replica)
            after, observed = developer_batch.apply(replica, before, original, replacement,
                                                     root / "journal.dpapi", lambda: None)
            self.assertEqual(after, snapshot(replica))
            self.assertEqual(set(original), set(observed))
            self.assertEqual(b"value = 3", (replica / "tests/second.py").read_bytes())
            self.assertTrue((root / "journal.dpapi").is_file())

    def test_second_write_failure_restores_first_file_and_removes_staging(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            replica = root / "replica"
            replica.mkdir()
            original = {"first.py": b"value = 1\n", "second.py": b"value = 2\n"}
            for name, data in original.items():
                (replica / name).write_bytes(data)
            before = snapshot(replica)
            replacement = {name: b"value = 3\n" for name in original}
            replace = developer_batch.os.replace

            def fail_second(source, target):
                if Path(source).name == "next-1":
                    raise OSError("fixture write failure")
                return replace(source, target)

            with patch.object(developer_batch.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(Blocked, "rolled back"):
                    developer_batch.apply(replica, before, original, replacement, root / "journal.dpapi", lambda: None)
            self.assertEqual(before, snapshot(replica))
            self.assertFalse((root / "journal.dpapi").exists())
            self.assertEqual(set(original), {path.name for path in replica.iterdir()})

    def test_rejects_ambiguous_paths_and_invalid_batch_before_apply(self):
        with self.assertRaises(Blocked):
            developer_batch.check_names(["first.py", "FIRST.py"], [])
        with self.assertRaises(Blocked):
            developer_batch.check_names(["first.py", "../second.py"], [])
        with self.assertRaises(Blocked):
            developer_batch.check_names(["first.py", "models/second.py"], [])
        with self.assertRaises(Blocked):
            developer_batch.replacement(b'{"first.py":"value = 1","second.py":"def bad"}', ["first.py", "second.py"])
