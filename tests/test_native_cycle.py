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
from devorchestra.native_developer_trial import CliToolEvidenceError, _cli_evidence


class NativeCycleTests(unittest.TestCase):
    def test_internal_discovery_keeps_settled_plan_but_other_tool_evidence_blocks_handoff(self):
        item = {"id": "discovery-1", "type": "mcp_tool_call", "server": "codex", "tool": "list_mcp_resources",
                "arguments": {}, "error": None}
        empty = {"content": [{"type": "text", "text": '{"resources":[]}'}], "structured_content": None}
        complete = {**item, "status": "completed", "result": empty}
        usage = {"input_tokens": 500, "output_tokens": 100}
        prefix = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"},
                  {"type": "item.started", "item": {**item, "status": "in_progress", "result": None}}]
        suffix = [{"type": "item.completed", "item": {"type": "agent_message", "text": "PLAN B"}},
                  {"type": "turn.completed", "usage": usage}]

        def transcript(item):
            return "\n".join(map(json.dumps, [*prefix, {"type": "item.completed", "item": item}, *suffix]))

        evidence = _cli_evidence(transcript(complete), retain_message=True)
        self.assertEqual(("PLAN B", usage, 1, []), (evidence["message"], evidence["provider_usage"],
                         evidence["empty_discovery_count"], evidence["command_events"]))
        for changed in ({"server": "external"}, {"tool": "read_mcp_resource"}, {"id": "unmatched"},
                        {"result": {**empty, "content": [{"type": "text", "text": '{"resources":["data"]}'}]}}):
            with self.subTest(changed=changed):
                stream = transcript({**complete, **changed})
                with self.assertRaises(CliToolEvidenceError):
                    _cli_evidence(stream, retain_message=True)
                settled = _cli_evidence(stream, retain_message=True, retain_settled=True)
                self.assertEqual(usage, settled["provider_usage"])
                self.assertIn("tool_evidence_error", settled)
        missing = "\n".join(map(json.dumps, [*prefix[:2], suffix[-1]]))
        settled = _cli_evidence(missing, retain_message=True, retain_settled=True)
        self.assertIsNone(settled["message"])
        self.assertEqual(usage, settled["provider_usage"])

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
            if phase == "manager-planning" and target == "B":
                discovery = {"id": "fixture-discovery", "type": "mcp_tool_call", "server": "codex",
                             "tool": "list_mcp_resource_templates", "arguments": {}, "error": None}
                rows.extend((
                    {"type": "item.started", "item": {**discovery, "status": "in_progress", "result": None}},
                    {"type": "item.completed", "item": {**discovery, "status": "completed", "result": {
                        "content": [{"type": "text", "text": '{"resourceTemplates":[]}'}],
                        "structured_content": None}}},
                ))
            if phase == "developer":
                (work / task["file"]).write_text(task["new"], encoding="ascii")
                rows.append({"type": "item.completed", "item": {"type": "command_execution",
                             "command": task["command"], "aggregated_output": task["expected"], "exit_code": 0}})
            message = {"manager-planning": f"PLAN {target}", "developer": "DONE", "reviewer": "ACCEPT",
                       "manager-final": "CONTINUE" if target == "A" else "COMPLETED"}[phase]
            if phase == "manager-final" and target == "A":
                self.assertIn("Independent check passed for this feature: True", argv[-1])
                self.assertIn("Other features pending a later cycle: ['B']", argv[-1])
            if phase == "reviewer" and target == "B":
                self.assertIn("--- a/total.cmd", argv[-1])
                self.assertIn("+++ b/total.cmd", argv[-1])
                self.assertNotIn("+++ b/subtotal.cmd", argv[-1])
            rows.append({"type": "item.completed", "item": {"type": "agent_message", "text": message}})
            usage = {"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 20}
            if phase == "reviewer" and target == "B":
                usage = {"input_tokens": 33246, "output_tokens": 167, "cached_input_tokens": 19200}
            rows.append({"type": "turn.completed", "usage": usage})
            stream = "\n".join(map(json.dumps, rows))
            if interrupted is None and target == "A" and phase in {"developer", "manager-final"}:
                (scratch / "probe.stdout").write_text(stream, encoding="utf-8")
                raise native_cycle.OwnedMxcStopped("MXC probe timeout")
            return {"stdout": stream, "exitCode": 0, "job_active": 0}

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
                self.assertEqual(result["known_reported_tokens"], 7 * 105 + 33413)
                self.assertEqual(result["staging_cleanup"]["status"], "REMOVED")
                decision = next(role for role in result["roles"]
                                if role["cycle"] == 1 and role["phase"] == "manager-final")
                self.assertEqual(decision["manager_decision"], "CONTINUE")
                self.assertEqual(decision["process_result"], "TERMINATED_AND_DRAINED")
                self.assertIsNone(decision["exit_code"])
                developer = next(role for role in result["roles"]
                                 if role["cycle"] == 1 and role["phase"] == "developer")
                self.assertEqual(developer["process_result"], "TERMINATED_AND_DRAINED")
                self.assertTrue(developer["test_commands"])
                rows = [event for event in ledger.read() if event["kind"] == "native_inspection_cycle_progress"]
                self.assertEqual([list(row["criteria"].values()).count("VERIFIED") for row in rows], [1, 2])
                self.assertEqual(list(rows[0]["criteria"].values()), ["VERIFIED", "NOT_STARTED"])
                self.assertEqual([row["controller_decision"] for row in rows], ["CONTINUE", "COMPLETED"])
            else:
                self.assertFalse(result["usage_complete"])
                self.assertEqual(result["staging_cleanup"]["status"], "RETAINED_FOR_RECOVERY")
                native_cycle._release_fixture_git_objects(
                    root / result["staging_cleanup"]["workspace"] / "work",
                    root / result["staging_cleanup"]["workspace"] / "original")
