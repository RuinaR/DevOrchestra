from hashlib import sha256
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devorchestra.lease import Blocked
from devorchestra.ledger import check_path
from devorchestra.system_powershell import hold_system_powershell


class SystemPowerShellIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "powershell.exe"
        self.path.write_bytes(b"synthetic binary")
        self.expected = sha256(self.path.read_bytes()).hexdigest()
        for name, result in (("_system_path", self.path),
                             ("_catalog_publisher", ("Microsoft Windows", "Microsoft Corporation"))):
            mocked = patch(f"devorchestra.system_powershell.{name}", return_value=result)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_hard_link_is_allowed_only_by_the_specialized_verifier(self):
        os.link(self.path, self.path.with_name("fixture-alias.exe"))
        self.assertEqual(2, self.path.lstat().st_nlink)
        with self.assertRaisesRegex(Blocked, "hard link"):
            check_path(self.path)
        with hold_system_powershell(self.expected) as (path, verify):
            self.assertEqual(self.path, path)
            verify()
            with self.assertRaises(OSError):
                with self.path.with_name("fixture-alias.exe").open("r+b"):
                    pass

    def test_wrong_hash_or_untrusted_publisher_is_blocked(self):
        with self.assertRaisesRegex(Blocked, "hash mismatch"):
            with hold_system_powershell("0" * 64):
                self.fail("bad hash admitted")
        for publisher in (None, ("Microsoft Windows", "Unexpected Publisher")):
            with patch("devorchestra.system_powershell._catalog_publisher", return_value=publisher):
                with self.assertRaisesRegex(Blocked, "signature or publisher"):
                    with hold_system_powershell(self.expected):
                        self.fail("bad publisher admitted")

    def test_path_identity_change_before_dispatch_is_blocked(self):
        original = self.path.lstat()
        replaced = SimpleNamespace(st_mode=original.st_mode, st_ino=original.st_ino + 1,
                                   st_dev=original.st_dev, st_size=original.st_size,
                                   st_mtime_ns=original.st_mtime_ns,
                                   st_file_attributes=original.st_file_attributes)
        lstat = Path.lstat
        with hold_system_powershell(self.expected) as (_path, verify):
            with patch.object(Path, "lstat", lambda path: replaced if path == self.path else lstat(path)):
                with self.assertRaisesRegex(Blocked, "file changed"):
                    verify()

    def test_reparse_leaf_is_blocked_before_signature_check(self):
        lstat = Path.lstat
        reparse = SimpleNamespace(st_mode=stat.S_IFREG, st_ino=1,
                                  st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with patch.object(Path, "lstat", lambda path: reparse if path == self.path else lstat(path)):
            with self.assertRaisesRegex(Blocked, "identity unverified"):
                with hold_system_powershell(self.expected):
                    self.fail("reparse point admitted")
