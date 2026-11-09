from base64 import b64decode
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch

from devorchestra.app_server import NativeInspectionApproval, _owned_stdio
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.native_access_probe import (
    ALLOWED, PROTECTED, NativeAccessApproval, build_command, probe_receipt,
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

    def test_missing_results_and_denial_are_distinct(self):
        receipt = {"exitCode": 0, "stdout": OUTPUT, "stderr": ""}
        self.assertEqual("PASS", probe_receipt(receipt, self.work, self.command)["status"])
        self.assertEqual("NOT_VERIFIED", probe_receipt(
            {**receipt, "stdout": OUTPUT.replace("PROTECTED_READ=DENIED_5", "PROTECTED_READ=ERROR")},
            self.work, self.command)["status"])
        self.assertEqual("FAIL", probe_receipt(
            {**receipt, "stdout": OUTPUT.replace("PROTECTED_READ=DENIED_5", "PROTECTED_READ=OK")},
            self.work, self.command)["status"])
        (self.work / "allowed.txt").write_bytes(b"mutated")
        self.assertEqual("FAIL", probe_receipt(receipt, self.work, self.command)["status"])

    def test_model_free_probe_commits_after_owned_drain_and_cannot_replay(self):
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
                patch("devorchestra.app_server.open_app_server", side_effect=peer) as launched:
            result = self.runner.inspect_native(self.approval)
            self.assertEqual("DRAINED", result["owned_tree"])
            self.assertEqual("PASS", result["access"]["status"])
            self.assertEqual("NOT_VERIFIED", result["access"]["desktop_noninterference"])
            self.assertNotIn(OUTPUT, str(self.ledger.read()))
            with self.assertRaisesRegex(Blocked, "DUPLICATE_REQUEST"):
                self.runner.inspect_native(self.approval)
            launched.assert_called_once()
