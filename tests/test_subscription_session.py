from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.lease import Blocked, development_session_scope, execution_slot, held_execution_slot
from devorchestra.ledger import Ledger, digest
from devorchestra.runtime import Runner, require_admission
from devorchestra import subscription_session


class SubscriptionSessionTests(unittest.TestCase):
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
