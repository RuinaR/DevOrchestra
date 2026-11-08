"""Windows tests use only bundled fixed children and a disposable ledger."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

from devorchestra.job_probe import KILL_EXIT, Probe, ProcessInfo, _cleanup
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner


class JobCleanupFaultTests(unittest.TestCase):
    def test_failed_accounting_still_terminates_and_closes_all_handles(self):
        api = Mock()
        info = ProcessInfo()
        info.process, info.thread = 11, 12
        with patch("devorchestra.job_probe._wait_empty", side_effect=OSError("query failed")):
            with self.assertRaisesRegex(OSError, "query failed"):
                _cleanup(api, 13, info, False)
        api.TerminateJobObject.assert_called_once_with(13, KILL_EXIT)
        self.assertEqual([call(12), call(11), call(13)], api.CloseHandle.call_args_list)

    def test_drain_timeout_closes_job_for_kill_on_close(self):
        api = Mock()
        info = ProcessInfo()
        info.process, info.thread = 11, 12
        with patch("devorchestra.job_probe._wait_empty", return_value=Mock(active=1)):
            with self.assertRaisesRegex(Blocked, "cleanup is unverified"):
                _cleanup(api, 13, info, False)
        self.assertEqual([call(12), call(11), call(13)], api.CloseHandle.call_args_list)

    def test_failed_thread_handle_close_still_attempts_remaining_closes(self):
        api = Mock()
        api.CloseHandle.side_effect = [OSError("close failed"), True, True]
        info = ProcessInfo()
        info.process, info.thread = 11, 12
        with self.assertRaisesRegex(OSError, "close failed"):
            _cleanup(api, 13, info, True)
        self.assertEqual([call(12), call(11), call(13)], api.CloseHandle.call_args_list)
        api.TerminateJobObject.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows Job contract")
class JobProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(Path(tmp.name) / "job.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.run = self.runner.command("start")
        self.run_id = self.run["run_id"]
        self.command("step")
        self.command("approve")
        self.command("step")

    def command(self, action):
        return self.runner.command(action, expected_run=self.run_id)

    def test_native_exit_and_nonzero_exit_receipts(self):
        for fixture, result, code in ((Probe.EXIT, "OK", 0), (Probe.ERROR, "FAIL", 17)):
            event = self.runner._probe_job(self.run_id, fixture)
            self.assertEqual((result, code, 0),
                             (event["result"], event["exit_code"], event["active_processes"]))
            self.assertGreaterEqual(event["total_processes"], 1)
            self.assertEqual((96 * 1024 * 1024, 2), (event["job_memory_limit"], event["job_process_limit"]))
            self.assertEqual(self.run["generation"], event["generation"])
            self.assertEqual("WINDOWS_SYNTHETIC_JOB", event["source"])
        self.assertEqual("DEVELOPING", self.command("step")["state"])

    def test_native_tree_drains_after_root_exit_and_timeout(self):
        event = self.runner._probe_job(self.run_id, Probe.TREE)
        self.assertEqual(("TIME_LIMIT", 0, 0),
                         (event["result"], event["exit_code"], event["active_processes"]))
        self.assertTrue(event["root_exited_before_stop"])
        self.assertGreater(event["active_before_stop"], 0)
        self.assertGreaterEqual(event["total_processes"], 2)
        safety = ProjectionReader(self.ledger).read("fixture-a")["safety"]
        self.assertEqual("BLOCKED", safety["sandbox"])
        self.assertFalse(safety["job_pending"])
        self.assertEqual(0, safety["job"]["active_processes"])
        self.assertEqual("DEVELOPING", self.command("step")["state"])

    def test_native_cancel(self):
        def request_cancel():
            with self.assertRaisesRegex(Blocked, "slot busy"):
                self.command("step")
            return True

        event = self.runner._probe_job(self.run_id, Probe.TREE, request_cancel)
        self.assertEqual(("CANCELLED", 0), (event["result"], event["active_processes"]))

    def test_reopen_with_unresolved_durable_intent_is_idempotently_blocked(self):
        with patch("devorchestra.job_probe._run_probe", side_effect=OSError("simulated crash window")):
            with self.assertRaises(OSError):
                self.runner._probe_job(self.run_id, Probe.EXIT)
        self.runner = Runner(Ledger(self.ledger.path))
        for action in ("start", "step", "run", "cancel"):
            with self.assertRaisesRegex(Blocked, "drain unverified"):
                self.command(action)
        with self.assertRaises(Blocked):
            self.runner.command("recover", expected_run="wrong")
        first = self.command("recover")
        self.assertEqual(("RECOVERING", "UNKNOWN"), (first["state"], first["result"]))
        count = len(self.ledger.read())
        self.assertEqual(first, self.command("recover"))
        self.assertEqual(count, len(self.ledger.read()))
        safety = ProjectionReader(self.ledger).read("fixture-b")["safety"]
        self.assertTrue(safety["job_pending"])
        self.assertEqual("DRAIN UNVERIFIED", safety["lease"])
        with self.assertRaises(Blocked):
            self.runner._probe_job(self.run_id, Probe.EXIT)

    def test_lost_drain_receipt_prevents_next_role(self):
        append = self.ledger.append

        def fail_drain(count, tail, event, **options):
            if event["kind"] == "job_drained":
                raise OSError("simulated receipt write failure")
            return append(count, tail, event, **options)

        def drained(_fixture, _cwd, created, _cancel):
            created(1)
            return {"active_processes": 0}

        with patch.object(self.ledger, "append", side_effect=fail_drain), \
                patch("devorchestra.job_probe._run_probe", side_effect=drained):
            with self.assertRaisesRegex(OSError, "receipt write failure"):
                self.runner._probe_job(self.run_id, Probe.EXIT)
        self.assertEqual("job_created", self.ledger.read()[-1]["kind"])
        self.runner = Runner(Ledger(self.ledger.path))
        with self.assertRaisesRegex(Blocked, "drain unverified"):
            self.command("step")
        self.assertEqual("UNKNOWN", self.command("recover")["result"])
