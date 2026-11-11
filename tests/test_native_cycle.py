import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from devorchestra import native_cycle
from devorchestra.ledger import Ledger
from devorchestra.lease import Blocked


class NativeCycleTests(unittest.TestCase):
    def test_early_manager_completion_cannot_override_observed_missing_feature(self):
        calls = []

        def role(cycle, phase, target, prompt):
            calls.append((cycle, phase, target))
            return {"message": {"manager-planning": "PLAN A", "developer": "DONE",
                                "reviewer": "ACCEPT", "manager-final": "COMPLETED"}[phase],
                    "diff": "fixture diff", "successful_fixture_command_sha256": ["observed"]}

        def verify(*_):
            return {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED", "verdict": "FAIL",
                    "observations": [{"title": "A", "input": 3, "expected": 9, "actual": 9},
                                     {"title": "B", "input": 3, "expected": 2, "actual": 3}]}

        with self.assertRaisesRegex(Blocked, "disagrees"):
            native_cycle._execute_cycles({"cycles": 2}, role, verify, iter(("base", "A")).__next__, lambda *_: None)
        self.assertEqual({cycle for cycle, _, _ in calls}, {1})

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"),
                         "Requires the local signed Codex Windows distribution")
    def test_two_cycles_real_git_mxc_and_ledger_with_stubbed_provider(self):
        archive = Path(os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"])
        scenarios = [
            (None, "COMPLETED", 8),
            (3, "REQUEST_UNKNOWN", 3),
        ]
        for interrupted, outcome, call_count in scenarios:
            with self.subTest(outcome=outcome):
                self._run_fixture(archive, interrupted, outcome, call_count)

    def _run_fixture(self, archive, interrupted, outcome, call_count):
        real_observe = native_cycle._observe
        model_calls = []

        def observe(binary, argv, work, scratch, environment, **kwargs):
            if "exec" not in argv and "login" not in argv:
                return real_observe(binary, argv, work, scratch, environment, **kwargs)
            (scratch / "probe.stderr").write_bytes(b"")
            if "login" in argv:
                return {"stdout": "Logged in using ChatGPT\n", "exitCode": 0, "job_active": 0}
            phase = scratch.name.split("-", 1)[1]
            target = "A" if scratch.name.startswith("1-") else "B"
            model_calls.append((target, phase))
            if len(model_calls) == interrupted:
                raise native_cycle.OwnedMxcStopped("fixture timeout after dispatch")
            task = native_cycle.TASKS[target]
            rows = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"}]
            if phase == "developer":
                (work / task["file"]).write_text(task["new"], encoding="ascii")
                rows.append({"type": "item.completed", "item": {"type": "command_execution",
                             "command": task["command"], "aggregated_output": task["expected"], "exit_code": 0}})
            message = {"manager-planning": f"PLAN {target}", "developer": "DONE", "reviewer": "ACCEPT",
                       "manager-final": "CONTINUE" if target == "A" else "COMPLETED"}[phase]
            rows.append({"type": "item.completed", "item": {"type": "agent_message", "text": message}})
            rows.append({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5,
                                                               "cached_input_tokens": 20}})
            return {"stdout": "\n".join(map(json.dumps, rows)), "exitCode": 0, "job_active": 0}

        approved_temp = Path(__file__).resolve().parents[1] / ".ai-local" / "temp"
        with TemporaryDirectory(prefix="cycle-test-", dir=approved_temp) as folder:
            root = Path(folder)
            ledger = Ledger(root / "test.sqlite3")
            ledger.initialize()
            (root / "codex-developer-home").mkdir()
            request = uuid4().hex
            options = ledger, archive, request, int(time()) + 300, 180, 270000, 2
            with patch.object(native_cycle, "_observe", side_effect=observe), patch.object(
                    native_cycle, "_diagnostic", return_value={"request_id": "local-fixture"}):
                review = native_cycle.run(*options)
                self.assertEqual(model_calls, [])
                result = native_cycle.run(*options, approval=review["approval_sha256"])
                with self.assertRaisesRegex(Blocked, "already used"):
                    native_cycle.run(*options, approval=review["approval_sha256"])
            self.assertEqual(result["status"], outcome)
            self.assertEqual(result["approval_count"], 1)
            self.assertEqual(len(model_calls), call_count)
            self.assertEqual(result["owned_tree"], "DRAINED")
            if outcome == "COMPLETED":
                self.assertEqual(result["cycle_count"], 2)
                self.assertEqual(result["known_reported_tokens"], 840)
                self.assertEqual(result["staging_cleanup"]["status"], "REMOVED")
                rows = [event for event in ledger.read() if event["kind"] == "native_inspection_cycle_progress"]
                self.assertEqual([list(row["criteria"].values()).count("VERIFIED") for row in rows], [1, 2])
            else:
                self.assertFalse(result["usage_complete"])
                self.assertEqual(result["staging_cleanup"]["status"], "RETAINED_FOR_RECOVERY")
                native_cycle._release_fixture_git_objects(
                    root / result["staging_cleanup"]["workspace"] / "work",
                    root / result["staging_cleanup"]["workspace"] / "original")
