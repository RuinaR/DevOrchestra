from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devorchestra import project_tools as tools
from devorchestra.ledger import digest
from devorchestra.lease import Blocked


class ToolArchiveTests(unittest.TestCase):
    def test_exact_ledger_bound_archive_reuse_and_integrity(self):
        item = {"repository": "example/tool", "ref": "v1", "asset": "tool.zip"}
        scope = {"install": True, "portable_source_policy": tools.GITHUB_POLICY, "permissions": ["portable-tools"]}
        approval = digest(scope)
        data = b"published archive"
        proof = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
        rows = [
            {"kind": "native_inspection_intent", "run_id": "run", "scope": scope, "approval_sha256": approval},
            {"kind": "native_inspection_project_tool_ready", "run_id": "run", "approval_sha256": approval,
             "integrity": "PUBLISHED_SHA256", **item, **proof},
        ]
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = SimpleNamespace(snapshot=lambda: (rows, "head"), local_directory=lambda *a, **k: root)
            archive = root / "original.zip"
            archive.write_bytes(data)
            tools._publish_archive(ledger, archive, proof, lambda: None)
            with patch.object(tools, "github_package") as network:
                for name in ("cycle-one.zip", "cycle-two.zip"):
                    dest = root / name
                    result = tools.prepare_github(ledger, item, dest, lambda: None, network_failure="offline")
                    self.assertEqual("VERIFIED_CACHE", result["archive_source"])
                    self.assertEqual(data, dest.read_bytes())
                with self.assertRaises(tools.PackageRuntimeUnavailable):
                    tools.prepare_github(ledger, {**item, "ref": "v2"}, root / "other.zip", lambda: None,
                                         network_failure="offline")
                network.return_value = {"sha256": proof["sha256"]}
                result = tools.prepare_github(ledger, {**item, "repository": "example/mirror"}, root / "mirror.zip",
                                              lambda: None)
                self.assertEqual("VERIFIED_CACHE", result["archive_source"])
                network.assert_called_once()
                network.reset_mock()
                tools._cache_path(ledger, proof["sha256"]).write_bytes(b"altered")
                with self.assertRaisesRegex(Blocked, "SHA-256"):
                    tools.prepare_github(ledger, item, root / "altered.zip", lambda: None)
                network.assert_not_called()
