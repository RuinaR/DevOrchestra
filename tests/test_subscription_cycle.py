import unittest

from devorchestra.lease import Blocked
from devorchestra.python_vectors import run_vectors, validate_vectors
from devorchestra.subscription_cycle import _final_decision


class SubscriptionCycleTests(unittest.TestCase):
    def test_fixed_python_vectors_execute_only_allowed_expressions(self):
        spec = {"function": "clamp", "acceptance": ["Clamp negatives"], "vectors": [[-7, 0], [9, 9]]}
        validate_vectors(spec, ["Clamp negatives"])
        with self.assertRaisesRegex(Blocked, "acceptance binding"):
            validate_vectors(spec, ["Different acceptance"])
        correct = run_vectors(b"def clamp(x):\n    return x if x >= 0 else 0\n", spec)
        self.assertEqual(("PASS", 0, [0, 9]), (correct["verdict"], correct["exit_code"],
                                               [item["actual"] for item in correct["observations"]]))
        wrong = run_vectors(b"def clamp(x):\n    return x\n", spec)
        self.assertEqual(("FAIL", 1), (wrong["verdict"], wrong["exit_code"]))
        with self.assertRaisesRegex(Blocked, "unsupported Python expression"):
            run_vectors(b"def clamp(x):\n    return abs(x)\n", spec)

    def test_final_manager_success_requires_matching_evidence_and_verifier_pass(self):
        scope = {"goal_hash": "goal", "verifier_sha256": "receipt"}
        output = {"decision": "GOAL_COMPLETE", "goal_hash": "goal", "evidence_ref": "receipt"}
        self.assertEqual("COMPLETED", _final_decision(output, scope, {"verdict": "PASS"}))
        for verdict in ("FAIL", "NOT_VERIFIED"):
            with self.assertRaisesRegex(Blocked, "deterministic PASS"):
                _final_decision(output, scope, {"verdict": verdict})
        with self.assertRaisesRegex(Blocked, "outside the evidence"):
            _final_decision({**output, "evidence_ref": "other"}, scope, {"verdict": "PASS"})
        self.assertEqual("REWORK_REQUIRED", _final_decision(
            {**output, "decision": "REWORK"}, scope, {"verdict": "FAIL"},
        ))
