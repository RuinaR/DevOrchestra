from pathlib import Path
import json
from hashlib import sha256
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from devorchestra import direct_git
from devorchestra.lease import Blocked
from devorchestra.ledger import digest
from devorchestra.replica_evidence import snapshot


@unittest.skipUnless(shutil.which("git"), "Git required")
class DirectGitTests(unittest.TestCase):
    def test_cycle_publication_pending_retry_and_conflict(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            work, remote = base / "project", base / "remote"
            work.mkdir()
            remote.mkdir()

            def git(root, scratch, check, *args, codes=(0,), raw=False):
                check()
                if args[0] == "push" and fail_push:
                    raise Blocked("test transport unavailable")
                result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=root,
                                        capture_output=True, timeout=10)
                if result.returncode not in codes:
                    raise Blocked(f"fixture git {args[0]}: {result.returncode}")
                return result.stdout if raw else result.stdout.decode("utf-8")

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
                clean = snapshot(work)
                start = {"kind": "native_inspection_intent", "run_id": "run", "approval_sha256": "approved",
                         "scope": {"project_id": "fixture", "direct_git": approved.copy()}}
                end = {"kind": "native_inspection_result", "replica_sha256": digest(clean)}
                receipts = [{"kind": kind, **row, "project_id": "fixture", "run_id": "run",
                             "approval_sha256": "approved", "cycle": 2} for kind, row in facts[-3:]]
                events = [start, *receipts, end]
                source.write_text("third draft", encoding="ascii")
                staging = base / "owned" / "build" / "candidate"
                staging.mkdir(parents=True)
                (staging / "game.txt").write_bytes(source.read_bytes())

                def partial():
                    return direct_git.partial_candidate(work, base, check, events, start, end, 3, snapshot(work),
                                                        base / "owned", validate_paths=lambda paths: None)

                head, paths, files, _, evidence = partial()
                self.assertEqual((commit2, ["game.txt"], clean["files"], "build/candidate"),
                                 (head["head"], paths, files, evidence))
                source.write_text("third draft with outside edit", encoding="ascii")
                with self.assertRaisesRegex(Blocked, "owned staging tree"):
                    partial()
                log = base / "commands.jsonl"

                def command_rows(item, script, *, exit_code=0, state="completed"):
                    command = shlex.join(["powershell.exe", "-Command", script])
                    common = {"id": item, "type": "command_execution", "command": command}
                    return [
                        {"type": "item.started", "item": common},
                        {"type": "item.completed", "item": {
                            **common, "exit_code": exit_code, "status": state}},
                    ]

                output = staging.parent
                staged_receipt = command_rows(
                    "build", f"& pack.ps1 -ProjectRoot '{work}' -OutputDirectory '{output}'", exit_code=1)
                witnessed = command_rows("read", f"$e='{output / 'evidence'}'; Get-ChildItem $e | Select-Object Name")

                def replacement(old, new):
                    return ("$p=Join-Path (Get-Location) 'game.txt'\n"
                            f"$s=[IO.File]::ReadAllText($p).Replace('{old}','{new}')\n"
                            "[IO.File]::WriteAllText($p,$s,(New-Object System.Text.UTF8Encoding($false)))")

                first = command_rows("first", replacement("third", "fourth"))
                second = command_rows("second", replacement("draft", "candidate"))
                source.write_text("fourth candidate", encoding="ascii")

                def reconstructed(rows, **kwargs):
                    log.write_bytes("\n".join(map(json.dumps, rows)).encode("utf-8"))
                    return direct_git.partial_candidate(
                        work, base, check, events, start, end, 3, snapshot(work), base / "owned",
                        validate_paths=lambda paths: None, tool_log=log, log_limit=8192, **kwargs)

                prefix = staged_receipt + witnessed
                complete = prefix + first + second
                evidence = reconstructed(complete)[4]
                self.assertEqual(["first", "second"], [edit["item"] for edit in evidence["edits"]])
                self.assertEqual(evidence, reconstructed(complete, transcript_sha=evidence["transcript_sha256"])[4])
                with self.assertRaisesRegex(Blocked, "role receipt"):
                    reconstructed(complete, transcript_sha="changed")
                for candidate in (prefix + first + second[:1], prefix + first + command_rows(
                        "failed", replacement("draft", "candidate"), exit_code=1, state="failed"),
                        prefix + [first[0], second[0], first[1], second[1]]):
                    with self.assertRaisesRegex(Blocked, "owned staging tree"):
                        reconstructed(candidate)
                source.write_text("fourth candidate with outside edit", encoding="ascii")
                with self.assertRaisesRegex(Blocked, "owned staging tree"):
                    reconstructed(complete)
                source.write_text("third draft", encoding="ascii")
                added = work / "new.txt"
                added.write_bytes(b"new content")
                (staging / added.name).write_bytes(b"new content")
                literal = command_rows("write", "[IO.File]::WriteAllText((Join-Path (Get-Location) 'new.txt'), "
                                       "'new content', [Text.UTF8Encoding]::new($false))")
                payload = "\n".join(map(json.dumps, literal)).encode("utf-8")
                sealed = sha256(payload).hexdigest()
                with self.assertRaisesRegex(Blocked, "sealed"):
                    reconstructed(literal)
                self.assertEqual(["game.txt", "new.txt"], reconstructed(literal, transcript_sha=sealed)[1])
                self.assertEqual("write", reconstructed(literal, transcript_sha=sealed)[4]["added"]["new.txt"]["item"])
                with self.assertRaisesRegex(Blocked, "outside scope"):
                    direct_git.partial_candidate(
                        work, base, check, events, start, end, 3, snapshot(work), base / "owned",
                        validate_paths=Mock(side_effect=Blocked("outside scope")), tool_log=log,
                        log_limit=8192, transcript_sha=sealed)
                added.write_bytes(b"unattested content")
                with self.assertRaisesRegex(Blocked, "settled literal"):
                    reconstructed(literal, transcript_sha=sealed)
                added.write_bytes(b"new content")
                unsettled = literal[:1]
                unsettled_sha = sha256(json.dumps(unsettled[0]).encode("utf-8")).hexdigest()
                with self.assertRaisesRegex(Blocked, "lack a supported"):
                    reconstructed(unsettled, transcript_sha=unsettled_sha)
                (staging / added.name).write_bytes(b"different retained bytes")
                with self.assertRaisesRegex(Blocked, "owned staging tree"):
                    reconstructed(literal, transcript_sha=sealed)
                added.unlink()
                (staging / added.name).unlink()
                with patch.dict(end, replica_sha256="invalid"):
                    with self.assertRaisesRegex(Blocked, "frozen dispatch"):
                        partial()
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
