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
    MAX_FRAME, MAX_NOTICES_PER_REQUEST, AppServer, NativeInspectionApproval, _owned_stdio, instruction_sources,
)
from devorchestra.lease import Blocked, execution_slot
from devorchestra.ledger import Ledger
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
"""


class AppServerTests(unittest.TestCase):
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

    def test_server_approval_request_is_never_answered(self):
        output = BytesIO()
        request = b'{"id":9,"method":"item/commandExecution/requestApproval","params":{}}\n'
        connection = AppServer(BytesIO(request), output, 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "SERVER_REQUEST_BLOCKED"):
            connection.initialize()
        self.assertEqual(1, len(output.getvalue().splitlines()))

    def test_allowed_warning_before_response_is_bounded_and_redacted(self):
        messages = (b'{"method":"warning","params":{"message":"private fixture"}}\n'
                    b'{"id":1,"result":{"config":{}}}\n')
        connection = AppServer(BytesIO(messages), BytesIO(), 1, lambda: False)
        self.assertEqual({"config": {}}, connection.request(1, "config/read", {}))
        receipt = connection.diagnostic_receipt()
        self.assertEqual(["NOTIFICATION", "MATCHED_RESPONSE"],
                         [item["classification"] for item in receipt["protocol"]])
        self.assertNotIn("private fixture", str(receipt))

    def test_config_warning_is_consumed_but_requires_policy_review(self):
        messages = (b'{"id":0,"result":{"platformOs":"windows"}}\n'
                    b'{"method":"configWarning","params":{"summary":"private fixture"}}\n'
                    b'{"id":1,"result":{"config":{"sandbox_mode":"read-only","approval_policy":"never"}}}\n'
                    b'{"id":2,"result":{"requirements":null}}\n')
        connection = AppServer(BytesIO(messages), BytesIO(), 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "CONFIG_WARNING_REQUIRES_REVIEW"):
            connection.inspect_policy()
        self.assertNotIn("private fixture", str(connection.diagnostic_receipt()))

    def test_message_failures_have_distinct_codes(self):
        cases = [
            (b'{"method":"private-user-value","params":{}}\n', "UNSUPPORTED_NOTIFICATION"),
            (b'{"id":1,"error":{"code":-32601,"message":"private detail"}}\n', "RPC_ERROR"),
            (b'{"id":1,"result":{},"error":{}}\n', "INVALID_ENVELOPE"),
            (b'{"id":1,"id":1,"result":{}}\n', "INVALID_JSON"),
            (b"", "EOF"),
            (b"x" * (MAX_FRAME + 1), "FRAME_TOO_LARGE"),
            (b'{"method":"warning","params":{"message":"fixture"}}\n'
             * (MAX_NOTICES_PER_REQUEST + 1), "NOTIFICATION_LIMIT"),
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
                with self.assertRaises(Blocked):
                    Runner(Ledger(ledger.path)).inspect_native(approval)
                launch.assert_called_once()

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
