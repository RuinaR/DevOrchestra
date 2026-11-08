from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.catalog import Catalog
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner
from devorchestra.token_reserve import reserve_status


class ReserveRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "runtime.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.catalog = Catalog(self.ledger)

    def plan(self):
        run = self.runner.command("start")["run_id"]
        for action in ("step", "approve", "step"):
            self.runner.command(action, expected_run=run)
        return run

    def step(self, run):
        return self.runner.command("step", expected_run=run)

    def test_completed_role_reserves_shutdown_before_next_role(self):
        self.catalog.save_token_reserve({"tokens": 0, "percent": 90}, 0)
        run = self.plan()
        result = self.step(run)
        self.assertEqual(("Manager", "TOKEN_RESERVE_LOW"), (result["role"], result["shutdown_reason"]))
        self.assertEqual(1, result["token_reservation"]["active_reservation"])
        view = ProjectionReader(self.ledger).read("fixture-a")
        self.assertEqual((1, 9, 0), tuple(view["token_budget"][key] for key in ("used", "reserve", "available")))
        self.assertEqual("UNKNOWN", view["token_budget"]["account_quota"])
        with self.assertRaisesRegex(Blocked, "Shutdown"):
            self.step(run)
        key = view["shutdown"]["request_id"]
        reopened = Runner(Ledger(self.ledger.path))
        ready = reopened.shutdown("finalize", key)
        self.assertEqual("Manager", ready["checkpoint"]["last_completed_role"])
        with self.assertRaisesRegex(Blocked, "reserve"):
            reopened.shutdown("resume", key)

    def test_tighter_budget_before_role_and_unknown_usage(self):
        run = self.plan()
        self.catalog.save_limits({"seconds": 600, "tokens": 0, "cost_microunits": 0}, 0)
        with patch("devorchestra.runtime.mock_role") as role:
            self.assertEqual("TOKEN_LIMIT", self.step(run)["reason"])
            role.assert_not_called()
        self.assertIsNone(reserve_status(10, 0, 1, {"tokens": 1, "percent": 0},
                                         active=1, verified=False)["available"])

    def test_ack_loss_after_commit_does_not_replay_role_or_bypass_stop(self):
        self.catalog.save_token_reserve({"tokens": 9, "percent": 0}, 0)
        run = self.plan()
        append = self.ledger.append

        def lose_response(count, head, event, **options):
            result = append(count, head, event, **options)
            if event["kind"] == "role_finished":
                raise OSError("injected lost response after commit")
            return result

        with patch.object(self.ledger, "append", side_effect=lose_response), self.assertRaises(OSError):
            self.step(run)
        reopened = Runner(Ledger(self.ledger.path))
        key = reopened.shutdown("request")["request_id"]
        self.assertEqual("TOKEN_RESERVE_LOW", reopened.shutdown("finalize", key)["reason"])
        self.assertEqual(1, sum(event["kind"] == "role_started" for event in self.ledger.read()))
        with self.assertRaises(Blocked):
            reopened.shutdown("resume", key)

    def test_verified_wal_and_invalid_wal_backed_chain_are_distinct(self):
        with closing(self.ledger.connect("rw")) as holder:
            self.assertEqual("wal", holder.execute("PRAGMA journal_mode=WAL").fetchone()[0])
            holder.execute("PRAGMA wal_autocheckpoint=0")
            holder.execute("BEGIN")
            holder.execute("SELECT * FROM anchor").fetchall()
            key = self.runner.shutdown("request")["request_id"]
            self.assertTrue(self.ledger.path.with_name(self.ledger.path.name + "-wal").exists())
            self.assertEqual("READY", Runner(Ledger(self.ledger.path)).shutdown("finalize", key)["state"])
            holder.rollback()
            with holder:
                holder.execute("UPDATE events SET hash = 'invalid' WHERE seq = 1")
            with self.assertRaisesRegex(Blocked, "Audit"):
                Runner(Ledger(self.ledger.path)).shutdown("resume", key)
