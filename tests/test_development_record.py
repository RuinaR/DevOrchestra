from pathlib import Path
import posixpath
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from devorchestra import development_record as records, direct_git, registered_cycle
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.replica_evidence import snapshot
from devorchestra.runtime import Runner


CRITERION = "C1-aabbccdd"


def report_for(image, head, run, *, final="REWORK", state="NOT_VERIFIED", branch="refs/heads/main"):
    candidate = digest(image)
    criteria = {CRITERION: state, "C2-00000000": "NOT_VERIFIED"}
    common = {"project_id": "fixture", "run_id": run, "cycle": 1, "source_sha256": candidate}
    protected = "ghp_abcdefghijklmnopqrstuvwxyz C:/Users/private/.ai-local/logs"
    evidence = [
        {"kind": "native_inspection_plan_accepted", "target": CRITERION, "mode": "edit", "plan": protected},
        {"kind": "native_inspection_developer_diff", "cycle_diff_sha256": "a" * 64},
        {"kind": "native_inspection_role_result", "phase": "developer", "outcome": "COMPLETED",
         "provider_usage": "UNKNOWN", "command_events": [{"command": protected}]},
        {"kind": "native_inspection_review_decision", "verdict": "REWORK" if final == "REWORK" else "ACCEPT",
         "finding": protected},
        {"kind": "native_inspection_role_result", "phase": "verifier", "verdict": "PASS",
         "observations": [{"level": "functional", "coverage": "partial", "expected": protected, "actual": protected}]},
        {"kind": "native_inspection_cycle_progress", "criteria": criteria, "decision_cause": "VERIFICATION_MISSING",
         "adaptive_play_actions": 0},
        {"kind": "native_inspection_decision_applied", "verdict": final},
    ]
    events = [{**common, **row} for row in evidence]
    return records.render(events, project="fixture", run=run, cycle=1, branch=branch,
                          head=head, candidate=candidate, criterion=CRITERION, final=final,
                          criteria=criteria, requirements=3, paths=["code.txt"])


class RecordTests(unittest.TestCase):
    def test_deterministic_public_projection_and_identity(self):
        params = ({"files": {}}, "1" * 40, "a" * 32)
        record = report_for(*params, final="USER_TEST_REQUIRED")
        self.assertEqual(record, report_for(*params, final="USER_TEST_REQUIRED"))
        self.assertNotIn("ghp_", record[-1])
        self.assertNotIn("C:/Users", record[-1])
        self.assertIn("functional / partial / PASS", record[-1])
        self.assertIn("**NOT_VERIFIED**", record[-1])
        self.assertIn("developer · UNKNOWN", record[-1])
        self.assertIn("Reviewer and Verifier outcomes differ", report_for(*params)[-1])
        self.assertIn("[Current status](../CURRENT.md)", record[-1])
        self.assertNotEqual(record[3], report_for(params[0], params[1], "b" * 32)[3])
        self.assertIn("**VERIFIED**", report_for(*params, final="COMPLETED", state="VERIFIED")[1])
        self.assertIn("LOCAL_ONLY", report_for(*params, branch=None)[1])
        self.assertEqual(records.candidate_files_digest({"code.txt": 1}),
                         records.candidate_files_digest({"code.txt": 1, records.CURRENT: 2}))
        with self.assertRaisesRegex(Blocked, "matching settled decision"):
            records.render([], project="fixture", run=params[2], cycle=1, branch="refs/heads/main",
                           head=params[1], candidate="a" * 64, criterion=CRITERION, final="REWORK",
                           criteria={CRITERION: "NOT_VERIFIED"}, requirements=0, paths=[])

    def test_unknown_usage_or_job_never_enters_direct_publication(self):
        for drained, settled in ((True, False), (False, True)):
            session = Mock(direct_binding={"head": "old"}, owned_verified=drained, usage_complete=settled)
            with self.assertRaisesRegex(Blocked, "settled usage and owned Jobs"):
                registered_cycle.RegisteredSession.publish_cycle(session, 1, CRITERION, "REWORK", {})
            session.check_image.assert_not_called()


@unittest.skipUnless(shutil.which("git"), "Git required")
class RecordGitTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.work, self.remote = self.base / "work", self.base / "remote"
        self.work.mkdir()
        self.remote.mkdir()
        self.git("init", "--bare", cwd=self.remote)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.source = self.work / "code.txt"
        self.source.write_text("base", encoding="ascii")
        self.git("add", "code.txt")
        self.git("commit", "-m", "Baseline")
        self.git("push", str(self.remote), "HEAD:refs/heads/main")
        self.ledger = Ledger(self.base / "fixture.sqlite3")
        self.ledger.initialize()
        self.git_failure = ""
        self.ledger_fail = ""
        self.run_id = "a" * 32
        self.verdict = "REWORK"
        for mock in (patch.object(direct_git, "_command", self.command),
                     patch.object(direct_git, "binding", self.binding),
                     patch("devorchestra.replica_readiness.baseline", return_value=(self.work, None, None, None))):
            mock.start()
            self.addCleanup(mock.stop)

    def git(self, *args, cwd=None, codes=(0,), raw=False):
        result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd or self.work,
                                capture_output=True, timeout=10)
        if result.returncode not in codes:
            raise Blocked(f"fixture Git {args[0]}: {result.stderr.decode(errors='replace')}")
        return result.stdout if raw else result.stdout.decode("utf-8")

    def command(self, root, scratch, check, *args, **kwargs):
        check()
        if args[0] == self.git_failure:
            raise Blocked("injected Git failure")
        return self.git(*args, cwd=root, **kwargs)

    def binding(self, root, scratch, check):
        return {"ref": "refs/heads/main", "url": str(self.remote), "head": self.git("rev-parse", "HEAD").strip()}

    def record(self, kind, **facts):
        if kind == self.ledger_fail:
            raise OSError("injected ledger failure")
        events, tail = self.ledger.snapshot()
        self.ledger.append(len(events), tail, {"kind": kind, "project_id": "fixture", "run_id": self.run_id,
                                               "cycle": 1, "verdict": self.verdict, **facts})

    def publish(self, text, *, paths=("code.txt",), branch="refs/heads/main", state="NOT_VERIFIED"):
        self.source.write_text(text, encoding="ascii")
        approved = self.binding(self.work, self.base, lambda: None)
        image = snapshot(self.work)
        report = report_for(image, approved["head"], self.run_id, final=self.verdict, branch=branch, state=state)
        return direct_git.publish(self.work, self.base, lambda: None, approved, image, list(paths), self.verdict,
                                  self.record, report=report)

    def test_atomic_report_and_code_retry_then_updates_current_and_keeps_history(self):
        self.git_failure = "push"
        with self.assertRaisesRegex(Blocked, "injected Git failure"):
            self.publish("first")
        self.assertEqual("base", self.git("show", "main:code.txt", cwd=self.remote))
        self.git_failure = ""
        first = direct_git.retry(Runner(self.ledger), "fixture")
        self.assertEqual(first, self.git("rev-parse", "main", cwd=self.remote).strip())
        self.assertEqual("first", self.git("show", "main:code.txt", cwd=self.remote))
        old_path = f"{records.ROOT}cycles/cycle-0001-{self.run_id}.md"
        old_report = self.git("show", "main:" + old_path, cwd=self.remote)
        for run, verdict, state in (("b" * 32, "USER_TEST_REQUIRED", "NOT_VERIFIED"),
                                   ("c" * 32, "COMPLETED", "VERIFIED")):
            self.run_id, self.verdict = run, verdict
            self.publish(verdict, state=state)
            self.assertIn(verdict, self.git("show", "main:" + records.CURRENT, cwd=self.remote))
        log = self.git("show", "main:" + records.LOG, cwd=self.remote)
        self.assertEqual(3, log.count("](cycles/cycle-0001-"))
        for path in (records.CURRENT, records.LOG, old_path):
            body = self.git("show", "main:" + path, cwd=self.remote)
            for link in re.findall(r"\]\(([^)]+)\)", body):
                destination = posixpath.normpath(posixpath.join(posixpath.dirname(path), link))
                self.git("cat-file", "-e", "main:" + destination, cwd=self.remote)
        self.assertIn(first, self.git("show", "HEAD~:" + records.CURRENT))
        self.assertEqual(old_report, self.git("show", "main:" + old_path, cwd=self.remote))
        with self.assertRaisesRegex(Blocked, "already exists"):
            self.publish("duplicate")

    def test_commit_failure_rolls_back_only_generated_files_and_staging(self):
        self.git_failure = "update-ref"
        with self.assertRaisesRegex(Blocked, "injected Git failure"):
            self.publish("retained code")
        self.assertEqual("retained code", self.source.read_text())
        self.assertEqual({"code.txt"}, snapshot(self.work)["files"].keys())
        self.assertEqual("", self.git("diff", "--cached", "--name-only"))
        self.assertIsNone(direct_git.pending(self.ledger.read(), "fixture"))

    def test_restart_after_either_commit_or_push_receipt_write_failure(self):
        for failed_receipt in ("direct_git_commit", "direct_git_pushed"):
            with self.subTest(failed_receipt=failed_receipt):
                self.ledger_fail = failed_receipt
                with self.assertRaisesRegex(OSError, "injected ledger failure"):
                    self.publish(failed_receipt)
                head = self.git("rev-parse", "HEAD").strip()
                self.ledger_fail = ""
                self.assertEqual(head, direct_git.retry(Runner(self.ledger), "fixture"))
                self.assertEqual(head, self.git("rev-parse", "main", cwd=self.remote).strip())
                self.assertIsNone(direct_git.pending(self.ledger.read(), "fixture"))
                self.run_id = "d" * 32
        self.assertEqual("3", self.git("rev-list", "--count", "HEAD").strip())

    def test_collision_and_generation_failure_leave_user_content(self):
        (self.work / records.ROOT).mkdir(parents=True)
        owned_by_user = self.work / records.CURRENT
        owned_by_user.write_text("Handwritten", encoding="ascii")
        self.git("add", records.CURRENT)
        self.git("commit", "-m", "User notes")
        self.git("push", str(self.remote), "HEAD:refs/heads/main")
        with self.assertRaisesRegex(Blocked, "owned by another project"):
            self.publish("candidate")
        self.assertEqual("Handwritten", owned_by_user.read_text())
        self.assertEqual([], self.ledger.read())
        with patch.object(records, "patch", side_effect=OSError("draft rendering failed")):
            with self.assertRaisesRegex(OSError, "draft rendering failed"):
                self.publish("candidate")
        self.assertFalse((self.work / records.LOG).exists())
        self.assertEqual("", self.git("diff", "--cached", "--name-only"))

    def test_no_empty_report_commit_and_foreign_branch_is_blocked(self):
        before = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(before, self.publish("base", paths=()))
        self.assertEqual([], self.ledger.read())
        with self.assertRaisesRegex(Blocked, "approved branch"):
            self.publish("branch change", branch="refs/heads/other")
        self.assertFalse((self.work / records.CURRENT).exists())
        self.assertEqual(before, self.git("rev-parse", "HEAD").strip())
