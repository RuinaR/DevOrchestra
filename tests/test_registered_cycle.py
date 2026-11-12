"""Offline integration across the real registry, Git onboarding, MXC and approval."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
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
    def test_shutdown_reservation_can_be_cancelled_until_checkpoint_is_claimed(self):
        pause = registered_cycle.PauseControl()
        pause.change("request")
        self.assertEqual(pause.state, "SCHEDULED")
        pause.change("cancel")
        self.assertFalse(pause.claim())
        pause.change("request")
        self.assertTrue(pause.claim())
        pause.change("cancel")
        self.assertEqual(pause.state, "SAVING")

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
            else:
                (original / "greeting.txt").write_text("old\n", encoding="ascii")
                (original / "closing.txt").write_text("old\n", encoding="ascii")
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
            if not resume:
                from devorchestra.mxc_launcher import _known_folder

                tests = {"executable": str(_known_folder(0x24) / "System32" / "cmd.exe"), "checks": {
                    keys[0]: {"args": ["/d", "/q", "/c", "type", "greeting.txt"], "stdout": "hello", "inputs": []},
                    keys[1]: {"args": ["/d", "/q", "/c", "type", "closing.txt"], "stdout": "goodbye", "inputs": []}}}
            params = ("registered-test", os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"], int(time()) + 600,
                      300, 270000, 2, ["formatting.py", "closing.py"] if resume else ["greeting.txt", "closing.txt"],
                      list(registered_cycle.PERMISSIONS), tests)
            calls = []
            developer_observed = Event() if not resume and os.environ.get("DEVORCHESTRA_REGISTERED_GUI_QA") else None

            def transport(binary, argv, work, logs, env, **options):
                (logs / "probe.stderr").write_bytes(b"")
                if "login" in argv:
                    return {"stdout": "Logged in using ChatGPT\n", "exitCode": 0, "job_active": 0}
                cycle, phase = logs.name.split("-", 1)
                cycle = int(cycle)
                calls.append((cycle, phase))
                shell = "true" if phase in {"developer", "reviewer"} else "false"
                self.assertIn(f"features.shell_tool={shell}", argv)
                command_events = []
                if phase == "developer":
                    if cycle == 1 and developer_observed is not None:
                        command = {"id": "fixture-command", "type": "command_execution",
                                   "command": "python -m fixture PRIVATE_FIXTURE_ARGUMENT"}
                        command_events.append({"type": "item.started", "item": command})
                        partial = [{"type": "thread.started", "thread_id": "offline"},
                                   {"type": "turn.started"}, *command_events]
                        (logs / "probe.stdout").write_text("\n".join(map(json.dumps, partial)) + "\n", encoding="utf-8")
                        options["check"]()
                        self.assertTrue(developer_observed.wait(15), "Main window never showed the running command")
                        command_events.append({"type": "item.completed", "item": {**command, "exit_code": 0}})
                    if not resume:
                        filename, value = ("greeting.txt", "hello") if cycle == 1 else ("closing.txt", "goodbye")
                        (work / filename).write_text(value + "\n", encoding="ascii")
                    elif cycle == 2:
                        (work / "closing.py").write_text('SIGNOFF = "goodbye"\n', encoding="utf-8")
                    else:
                        code = 'GREETING = "hello"\nSIGNOFF = "old"\n' if cycle == 1 else (
                            'GREETING = "hello"\nSIGNOFF = "goodbye"\n')
                        (work / "formatting.py").write_text(code, encoding="utf-8")
                message = {"manager-planning": json.dumps({"criterion": keys[cycle - 1], "plan": "Update the text"}),
                           "developer": "Edited", "reviewer": "ACCEPT — the diff meets the criterion.",
                           "manager-final": "CONTINUE — one check remains." if cycle == 1 else "COMPLETED"}[phase]
                events = [{"type": "thread.started", "thread_id": "offline"}, {"type": "turn.started"},
                          *command_events,
                          {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                          {"type": "turn.completed", "usage": {
                              "input_tokens": 60000 if resume and (cycle, phase) == (2, "developer") else 100,
                              "output_tokens": 5}}]
                return {"stdout": "\n".join(map(json.dumps, events)), "exitCode": 0, "job_active": 0}

            with patch.object(native_cycle, "_observe", side_effect=transport), patch.object(
                    registered_cycle, "_diagnostic", return_value={"request_id": "offline"}):
                scope = registered_cycle.review(runner, *params)
                preview = registered_cycle.review(runner, *params[:-1],
                                                  {"mode": registered_cycle.BASELINE_VERIFIER},
                                                  visual_review=True)
                self.assertEqual(preview["visual_preview"], "scratch-windows-exe")
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
                    result = self._drive_gui(ledger, params, developer_observed)
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
            if not resume:
                self.assertEqual((original / "greeting.txt").read_text(), "old\n")
                self.assertEqual((original / "closing.txt").read_text(), "old\n")
            self.assertEqual((original / "README.md").read_text(), "User working change\n")
            apply, patch_text = original_apply.review(runner, "registered-test", int(time()) + 120)
            self.assertEqual(set(apply["paths"]), set(params[6]))
            self.assertIn('SIGNOFF = "goodbye"' if resume else "+goodbye", patch_text)

    def _drive_gui(self, ledger, params, developer_observed):
        import tkinter as tk
        from tkinter import ttk

        from devorchestra.gui import ACTIVE_AGENT_BORDER, TILE, CommandCenter
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
            root.deiconify()
            center.project.current(center.project_ids.index("registered-test"))
            center.select_project(None)
            center.development_start()
            button(root, "등록 프로젝트 Codex 자동 개발").invoke()
            panel = next(widget for widget in root.winfo_children()
                         if isinstance(widget, tk.Toplevel) and "Codex 개발 세션" in widget.title())
            children = list(descendants(panel))
            entries = [widget for widget in children if isinstance(widget, ttk.Entry)]
            for entry, value in zip(entries, (params[1], ",".join(params[6]), "300", "270000", "2", "45000",
                                              "60000", "55000", "45000"),
                                    strict=True):
                entry.delete(0, "end")
                entry.insert(0, value)
            text = next(widget for widget in children if isinstance(widget, tk.Text))
            text.delete("1.0", "end")
            text.insert("1.0", json.dumps(params[-1]))
            from devorchestra.gui_registered_cycle import LABELS

            for checkbox in (widget for widget in children
                             if isinstance(widget, ttk.Checkbutton) and widget["text"] in LABELS.values()):
                if not checkbox.instate(["selected"]):
                    checkbox.invoke()
            confirmations = []
            settled = []
            deadline = monotonic() + 120
            paused = []

            def approve():
                dialog = next((widget for widget in descendants(root) if isinstance(widget, tk.Toplevel)
                               and widget.title() in {"등록 프로젝트 Codex 세션 승인", "체크포인트에서 이어서 개발"}), None)
                if dialog is not None:
                    confirmations.append(dialog.title())
                    button(dialog, "확인").invoke()
                if len(confirmations) < 2 and monotonic() < deadline:
                    root.after(30, approve)

            def inspect():
                heading = center.registered_status.cget("text")
                detail = center.registered_detail.cget("text")
                if (not developer_observed.is_set() and not panel.winfo_exists() and center.page == "operations"
                        and "1사이클 · Developer" in heading
                        and "실행 중" in center.phases[1][1].cget("text")
                        and center.card_frames["Developer"].cget("highlightbackground") == ACTIVE_AGENT_BORDER
                        and "도구 명령 실행 중 · python" in center.cards["Developer"][1].cget("text")
                        and "Manager가 전달한 계획 · Update the text" in center.cards["Developer"][1].cget("text")
                        and "확정 사용량 105" in detail and center.registered_cancel.winfo_ismapped()):
                    center.registered_history_button.invoke()
                    history = center.registered_history[2].get("1.0", "end")
                    if "도구 명령 실행 중 · python" in history and "PRIVATE_FIXTURE_ARGUMENT" not in history:
                        controls = center.shutdown_buttons
                        controls["request"].invoke()
                        if controls["cancel"].instate(["!disabled"]):
                            controls["cancel"].invoke()
                            if controls["request"].instate(["!disabled"]):
                                controls["request"].invoke()
                                controls["finalize"].invoke()
                                if "체크포인트를 저장합니다" in center.shutdown_label.cget("text"):
                                    developer_observed.set()
                progress = center.view.get("registered_progress") if center.view else None
                if progress and progress["end"] and center.registered_job["settled"]:
                    if (not paused and progress["end"]["outcome"] == registered_cycle.MANUAL_CHECKPOINT
                            and center.shutdown_buttons["resume"].instate(["!disabled"])):
                        paused.append(progress["end"])
                        center.shutdown_buttons["resume"].invoke()
                    if "완료 · 2사이클" in heading and "closing.txt" in detail:
                        settled.append(progress["end"])
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
                self.assertEqual(confirmations, ["등록 프로젝트 Codex 세션 승인", "체크포인트에서 이어서 개발"])
                self.assertEqual(paused[0]["cycle_count"], 1)
                self.assertEqual(paused[0]["owned_tree"], "DRAINED")
                self.assertTrue(developer_observed.is_set())
                self.assertFalse(panel.winfo_exists())
                self.assertTrue(all(frame.cget("highlightbackground") == TILE for frame in center.card_frames.values()))
                history = center.registered_history[2].get("1.0", "end")
                self.assertIn("변경 파일 · closing.txt", history)
                self.assertIn("세션 종료 · 완료", history)
                center.original_apply_panel("registered-test")
                apply_window = next(widget for widget in root.winfo_children()
                                    if isinstance(widget, tk.Toplevel) and "검증된 변경 원본 반영" in widget.title())
                patch_view = next(widget for widget in descendants(apply_window) if isinstance(widget, tk.Text))
                self.assertIn("+goodbye", patch_view.get("1.0", "end"))
                return settled[0]
            self.fail(f"Command Center did not render a terminal registered result: "
                      f"{center.registered_status.cget('text')}; confirmations={confirmations}")
        finally:
            developer_observed.set()
            if center.registered_job and not center.registered_job["future"].done():
                center.cancel_registered_run()
                try:
                    center.registered_job["future"].result(timeout=15)
                except (RuntimeError, OSError, ValueError):
                    pass
            root.destroy()
