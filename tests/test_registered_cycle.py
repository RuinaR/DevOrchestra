"""Offline integration across the real registry, Git onboarding, MXC and approval."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, time
import unittest
from unittest.mock import patch

from devorchestra import cycle_progress, native_cycle, original_apply, project_onboarding, registered_cycle
from devorchestra.catalog import Catalog, new_project
from devorchestra.ledger import Ledger, digest
from devorchestra.lease import Blocked
from devorchestra.runtime import Runner
from devorchestra.native_developer_trial import _check_dedicated_config


class RegisteredTrustTests(unittest.TestCase):
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
                'import sys\nimport formatting\nprint(getattr(formatting, sys.argv[1]))\n', encoding="utf-8")
            (original / "README.md").write_text("Text formatting utility\n", encoding="utf-8")
            def git(*args):
                subprocess.run(["git", *args], cwd=original, check=True, timeout=15, capture_output=True)

            git("init", "--template=")
            git("config", "core.autocrlf", "false")
            git("add", "formatting.py", "check.py", "README.md")
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
                      300, 270000, 2, ["formatting.py"], list(registered_cycle.PERMISSIONS), tests)
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
                    code = 'GREETING = "hello"\nSIGNOFF = "old"\n' if cycle == 1 else (
                        'GREETING = "hello"\nSIGNOFF = "goodbye"\n')
                    (work / "formatting.py").write_text(code, encoding="utf-8")
                message = {"manager-planning": json.dumps({"criterion": keys[cycle - 1], "plan": "Update the text"}),
                           "developer": "Edited", "reviewer": "ACCEPT — the diff meets the criterion.",
                           "manager-final": "CONTINUE — one check remains." if cycle == 1 else "COMPLETED"}[phase]
                events = [{"type": "thread.started", "thread_id": "offline"}, {"type": "turn.started"},
                          {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                          {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5}}]
                return {"stdout": "\n".join(map(json.dumps, events)), "exitCode": 0, "job_active": 0}

            with patch.object(native_cycle, "_observe", side_effect=transport), patch.object(
                    registered_cycle, "_diagnostic", return_value={"request_id": "offline"}):
                scope = registered_cycle.review(runner, *params)
                self.assertEqual(calls, [])
                if os.environ.get("DEVORCHESTRA_REGISTERED_GUI_QA"):
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
            self.assertEqual(set(apply["paths"]), {"formatting.py"})
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
