from base64 import b64decode
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch

from devorchestra.app_server import NativeInspectionApproval, _owned_stdio
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.native_access_probe import (
    ALLOWED, PROTECTED, PROFILE_ID, NativeAccessApproval, NativeBoundaryApproval, build_command,
    native_boundary_contract, probe_receipt, validate_restricted_command,
)
from devorchestra.runtime import Runner


OUTPUT = "ALLOWED_READ=OK\nALLOWED_WRITE=DENIED_5\nPROTECTED_READ=DENIED_5\n"
PEER = """
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    if method == 'initialized':
        continue
    if method == 'command/exec':
        p = msg['params']
        assert msg['id'] == 3 and p['timeoutMs'] == 5000
        assert p['sandboxPolicy']['access']['readableRoots'] == [p['cwd']]
        assert p['sandboxPolicy']['type'] == 'readOnly'
        assert p['command'][-2] == '-EncodedCommand'
    result = {
        'initialize': {'platformOs': 'windows'},
        'config/read': {'config': {'sandbox_mode': 'read-only', 'approval_policy': 'never'}},
        'configRequirements/read': {'requirements': None},
        'command/exec': {'exitCode': 0, 'stdout': sys.argv[1], 'stderr': ''},
    }[method]
    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)
"""


class NativeAccessProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.ledger = Ledger.for_project(root)
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        lab = self.ledger.local_directory("temp") / ("app-server-" + "a" * 32)
        self.work, self.protected = lab / "work", lab / "protected"
        self.work.mkdir(parents=True)
        self.protected.mkdir()
        (self.work / "allowed.txt").write_bytes(ALLOWED)
        (self.protected / "protected.txt").write_bytes(PROTECTED)
        self.command = build_command(root / "powershell.exe", self.work / "allowed.txt",
                                     self.protected / "protected.txt")
        self.approval = NativeAccessApproval(
            NativeInspectionApproval("a" * 32, time() + 60, root / "codex.exe", lab), "b" * 64,
        )

    def test_command_only_contains_fixed_synthetic_file_operations(self):
        script = b64decode(self.command[-1]).decode("utf-16le")
        self.assertIn(str(self.work / "allowed.txt"), script)
        self.assertIn(str(self.protected / "protected.txt"), script)
        self.assertNotIn("Get-Process", script)
        self.assertNotIn("Invoke-WebRequest", script)

    def test_native_profile_candidate_is_exact_and_cannot_launch(self):
        lab = self.work.parent
        (lab / "home").mkdir()
        (lab / "tmp").mkdir()
        for path in (lab / "home" / "AGENTS.md", self.work / "AGENTS.md"):
            path.write_bytes(b"Synthetic instructions")
        config, request = native_boundary_contract(self.work)
        self.assertEqual({
            "command": [str(self.work / "mxc-probe.exe"), "--app-server-file-test"],
            "cwd": str(self.work), "timeoutMs": 5000, "permissionProfile": PROFILE_ID,
        }, request)
        self.assertIn((json.dumps(str(self.work), ensure_ascii=False) + ' = "read"').encode(), config)
        self.assertIn(b'enabled = false', config)
        for forbidden in (b"extends", b":minimal", b'"write"', b"workspace_roots"):
            self.assertNotIn(forbidden, config)
        config_path = lab / "home" / "config.toml"
        config_path.write_bytes(config)
        fixture = b"synthetic executable identity only"
        (self.work / "mxc-probe.exe").write_bytes(fixture)
        approval = NativeBoundaryApproval(
            self.approval.inspection, sha256(fixture).hexdigest(), sha256(config).hexdigest(), digest(request),
        )
        with patch("devorchestra.app_server.open_app_server") as launch:
            with self.assertRaisesRegex(Blocked, "NATIVE_PROBE_IDENTITY_INVALID"):
                self.runner.inspect_native(approval)
        with patch("devorchestra.native_access_probe.BOUNDARY_PROBE_SHA256", approval.probe_sha256), \
                patch("devorchestra.app_server.open_app_server") as launch:
            for candidate, code in (
                (replace(approval, config_sha256="0" * 64), "NATIVE_BOUNDARY_CONTRACT_MISMATCH"),
                (replace(approval, request_sha256="0" * 64), "NATIVE_BOUNDARY_CONTRACT_MISMATCH"),
                (replace(approval, probe_sha256="0" * 64), "NATIVE_PROBE_IDENTITY_INVALID"),
                (approval, "BINARY_IDENTITY_INVALID"),
            ):
                with self.subTest(code=code), self.assertRaisesRegex(Blocked, code):
                    self.runner.inspect_native(candidate)
            with patch("devorchestra.app_server.verify_app_server_binary") as verify_cli:
                with self.assertRaisesRegex(Blocked, "NATIVE_LOADER_SCOPE_UNVERIFIED"):
                    self.runner.inspect_native(approval)
                verify_cli.assert_called_once_with(approval.inspection.binary)
            config_path.write_bytes(config + b"\n")
            with self.assertRaisesRegex(Blocked, "SYNTHETIC_LAB_INVALID"):
                self.runner.inspect_native(approval)
            launch.assert_not_called()
        self.assertEqual([], self.ledger.read())

    def test_missing_results_and_denial_are_distinct(self):
        receipt = {"exitCode": 0, "stdout": OUTPUT, "stderr": ""}
        result = probe_receipt(receipt, self.work, self.command)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(sha256(ALLOWED).hexdigest(), result["marker_hashes"]["allowed.txt"]["after"])
        self.assertEqual("NOT_VERIFIED", probe_receipt(
            {**receipt, "stdout": OUTPUT.replace("PROTECTED_READ=DENIED_5", "PROTECTED_READ=ERROR")},
            self.work, self.command)["status"])
        self.assertEqual("FAIL", probe_receipt(
            {**receipt, "stdout": OUTPUT.replace("PROTECTED_READ=DENIED_5", "PROTECTED_READ=OK")},
            self.work, self.command)["status"])
        (self.work / "allowed.txt").write_bytes(b"mutated")
        self.assertEqual("FAIL", probe_receipt(receipt, self.work, self.command)["status"])

    def test_legacy_restricted_request_is_blocked_before_launch_and_intent(self):
        with patch("devorchestra.app_server.prepare_inspection", return_value=(self.work, {}, {})), \
                patch("devorchestra.native_access_probe.prepare_probe",
                      return_value=nullcontext((self.command, lambda: None))), \
                patch("devorchestra.app_server.open_app_server") as launched:
            with self.assertRaisesRegex(Blocked, "LEGACY_RESTRICTED_READ_UNSUPPORTED"):
                self.runner.inspect_native(self.approval)
            launched.assert_not_called()
        self.assertEqual([], self.ledger.read())
        (receipt,) = self.ledger.local_directory("logs").glob("app-server-preflight-*.json")
        self.assertEqual({
            "request_id": self.approval.inspection.request_id, "status": "BLOCKED",
            "stage": "COMMAND_POLICY", "failure_code": "LEGACY_RESTRICTED_READ_UNSUPPORTED",
        }, json.loads(receipt.read_bytes()))
        for policy in ({},
                       {"sandboxPolicy": {"type": "readOnly"}},
                       {"sandboxPolicy": {"type": "workspaceWrite"}},
                       {"sandboxPolicy": {"type": "dangerFullAccess"}},
                       {"sandboxPolicy": {"type": "externalSandbox"}},
                       {"permissionProfile": "unverified-profile"}):
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(Blocked, "RESTRICTED_READ_CONTRACT_UNVERIFIED"):
                    validate_restricted_command(policy)

    def test_synthetic_transport_commits_after_owned_drain_and_cannot_replay(self):
        @contextmanager
        def peer(_binary, cwd, _env, timeout, cancelled, *, explicit_mxc):
            self.assertTrue(explicit_mxc)
            self.assertEqual("native_inspection_intent", self.ledger.read()[-1]["kind"])
            with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER, OUTPUT], cwd,
                              {"SystemRoot": os.environ["SystemRoot"]}, timeout, cancelled) as server:
                yield server
                self.assertEqual("native_inspection_dispatch", self.ledger.read()[-1]["kind"])

        with patch("devorchestra.app_server.prepare_inspection", return_value=(self.work, {}, {})), \
                patch("devorchestra.native_access_probe.prepare_probe",
                      return_value=nullcontext((self.command, lambda: None))), \
                patch("devorchestra.native_access_probe.validate_restricted_command"), \
                patch("devorchestra.app_server.open_app_server", side_effect=peer) as launched:
            result = self.runner.inspect_native(self.approval)
            self.assertEqual("DRAINED", result["owned_tree"])
            self.assertEqual("PASS", result["access"]["status"])
            self.assertEqual("NOT_VERIFIED", result["access"]["desktop_noninterference"])
            self.assertNotIn(OUTPUT, str(self.ledger.read()))
            with self.assertRaisesRegex(Blocked, "DUPLICATE_REQUEST"):
                self.runner.inspect_native(self.approval)
            launched.assert_called_once()
