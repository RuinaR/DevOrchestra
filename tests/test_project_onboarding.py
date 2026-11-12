import os
from pathlib import Path
from shutil import copyfile
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from devorchestra import codex_setup, project_onboarding, registered_cycle, replica_readiness
from devorchestra.catalog import Catalog, new_project
from devorchestra.ledger import Ledger, digest
from devorchestra.lease import Blocked
from devorchestra.project_onboarding import _protected, _records, _relative
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner


class ProjectOnboardingTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"), "Requires signed local Windows tools")
    def test_restart_reuses_deleted_path_and_keeps_old_audit(self):
        with TemporaryDirectory(prefix="restart-test-", dir=Path.cwd() / ".ai-local/temp") as folder:
            work = Path(folder)
            local = work / ".ai-local"
            ledger = Ledger(local / "state/p1.sqlite3", local_root=local)
            ledger.initialize()
            archive = codex_setup.archive_path(ledger)
            archive.parent.mkdir(parents=True, exist_ok=True)
            copyfile(os.environ["DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE"], archive)
            original, replica = work / "original", work / "development-copy"
            original.mkdir()
            (original / "README.md").write_text("Original baseline\n", encoding="utf-8")
            for args in (("init", "--template="), ("add", "README.md"),
                         ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                          "commit", "--no-gpg-sign", "-m", "Original baseline")):
                subprocess.run(["git", *args], cwd=original, timeout=15, check=True, capture_output=True)
            project = "restart-fixture"
            data = new_project(project, "Restart fixture", "Keep the goal and start with a clean replica")
            data.update(local_path=str(original), acceptance=["Preserve original bytes"])
            Catalog(ledger).save_project(data)
            runner = Runner(ledger)
            scope = project_onboarding.review(runner, project, str(replica), [])
            first = project_onboarding.create(runner, project, str(replica), [], digest(scope))
            self.assertEqual(project_onboarding.restart_destination(ledger, project), str(work / "development-copy-2"))
            events, tail = ledger.snapshot()
            run = {"project_id": project, "run_id": "restart-old", "request_id": "restart-old",
                   "probe_kind": registered_cycle.KIND, "state": "DIAGNOSTIC"}
            size, checksum = ledger.append(len(events), tail, {**run, "kind": "native_inspection_intent",
                                           "scope": {"onboarding_sha256": digest(first), "seconds": 300,
                                                     "tokens": 100000, "criteria": {}}})
            ledger.append(size, checksum, {**run, "kind": "native_inspection_result", "outcome": "COMPLETED",
                                          "owned_tree": "DRAINED"})
            self.assertIsNotNone(ProjectionReader(ledger).read(project)["registered_progress"])
            (replica / "old-work.txt").write_text("Keep the previous work\n", encoding="utf-8")
            retired = work / "previous-development-copy"
            self.assertTrue(replica.resolve().is_relative_to(work.resolve()))
            self.assertTrue(retired.resolve().is_relative_to(work.resolve()))
            replica.rename(retired)
            with self.assertRaisesRegex(Blocked, "복제본 폴더가 없습니다"):
                replica_readiness.baseline(ledger, project)
            self.assertEqual(project_onboarding.restart_destination(ledger, project), str(replica))
            if os.environ.get("DEVORCHESTRA_REGISTERED_GUI_QA"):
                self._restart_from_gui(ledger, project)
            else:
                restart = project_onboarding.review(runner, project, str(replica), [], restart=True)
                project_onboarding.create(runner, project, str(replica), [], digest(restart), restart=True)
            source, current, frozen, second = replica_readiness.baseline(ledger, project)
            self.assertEqual(current, replica)
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertTrue(replica_readiness.original_matches(source, frozen))
            self.assertEqual((replica / "README.md").read_bytes(), (original / "README.md").read_bytes())
            self.assertTrue((retired / "old-work.txt").is_file())
            self.assertFalse((replica / "old-work.txt").exists())
            self.assertEqual(Catalog(ledger).read()["projects"][project]["data"], data)
            self.assertIsNone(ProjectionReader(ledger).read(project)["registered_progress"])
            self.assertEqual(registered_cycle.history(ledger, project, current_replica=True), [])
            self.assertEqual(registered_cycle.history(ledger, project)[-1]["outcome"], "COMPLETED")

    def _restart_from_gui(self, ledger, project):
        import tkinter as tk
        from tkinter import ttk

        from devorchestra.gui import CommandCenter

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def button(widget, text):
            return next(child for child in descendants(widget) if isinstance(child, ttk.Button)
                        and child["text"] == text)

        root = tk.Tk()
        try:
            center = CommandCenter(root, ProjectionReader(ledger))
            center.project.current(center.project_ids.index(project))
            center.select_project(None)
            center.navigate("projects")
            button(root, "처음부터 다시 시작").invoke()
            panel = next(widget for widget in root.winfo_children()
                         if isinstance(widget, tk.Toplevel) and widget.title() == "처음부터 다시 시작")
            requested, complete = [], []
            deadline = monotonic() + 150

            def tick():
                if monotonic() >= deadline:
                    root.quit()
                    return
                root.after(40, tick)
                dialog = next((widget for widget in panel.winfo_children() if isinstance(widget, tk.Toplevel)
                               and widget.title() == "새 복제본으로 다시 시작"), None)
                if dialog:
                    button(dialog, "확인").invoke()
                elif not requested:
                    create = button(panel, "승인하고 새 복제본 만들기")
                    if create.instate(["!disabled"]):
                        requested.append(True)
                        create.invoke()
                elif any(isinstance(widget, ttk.Button) and widget["text"] == "새 개발 세션 설정"
                         for widget in descendants(panel)):
                    complete.append(True)
                    root.quit()

            root.after(40, tick)
            root.mainloop()
            self.assertTrue(complete, "Restart dialog did not finish creating the replica")
        finally:
            root.destroy()

    def test_private_paths_are_distinct_from_explicit_examples(self):
        for path in ("private/", "data/account.sqlite3", "data/account.sqlite3-wal", "src/.env.local", "src/appsettings.json",
                     "engine/models/weights.dat", "keys/signing.pem"):
            with self.subTest(path=path):
                self.assertTrue(_protected(path))
        self.assertFalse(_protected("config/runtime.local.example.json"))

    def test_path_boundary_rejects_aliases(self):
        for path in ("../outside", "src/a:stream", "src/NUL.txt", "src/a/../b", "src/a\\b", "src/a."):
            with self.subTest(path=path), self.assertRaises(Blocked):
                _relative(path)

    def test_unmerged_index_is_not_admitted(self):
        oid = "a" * 40
        self.assertEqual({"src/app.py": ("100644", oid)}, _records(f"100644 {oid} 0\tsrc/app.py\0"))
        with self.assertRaisesRegex(Blocked, "unresolved"):
            _records(f"100644 {oid} 2\tsrc/app.py\0")
