from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.lease import Blocked, development_session_scope, execution_slot, held_execution_slot
from devorchestra.ledger import Ledger, digest
from devorchestra.runtime import Runner, require_admission
from devorchestra import session_permissions, subscription_session


class SubscriptionSessionTests(unittest.TestCase):
    def test_wall_time_expiry_and_token_limit_gate_new_roles(self):
        scope = {"project_id": "fixture-a", "seconds": 30, "tokens": 500, "replica_sha256": "initial",
                 "expires": 2000}
        with (patch.object(subscription_session, "monotonic", return_value=100) as clock,
              patch.object(subscription_session, "time", return_value=1000) as wall):
            session = subscription_session._Session(None, scope, "fixture", "approval", lambda: False)
            session.phase = "developer"
            clock.return_value = 122
            budget = {"seconds": 60, "tokens": 1000}
            session.limit(budget, "fixture-a", "developer")
            self.assertEqual({"seconds": 8, "tokens": 500}, budget)
            session.remaining = 0
            with self.assertRaisesRegex(Blocked, "TOKEN_LIMIT"):
                session.limit(budget, "fixture-a", "developer")
            wall.return_value = 2000
            self.assertEqual("APPROVAL_EXPIRED", session.stop_reason())
            wall.return_value = 1000
            clock.return_value = 130
            self.assertEqual("TIME_LIMIT", session.stop_reason())

    def test_permissions_require_each_selected_verifier_operation(self):
        grants = session_permissions.contract(None, runtime=True, gui=True)["granted"]
        for excluded in ("owned-window-input", "owned-window-capture", "build-test"):
            with self.subTest(excluded=excluded), self.assertRaisesRegex(Blocked, "추가 승인 필요"):
                session_permissions.contract([item for item in grants if item != excluded], runtime=True, gui=True)
        with self.assertRaisesRegex(Blocked, "집행할 수 없는"):
            session_permissions.contract([*grants, "original-apply"], runtime=True, gui=True)

    def test_revocation_stops_before_dispatch_and_consumes_approval(self):
        with TemporaryDirectory() as folder:
            ledger = Ledger.for_project(Path(folder))
            ledger.initialize()
            _, head = ledger.snapshot()
            scope = {"ledger_head": head, "replica_sha256": "fixture", "seconds": 30, "tokens": 5000,
                     "expires": 9999999999, "models": {"Manager": "fixture"}}
            runner = Runner(ledger)
            with (patch.object(subscription_session, "review", return_value=scope),
                  patch.object(subscription_session.manager, "run") as launch):
                result = subscription_session.run(
                    runner, "fixture-a", "fixture", 0, 30, 5000, 0, {}, digest(scope), revoked=lambda: True,
                )
                launch.assert_not_called()
                self.assertEqual(("PERMISSIONS_REVOKED", "DRAINED"), (result["outcome"], result["owned_tree"]))
                with self.assertRaisesRegex(Blocked, "approval changed"):
                    subscription_session.run(runner, "fixture-a", "fixture", 0, 30, 5000, 0, {}, digest(scope))

    def test_next_runs_two_full_cycles_under_one_approval(self):
        with TemporaryDirectory() as folder:
            ledger = Ledger.for_project(Path(folder))
            ledger.initialize()
            _, head = ledger.snapshot()
            scope = {"ledger_head": head, "replica_sha256": "initial", "seconds": 30, "tokens": 5000,
                     "expires": 9999999999, "work_limit": None,
                     "models": dict.fromkeys(("Manager", "Developer", "Reviewer"), "fixture")}
            calls = []

            def execute(*args, session):
                phase = session.phase
                calls.append((phase, session.approval))
                result = {"run_id": str(len(calls)), "outcome": "COMPLETED", "owned_tree": "DRAINED", "usage": 1,
                          **session.binding()}
                if phase == "developer":
                    result["after_replica_sha256"] = f"source-{len(calls)}"
                if phase == "reviewer":
                    result["decision"] = "ACCEPT_FOR_VERIFICATION"
                if phase == "verifier":
                    result["verdict"] = "PASS"
                if phase == "manager-final":
                    result["decision"] = "CONTINUE" if len(calls) == 5 else "COMPLETED"
                return result

            with (patch.object(subscription_session, "review", return_value=scope),
                  patch.object(subscription_session._Session, "check_scope"),
                  patch.object(subscription_session.manager, "review", return_value={}),
                  patch.object(subscription_session.manager, "run", side_effect=execute),
                  patch.object(subscription_session.developer, "review", return_value={}),
                  patch.object(subscription_session.developer, "run", side_effect=execute),
                  patch.object(subscription_session.cycle, "review", return_value={}),
                  patch.object(subscription_session.cycle, "run", side_effect=execute),
                  patch.object(subscription_session.cycle, "_model_output", return_value={"decision": "NEXT"})):
                approval = digest(scope)
                result = subscription_session.run(Runner(ledger), "fixture-a", "fixture", 0, 30, 5000,
                                                  0, {}, approval)
            expected = ["manager-planning", "developer", "reviewer", "verifier", "manager-final"]
            self.assertEqual([(phase, approval) for phase in expected * 2], calls)
            self.assertEqual(("COMPLETED", 2, 8),
                             (result["outcome"], result["cycle_count"], result["used_reported_tokens"]))

    def test_cancelled_session_records_stop_without_launching_role(self):
        with TemporaryDirectory() as folder:
            ledger = Ledger.for_project(Path(folder))
            ledger.initialize()
            _, head = ledger.snapshot()
            scope = {"ledger_head": head, "replica_sha256": "fixture", "seconds": 30, "tokens": 5000,
                     "expires": 9999999999, "models": {"Manager": "fixture"}}
            runner = Runner(ledger)
            with (
                patch.object(subscription_session, "review", return_value=scope),
                patch.object(subscription_session.manager, "run") as launch,
            ):
                receipt = subscription_session.run(
                    runner, "fixture-a", "fixture", 0, 30, 5000, 2, {}, digest(scope), lambda: True,
                )
            launch.assert_not_called()
            self.assertEqual(("CANCELLED", [], "DRAINED"), (receipt["outcome"], receipt["roles"], receipt["owned_tree"]))
            require_admission(ledger.read())

    def test_interrupted_session_cannot_replay_and_owner_can_close_between_roles(self):
        with TemporaryDirectory() as folder:
            ledger = Ledger.for_project(Path(folder))
            ledger.initialize()
            _, head = ledger.snapshot()
            record = {"kind": "subscription_session_started", "project_id": "fixture-a", "run_id": "fixture",
                      "development_session_id": "fixture", "session_approval_sha256": "fixture", "state": "DIAGNOSTIC"}
            ledger.append(0, head, record)
            with self.assertRaisesRegex(Blocked, "requires owner recovery"):
                require_admission(ledger.read())
            with execution_slot(), development_session_scope("fixture"), held_execution_slot():
                require_admission(ledger.read())
            result = subscription_session.recover(Runner(ledger), "fixture-a", "fixture")
            self.assertEqual("UNKNOWN", result["outcome"])
            require_admission(ledger.read())
            with self.assertRaisesRegex(Blocked, "No interrupted"):
                subscription_session.recover(Runner(ledger), "fixture-a", "fixture")
