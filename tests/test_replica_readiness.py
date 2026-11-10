import unittest

from devorchestra.replica_readiness import BOUNDARIES, evidence_passed


class ReadinessTests(unittest.TestCase):
    def test_ready_requires_executed_vectors_boundary_cleanup_and_original(self):
        evidence = {"scope": {"vectors": [[9, 31]]}, "result": "PASS", "job_active": 0,
                    "original_rechecked": True, "cleanup": {"status": "REMOVED"}, "boundaries": BOUNDARIES,
                    "observations": [{"input": 9, "expected": 31, "actual": 31}],
                    "steps": {"compile": {"exitCode": 0}, "run-0": {"exitCode": 0}},
                    "artifacts": {"app.dll": {}, "app.runtimeconfig.json": {}}}
        self.assertTrue(evidence_passed(evidence))
        for missing in ("boundaries", "observations", "steps", "artifacts", "cleanup", "original_rechecked"):
            with self.subTest(missing=missing):
                self.assertFalse(evidence_passed({key: value for key, value in evidence.items() if key != missing}))
