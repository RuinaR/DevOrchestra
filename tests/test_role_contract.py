from dataclasses import replace
import json
import unittest

from devorchestra.adapter import Budget, RoleRequest, Scope
from devorchestra.lease import Blocked
from devorchestra.role_contract import (
    MAX_OUTPUT_BYTES, TransitionEvidence, bind_instructions, confirm_task_plan, decide_transition, parse_role_output,
)


def payload(**fields):
    return json.dumps(fields).encode()


class DraftRoleContractTests(unittest.TestCase):
    def setUp(self):
        self.evidence = TransitionEvidence(True, True, True, True, True, 1, False,
                                           "a" * 64, "b" * 64, "PASS", True)

    def test_plan_admits_security_enabler_and_requires_authority_for_support_only_work(self):
        plan = dict(disposition="PROPOSED", priority_reason="Protect customer records",
                    task_spec="Reject writes to the original project", proposed_paths=["guard.py"],
                    acceptance_checks=["Original remains unchanged"], stop_conditions=[],
                    work_kind="ENABLING", product_outcome="Users can safely approve replica edits",
                    validation_reason="Confirm the guard prevents original writes",
                    approach="Enforce the existing scope at the write boundary")
        limits = dict(allowed_files=("guard.py",), acceptance=("Original remains unchanged",),
                      budget={"tokens": 10}, authority_ref="fixture")
        self.assertEqual("ENABLING", confirm_task_plan(payload(**plan), **limits)["work_kind"])
        plan["work_kind"] = "SUPPORT"
        with self.assertRaises(Blocked):
            confirm_task_plan(payload(**plan), **limits)
        self.assertEqual("SUPPORT", confirm_task_plan(
            payload(**plan), **limits, supporting_work_authorized=True)["work_kind"])

    def test_rejects_output_expansion_duplicates_and_invalid_nested_data(self):
        valid = {"disposition": "READY_FOR_REVIEW", "summary": "Fixture",
                 "changes": [{"path": "fixture.txt", "reason": "Fixture change"}],
                 "validation_attempts": [], "unfinished": []}
        self.assertEqual(valid, parse_role_output("developer", payload(**valid)))
        for invalid in (payload(**valid, budget=10), payload(**{**valid, "changes": [True]}),
                        payload(**{**valid, "summary": " "}),
                        b'{"summary":"a","summary":"b"}', b"x" * (MAX_OUTPUT_BYTES + 1)):
            with self.assertRaises(Blocked):
                parse_role_output("developer", invalid)

    def test_reviewer_rework_requires_drain_budget_and_progress(self):
        empty = payload(decision="REWORK", findings=[], coverage_gaps=[])
        self.assertEqual("BLOCKED", decide_transition("reviewer", empty, self.evidence))
        proposal = payload(decision="REWORK", coverage_gaps=[],
                           findings=[{"severity": "HIGH", "location": "fixture.txt",
                                      "observed": "missing check", "impact": "incorrect result",
                                      "required_fix": "add required check"}])
        for changes, expected in (
                ({}, "NEW_REWORK_CYCLE"), ({"owned_tree_drained": False}, "BLOCKED"),
                ({"receipt_committed": False}, "BLOCKED"), ({"admission_open": False}, "BLOCKED"),
                ({"repeated_failure": True}, "NO_PROGRESS"), ({"rework_remaining": 0}, "NO_PROGRESS"),
                ({"next_role_and_reserve_funded": False}, "SCHEDULE_SHUTDOWN")):
            self.assertEqual(expected, decide_transition("reviewer", proposal, replace(self.evidence, **changes)))

    def test_final_manager_binds_evidence_and_separates_goal_completion(self):
        fields = {"goal_hash": "a" * 64, "evidence_ref": "b" * 64,
                  "reason": "Fixture", "next_task_or_rework": "Fixture follow-up"}
        for decision, changes, expected in (
                ("NEXT", {}, "NEW_TASK_CYCLE"), ("GOAL_COMPLETE", {}, "COMPLETED"),
                ("NEXT", {"verifier_ref": "c" * 64}, "BLOCKED"),
                ("GOAL_COMPLETE", {"goal_criteria_passed": False}, "BLOCKED"),
                ("NEXT", {"verifier_verdict": "FAIL"}, "BLOCKED"),
                ("REWORK", {"verifier_verdict": "FAIL"}, "NEW_REWORK_CYCLE")):
            self.assertEqual(expected, decide_transition(
                "manager-final", payload(decision=decision, **fields), replace(self.evidence, **changes)))

    def test_binding_requires_order_one_global_and_unique_bytes(self):
        request = RoleRequest("p", "t", "r", 1, "a", "Developer", "m", "low", "c",
                              Scope("fixture", ()), Budget(1, 2, 0), ())
        layers = [{"layer": name, "version": "v1", "sha256": char * 64}
                  for name, char in zip(("global", "common", "project", "role"), "abcd")]
        before = bind_instructions(request, layers)
        self.assertNotEqual(before, bind_instructions(request, [*layers[:-1], {**layers[-1], "version": "v2"}]))
        with self.assertRaises(Blocked):
            bind_instructions(request, [layers[0], *layers])
        with self.assertRaises(Blocked):
            bind_instructions(request, [*layers[:-1], {**layers[-1], "sha256": layers[0]["sha256"]}])
