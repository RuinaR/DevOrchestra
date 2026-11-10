import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra import original_apply
from devorchestra.runtime import native_unresolved


@unittest.skipUnless(os.name == "nt", "Windows no-overwrite semantics")
class OriginalApplyTests(unittest.TestCase):
    def test_second_publish_failure_restores_both_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            before = {"src/item.cs": b"old", "tests/item.cs": b"other"}
            after = dict.fromkeys(before, b"new")
            for name, data in before.items():
                target = root / name
                target.parent.mkdir()
                target.write_bytes(data)
            rename = os.rename

            def fail_second(source, target):
                if source.name == "next-1":
                    raise OSError("Injected publish failure")
                return rename(source, target)

            with patch.object(original_apply.os, "rename", side_effect=fail_second):
                self.assertEqual(("ROLLED_BACK", "Injected publish failure"),
                                 original_apply._write(root, stage, before, after, lambda: None,
                                                       lambda: None, lambda: None))
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})

    def test_collision_blocks_without_overwriting_new_original_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            target = root / "new.py"
            target.write_bytes(b"owner")
            self.assertEqual(("BLOCKED", "Original target changed"),
                             original_apply._write(root, stage, {"new.py": None}, {"new.py": b"model"},
                                                   lambda: None, lambda: None, lambda: None))
            self.assertEqual(b"owner", target.read_bytes())
        intent = {"kind": "original_apply_intent", "run_id": "fixture"}
        self.assertTrue(native_unresolved([intent]))
        self.assertFalse(native_unresolved([intent, {**intent, "kind": "original_apply_result"}]))
