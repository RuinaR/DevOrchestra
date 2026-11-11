import unittest

from devorchestra import cycle_progress, python_vectors


class CycleProgressTests(unittest.TestCase):
    def test_independent_observations_keep_progress_and_regression_separate(self):
        acceptance = ["passes one", "passes two", "requires visual inspection"]
        first, second, manual = cycle_progress.freeze(acceptance, None)
        criteria = cycle_progress.freeze(acceptance, {first: [{"input": 1}], second: [{"input": 2}]})
        vectors = {"function": "answer", "acceptance": acceptance, "vectors": [[1, 1], [2, 3]]}

        def verify(expression):
            return {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
                    **python_vectors.run_vectors(f"def answer(value):\n    return {expression}\n", vectors)}

        before = cycle_progress.compare(criteria, {}, verify("value"))
        after = cycle_progress.compare(criteria, before["criteria"], verify("value + 1"))
        self.assertEqual([first], before["newly_verified"])
        self.assertEqual([second], after["newly_verified"])
        self.assertEqual([first], after["regressed"])
        self.assertEqual("NOT_VERIFIED", after["criteria"][manual])

    def test_model_or_shared_stdout_cannot_certify_a_criterion(self):
        key = next(iter(cycle_progress.freeze(["independent result"], None)))
        criteria = cycle_progress.freeze(["independent result"], {key: [{"input": "test"}]})
        receipt = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED", "verdict": "PASS",
                   "observations": [{"input": "test", "expected": True, "actual": True}]}
        for override in ({"phase": "developer"}, {"runtime": "mxc-dotnet-sdk-project-v1"},
                         {"observations": receipt["observations"] * 2}):
            self.assertEqual("NOT_VERIFIED", cycle_progress.compare(criteria, {}, receipt | override)["criteria"][key])

    def test_repeat_fingerprint_ignores_report_order_but_preserves_environment(self):
        feedback = [{"location": " A.py ", "observed": "Same   bug"}, {"location": "B.py", "observed": "Other"}]
        similar = [feedback[1], {"location": "a.py", "observed": "same bug"}]
        fingerprint = cycle_progress.failure_fingerprint
        self.assertEqual(fingerprint("reviewer", feedback, "sdk1"), fingerprint("reviewer", similar, "sdk1"))
        self.assertNotEqual(fingerprint("reviewer", feedback, "sdk1"), fingerprint("reviewer", similar, "sdk2"))
