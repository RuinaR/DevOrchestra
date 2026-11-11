"""Windows tests use only bundled fixed children and a disposable ledger."""
from contextlib import ExitStack
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, call, patch

from devorchestra.job_probe import KILL_EXIT, Probe, ProcessInfo, _cleanup
from devorchestra.lease import Blocked, execution_slot
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner, require_admission


def _crash_fixture(root):
    """Create two live Job members and publish only their verified process handles."""
    from devorchestra.job_probe import (
        ExtendedLimits, JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        _api, _cleanup, _create_owned, _require,
    )
    from devorchestra.mxc_owned_gui import _job_pids

    api = _api()
    api.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
    api.OpenProcess.restype = w.HANDLE
    api.IsProcessInJob.argtypes = (w.HANDLE, w.HANDLE, c.POINTER(w.BOOL))
    api.IsProcessInJob.restype = w.BOOL
    with execution_slot():
        ledger = Ledger(root / "trial.sqlite3")
        ledger.initialize()
        events, tail = ledger.snapshot()
        ledger.append(len(events), tail, {
            "kind": "native_egress_trial_intent", "request_id": "0" * 32, "state": "DIAGNOSTIC",
        }, admission=require_admission)
        job = _require(api.CreateJobObjectW(None, None))
        info = ProcessInfo()
        try:
            limits = ExtendedLimits()
            limits.basic.flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            limits.basic.active_limit = 2
            _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
            script = """\
import subprocess, sys, time
command = [sys.executable, '-I', '-S', '-B', '-c', 'import time; time.sleep(20)']
try:
    detached = subprocess.Popen(command, close_fds=True, creationflags=(
        subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB))
except OSError as error:
    if error.winerror != 5:
        raise
else:
    detached.terminate()
    detached.wait(timeout=3)
    raise RuntimeError('Job unexpectedly allowed CREATE_BREAKAWAY_FROM_JOB')
child = subprocess.Popen(command, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
time.sleep(20)
"""
            _create_owned(api, job, sys.executable, ["-I", "-S", "-B", "-c", script],
                          root, {"SystemRoot": os.environ["SystemRoot"]}, info)
            if api.ResumeThread(info.thread) != 1:
                raise Blocked("Crash fixture did not resume")
            deadline = monotonic() + 8
            members = _job_pids(api, job)
            while len(members) < 2 and monotonic() < deadline:
                sleep(0.02)
                members = _job_pids(api, job)
            if len(members) != 2 or info.pid not in members:
                raise Blocked("Crash fixture did not observe both Job members")
            child_pid = (members - {info.pid}).pop()
            child = _require(api.OpenProcess(0x100000 | 0x1000, False, child_pid))
            try:
                for handle in (info.process, child):
                    in_job = w.BOOL()
                    _require(api.IsProcessInJob(handle, job, c.byref(in_job)))
                    if not in_job.value or api.WaitForSingleObject(handle, 0) != 258:
                        raise Blocked("Crash fixture member identity or liveness unverified")
                pending = root / "handles.tmp"
                pending.write_text(json.dumps([info.process, child]), encoding="ascii")
                pending.rename(root / "handles.json")
                sleep(12)  # Bounded fallback if the observer never terminates this owner.
            finally:
                _require(api.CloseHandle(child))
        finally:
            _cleanup(api, job, info, False)


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
class JobOwnerCrashTest(unittest.TestCase):
    def test_terminating_owner_signals_both_handles_and_keeps_recovery_gate(self):
        import _winapi

        with TemporaryDirectory(dir=Path.cwd()) as workspace, ExitStack() as handles:
            root = Path(workspace)
            entry = (
                "from pathlib import Path; import runpy, sys; "
                "runpy.run_path(sys.argv[1])['_crash_fixture'](Path(sys.argv[2]))"
            )
            with subprocess.Popen(
                [sys.executable, "-B", "-c", entry, str(Path(__file__).resolve()), str(root)],
                creationflags=subprocess.CREATE_NO_WINDOW,
            ) as owner:
                try:
                    receipt = root / "handles.json"
                    deadline = monotonic() + 10
                    while not receipt.exists() and owner.poll() is None and monotonic() < deadline:
                        sleep(0.02)
                    self.assertTrue(receipt.is_file(), "Job owner did not attest two live members")
                    # Borrow the original CreateProcess handle from Windows CPython Popen.
                    source = owner._handle
                    current = _winapi.GetCurrentProcess()
                    observed = []
                    for remote in json.loads(receipt.read_text(encoding="ascii")):
                        handle = _winapi.DuplicateHandle(source, remote, current, 0x100000, False, 0)
                        handles.callback(_winapi.CloseHandle, handle)
                        observed.append(handle)
                    self.assertEqual(len(observed), 2)
                    self.assertEqual([258, 258], [_winapi.WaitForSingleObject(h, 0) for h in observed])
                    owner.kill()
                    self.assertEqual(owner.wait(timeout=3), 1)
                    self.assertEqual([0, 0], [_winapi.WaitForSingleObject(h, 3000) for h in observed])
                    with execution_slot():
                        with self.assertRaisesRegex(Blocked, "Native process ownership requires recovery"):
                            require_admission(Ledger(root / "trial.sqlite3").read())
                finally:
                    if owner.poll() is None:
                        owner.kill()
                        owner.wait(timeout=3)


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
