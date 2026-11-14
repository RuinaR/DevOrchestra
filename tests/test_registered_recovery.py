import json
import os
from contextlib import contextmanager
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devorchestra import native_cycle, registered_cycle, registered_recovery as recovery
from devorchestra.lease import Blocked, execution_slot
from devorchestra.ledger import Ledger, digest
from devorchestra.runtime import Runner, native_unresolved, require_admission


class RegisteredRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = Ledger(self.root / "audit.sqlite3")
        self.ledger.initialize()
        self.target = ("test-project", "test-run", "test-request")
        self.expiry = int(time()) + 60
        scope = {"project_id": self.target[0]}
        self.identity = dict(zip(recovery.IDENTITY, (*self.target, registered_cycle.KIND, digest(scope))))
        self.events = [
            {**self.identity, "kind": "native_inspection_intent", "scope": scope},
            {**self.identity, "kind": "native_inspection_role_intent", "cycle": 1, "phase": "manager-planning"},
        ]
        self.role_receipt = {**self.identity, "kind": "native_inspection_role_result", "cycle": 1,
                             "phase": "manager-planning", "owned_tree": "DRAINED",
                             "process_result": "TERMINATED_AND_DRAINED",
                             "process_stop_reason": native_cycle.SUPERVISOR_CALLBACK_FAILED,
                             "provider_usage": "UNKNOWN"}
        self.marker = {**self.identity, "kind": recovery.DRAINED_MARKER, "cycle": 1,
                       "owned_tree": "DRAINED", "staged_evidence_retained": True}

    def append(self, *events):
        for event in events:
            rows, tail = self.ledger.snapshot()
            self.ledger.append(len(rows), tail, event)

    def test_review_and_approved_recovery_only_append_a_safe_abort(self):
        self.append(*self.events, self.role_receipt, self.marker)
        before, tail = self.ledger.snapshot()
        with self.assertRaises(Blocked):
            require_admission(before)
        review = recovery.close(self.ledger, *self.target, self.expiry)
        self.assertEqual(review["scope"]["ledger_head"], tail)
        self.assertEqual(self.ledger.snapshot(), (before, tail))
        result = recovery.close(self.ledger, *self.target, self.expiry, approval=review["approve"])
        self.assertEqual((recovery.RECOVERED, "RECOVERED_ABORT", "UNKNOWN", "UNKNOWN"),
                         (result["kind"], result["outcome"], result["model_response"], result["provider_usage"]))
        after = self.ledger.read()
        self.assertEqual(after[:-1], before)
        require_admission(after)
        self.assertTrue(native_unresolved([*before, {**after[-1], "request_id": "foreign"}]))
        with self.assertRaises(Blocked):
            recovery.close(self.ledger, *self.target, self.expiry, approval=review["approve"])

    def test_restart_without_drain_or_with_stale_or_mismatched_evidence_blocks(self):
        self.append(*self.events)
        with self.assertRaisesRegex(Blocked, "no current Controller drain"):
            recovery.close(self.ledger, *self.target, self.expiry)
        self.append(self.role_receipt, self.marker)
        review = recovery.close(self.ledger, *self.target, self.expiry)
        for target in (("foreign", *self.target[1:]), (*self.target[:2], "foreign")):
            with self.assertRaises(Blocked):
                recovery.close(self.ledger, *target, self.expiry)
        with self.assertRaises(Blocked):
            recovery.close(self.ledger, *self.target, int(time()) - 1)
        self.append({**self.marker, "owned_tree": "UNRESOLVED"})
        with self.assertRaises(Blocked):
            recovery.close(self.ledger, *self.target, self.expiry, approval=review["approve"])
        self.assertTrue(native_unresolved(self.ledger.read()))

    def test_recovery_does_not_clear_another_project(self):
        other = {**self.events[0], "project_id": "other", "run_id": "other", "request_id": "other"}
        self.append(other, *self.events, self.role_receipt, self.marker)
        review = recovery.close(self.ledger, *self.target, self.expiry)
        recovery.close(self.ledger, *self.target, self.expiry, approval=review["approve"])
        self.assertEqual(self.ledger.read()[0], other)
        with self.assertRaisesRegex(Blocked, "Native process ownership"):
            require_admission(self.ledger.read())

    def test_registered_controller_can_recover_after_final_receipt_write_fails(self):
        scope = {"project_id": self.target[0], "auth_home": str(self.root), "replica": str(self.root)}
        approval = digest(scope)
        append = self.ledger.append
        rc = registered_cycle

        @contextmanager
        def staging(_ledger):
            yield self.root, {"preserve": False}

        def session(_runner, _scope, _lab, count, fence, fields, _cancelled):
            current = SimpleNamespace(count=count, fence=fence, fields=fields, owned_verified=True,
                                      phases=[], progress={}, cycles_checked=0, calls=1, usage=0,
                                      usage_complete=False, started=rc.monotonic(), prepare=lambda: None)

            def record(kind, **facts):
                current.count, current.fence = self.ledger.append(current.count, current.fence,
                                                                 {**fields, "kind": kind, **facts})

            current.record = record
            return current

        def cycles(current, **_):
            role = {key: self.role_receipt[key] for key in (
                "cycle", "phase", "owned_tree", "process_result", "process_stop_reason", "provider_usage")}
            current.record("native_inspection_role_intent", cycle=1, phase="manager-planning")
            current.phases.append(role)
            current.record("native_inspection_role_result", **role)
            raise Blocked(native_cycle.SUPERVISOR_CALLBACK_FAILED)

        def fail_final(count, tail, event, **kwargs):
            if event["kind"] == "native_inspection_result":
                raise OSError("injected final receipt failure")
            return append(count, tail, event, **kwargs)

        with (patch.object(rc, "_contract", return_value=scope), patch.object(rc, "_staging", staging),
              patch.object(rc, "RegisteredSession", side_effect=session), patch.object(rc, "_cycles", cycles),
              patch.object(rc, "_check_dedicated_config"), patch.object(self.ledger, "append", side_effect=fail_final)):
            with self.assertRaisesRegex(OSError, "final receipt failure"):
                rc.run(Runner(self.ledger), approval=approval)
        marker = self.ledger.read()[-1]
        self.assertEqual(marker["kind"], recovery.DRAINED_MARKER)
        target = [marker[key] for key in recovery.IDENTITY[:3]]
        review = recovery.close(self.ledger, *target, self.expiry)
        with execution_slot():
            with self.assertRaises(Blocked):
                recovery.close(self.ledger, *target, self.expiry, approval=review["approve"])
        recovery.close(self.ledger, *target, self.expiry, approval=review["approve"])
        require_admission(self.ledger.read())

    @unittest.skipUnless(os.name == "nt", "Requires a Windows Job")
    def test_callback_normal_start_failure_and_settled_failure_in_real_job(self):
        phase = "manager-planning"
        completed = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"},
                     {"type": "item.completed", "item": {"type": "agent_message", "text": "fixture plan"}},
                     {"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 2}}]
        for mode in ("normal", "start", "complete", "cancel", "wrong signature"):
            with self.subTest(mode=mode), execution_slot() as abandoned:
                self.assertFalse(abandoned)
                session = object.__new__(native_cycle._FixtureSession)
                work = self.root / mode
                work.mkdir()
                session.binary = Path(sys.executable)
                session.work = work
                session.env = {"SystemRoot": os.environ["SystemRoot"]}
                session.scope = {"model": "fixture", "model_calls_max": 1, "tokens": 100,
                                 "role_tokens": {phase: 100}, "instructions": {
                                     phase: native_cycle.public_instruction(phase)["sha256"]}}
                session.calls = session.usage = 0
                session.phases = []
                events, emitted = [], []
                cancelled = False

                def check():
                    if cancelled:
                        raise Blocked("CANCELLED")

                def activity(cycle, role, message, kind):
                    nonlocal cancelled
                    self.assertEqual((cycle, role, kind), (1, phase, "activity"))
                    emitted.append(message)
                    if mode in {"start", "cancel"} or (mode == "complete" and message == native_cycle._live_activity("turn.completed", {})):
                        cancelled = mode == "cancel"
                        raise RuntimeError("private observer exception")

                def logs(label):
                    path = work / label
                    path.mkdir()
                    return path

                session.check, session.check_image = check, lambda: None
                session.logs, session.source = logs, lambda: "fixture"
                session.seconds = lambda _: 8
                session.activity = (lambda _cycle, _role: None) if mode == "wrong signature" else activity
                session.record = lambda kind, **data: events.append((kind, data))
                rows = ([{"type": []}, *completed[:2]] if mode in {"start", "cancel", "wrong signature"}
                        else completed)
                stream = "\n".join(map(json.dumps, rows))
                command = f"import time; print({stream!r}, flush=True); time.sleep({0.4 if mode == 'normal' else 5})"
                with patch.object(native_cycle, "_exec_argv", return_value=["-I", "-S", "-B", "-c", command]):
                    if mode == "normal":
                        self.assertEqual(session.dispatch(1, phase, None, "fixture")[0]["message"], "fixture plan")
                        self.assertEqual(len(emitted), len(set(emitted)))
                    else:
                        with self.assertRaisesRegex(Blocked, native_cycle.SUPERVISOR_CALLBACK_FAILED):
                            session.dispatch(1, phase, None, "fixture")
                self.assertTrue(session.owned_verified)
                self.assertEqual(events[-1][0], "native_inspection_role_result")
                final = events[-1][1]
                self.assertEqual(final["owned_tree"], "DRAINED")
                self.assertEqual(session.usage, 13 if mode in {"normal", "complete"} else 0)
                if mode != "normal":
                    self.assertEqual(final["process_result"], "TERMINATED_AND_DRAINED")
                    self.assertIn("retained_stdout_sha256", final)
                if mode in {"start", "cancel", "wrong signature"}:
                    self.assertEqual(final["provider_usage"], "UNKNOWN")
