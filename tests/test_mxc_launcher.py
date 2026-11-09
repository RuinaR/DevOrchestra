from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import Mock, patch

from devorchestra.lease import Blocked, execution_slot
from devorchestra.ledger import Ledger, canonical
from devorchestra.mxc_diagnostic import MAX_STREAM_BYTES
from devorchestra.mxc_launcher import _OwnerApproval, _run_approved_self_test
from devorchestra.runtime import Runner


class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "runtime.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.approval = _OwnerApproval("a" * 32, int(time()) + 60)
        self.prepared = ("cli.exe", "native.exe", directory.name, {"SystemRoot": "fixture"})
        prepare = patch("devorchestra.mxc_launcher._prepare", return_value=self.prepared)
        self.prepare = prepare.start()
        self.addCleanup(prepare.stop)
        launch = patch("devorchestra.mxc_launcher.subprocess.Popen")
        self.launch = launch.start()
        self.addCleanup(launch.stop)

    def capture(self, exit_code=1, stdout=b"", stderr=b""):
        process = Mock(stdout=BytesIO(stdout), stderr=BytesIO(stderr))
        process.wait.return_value = exit_code
        self.launch.return_value = process
        return process

    def run_probe(self):
        return _run_approved_self_test(self.ledger, self.approval)

    def assert_no_repeat(self):
        count = self.launch.call_count
        with self.assertRaises(Blocked):
            _run_approved_self_test(Ledger(self.ledger.path), _OwnerApproval("b" * 32, self.approval.expires_epoch))
        self.assertEqual(count, self.launch.call_count)

    def test_launch_failure_is_sanitized(self):
        self.launch.side_effect = OSError("private bootstrap string")
        receipt = self.run_probe()
        self.assertEqual("CLI_START_FAILED", receipt["error_type"])
        self.assertNotIn("private", canonical(self.ledger.read()))
        self.assert_no_repeat()

    def test_preflight_and_intent_failure_never_spawn(self):
        with patch.object(self.ledger, "append", side_effect=OSError("private journal path")):
            with self.assertRaisesRegex(Blocked, "^Diagnostic storage or evidence verification failed$"):
                self.run_probe()
        self.launch.assert_not_called()
        self.prepare.side_effect = OSError("private binary path")
        self.assertEqual("PREFLIGHT_FAILED", self.run_probe()["error_type"])
        self.assertNotIn("private", canonical(self.ledger.read()))
        self.assert_no_repeat()

    def test_reports_are_classified_and_raw_streams_never_reach_ledger(self):
        self.capture(stderr=b"Error: MXC requires nonempty Unicode host LOCALAPPDATA\nprivate=synthetic\n")
        receipt = self.run_probe()
        self.assertEqual("HOST_LOCALAPPDATA_REQUIRED", receipt["error_type"])
        self.assertEqual("REPORTED_FAILURE", receipt["stages"]["MXC_SETUP"])
        self.assertNotIn("private", canonical(self.ledger.read()))
        self.assertEqual(["cli.exe", "-c", "windows.sandbox=mxc", "sandbox", "--include-managed-config",
                          "--permission-profile", "mxc-file-test", "--", "native.exe", "--self-test"],
                         self.launch.call_args.args[0])
        self.assert_no_repeat()

    def test_internal_create_failure_has_reported_evidence_only(self):
        self.capture(stderr=b"CreateProcessInSandbox failed: error 203 (0xCB)\n")
        receipt = self.run_probe()
        self.assertEqual("REPORTED_FAILURE", receipt["stages"]["COMMAND_START"])
        self.assertEqual(203, receipt["reported_error_code"])
        self.assertEqual("UNKNOWN", receipt["stages"]["IMAGE_PREPARATION"])
        self.assert_no_repeat()

    def test_unknown_stderr_and_empty_native_exit_do_not_imply_command_started(self):
        self.capture(stderr=b"unrecognized: C:\\secret\\fixture")
        receipt = self.run_probe()
        self.assertEqual((1, "UNRECOGNIZED_STDERR"), (receipt["cli_exit_code"], receipt["error_type"]))
        self.assertEqual("UNKNOWN", receipt["stages"]["COMMAND_START"])
        self.assert_no_repeat()

    def test_empty_native_exit(self):
        self.capture(exit_code=0xC0000022)
        receipt = self.run_probe()
        self.assertEqual((0xC0000022, "NO_STDERR"), (receipt["cli_exit_code"], receipt["error_type"]))
        self.assertEqual("UNKNOWN", receipt["stages"]["COMMAND_START"])
        self.assert_no_repeat()

    def test_output_overflow_never_turns_truncated_marker_into_success(self):
        self.capture(exit_code=0, stdout=b"x" * (MAX_STREAM_BYTES + 1), stderr=b"y" * (MAX_STREAM_BYTES + 1))
        receipt = self.run_probe()
        self.assertTrue(receipt["capture_overflow"])
        self.assertFalse(receipt["capture_complete"])
        self.assertEqual("NOT_VERIFIED", receipt["result"])
        self.assert_no_repeat()

    def test_timeout_only_targets_owned_cli_and_missing_exit_blocks_runtime(self):
        process = self.capture()
        process.wait.side_effect = subprocess.TimeoutExpired("private command", 1)
        result = self.run_probe()
        process.kill.assert_called_once_with()
        self.assertTrue(result["timed_out"])
        self.assertEqual("UNKNOWN", result["stages"]["CLI_EXIT"])
        self.assertNotIn("private", canonical(self.ledger.read()))
        self.assert_no_repeat()
        with self.assertRaisesRegex(Blocked, "ownership"):
            self.runner.command("start")
        key = self.runner.shutdown("request")["request_id"]
        self.assertEqual("RECOVERY_REQUIRED", self.runner.shutdown("finalize", key)["state"])

    def test_shutdown_race_and_slot_contention_prevent_process_creation(self):
        with execution_slot():
            with self.assertRaisesRegex(Blocked, "slot busy"):
                self.run_probe()
        self.prepare.side_effect = lambda _lab: (self.runner.shutdown("request"), self.prepared)[1]
        with self.assertRaises(Blocked):
            self.run_probe()
        self.launch.assert_not_called()
        with self.assertRaisesRegex(Blocked, "Shutdown"):
            self.run_probe()

    def test_result_write_failure_leaves_intent_and_never_reexecutes(self):
        self.capture(exit_code=0, stdout=b"MXC_NATIVE_OK\n")
        append = self.ledger.append

        def fail_result(count, tail, event, **options):
            if event["kind"] == "mxc_diagnostic_result":
                raise OSError("injected result write failure")
            return append(count, tail, event, **options)

        with patch.object(self.ledger, "append", side_effect=fail_result), self.assertRaises(Blocked):
            self.run_probe()
        self.assertEqual("mxc_diagnostic_intent", self.ledger.read()[-1]["kind"])
        self.assert_no_repeat()

    def test_success_receipt_confirms_only_cli_not_tree(self):
        self.capture(exit_code=0, stdout=b"MXC_NATIVE_OK\n")
        receipt = self.run_probe()
        self.assertEqual("PASS", receipt["result"])
        self.assertEqual("CONFIRMED", receipt["stages"]["CLI_EXIT"])
        self.assertEqual("UNKNOWN", self.ledger.read()[-1]["owned_tree"])
        self.assert_no_repeat()
