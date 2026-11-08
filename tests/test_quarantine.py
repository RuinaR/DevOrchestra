from hashlib import sha256
from io import BytesIO
import stat
import unittest
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from devorchestra.lease import Blocked
from devorchestra.quarantine import FileSpec, MAX_BUNDLE_BYTES, inspect_bundle, package_inputs


def manifest(path, data):
    return (FileSpec(path, len(data), sha256(data).hexdigest()),)


def archive(name, data, mode=0, compression=0, dos_attributes=0):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as output:
        info = ZipInfo(name)
        info.external_attr = (mode << 16) | dos_attributes
        output.writestr(info, data, compress_type=compression)
    return buffer.getvalue()


class QuarantineTests(unittest.TestCase):
    def test_round_trip_returns_review_bytes_only(self):
        content = b"synthetic evidence\n"
        approved = manifest("work/result.txt", content)
        bundle = package_inputs({"work/result.txt": content}, approved)
        self.assertEqual((("work/result.txt", content),), inspect_bundle(bundle, approved))
        with self.assertRaises(Blocked):
            package_inputs({"work/result.txt": content, "extra.txt": b""}, approved)

    def test_rejects_windows_aliases_and_escape(self):
        for path in ("../result.txt", "/result.txt", "c:/result.txt", "work\\result.txt",
                     "work/result.txt:secret", "work//result.txt", "con.txt", "RESULT.txt",
                     "work./result.txt"):
            with self.subTest(path=path), self.assertRaises(Blocked):
                inspect_bundle(archive(path, b"x"), manifest(path, b"x"))

    def test_rejects_reparse_symlink_compression_and_inventory(self):
        name = "result.txt"
        approved = manifest(name, b"x")
        for bundle in (archive(name, b"x", stat.S_IFLNK),
                       archive(name, b"x", dos_attributes=0x400),
                       archive(name, b"x", compression=ZIP_DEFLATED),
                       archive("different.txt", b"x"),
                       archive(name, b"xx"), archive(name, b"y"),
                       b"broken zip", b"x" * (MAX_BUNDLE_BYTES + 1)):
            with self.subTest(length=len(bundle)), self.assertRaises(Blocked):
                inspect_bundle(bundle, approved)

    def test_correct_hash_does_not_allow_binary_format(self):
        data = b"\x00\xff"
        approved = manifest("result.txt", data)
        with self.assertRaises(Blocked):
            inspect_bundle(archive("result.txt", data), approved)