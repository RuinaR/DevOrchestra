import unittest

from devorchestra.job_probe import CODEX_COMMIT_RESERVE, LARGE_CODEX_JOB_LIMIT, admits_large_codex_job


class CodexJobCommitTests(unittest.TestCase):
    def test_admission_preserves_commit_reserve(self):
        total = 12 * 1024**3
        limit = total + LARGE_CODEX_JOB_LIMIT + CODEX_COMMIT_RESERVE
        self.assertTrue(admits_large_codex_job({"commit_total": total, "commit_limit": limit}))
        self.assertFalse(admits_large_codex_job({"commit_total": total + 1, "commit_limit": limit}))
        self.assertFalse(admits_large_codex_job({"commit_total": limit, "commit_limit": total}))
        self.assertFalse(admits_large_codex_job(None))
