from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra import direct_git
from devorchestra.lease import Blocked
from devorchestra.replica_evidence import snapshot


@unittest.skipUnless(shutil.which("git"), "Git required")
class DirectGitTests(unittest.TestCase):
    def test_cycle_publication_pending_retry_and_conflict(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            work, remote = base / "project", base / "remote"
            work.mkdir()
            remote.mkdir()

            def git(root, scratch, check, *args, codes=(0,)):
                check()
                if args[0] == "push" and fail_push:
                    raise Blocked("test transport unavailable")
                result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=root,
                                        capture_output=True, text=True, timeout=10)
                if result.returncode not in codes:
                    raise Blocked(f"fixture git {args[0]}: {result.returncode}")
                return result.stdout

            fail_push = False
            git(remote, base, lambda: None, "init", "--bare")
            git(work, base, lambda: None, "init", "-b", "main")
            git(work, base, lambda: None, "config", "user.name", "Fixture")
            git(work, base, lambda: None, "config", "user.email", "fixture@example.test")
            facts = []
            check = lambda: None
            record = lambda kind, **row: facts.append((kind, row))
            approved = {"ref": "refs/heads/main", "url": str(remote), "head": None}

            def bind(root, scratch, check):
                head = git(root, scratch, check, "rev-parse", "--verify", "--quiet", "HEAD", codes=(0, 1)).strip()
                return {**approved, "head": head or None}

            with patch.object(direct_git, "_command", git), patch.object(direct_git, "binding", bind):
                self.assertIsNone(direct_git.publish(work, base, check, approved, snapshot(work), [], "", record))
                source = work / "game.txt"
                source.write_text("draft", encoding="ascii")
                with self.assertRaisesRegex(Blocked, "differ"):
                    direct_git.publish(work, base, check, approved, snapshot(work), [], "bad", record)
                fail_push = True
                with self.assertRaisesRegex(Blocked, "transport"):
                    direct_git.publish(work, base, check, approved, snapshot(work), ["game.txt"], "REWORK", record)
                commit = bind(work, base, check)["head"]
                self.assertEqual("direct_git_push_pending", facts[-1][0])
                fail_push = False
                direct_git.push_pending(work, base, check, approved, commit, record)
                direct_git.push_pending(work, base, check, approved, commit, record)
                self.assertEqual("1", git(work, base, check, "rev-list", "--count", "HEAD").strip())
                self.assertEqual("draft", git(remote, base, check, "show", "main:game.txt"))
                source.write_text("second draft", encoding="ascii")
                approved["head"] = commit
                commit2 = direct_git.publish(work, base, check, approved, snapshot(work), ["game.txt"],
                                             "USER_TEST_REQUIRED", record)
                self.assertEqual(commit2, git(remote, base, check, "rev-parse", "main").strip())
                start = {"kind": "native_inspection_intent", "run_id": "run", "approval_sha256": "approved",
                         "scope": {"project_id": "fixture", "direct_git": approved.copy()}}
                end = {"kind": "native_inspection_result"}
                receipts = [{"kind": kind, **row, "project_id": "fixture", "run_id": "run",
                             "approval_sha256": "approved", "cycle": 2} for kind, row in facts[-3:]]
                events = [start, *receipts, end]
                source.write_text("third draft", encoding="ascii")
                self.assertEqual(commit2, direct_git.resume_candidate(
                    work, base, check, events, start, end, 3, ["game.txt"])["head"])
                with self.assertRaisesRegex(Blocked, "checkpoint publication history"):
                    direct_git.resume_candidate(work, base, check, [start, end], start, end, 3, ["game.txt"])
                with self.assertRaisesRegex(Blocked, "pending"):
                    direct_git.resume_candidate(work, base, check, events[:-2] + [end], start, end, 3, ["game.txt"])
                (work / "foreign.txt").write_text("outside the approved candidate", encoding="ascii")
                with self.assertRaisesRegex(Blocked, "publication paths"):
                    direct_git.resume_candidate(work, base, check, events, start, end, 3, ["game.txt"])
                (work / "foreign.txt").unlink()
                git(remote, base, check, "update-ref", "refs/heads/main", commit, commit2)
                with self.assertRaisesRegex(Blocked, "checkpoint publication history"):
                    direct_git.resume_candidate(work, base, check, events, start, end, 3, ["game.txt"])
                approved["head"] = commit2
                with self.assertRaisesRegex(Blocked, "Remote Git HEAD"):
                    direct_git.publish(work, base, check, approved, snapshot(work), ["game.txt"], "third", record)

    def test_private_paths_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "handoff.md"
            path.write_text("See C:/Users/fixture/.ai-local/logs", encoding="ascii")
            with self.assertRaisesRegex(Blocked, "Private material"):
                direct_git._safe_file(root, path.name, snapshot(root)["files"][path.name])
