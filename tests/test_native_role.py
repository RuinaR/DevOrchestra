import sqlite3
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from devorchestra.adapter import NativeReceipt, Outcome, WindowsNativeCodexAdapter
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.runtime import Runner


class NativeRoleTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "native.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.run_id = self.runner.command("start")["run_id"]
        for action in ("step", "approve", "step"):
            self.runner.command(action, expected_run=self.run_id)
        self.transport = Mock()
        adapter = WindowsNativeCodexAdapter(self.transport)
        self.patch = patch("devorchestra.runtime.WindowsNativeCodexAdapter", return_value=adapter)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.enable = patch.object(adapter, "preflight")

    def step(self):
        with self.enable:
            return self.runner.native_step(self.run_id)

    def test_default_admission_does_not_write_or_call_transport(self):
        before = self.ledger.read()
        with self.assertRaisesRegex(Blocked, "ISOLATION_UNVERIFIED"):
            self.runner.native_step(self.run_id)
        self.assertEqual(before, self.ledger.read())
        self.transport.assert_not_called()

    def test_intent_write_failure_does_not_call_transport(self):
        with patch.object(self.ledger, "append", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                self.step()
        self.transport.assert_not_called()

    def test_shutdown_racing_dispatch_prevents_send(self):
        reservation = {}

        def transport(request, before_send, cancelled):
            reservation.update(self.runner.shutdown("request"))
            self.assertTrue(cancelled())
            before_send()
            self.fail("dispatch must remain closed")

        self.transport.side_effect = transport
        with self.assertRaises(Blocked):
            self.step()
        self.assertEqual("NOT_DISPATCHED", self.ledger.read()[-1]["external_request"])
        self.assertEqual("RECOVERY_REQUIRED",
                         self.runner.shutdown("finalize", reservation["request_id"])["state"])

    def test_crash_before_dispatch_leaves_only_role_intent(self):
        self.transport.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.step()
        self.assertEqual("native_role_intent", self.ledger.read()[-1]["kind"])
        with self.assertRaises(Blocked):
            Runner(Ledger(self.ledger.path)).native_step(self.run_id)
        self.transport.assert_called_once()

    def test_lost_response_after_commit_does_not_replay(self):
        append = self.ledger.append

        def lose_response(count, tail, event, **kwargs):
            result = append(count, tail, event, **kwargs)
            if event["kind"] == "native_role_result":
                raise sqlite3.OperationalError
            return result

        def transport(request, before_send, cancelled):
            before_send()
            return NativeReceipt(digest(asdict(request)), Outcome.COMPLETED, True, 0, 0)

        self.transport.side_effect = transport
        with patch.object(self.ledger, "append", side_effect=lose_response):
            with self.assertRaises(sqlite3.OperationalError):
                self.step()
        self.assertEqual("native_role_result", self.ledger.read()[-1]["kind"])
        with self.assertRaises(Blocked):
            Runner(Ledger(self.ledger.path)).native_step(self.run_id)
        self.transport.assert_called_once()

    def test_owned_tree_unknown_prevents_replay_after_restart(self):
        def transport(request, before_send, cancelled):
            before_send()
            return NativeReceipt(digest(asdict(request)), Outcome.COMPLETED, False, 0, 0)

        self.transport.side_effect = transport
        with self.assertRaises(Blocked):
            self.step()
        self.assertEqual("UNKNOWN", self.ledger.read()[-1]["owned_tree"])
        restarted = Runner(Ledger(self.ledger.path))
        with self.assertRaises(Blocked):
            restarted.native_step(self.run_id)
        with self.assertRaises(Blocked):
            restarted.command("step", expected_run=self.run_id)
        self.transport.assert_called_once()

    def test_result_write_failure_keeps_dispatch_pending(self):
        append = self.ledger.append

        def fail_result(count, tail, event, **kwargs):
            if event["kind"] == "native_role_result":
                raise sqlite3.OperationalError
            return append(count, tail, event, **kwargs)

        def transport(request, before_send, cancelled):
            before_send()
            return NativeReceipt(digest(asdict(request)), Outcome.COMPLETED, True, 0, 0)

        self.transport.side_effect = transport
        with patch.object(self.ledger, "append", side_effect=fail_result):
            with self.assertRaises(sqlite3.OperationalError):
                self.step()
        self.assertEqual("native_dispatch_intent", self.ledger.read()[-1]["kind"])
        with self.assertRaises(Blocked):
            Runner(Ledger(self.ledger.path)).native_step(self.run_id)
        self.transport.assert_called_once()

    def test_token_overrun_schedules_stop(self):
        def transport(request, before_send, cancelled):
            before_send()
            return NativeReceipt(digest(asdict(request)), Outcome.COMPLETED, True,
                                 request.budget.tokens + 1, 0)

        self.transport.side_effect = transport
        result = self.step()
        self.assertEqual("TOKEN_LIMIT", result["reason"])
        self.assertEqual("BLOCKED", result["state"])
        with self.assertRaises(Blocked):
            self.step()
        self.transport.assert_called_once()
