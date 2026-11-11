from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch

from devorchestra.app_server import (
    INSPECTION_CONFIG, MAX_FRAME, MAX_NOTICES_PER_REQUEST, AppServer, NativeInspectionApproval, _owned_stdio,
    STANDALONE_WINDOWS_APP_SERVER, instruction_sources, open_app_server,
)
from devorchestra.lease import Blocked, execution_slot
from devorchestra.ledger import Ledger
from devorchestra.native_access_probe import ALLOWED, PROTECTED, NativeAccessApproval
from devorchestra.runtime import Runner


PEER = """
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        assert msg['method'] == 'initialized'
        continue
    result = {
        'initialize': {'platformOs': 'windows'},
        'config/read': {'config': {'sandbox_mode': 'read-only', 'approval_policy': 'never'}},
        'configRequirements/read': {'requirements': None},
    }[msg['method']]
    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)
    if msg['method'] == 'initialize':
        print(json.dumps({'method': 'remoteControl/status/changed', 'params': {
            'status': 'disabled', 'installationId': 'synthetic-installation',
            'serverName': 'synthetic-host', 'environmentId': None,
        }, 'emittedAtMs': 1794172800000}), flush=True)
"""

REMOTE_DISABLED = (b'{"method":"remoteControl/status/changed","params":{"status":"disabled",'
                   b'"installationId":"private installation","serverName":"private name",'
                   b'"environmentId":null},"emittedAtMs":1794172800000}\n')


class AppServerTests(unittest.TestCase):
    def test_standalone_candidate_uses_stdio_without_cli_subcommand(self):
        cwd = Path.cwd()
        binary = cwd / STANDALONE_WINDOWS_APP_SERVER
        pin = "a" * 64
        seen = []

        @contextmanager
        def transport(executable, argv, *_args):
            seen.append((executable, argv))
            yield None

        with patch("devorchestra.app_server.verify_app_server_binary") as verify, \
                patch("devorchestra.app_server._owned_stdio", side_effect=transport):
            with open_app_server(binary, cwd, {}, 15, lambda: False,
                                 explicit_mxc=True, approved_sha256=pin):
                pass
        verify.assert_called_once_with(binary, approved_sha256=pin)
        self.assertEqual([(binary, ["-c", "windows.sandbox=mxc", "--listen", "stdio://"])], seen)

    def test_owned_stdio_handshake_and_drain_with_local_peer(self):
        with execution_slot() as abandoned:
            self.assertFalse(abandoned)
            with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER],
                              Path.cwd(), {"SystemRoot": os.environ["SystemRoot"]},
                              5, lambda: False) as connection:
                result = connection.inspect_policy()
        self.assertEqual({"sandbox": "read-only", "approval": "never",
                          "managed_requirements_present": False}, result)
        self.assertFalse(connection.reader.is_alive())

    def test_wrong_response_id_is_rejected(self):
        connection = AppServer(BytesIO(b'{"id":1,"result":{}}\n'), BytesIO(), 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "UNEXPECTED_RESPONSE_ID"):
            connection.initialize()

    def test_outbound_scope_denies_rpc_before_write_and_cannot_widen(self):
        from devorchestra.native_manager import MANAGER_TURN_METHODS

        output = BytesIO()
        connection = AppServer(BytesIO(b'{"id":0,"result":{"platformOs":"windows"}}\n'),
                               output, 1, lambda: False)
        connection.restrict_outbound(MANAGER_TURN_METHODS)
        with self.assertRaisesRegex(Blocked, "outbound RPC denied before write"):
            connection.request(10, "command/exec", {})
        with self.assertRaisesRegex(Blocked, "outbound RPC denied before write"):
            connection.send({"id": 11, "method": "fs/writeFile", "params": {}})
        with self.assertRaisesRegex(Blocked, "outbound scope cannot be changed"):
            connection.restrict_outbound(MANAGER_TURN_METHODS | {"command/exec"})
        self.assertEqual(b"", output.getvalue())
        self.assertFalse(connection.request_ids)
        connection.initialize()
        self.assertEqual(["initialize", "initialized"],
                         [json.loads(line)["method"] for line in output.getvalue().splitlines()])

    def test_server_approval_request_is_never_answered(self):
        output = BytesIO()
        request = (b'{"id":"server-9","method":"item/commandExecution/requestApproval","params":{},'
                   b'"trace":{"traceparent":"private fixture","tracestate":null}}\n')
        connection = AppServer(BytesIO(request), output, 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "SERVER_REQUEST_BLOCKED"):
            connection.initialize()
        self.assertEqual(1, len(output.getvalue().splitlines()))
        self.assertNotIn("private fixture", str(connection.diagnostic_receipt()))

    def test_disabled_remote_status_before_response_is_bounded_and_redacted(self):
        messages = REMOTE_DISABLED + b'{"id":1,"result":{"config":{}}}\n'
        connection = AppServer(BytesIO(messages), BytesIO(), 1, lambda: False)
        self.assertEqual({"config": {}}, connection.request(1, "config/read", {}))
        receipt = connection.diagnostic_receipt()
        self.assertEqual(["NOTIFICATION", "MATCHED_RESPONSE"],
                         [item["classification"] for item in receipt["protocol"]])
        self.assertNotIn("private", str(receipt))
        self.assertNotIn("1794172800000", str(receipt))

    def test_config_warning_stops_policy_inspection(self):
        messages = (b'{"id":0,"result":{"platformOs":"windows"}}\n'
                    b'{"method":"configWarning","params":{"summary":"private fixture","details":null,'
                    b'"path":"private path","range":{"start":{"line":1,"column":2},'
                    b'"end":{"line":1,"column":3}}}}\n'
                    b'{"id":1,"result":{"config":{"sandbox_mode":"read-only","approval_policy":"never"}}}\n'
                    b'{"id":2,"result":{"requirements":null}}\n')
        connection = AppServer(BytesIO(messages), BytesIO(), 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "CONFIG_WARNING_REQUIRES_REVIEW"):
            connection.inspect_policy()
        self.assertNotIn("private fixture", str(connection.diagnostic_receipt()))
        self.assertNotIn("private path", str(connection.diagnostic_receipt()))

    def test_official_notifications_are_classified_without_granting_permission(self):
        cases = [
            (REMOTE_DISABLED.replace(b'"disabled"', b'"connected"'), "STATUS",
             "REMOTE_CONTROL_REQUIRES_REVIEW"),
            (REMOTE_DISABLED.replace(b'"private name"', b'false'), "STATUS", "INVALID_NOTIFICATION_METADATA"),
            (b'{"method":"warning","params":{"message":"private"}}\n', "WARNING", "WARNING_REQUIRES_REVIEW"),
            (b'{"method":"windows/worldWritableWarning","params":{"private-key":"private"}}\n',
             "SECURITY", "SECURITY_NOTIFICATION_REQUIRES_REVIEW"),
            (b'{"method":"account/updated","params":{"private-key":"private"}}\n',
             "ACCOUNT", "ACCOUNT_NOTIFICATION_BLOCKED"),
            (b'{"method":"thread/started","params":{}}\n', "ACTIVITY", "UNSUPPORTED_NOTIFICATION"),
            (b'{"method":"private-method","params":{"private-key":"private"}}\n',
             "UNCLASSIFIED", "UNSUPPORTED_NOTIFICATION"),
        ]
        for frame, category, code in cases:
            with self.subTest(category=category, code=code):
                output = BytesIO()
                connection = AppServer(BytesIO(frame), output, 1, lambda: False)
                with self.assertRaisesRegex(Blocked, code):
                    connection.request(1, "config/read", {})
                notice, failure = connection.diagnostic_receipt()["protocol"]
                self.assertEqual(category, notice["shape"]["category"])
                self.assertEqual("dict", notice["shape"]["params_type"])
                self.assertEqual(code, failure["classification"])
                self.assertNotIn("private", str((notice, failure)))
                self.assertEqual(1, len(output.getvalue().splitlines()))

    def test_queued_security_warning_blocks_command_before_send(self):
        output = BytesIO()
        frame = b'{"method":"windows/worldWritableWarning","params":{}}\n'
        connection = AppServer(BytesIO(frame), output, 1, lambda: False)
        connection.reader.join(1)
        with self.assertRaisesRegex(Blocked, "SECURITY_NOTIFICATION_REQUIRES_REVIEW"):
            connection.request(3, "command/exec", {})
        self.assertEqual(b"", output.getvalue())

    def test_message_failures_have_distinct_codes(self):
        cases = [
            (b'{"method":"private-user-value","params":{},"emittedAtMs":1794172800000}\n',
             "UNSUPPORTED_NOTIFICATION"),
            (b'{"id":1,"error":{"code":-32601,"message":"private detail"}}\n', "RPC_ERROR"),
            (b'[]\n', "ENVELOPE_TOP_LEVEL_TYPE"),
            (b'{"jsonrpc":"private","id":1,"result":{}}\n', "ENVELOPE_JSONRPC_FIELD"),
            (b'{"method":false}\n', "ENVELOPE_METHOD_FIELD"),
            (b'{"method":"warning","params":"private"}\n', "ENVELOPE_PARAMS_TYPE"),
            (b'{"method":"warning","params":{},"trace":{}}\n', "ENVELOPE_METADATA_FIELD"),
            (b'{"method":"warning","params":{},"emittedAtMs":true}\n', "ENVELOPE_METADATA_FIELD"),
            (b'{"method":"warning","params":{},"emittedAtMs":9223372036854775808}\n',
             "ENVELOPE_METADATA_FIELD"),
            (b'{"id":1,"result":{},"emittedAtMs":1794172800000}\n', "ENVELOPE_METADATA_FIELD"),
            (b'{"id":1,"method":"warning","emittedAtMs":1794172800000}\n',
             "ENVELOPE_METADATA_FIELD"),
            (b'{"id":1,"result":{},"private-key":"private-value"}\n', "ENVELOPE_METADATA_FIELD"),
            (b'{"id":1,"method":"warning","trace":{"private-key":"private-value"}}\n',
             "ENVELOPE_METADATA_FIELD"),
            (b'{"result":{}}\n', "ENVELOPE_ID_FIELD"),
            (b'{"id":true,"result":{}}\n', "ENVELOPE_ID_FIELD"),
            (b'{"id":1,"result":{},"error":{}}\n', "ENVELOPE_RESULT_ERROR_CONFLICT"),
            (b'{"id":1}\n', "ENVELOPE_RESPONSE_FIELDS"),
            (b'{"id":1,"result":[]}\n', "INVALID_RESULT"),
            (b'{"method":"configWarning","params":{"summary":"private","path":[]}}\n',
             "INVALID_NOTIFICATION_METADATA"),
            (b'{"id":1,"id":1,"result":{}}\n', "INVALID_JSON"),
            (b"", "EOF"),
            (b"x" * (MAX_FRAME + 1), "FRAME_TOO_LARGE"),
            (REMOTE_DISABLED * (MAX_NOTICES_PER_REQUEST + 1), "NOTIFICATION_LIMIT"),
        ]
        for frames, expected in cases:
            with self.subTest(expected=expected):
                connection = AppServer(BytesIO(frames), BytesIO(), 1, lambda: False)
                with self.assertRaisesRegex(Blocked, expected):
                    connection.request(1, "config/read", {})
                record = connection.diagnostic_receipt()["protocol"][-1]
                self.assertEqual(expected, record["classification"])
                self.assertNotIn("private", str(connection.diagnostic_receipt()))
                if expected == "RPC_ERROR":
                    self.assertEqual("METHOD_NOT_FOUND", record["error"])

    def test_nested_mxc_error_keeps_only_the_known_diagnostic(self):
        frame = (b'{"id":1,"error":{"code":-32603,"message":"exec failed: unsupported operation: '
                 b'native MXC is unavailable on this executor","data":"private fixture"}}\n')
        connection = AppServer(BytesIO(frame), BytesIO(), 1, lambda: False)
        connection.request_id, connection.request_method = 1, "command/exec"
        with self.assertRaisesRegex(Blocked, "RPC_ERROR"):
            connection.receive()
        receipt = connection.diagnostic_receipt()["protocol"][-1]
        self.assertEqual("INTERNAL_ERROR", receipt["error"])
        self.assertEqual({"detail": "NATIVE_MXC_UNAVAILABLE_ON_EXECUTOR"}, receipt["shape"])
        self.assertNotIn("private fixture", str(receipt))

    def test_envelope_receipt_records_types_and_counts_without_raw_fields(self):
        frame = b'{"id":1,"result":{},"private-key":"private-value"}\n'
        connection = AppServer(BytesIO(frame), BytesIO(), 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "ENVELOPE_METADATA_FIELD"):
            connection.request(1, "config/read", {})
        record = connection.diagnostic_receipt()["protocol"][-1]
        self.assertEqual({"field_types": {"id": "int", "result": "dict"},
                          "unexpected_field_count": 1}, record["shape"])
        self.assertTrue(record["id_matches"])
        self.assertEqual(len(frame), record["frame_bytes"])
        self.assertNotIn("private", str(record))

    def test_duplicate_response_is_rejected_during_next_request_and_after_close(self):
        frames = b'{"id":1,"result":{}}\n{"id":1,"result":{}}\n'
        for next_request in (False, True):
            connection = AppServer(BytesIO(frames), BytesIO(), 1, lambda: False)
            connection.request(1, "config/read", {})
            connection.reader.join(1)
            with self.assertRaisesRegex(Blocked, "DUPLICATE_RESPONSE"):
                if next_request:
                    connection.request(2, "configRequirements/read", {})
                else:
                    connection.check_trailing_messages()

    def test_timeout_with_owned_peer_still_reaps_job_and_pipes(self):
        with execution_slot():
            with self.assertRaisesRegex(Blocked, "TIMEOUT"):
                with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", "import time; time.sleep(3)"],
                                  Path.cwd(), {"SystemRoot": os.environ["SystemRoot"]},
                                  0.3, lambda: False) as connection:
                    connection.request(1, "config/read", {})
        self.assertEqual({"job": "TERMINATED_AND_DRAINED", "pipes": "CLOSED"},
                         connection.diagnostic_receipt()["cleanup"])

    def test_instruction_override_and_missing_scope(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            home = Path(directory) / "home"
            project = Path(directory) / "project"
            home.mkdir()
            project.mkdir()
            (home / "AGENTS.override.md").write_text("synthetic global override")
            (home / "AGENTS.md").write_text("synthetic global default")
            with self.assertRaisesRegex(Blocked, "Missing project"):
                instruction_sources(home, project)
            (project / "AGENTS.md").write_text("synthetic project rules")
            self.assertEqual("AGENTS.override.md", instruction_sources(home, project)["global"]["file"])

    def test_controller_commits_intent_before_launch_and_blocks_replay(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            ledger = Ledger(root / "runtime.sqlite3")
            runner = Runner(ledger)
            runner.command("init")
            approval = NativeInspectionApproval("a" * 32, time() + 60, root / "codex.exe", root)

            @contextmanager
            def transport(*args):
                self.assertEqual("native_inspection_intent", ledger.read()[-1]["kind"])
                with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER],
                                  root, {"SystemRoot": os.environ["SystemRoot"]}, 5, lambda: False) as server:
                    yield server

            with patch("devorchestra.app_server.prepare_inspection", return_value=(root, {}, {})), \
                    patch("devorchestra.app_server.open_app_server", side_effect=transport) as launch:
                receipt = runner.inspect_native(approval)
                self.assertEqual("DRAINED", receipt["owned_tree"])
                self.assertEqual("NOT_VERIFIED", receipt["instruction_loading"])
                with self.assertRaisesRegex(Blocked, "DUPLICATE_REQUEST"):
                    Runner(Ledger(ledger.path)).inspect_native(approval)
                launch.assert_called_once()

    def test_controller_distinguishes_preflight_blocks_before_launch(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            audit = root / "audit"
            ledger = Ledger(audit / "probe.sqlite3")
            runner = Runner(ledger)
            runner.command("init")
            lab = root / ("app-server-" + "d" * 32)
            approval = NativeInspectionApproval("d" * 32, time() + 60, root / "codex.exe", lab)
            expired = NativeInspectionApproval("d" * 32, time() - 1, approval.binary, lab)

            def expect(value, stage, code):
                previous = set(audit.glob("app-server-preflight-*.json"))
                with self.assertRaisesRegex(Blocked, code):
                    runner.inspect_native(value)
                (receipt,) = set(audit.glob("app-server-preflight-*.json")) - previous
                data = json.loads(receipt.read_bytes())
                self.assertEqual({"request_id": approval.request_id if value else None,
                                  "status": "BLOCKED", "stage": stage, "failure_code": code}, data)

            with patch("devorchestra.app_server.open_app_server") as launch:
                expect(None, "APPROVAL", "APPROVAL_INVALID")
                expect(expired, "APPROVAL", "APPROVAL_EXPIRED")
                with execution_slot():
                    expect(approval, "EXECUTION_SLOT", "EXECUTION_SLOT_BUSY")
                with patch.object(ledger, "snapshot", side_effect=Blocked("private path")):
                    expect(approval, "LEDGER", "LEDGER_INTEGRITY_FAILED")
                expect(approval, "SYNTHETIC_LAB", "SYNTHETIC_LAB_PARENT_MISMATCH")
                self.assertEqual([], ledger.read())
                ledger.append(0, ledger.snapshot()[1], {
                    "kind": "native_inspection_intent", "request_id": "e" * 32,
                })
                expect(approval, "ADMISSION", "PRIOR_NATIVE_UNRESOLVED")
                self.assertEqual(["native_inspection_intent"], [e["kind"] for e in ledger.read()])
                launch.assert_not_called()

    def test_nested_lab_passes_parent_check_and_reports_distinct_file_preflights(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            ledger = Ledger(root / "probe.sqlite3")
            runner = Runner(ledger)
            runner.command("init")
            lab = ledger.local_directory("temp") / ("app-server-" + "a" * 32)
            for name in ("home", "tmp", "work", "protected"):
                (lab / name).mkdir(parents=True)
            (lab / "home" / "config.toml").write_bytes(INSPECTION_CONFIG.encode("ascii"))
            (lab / "home" / "AGENTS.md").write_bytes(b"synthetic")
            (lab / "work" / "allowed.txt").write_bytes(b"wrong")
            (lab / "protected" / "protected.txt").write_bytes(PROTECTED)
            approval = NativeAccessApproval(
                NativeInspectionApproval("a" * 32, time() + 60, root / "codex.exe", lab), "b" * 64,
            )
            with patch("devorchestra.app_server.open_app_server") as launch, \
                    patch("devorchestra.mxc_launcher._known_folder", return_value=root), \
                    patch("devorchestra.system_powershell.hold_system_powershell",
                          side_effect=Blocked("private signature detail")):
                with self.assertRaisesRegex(Blocked, "INSTRUCTION_INVALID"):
                    runner.inspect_native(approval)
                (lab / "work" / "AGENTS.md").write_bytes(b"synthetic")
                with self.assertRaisesRegex(Blocked, "MARKER_INVALID"):
                    runner.inspect_native(approval)
                (lab / "work" / "allowed.txt").write_bytes(ALLOWED)
                with self.assertRaisesRegex(Blocked, "POWERSHELL_IDENTITY_INVALID"):
                    runner.inspect_native(approval)
                launch.assert_not_called()
            self.assertEqual([], ledger.read())

    def test_binary_identity_block_retains_durable_intent_and_writes_preflight_receipt(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            ledger = Ledger(root / "probe.sqlite3")
            runner = Runner(ledger)
            runner.command("init")
            approval = NativeInspectionApproval("f" * 32, time() + 60, root / "codex.exe", root)
            with patch("devorchestra.app_server.prepare_inspection", return_value=(root, {}, {})), \
                    patch("devorchestra.app_server._owned_stdio") as launch:
                with self.assertRaisesRegex(Blocked, "BINARY_IDENTITY_INVALID"):
                    runner.inspect_native(approval)
                launch.assert_not_called()
            self.assertEqual(["native_inspection_intent"], [e["kind"] for e in ledger.read()])
            (receipt,) = root.glob("app-server-preflight-*.json")
            self.assertEqual("CLI_IDENTITY", json.loads(receipt.read_bytes())["stage"])

    def test_result_write_failure_keeps_intent_and_preserves_cleanup_receipt(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            ledger = Ledger(root / "runtime.sqlite3")
            runner = Runner(ledger)
            runner.command("init")
            approval = NativeInspectionApproval("b" * 32, time() + 60, root / "codex.exe", root)
            original = ledger.append

            def append(count, tail, event, **kwargs):
                if event["kind"] == "native_inspection_result":
                    raise OSError("fixture private write error")
                return original(count, tail, event, **kwargs)

            @contextmanager
            def transport(*args):
                with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER],
                                  root, {"SystemRoot": os.environ["SystemRoot"]}, 5, lambda: False) as server:
                    yield server

            with patch.object(ledger, "append", side_effect=append), \
                    patch("devorchestra.app_server.prepare_inspection", return_value=(root, {}, {})), \
                    patch("devorchestra.app_server.open_app_server", side_effect=transport):
                with self.assertRaisesRegex(Blocked, "result commit unverified"):
                    runner.inspect_native(approval)
            self.assertEqual(["native_inspection_intent"], [event["kind"] for event in ledger.read()])
            receipt = json.loads((root / ("app-server-" + approval.request_id + ".json")).read_bytes())
            self.assertEqual("RESULT_COMMIT_UNVERIFIED", receipt["status"])
            self.assertEqual({"job": "DRAINED_NORMALLY", "pipes": "CLOSED"}, receipt["cleanup"])
            self.assertNotIn("fixture private write error", str(receipt))
