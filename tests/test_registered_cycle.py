"""Offline integration across the real registry, Git onboarding, MXC and approval."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, time
import unittest
from unittest.mock import Mock, patch

from devorchestra import cycle_progress, game_pilot, native_cycle, original_apply, project_onboarding, registered_cycle
from devorchestra.catalog import Catalog, new_project
from devorchestra.ledger import Ledger, digest
from devorchestra.lease import Blocked
from devorchestra.runtime import Runner
from devorchestra.native_developer_trial import _check_dedicated_config


class RegisteredTrustTests(unittest.TestCase):
    def test_feedback_is_linked_and_credential_like_findings_are_omitted(self):
        session = Mock()
        session.source.return_value = "source"
        message = "REWORK — The HUD clips at narrow widths."
        verdict, finding = registered_cycle._verdict(message, {"REWORK"})
        registered_cycle._record_feedback(session, 1, "C4", "reviewer", verdict, message, finding, ["hud.gd"])
        event = session.record.call_args.kwargs
        self.assertEqual((event["reason"], event["target"], event["files"]),
                         (finding, "C4", ["hud.gd"]))
        self.assertEqual(event["message_sha256"], digest(message))
        registered_cycle._record_feedback(session, 1, "C4", "manager-final", "REWORK",
                                          "REWORK — password fixture", "password fixture")
        self.assertEqual(session.record.call_args.kwargs["reason"], "[omitted: possible sensitive text]")

    def test_player_report_is_bound_to_candidate_and_is_not_a_verifier_result(self):
        candidate = {"source_sha256": "source", "run_id": "export", "identity": {"sha256": "exe"}}
        runner = Mock()
        runner._idle_diagnostic.return_value = ([], "head")
        ratings = dict.fromkeys(game_pilot.PLAYER_FEEDBACK_FIELDS, "OK")
        with patch.object(game_pilot, "player_candidate", return_value=candidate):
            game_pilot.save_player_feedback(runner, candidate, ratings, "test note", True)
        _, _, event = runner.ledger.append.call_args.args
        self.assertEqual(event["origin"], "USER_REPORTED")
        self.assertNotIn("verdict", event)
        self.assertIs(game_pilot.latest_player_feedback([event], "source", "export"), event)
        self.assertIsNone(game_pilot.latest_player_feedback([event], "source", "other-export"))

    def test_review_only_game_plan_reviews_diff_without_dispatching_developer(self):
        key = game_pilot.CRITERIA[1]
        session = Mock()
        session.image = {"files": {"game.gd": {}}}
        session.scope = {"project_id": game_pilot.PROJECT, "criteria": {key: {"text": "movement"}},
                         "goal": "Review play", "files": ["game.gd"], "cycles": None, "requirements": [],
                         "verifier": {"manager_brief": "game", "executable": "godot"},
                         "candidate_base": {"changed": ["game.gd"], "existing_plan": "review",
                                            "design_present": True, "parent_outcome": "REWORK"}}
        session.call.return_value = json.dumps({"criterion": key, "plan": "Review movement", "mode": "verify"})
        comparison = {"criteria": {key: "USER_TEST_REQUIRED"}, "newly_verified": [], "regressed": []}
        with patch.object(registered_cycle, "Catalog"), \
                patch.object(registered_cycle, "read_frozen_requirements", return_value=[]), \
                patch.object(registered_cycle, "_read_source", side_effect=["old\n", "new\n"]), \
                patch.object(registered_cycle, "_review_and_verify",
                             return_value=("ACCEPT", "ACCEPT", "", {}, comparison)) as verify, \
                patch.object(registered_cycle, "_decide", return_value="USER_TEST_REQUIRED"):
            self.assertEqual(registered_cycle._cycles(session), "USER_TEST_REQUIRED")
        self.assertEqual([call.args[1] for call in session.call.call_args_list], ["manager-planning"])
        self.assertIn("+new", verify.call_args.args[3])

    def test_only_approved_registered_replica_trust_is_accepted(self):
        with TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            replica = root / "old-replica"
            config = root / "config.toml"
            config.write_text(f"[projects.{json.dumps(str(replica))}]\ntrust_level = \"trusted\"\n",
                              encoding="utf-8")
            with self.assertRaises(Blocked):
                _check_dedicated_config(root, root / "temp")
            _check_dedicated_config(root, root / "temp", approved_replicas={replica})
            config.write_text(config.read_text(encoding="utf-8") + "[mcp_servers.example]\ncommand = \"ignored\"\n",
                              encoding="utf-8")
            with self.assertRaises(Blocked):
                _check_dedicated_config(root, root / "temp", approved_replicas={replica})


@unittest.skipUnless(os.environ.get("DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"), "Requires signed local Windows tools")
class RegisteredCycleTests(unittest.TestCase):
    def test_onboard_run_verify_and_review_original_write(self):
        self._exercise(False)

    def test_resume_settled_developer_without_repeating_model_calls(self):
        self._exercise(True)

    def _exercise(self, resume):
        approved = Path(__file__).resolve().parents[1] / ".ai-local" / "temp"
        with TemporaryDirectory(prefix="registered-test-", dir=approved) as folder:
            base = Path(folder)
            original, replica = base / "original", base / "replica"
            original.mkdir()
            runtime = base / ".ai-local"
            ledger = Ledger(runtime / "state" / "p1.sqlite3", local_root=runtime)
            ledger.initialize()
            (ledger.local_directory("state") / "codex-developer-home").mkdir()
            runner = Runner(ledger)
            (original / "formatting.py").write_text('GREETING = "old"\nSIGNOFF = "old"\n', encoding="utf-8")
            (original / "check.py").write_text(
                ('import sys\nimport formatting, closing\n'
                 'print(formatting.GREETING if sys.argv[1] == "GREETING" else closing.SIGNOFF)\n'
                 if resume else 'import sys\nimport formatting\nprint(getattr(formatting, sys.argv[1]))\n'),
                encoding="utf-8")
            if resume:
                (original / "closing.py").write_text('SIGNOFF = "old"\n', encoding="utf-8")
            (original / "README.md").write_text("Text formatting utility\n", encoding="utf-8")
            def git(*args):
                subprocess.run(["git", *args], cwd=original, check=True, timeout=15, capture_output=True)

            git("init", "--template=")
            git("config", "core.autocrlf", "false")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "--no-gpg-sign", "-m", "Existing formatting utility")
            (original / "README.md").write_text("User working change\n", encoding="utf-8")
            source = new_project("registered-test", "Formatting", "Change the greeting and signoff")
            source.update(local_path=str(original), acceptance=["Greeting displays hello", "Signoff displays goodbye"])
            Catalog(ledger).save_project(source)
            onboarding = project_onboarding.review(runner, "registered-test", str(replica), [])
            self.assertIn("README.md", onboarding["baseline"]["raw_worktree_changed"])
            receipt = project_onboarding.create(runner, "registered-test", str(replica), [], digest(onboarding))
            self.assertEqual(receipt["status"], "REPLICA_READY")
            keys = list(cycle_progress.freeze(source["acceptance"], None))
            tests = {"executable": sys.executable, "checks": {
                keys[0]: {"args": ["-B", "check.py", "GREETING"], "stdout": "hello", "inputs": ["check.py"]},
                keys[1]: {"args": ["-B", "check.py", "SIGNOFF"], "stdout": "goodbye", "inputs": ["check.py"]}}}
            params = ("registered-test", os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"], int(time()) + 600,
                      300, 270000, 2, ["formatting.py", "closing.py"] if resume else ["formatting.py"],
                      list(registered_cycle.PERMISSIONS), tests)
            calls = []

            def transport(binary, argv, work, logs, env, **options):
                (logs / "probe.stderr").write_bytes(b"")
                if "login" in argv:
                    return {"stdout": "Logged in using ChatGPT\n", "exitCode": 0, "job_active": 0}
                cycle, phase = logs.name.split("-", 1)
                cycle = int(cycle)
                calls.append((cycle, phase))
                shell = "true" if phase in {"developer", "reviewer"} else "false"
                self.assertIn(f"features.shell_tool={shell}", argv)
                if phase == "developer":
                    if resume and cycle == 2:
                        (work / "closing.py").write_text('SIGNOFF = "goodbye"\n', encoding="utf-8")
                    else:
                        code = 'GREETING = "hello"\nSIGNOFF = "old"\n' if cycle == 1 else (
                            'GREETING = "hello"\nSIGNOFF = "goodbye"\n')
                        (work / "formatting.py").write_text(code, encoding="utf-8")
                message = {"manager-planning": json.dumps({"criterion": keys[cycle - 1], "plan": "Update the text"}),
                           "developer": "Edited", "reviewer": "ACCEPT — the diff meets the criterion.",
                           "manager-final": "CONTINUE — one check remains." if cycle == 1 else "COMPLETED"}[phase]
                events = [{"type": "thread.started", "thread_id": "offline"}, {"type": "turn.started"},
                          {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                          {"type": "turn.completed", "usage": {
                              "input_tokens": 60000 if resume and (cycle, phase) == (2, "developer") else 100,
                              "output_tokens": 5}}]
                return {"stdout": "\n".join(map(json.dumps, events)), "exitCode": 0, "job_active": 0}

            with patch.object(native_cycle, "_observe", side_effect=transport), patch.object(
                    registered_cycle, "_diagnostic", return_value={"request_id": "offline"}):
                scope = registered_cycle.review(runner, *params)
                self.assertEqual(calls, [])
                if resume:
                    stopped = registered_cycle.run(runner, *params, approval=digest(scope))
                    self.assertEqual(stopped["outcome"], "TOKEN_LIMIT")
                    args = ("registered-test", int(time()) + 600, 300, 110000)
                    with self.assertRaisesRegex(Blocked, "No independent"):
                        registered_cycle.review_resume(runner, *args)
                    self.assertEqual(registered_cycle.verify_resume_candidate(runner, "registered-test")["verdict"],
                                     "PASS")
                    approval = registered_cycle.review_resume(runner, *args)
                    self.assertEqual(approval["resume"]["changed"], ["closing.py"])
                    candidate = replica / "closing.py"
                    original_bytes = candidate.read_bytes()
                    candidate.write_bytes(b'SIGNOFF = "tampered"\n')
                    with self.assertRaises(Blocked):
                        registered_cycle.run_resume(runner, *args, approval=digest(approval))
                    candidate.write_bytes(original_bytes)
                    result = registered_cycle.run_resume(runner, *args, approval=digest(approval))
                    self.assertEqual(calls[-2:], [(2, "reviewer"), (2, "manager-final")])
                    with self.assertRaises(Blocked):
                        registered_cycle.run_resume(runner, *args, approval=digest(approval))
                elif os.environ.get("DEVORCHESTRA_REGISTERED_GUI_QA"):
                    result = self._drive_gui(ledger, params)
                else:
                    result = registered_cycle.run(runner, *params, approval=digest(scope))
                self.assertEqual(result["outcome"], "COMPLETED", result)
                with self.assertRaises(Blocked):
                    registered_cycle.run(runner, *params, approval=digest(scope))
            self.assertEqual(len(calls), 8)
            self.assertEqual(result["cycle_count"], 2)
            self.assertEqual(result["criteria_status"], dict.fromkeys(keys, "VERIFIED"))
            self.assertEqual(result["staging_cleanup"]["status"], "REMOVED")
            self.assertIn('"old"', (original / "formatting.py").read_text())
            self.assertEqual((original / "README.md").read_text(), "User working change\n")
            apply, patch_text = original_apply.review(runner, "registered-test", int(time()) + 120)
            self.assertEqual(set(apply["paths"]), set(params[6]))
            self.assertIn('SIGNOFF = "goodbye"', patch_text)

    def _drive_gui(self, ledger, params):
        import tkinter as tk
        from tkinter import ttk

        from devorchestra.gui import CommandCenter
        from devorchestra.projection import ProjectionReader

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def button(widget, label):
            return next(child for child in descendants(widget)
                        if isinstance(child, ttk.Button) and child["text"] == label)

        root = tk.Tk()
        root.withdraw()
        try:
            center = CommandCenter(root, ProjectionReader(ledger))
            center.project.current(center.project_ids.index("registered-test"))
            center.select_project(None)
            center.development_start()
            button(root, "등록 프로젝트 Codex 자동 개발").invoke()
            panel = next(widget for widget in root.winfo_children()
                         if isinstance(widget, tk.Toplevel) and "Codex 개발 세션" in widget.title())
            children = list(descendants(panel))
            entries = [widget for widget in children if isinstance(widget, ttk.Entry)]
            for entry, value in zip(entries, (params[1], "formatting.py", "300", "270000", "2", "45000"),
                                    strict=True):
                entry.delete(0, "end")
                entry.insert(0, value)
            text = next(widget for widget in children if isinstance(widget, tk.Text))
            text.delete("1.0", "end")
            text.insert("1.0", json.dumps(params[-1]))
            for checkbox in (widget for widget in children if isinstance(widget, ttk.Checkbutton)):
                if not checkbox.instate(["selected"]):
                    checkbox.invoke()
            confirmations = []
            settled = []
            deadline = monotonic() + 75

            def approve():
                dialog = next((widget for widget in panel.winfo_children() if isinstance(widget, tk.Toplevel)), None)
                if dialog is not None:
                    confirmations.append(dialog.title())
                    button(dialog, "확인").invoke()
                elif monotonic() < deadline:
                    root.after(30, approve)

            def inspect():
                rows = registered_cycle.history(ledger, "registered-test")
                if rows and rows[-1]["kind"] == "native_inspection_result":
                    detail = [widget for widget in children if isinstance(widget, tk.Text)][-1]
                    if f"{rows[-1]['outcome']} · {rows[-1]['cycle_count']}회" in detail.get("1.0", "end"):
                        settled.append((rows[-1], detail.get("1.0", "end")))
                        root.quit()
                        return
                if monotonic() < deadline:
                    root.after(50, inspect)
                else:
                    root.quit()

            root.after(30, approve)
            root.after(30, button(panel, "세션 검토 및 시작").invoke)
            root.after(60, inspect)
            root.mainloop()
            if settled:
                self.assertEqual(confirmations, ["등록 프로젝트 Codex 세션 승인"])
                self.assertIn("formatting.py", settled[0][1])
                button(panel, "원본 반영 검토").invoke()
                apply_window = next(widget for widget in root.winfo_children()
                                    if isinstance(widget, tk.Toplevel) and "검증된 변경 원본 반영" in widget.title())
                patch_view = next(widget for widget in descendants(apply_window) if isinstance(widget, tk.Text))
                self.assertIn('SIGNOFF = "goodbye"', patch_view.get("1.0", "end"))
                return settled[0][0]
            status = next(widget for widget in children if isinstance(widget, ttk.Label) and widget["textvariable"])
            self.fail(f"Command Center did not render a terminal registered result: "
                      f"{panel.getvar(status['textvariable'])}; confirmations={confirmations}")
        finally:
            root.destroy()
