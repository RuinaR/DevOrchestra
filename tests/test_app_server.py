from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch

from devorchestra.app_server import AppServer, NativeInspectionApproval, _owned_stdio, instruction_sources
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
        with self.assertRaisesRegex(Blocked, "Unexpected"):
            connection.initialize()

    def test_server_approval_request_is_never_answered(self):
        output = BytesIO()
        request = b'{"id":9,"method":"item/commandExecution/requestApproval","params":{}}\n'
        connection = AppServer(BytesIO(request), output, 1, lambda: False)
        with self.assertRaisesRegex(Blocked, "Unexpected"):
            connection.initialize()
        self.assertEqual(1, len(output.getvalue().splitlines()))

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
