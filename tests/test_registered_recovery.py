import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devorchestra import codex_setup, native_cycle, registered_cycle, registered_recovery as recovery
from devorchestra.ai_usage import QUOTA_STOP_PERCENT, SUBSCRIPTION_MODE
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


class RebootRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ledger = Ledger(Path(temp.name) / "ledger.sqlite3")
        self.ledger.initialize()
        self.project = "synthetic-project"
        self.old = "a" * 32
        self.now = int(time())
        self.expires = self.now + 60
        self.boot = dict(started=self.now - 60, start_event=self.now - 60, type_event=self.now - 60,
                         start_record=12, type_record=13, boot_type=0)
        self.git = dict(ref="refs/heads/pilot", url="https://github.com/synthetic/fixture", head="b" * 40)
        self.image = {"files": {}}
        self.scope = dict(project_id=self.project, replica=temp.name, replica_sha256=digest(self.image),
                          development_mode="DIRECT_GIT_WORKTREE", direct_git=self.git,
                          original_apply=False, shell_roles=["developer", "reviewer"],
                          cycles=1, token_mode=SUBSCRIPTION_MODE, quota_stop_percent=QUOTA_STOP_PERCENT)
        self.identity = dict(project_id=self.project, run_id=self.old, request_id=self.old,
                             probe_kind=registered_cycle.KIND, approval_sha256=digest(self.scope))
        self.start = dict(self.identity, kind="native_inspection_intent", scope=self.scope,
                          workspace="native-command-fixture", started_at=self.now - 120)
        self.role = dict(self.identity, kind="native_inspection_role_intent",
                         phase="manager-planning", role_call=1, cycle=1)
        self.append(self.start)
        self.append(self.role)

    def append(self, event, admission=None):
        events, tail = self.ledger.snapshot()
        return self.ledger.append(len(events), tail, event, admission=admission)

    def reviewed(self, *, events=None, boot=None, expires=None, project=None):
        current, head = self.ledger.snapshot()
        return recovery.reboot_scope(current if events is None else events, head, project or self.project,
                                     self.old, self.expires if expires is None else expires,
                                     self.boot if boot is None else boot, self.now)

    def fresh(self):
        boundary = self.reviewed()
        scope = recovery.bind_reboot(dict(self.scope, ledger_head=boundary["ledger_head"]), boundary)
        new = digest(boundary)[:32]
        intent = dict(self.identity, request_id=new, run_id=new, approval_sha256=digest(scope),
                      kind="native_inspection_intent", started_at=self.now, scope=scope)
        return scope, intent

    def test_usage_only_can_read_after_verified_boot_without_admitting_a_run(self):
        before = self.ledger.snapshot()
        runner = Runner(self.ledger)
        with patch.object(recovery, "windows_boot_boundary", return_value=self.boot):
            self.assertEqual(before, codex_setup._quota_context(runner, False))
        with patch.object(recovery, "windows_boot_boundary", return_value=dict(self.boot, boot_type=1)):
            with self.assertRaises(Blocked):
                codex_setup._quota_context(runner, False)
        self.assertEqual(before, self.ledger.snapshot())
        with self.assertRaises(Blocked):
            require_admission(self.ledger.read())

    def test_old_request_stays_pending_until_atomic_fresh_intent_finishes(self):
        scope, intent = self.fresh()
        original = self.ledger.read()
        reopened = Ledger(self.ledger.path)
        with self.assertRaises(Blocked):
            require_admission(reopened.read())
        self.assertEqual(recovery.reboot_predecessor(original, intent), self.old)
        self.append(intent, recovery.reboot_admission(scope))
        self.assertEqual(reopened.read()[:2], original)
        self.assertTrue(native_unresolved(reopened.read()))
        self.assertEqual(intent["scope"]["reboot_boundary"]["model_response"], "UNKNOWN")
        self.assertEqual(intent["scope"]["reboot_boundary"]["provider_usage"], "UNKNOWN")
        with self.assertRaises(Blocked):
            self.append(intent, recovery.reboot_admission(scope))
        result = {key: intent[key] for key in recovery.IDENTITY}
        self.append(dict(result, kind="native_inspection_result"))
        require_admission(reopened.read())
        with self.assertRaises(Blocked):
            self.reviewed()

    def test_boot_evidence_and_role_must_match(self):
        for override in ({"started": self.start["started_at"] - 1}, {"boot_type": 1},
                         {"start_record": self.boot["type_record"]}, {"type_event": self.now}):
            with self.subTest(override=override), self.assertRaises(Blocked):
                self.reviewed(boot=dict(self.boot, **override))
        with self.assertRaises(Blocked):
            self.reviewed(project="other")
        with self.assertRaises(Blocked):
            self.reviewed(expires=self.now)
        with self.assertRaises(Blocked):
            self.reviewed(events=[self.start, dict(self.role, phase="developer")])
        with patch.object(recovery, "windows_boot_boundary", side_effect=Blocked("Old run may have restarted")):
            with self.assertRaisesRegex(Blocked, "Old run may"):
                registered_cycle._reboot_review(Runner(self.ledger), False, self.project, (self.old, self.expires))
        with self.assertRaisesRegex(Blocked, "Abandoned slot"):
            registered_cycle._reboot_review(Runner(self.ledger), True, self.project, (self.old, self.expires))

    def test_binding_changed_boot_and_approval_fail_closed(self):
        scope, intent = self.fresh()
        boundary = scope["reboot_boundary"]
        for override in ({"ref": "refs/heads/other"}, {"head": "e" * 40}):
            with self.assertRaises(Blocked):
                recovery.bind_reboot(dict(scope, direct_git=dict(self.git, **override)), boundary)
        with self.assertRaises(Blocked):
            recovery.bind_reboot(dict(scope, replica_sha256="f" * 64), boundary)
        altered = dict(scope, reboot_boundary=dict(boundary, boot=dict(self.boot, start_record=14)))
        self.assertIsNone(recovery.reboot_predecessor(self.ledger.read(), dict(intent, scope=altered)))
        with self.assertRaisesRegex(Blocked, "Invalid post-reboot"):
            self.append(dict(intent, approval_sha256=self.identity["approval_sha256"]))
        self.append(dict(kind="test_unrelated_update"))
        with self.assertRaises(Blocked):
            self.append(intent, recovery.reboot_admission(scope))

    def test_late_result_and_mixed_publication_are_rejected(self):
        scope, intent = self.fresh()
        self.append(intent, recovery.reboot_admission(scope))
        with self.assertRaisesRegex(Blocked, "superseded"):
            self.append(dict(self.identity, kind="native_inspection_role_result"))
        fresh = {key: intent[key] for key in recovery.IDENTITY}
        pinned = dict(fresh, kind="direct_git_intent", branch=self.git["ref"],
                      before=self.git["head"], origin=self.git["url"])
        for changed in (dict(run_id="f" * 32), dict(request_id=self.old), dict(branch="refs/heads/other"),
                        dict(before="d" * 40), dict(project_id="other")):
            with self.subTest(changed=changed), self.assertRaises(Blocked):
                self.append(dict(pinned, **changed))
        self.append(pinned)

    def test_registered_review_and_run_recheck_boot_before_intent(self):
        rc = registered_cycle
        options = {"direct_branch": "pilot", "reboot": (self.old, self.expires)}
        original, head = self.ledger.snapshot()
        scope = dict(self.scope, ledger_head=head, auth_home=self.scope["replica"])

        @contextmanager
        def staging(_ledger):
            yield Path(self.scope["replica"]), {}

        runner = Runner(self.ledger)
        observer = SimpleNamespace(scratch=Path(self.scope["replica"]), check=lambda: None)
        changed_boot = dict(self.boot, start_record=50)
        with (patch.object(rc, "_contract", return_value=scope),
              patch.object(recovery, "windows_boot_boundary", side_effect=[self.boot, self.boot, changed_boot]),
              patch.object(rc, "_staging", staging), patch.object(rc, "RegisteredSession", return_value=observer),
              patch.object(rc, "_check_dedicated_config"),
              patch("devorchestra.direct_git.admit", return_value=self.git),
              patch.object(rc, "snapshot", return_value=self.image)):
            reviewed = rc.review(runner, self.project, **options)
            with self.assertRaisesRegex(Blocked, "Boot evidence or preserved checkout changed"):
                rc.run(runner, self.project, approval=digest(reviewed), **options)
        self.assertEqual(self.ledger.snapshot(), (original, head))

    def test_fixed_criterion_mismatch_settles_manager_without_opening_a_new_job(self):
        rc = registered_cycle
        runner = Runner(self.ledger)
        before, head = self.ledger.snapshot()
        scope = dict(self.scope, ledger_head=head, auth_home=self.scope["replica"], goal="Fixture goal",
                     requirements=[], files=["feature.txt"], verifier={"mode": rc.BASELINE_VERIFIER},
                     criteria={key: {"text": key, "checks": []} for key in ("C1", "C2")})
        response = json.dumps({"criterion": "C2", "plan": "Fixture plan for the wrong criterion"})
        roles = []

        @contextmanager
        def staging(_ledger):
            yield Path(self.scope["replica"]), {}

        def contract(*_args, approved_criterion, **_options):
            return dict(scope, approved_criterion=approved_criterion)

        def session(_runner, approved, lab, count, fence, fields, cancelled):
            current = SimpleNamespace(scope=approved, image=self.image, runner=runner, check=lambda: None,
                                      source=lambda: digest(self.image), check_image=lambda: None, scratch=lab,
                                      prepare=lambda: None, owned_verified=True, usage_complete=True, phases=[],
                                      changes=[], calls=0, usage=0, cycles_checked=0,
                                      progress=dict.fromkeys(scope["criteria"], "NOT_STARTED"),
                                      count=count, fence=fence, started=rc.monotonic())

            def record(kind, **facts):
                current.count, current.fence = self.ledger.append(current.count, current.fence,
                                                                 {**fields, "kind": kind, **facts})

            def call(cycle, phase, key, prompt, **_options):
                roles.append(phase)
                self.assertEqual("manager-planning", phase)
                facts = dict(cycle=cycle, phase=phase, owned_tree="DRAINED", outcome="COMPLETED",
                             provider_usage={"input_tokens": 3, "output_tokens": 2}, message_sha256=digest(response))
                record("native_inspection_role_result", **facts)
                current.phases.append(facts)
                current.calls, current.usage = 1, 5
                return response

            current.record, current.call = record, call
            return current

        options = {"direct_branch": "pilot", "reboot": (self.old, self.expires), "approved_criterion": "C1"}
        with (patch.object(rc, "_contract", side_effect=contract),
              patch.object(recovery, "windows_boot_boundary", return_value=self.boot),
              patch.object(rc, "_staging", staging), patch.object(rc, "RegisteredSession", side_effect=session),
              patch.object(rc, "_check_dedicated_config"), patch.object(rc, "Catalog"),
              patch.object(rc, "read_frozen_requirements", return_value=[]),
              patch("devorchestra.direct_git.admit", return_value=self.git),
              patch.object(rc, "snapshot", return_value=self.image)):
            reviewed = rc.review(runner, self.project, **options)
            with self.assertRaisesRegex(Blocked, "approval changed"):
                rc.run(runner, self.project, approval=digest(reviewed), **dict(options, approved_criterion="C2"))
            receipt = rc.run(runner, self.project, approval=digest(reviewed), **options)
        self.assertEqual(["manager-planning"], roles)
        self.assertEqual(rc.CRITERION_APPROVAL_REQUIRED, receipt["outcome"])
        self.assertEqual(("DRAINED", True, 5), (receipt["owned_tree"], receipt["usage_complete"],
                                                receipt["known_reported_tokens"]))
        self.assertEqual(digest(response), receipt["roles"][0]["message_sha256"])
        self.assertEqual("RETAINED_FOR_RECOVERY", receipt["staging_cleanup"]["status"])
        self.assertEqual(before, self.ledger.read()[:len(before)])
        self.assertNotIn("native_inspection_plan_accepted", {row["kind"] for row in self.ledger.read()})
        require_admission(self.ledger.read())

    @unittest.skipUnless(os.name == "nt", "Requires the Windows execution slot")
    def test_second_controller_cannot_review_during_a_held_slot(self):
        script = (
            "from devorchestra.lease import SlotBlocked, execution_slot\n"
            "try:\n"
            " with execution_slot(): pass\n"
            "except SlotBlocked as error:\n"
            " raise SystemExit(0 if error.code == 'EXECUTION_SLOT_BUSY' else 2)\n"
            "raise SystemExit(1)\n"
        )
        with execution_slot() as abandoned:
            self.assertFalse(abandoned)
            result = subprocess.run([sys.executable, "-B", "-c", script], timeout=10)
        self.assertEqual(result.returncode, 0)
