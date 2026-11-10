import unittest

from devorchestra.lease import Blocked
from devorchestra.project_onboarding import _protected, _records, _relative


class ProjectOnboardingTests(unittest.TestCase):
    def test_private_paths_are_distinct_from_explicit_examples(self):
        for path in ("private/", "data/account.sqlite3", "src/.env.local", "src/appsettings.json",
                     "engine/models/weights.dat", "keys/signing.pem"):
            with self.subTest(path=path):
                self.assertTrue(_protected(path))
        self.assertFalse(_protected("config/runtime.local.example.json"))

    def test_path_boundary_rejects_aliases(self):
        for path in ("../outside", "src/a:stream", "src/NUL.txt", "src/a/../b", "src/a\\b", "src/a."):
            with self.subTest(path=path), self.assertRaises(Blocked):
                _relative(path)

    def test_unmerged_index_is_not_admitted(self):
        oid = "a" * 40
        self.assertEqual({"src/app.py": ("100644", oid)}, _records(f"100644 {oid} 0\tsrc/app.py\0"))
        with self.assertRaisesRegex(Blocked, "unresolved"):
            _records(f"100644 {oid} 2\tsrc/app.py\0")
