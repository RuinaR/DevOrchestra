from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.event_audit import Binding, FIXTURE_USAGE, Packet
from devorchestra.event_journal import EventJournal
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding("p", "d", "r", 1, "a", "c", "thread", "turn")
        self.events = []
        self.journal = EventJournal(self.binding, self.commit, fixture_usage=True)
        self.start = self.packet(1, "turn/started", turn={"id": "turn", "status": "inProgress"})

    def commit(self, kind, receipt):
        self.events.append((kind, receipt))

    def packet(self, seq, method, **params):
        return Packet(self.binding, seq, str(seq), {"method": method, "params": {
            "threadId": "thread", "turnId": "turn", **params}})

    def test_commit_replay_and_conflict(self):
        self.assertEqual("COMMITTED", self.journal.receive(self.start))
        self.assertEqual("DUPLICATE", self.journal.receive(self.start))
        replay = EventJournal.restore(self.binding, [self.events[0][1]], self.commit)
        self.assertEqual("DUPLICATE", replay.receive(self.start))
        with self.assertRaisesRegex(Blocked, "continuity"):
            replay.receive(self.packet(2, "thread/tokenUsage/updated"))
        with self.assertRaisesRegex(Blocked, "Conflicting"):
            self.journal.receive(replace(self.start, message={**self.start.message, "extra": "changed"}))
        self.assertEqual(1, len(self.events))

    def test_gap_foreign_generation_and_write_failure_never_acknowledged(self):
        for packet in (replace(self.start, sequence=2),
                       replace(self.start, binding=replace(self.binding, generation=2)),
                       replace(self.start, binding=replace(self.binding, project_id="other")),
                       replace(self.start, binding=replace(self.binding, development_id="old"))):
            journal = EventJournal(self.binding, self.commit)
            with self.assertRaises(Blocked):
                journal.receive(packet)
            self.assertTrue(journal.blocked)

        def fail(_kind, _receipt):
            raise OSError("storage failure")

        journal = EventJournal(self.binding, fail)
        with self.assertRaises(OSError):
            journal.receive(self.start)
        self.assertEqual({}, journal.receipts)
        self.assertTrue(journal.blocked)
        self.assertEqual([], self.events)

    def test_usage_snapshots_never_sum_or_reduce_spend(self):
        self.journal.receive(self.start)
        for seq, tokens, final in ((2, 7, False), (3, 7, False), (4, 3, True)):
            packet = self.packet(seq, FIXTURE_USAGE, tokens=tokens, cost_microunits=tokens * 2, final=final)
            self.journal.receive(packet)
        self.assertEqual("DUPLICATE", self.journal.receive(packet))
        self.journal.receive(self.packet(5, "turn/completed", turn={"id": "turn", "status": "completed"}))
        with self.assertRaises(Blocked):
            self.journal.fixture_settlement(expected_status="completed", resources_released=False)
        result = self.journal.fixture_settlement(expected_status="completed", resources_released=True)
        self.assertEqual((7, 14, "UNKNOWN"), (result["synthetic_tokens"], result["synthetic_cost_microunits"],
                                              result["tokens"]))

    def test_disconnect_keeps_open_command_and_unverified_usage(self):
        self.journal.receive(self.start)
        self.journal.receive(self.packet(2, "item/started", item={"id": "cmd", "type": "commandExecution",
                                                                 "command": "raw fixture"}))
        self.journal.receive(self.packet(3, "thread/tokenUsage/updated"))
        self.journal.disconnect()
        self.assertEqual("stream_interrupted", self.events[-1][0])
        self.assertNotIn("raw fixture", str(self.events))
        self.assertEqual(1, self.journal.cursor.summary()["open_items"])
        self.assertEqual("PARTIAL_UNVERIFIED", self.journal.cursor.summary()["provider_usage"])
        self.assertEqual("UNKNOWN", self.journal.cursor.summary()["tokens"])
        with self.assertRaises(Blocked):
            self.journal.fixture_settlement(expected_status="completed", resources_released=True)


class StreamRunnerTests(unittest.TestCase):
    def test_lost_completion_retains_prefix_and_blocks_next_role(self):
        with TemporaryDirectory(dir=Path.cwd()) as tmp:
            ledger = Ledger(Path(tmp) / "runtime.sqlite3")
            runner = Runner(ledger)
            runner.command("init")
            session = runner.development("start", limits={"seconds": 600, "tokens": 10, "cost_microunits": 0})
            run = runner.development("next", development_id=session["development_id"])["run_id"]
            for action in ("step", "approve", "step"):
                runner.command(action, expected_run=run)
            append = ledger.append

            def fail(count, tail, event, **options):
                if event["kind"] == "stream_event" and event["stream_receipt"]["record"]["sequence"] == 7:
                    raise OSError("lost terminal receipt")
                return append(count, tail, event, **options)

            with patch.object(ledger, "append", side_effect=fail), self.assertRaises(OSError):
                runner.command("step", expected_run=run)
            view = ProjectionReader(ledger).read("fixture-a")
            self.assertEqual(("Manager", 6), (view["unconfirmed_role"],
                             view["latest_stream"]["stream_receipt"]["record"]["sequence"]))
            self.assertEqual((1, "UNKNOWN"), (view["development"]["tokens"], view["development"]["usage_kind"]))
            reopened = Runner(ledger)
            with self.assertRaises(Blocked):
                reopened.command("step", expected_run=run)
            self.assertEqual("UNKNOWN", reopened.command("recover", expected_run=run)["result"])
