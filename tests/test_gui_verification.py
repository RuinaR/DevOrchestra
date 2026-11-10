import json
from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep, time
import unittest
from unittest.mock import Mock, patch

from devorchestra.catalog import Catalog, DEFAULT_BUDGET, new_project
from devorchestra.gui_verification import _Window, verified_image
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.runtime import Runner, native_unresolved


class GuiVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = Ledger.for_project(Path(temporary.name))
        self.runner = Runner(self.ledger)
        self.runner.command("init")

    def register_form(self):
        root = self.ledger.state_boundary.parent
        original, replica = root / "original", root / "replica"
        original.mkdir()
        replica.mkdir()
        descriptor = replica / "gui-test.json"
        example = Path(__file__).resolve().parents[1] / "examples" / "native-form" / descriptor.name
        descriptor.write_bytes(example.read_bytes())
        Catalog(self.ledger).save_setup({
            "project": {**new_project("inventory", "Inventory", "Select an item"), "local_path": str(original)},
            "replica": str(replica), "wait_seconds": 20,
        })
        return descriptor

    def test_registered_scope_denies_manager_stale_descriptor_and_unsupported_exe(self):
        descriptor = self.register_form()
        with self.assertRaisesRegex(Blocked, "denied for this role"):
            self.runner.gui_verification_scope("inventory", registered=True, role="Manager")
        scope = self.runner.gui_verification_scope("inventory", registered=True)
        original = descriptor.read_bytes()
        descriptor.write_bytes(original + b" ")
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.verify_gui_fixture("inventory", digest(scope), registered=True)
        descriptor.write_text('{"format":"existing-exe","exe":"app.exe"}', encoding="utf-8")
        with self.assertRaisesRegex(Blocked, "NOT_VERIFIED"):
            self.runner.gui_verification_scope("inventory", registered=True)
        self.assertEqual([], self.ledger.read())

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Interactive Windows GUI opt-in")
    def test_build_registered_exe_and_dispatch_bound_tool(self):
        from devorchestra.gui_exe import dynamic_gui_call, tool_request

        descriptor = self.register_form()
        name = "InventoryQa.exe"
        form = json.loads(descriptor.read_text(encoding="utf-8"))
        form["scenes"][1]["steps"] = [{"op": "menu", "path": ["Actions", "Browse stock"]}]
        descriptor.write_text(json.dumps(form), encoding="utf-8")
        options = {"registered": True, "exe_name": name}
        with self.assertRaisesRegex(Blocked, "build provenance"):
            self.runner.gui_verification_scope("inventory", **options)
        build = self.runner.gui_exe_build_scope("inventory", name)
        built = self.runner.build_gui_exe("inventory", name, digest(build))
        self.assertEqual("PASS", built["result"])
        scope = self.runner.gui_verification_scope("inventory", **options)
        request = tool_request(scope)
        with self.assertRaisesRegex(Blocked, "execution binding"):
            self.runner.dispatch_gui_tool({**request, "generation": request["generation"] + 1}, digest(scope))
        context = {"thread_id": "thread1", "turn_id": "turn1", "request": request,
                   "owner_scope_sha256": digest(scope), "runner": self.runner, "cancelled": lambda: False}
        params = {"threadId": "thread1", "turnId": "turn1", "callId": "call1",
                  "tool": request["tool"], "arguments": {}}
        with self.assertRaisesRegex(Blocked, "outside the bound turn"):
            dynamic_gui_call({**params, "turnId": "turn2"}, **context)
        reply = dynamic_gui_call(params, **context)
        receipt = self.runner.gui_verification_receipt("inventory")
        self.assertTrue(reply["success"])
        self.assertEqual("WINDOWS_TRUSTED_PROJECT_EXE", receipt["source"])
        self.assertEqual((0, 0, 1), (receipt["exit_code"], receipt["job_active"], receipt["job_total"]))
        self.assertEqual("Selected: QA-024", receipt["observed"]["Selection confirmed"])
        self.assertEqual(3, len(receipt["scenes"]))
        self.assertEqual("Menu Actions › Browse stock", receipt["scenes"][1]["input"])
        self.assertEqual(built["exe_sha256"], receipt["scope"]["exe_sha256"])
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.dispatch_gui_tool(request, digest(scope))

        fresh = self.runner.gui_verification_scope("inventory", **options)

        def cancel_after_launch():
            return any(self.ledger.local_directory("temp").glob("gui-verification-*/windows.json"))

        stopped = self.runner.dispatch_gui_tool(tool_request(fresh), digest(fresh), cancel_after_launch)
        self.assertEqual(("NOT_VERIFIED", "CANCELLED", 0),
                         (stopped["result"], stopped["reason"], stopped["job_active"]))
        exe = descriptor.parent / name
        exe.write_bytes(exe.read_bytes() + b"changed")
        with self.assertRaisesRegex(Blocked, "hash changed"):
            self.runner.gui_verification_scope("inventory", **options)
        self.assertFalse(native_unresolved(self.ledger.read()))

    def test_wrong_approval_and_unresolved_job_block_admission(self):
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.verify_gui_fixture("fixture-a", "invalid")
        self.assertEqual([], self.ledger.read())
        approval = digest(self.runner.gui_verification_scope("fixture-a"))
        self.ledger.update(lambda _events, _tail: {
            "kind": "gui_validation_intent", "approval_id": "lost", "project_id": "fixture-a",
        })
        with self.assertRaisesRegex(Blocked, "ownership requires recovery"):
            self.runner.verify_gui_fixture("fixture-a", approval)
        self.assertTrue(native_unresolved(self.ledger.read()))

    def test_gui_session_rejects_role_descriptor_and_expiry_changes(self):
        descriptor = self.register_form()
        scope = self.runner.gui_verification_scope("inventory", registered=True, role="Developer")
        expires = int(time()) + 300
        session = self.runner.approve_gui_session(scope, None, expires)
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.verify_gui_fixture("inventory", None, registered=True, role="Reviewer",
                                           gui_session_id=session)
        with patch("devorchestra.runtime.time", return_value=expires):
            with self.assertRaisesRegex(Blocked, "expired"):
                self.runner.verify_gui_fixture("inventory", None, registered=True, role="Developer",
                                               gui_session_id=session)
        descriptor.write_bytes(descriptor.read_bytes() + b" ")
        with self.assertRaisesRegex(Blocked, "scope changed"):
            self.runner.verify_gui_fixture("inventory", None, registered=True, role="Developer",
                                           gui_session_id=session)
        self.assertEqual(["gui_session_approved"], [event["kind"] for event in self.ledger.read()])

    def test_window_input_is_addressed_and_rejects_reused_pid_and_replaced_window(self):
        window = object.__new__(_Window)
        window.handles, window.pid, window.thread = {"root": 100, "button": 200}, 123, 7
        window.check, window.send = Mock(), Mock(return_value=1)
        window.ancestor = Mock(return_value=100)
        window.alive = Mock(return_value=True)

        def owner(_target, pid):
            pid._obj.value = 123
            return 7

        window.owner = Mock(side_effect=owner)
        window.click("button")
        self.assertEqual((200, 0x00F5), window.send.call_args.args[:2])
        window.send.reset_mock()
        window.alive.return_value = False  # A replacement window reports the same PID.
        with self.assertRaisesRegex(Blocked, "ownership changed"):
            window.click("button")
        window.alive.return_value = True
        window.ancestor.return_value = 300
        with self.assertRaisesRegex(Blocked, "ownership changed"):
            window.click("button")
        window.send.assert_not_called()

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Interactive Windows GUI opt-in")
    def test_separate_same_executable_survives_success_cancel_and_timeout(self):
        from devorchestra.system_powershell import _system_path

        scope = self.runner.gui_verification_scope("fixture-a")
        powershell = _system_path()
        self.assertEqual(str(powershell), scope["exe"])
        root = self.ledger.state_boundary.parent
        # The unrelated fixture is started before the Controller run and exits on pipe EOF.
        with subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command",
             "[IO.File]::WriteAllText('ready.txt', 'protected'); [void][Console]::ReadLine()"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ) as protected:
            try:
                ready = root / "ready.txt"
                deadline = monotonic() + 8
                while not ready.is_file() and monotonic() < deadline and protected.poll() is None:
                    sleep(0.02)
                self.assertEqual("protected", ready.read_text())
                receipt = self.runner.verify_gui_fixture("fixture-a", digest(scope))
                self.assertEqual(("PASS", 0), (receipt["result"], receipt["job_active"]))
                self.assertIsNone(protected.poll())

                def launched():
                    return any(self.ledger.local_directory("temp").glob("gui-verification-*/windows.json"))

                stopped = self.runner.verify_gui_fixture("fixture-a", digest(scope), launched)
                self.assertEqual(("CANCELLED", 0), (stopped["reason"], stopped["job_active"]))
                self.assertIsNone(protected.poll())

                Catalog(self.ledger).save_limits({**DEFAULT_BUDGET, "seconds": 1}, 0)
                short = self.runner.gui_verification_scope("fixture-a")

                def stall():
                    if launched():
                        sleep(1.05)
                    return False

                timed_out = self.runner.verify_gui_fixture("fixture-a", digest(short), stall)
                self.assertEqual(("TIME_LIMIT", 0), (timed_out["reason"], timed_out["job_active"]))
                self.assertIsNone(protected.poll())
            finally:
                protected.stdin.close()
                try:
                    protected.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    protected.kill()
                    protected.wait(timeout=3)

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Interactive Windows GUI opt-in")
    def test_registered_project_runs_independent_role_scopes(self):
        descriptor = self.register_form()
        original = descriptor.read_bytes()
        for role in ("Developer", "Reviewer", "Verifier"):
            with self.subTest(role=role):
                options = {"registered": True, "role": role}
                scope = self.runner.gui_verification_scope("inventory", **options)
                receipt = self.runner.verify_gui_fixture("inventory", digest(scope), **options)
                self.assertEqual("PASS", receipt["result"], receipt["reason"])
                self.assertEqual("Selected: QA-024", receipt["observed"]["Selection confirmed"])
                self.assertEqual((24, 0, role), (receipt["observed"]["list_top_index"],
                                                  receipt["job_active"], receipt["role"]))
                self.assertEqual(3, len(receipt["scenes"]))
                self.assertEqual(receipt, self.runner.gui_verification_receipt("inventory", role="Manager"))
                self.assertFalse(native_unresolved(self.ledger.read()))
        options = {"registered": True, "role": "Developer"}
        scope = self.runner.gui_verification_scope("inventory", **options)
        session = self.runner.approve_gui_session(scope, None, int(time()) + 300)
        for _ in range(3):
            receipt = self.runner.verify_gui_fixture("inventory", None, gui_session_id=session, **options)
            self.assertEqual(("PASS", session, 0),
                             (receipt["result"], receipt["gui_session_id"], receipt["job_active"]))
        with self.assertRaisesRegex(Blocked, "exhausted"):
            self.runner.verify_gui_fixture("inventory", None, gui_session_id=session, **options)
        self.assertFalse(native_unresolved(self.ledger.read()))
        self.assertEqual(original, descriptor.read_bytes())
        self.assertEqual([descriptor], list(descriptor.parent.iterdir()))
        self.assertFalse(list(self.ledger.local_directory("temp").iterdir()))

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Interactive Windows GUI opt-in")
    def test_actual_gui_receipt_and_cancellation(self):
        approval = digest(self.runner.gui_verification_scope("fixture-a"))
        receipt = self.runner.verify_gui_fixture("fixture-a", approval)
        self.assertEqual("PASS", receipt["result"], receipt["reason"])
        self.assertEqual(("CLOSED", 0, 0, True), (receipt["job_handles"], receipt["job_active"],
                                                    receipt["exit_code"], receipt["graceful_close"]))
        self.assertEqual(3, len(receipt["scenes"]))
        self.assertEqual(30, receipt["observed"]["list_top_index"])
        directory = self.ledger.local_directory("logs") / receipt["artifact_dir"]
        for item in [receipt["comparison"], *(scene[key] for scene in receipt["scenes"]
                                               for key in ("original", "processed"))]:
            self.assertEqual(item, verified_image(directory / item["file"], item["sha256"])[1])
        events = self.ledger.read()
        self.assertEqual(receipt["approval_id"], events[-2]["approval_id"])
        self.assertEqual(receipt, events[-1])
        self.assertFalse(native_unresolved(events))

        def cancel_after_launch():
            return any(self.ledger.local_directory("temp").glob("gui-verification-*/windows.json"))

        stopped = self.runner.verify_gui_fixture("fixture-a", approval, cancel_after_launch)
        self.assertEqual(("NOT_VERIFIED", "CANCELLED", 0, "CLOSED"),
                         (stopped["result"], stopped["reason"], stopped["job_active"], stopped["job_handles"]))
        self.assertFalse(native_unresolved(self.ledger.read()))

        with patch("devorchestra.gui_verification._exercise", side_effect=Blocked("QA_INJECTED_UI_FAILURE")):
            failed = self.runner.verify_gui_fixture("fixture-a", approval)
        self.assertEqual(("FAIL", "QA_INJECTED_UI_FAILURE", 0, True),
                         (failed["result"], failed["reason"], failed["job_active"], failed["graceful_close"]))
        self.assertFalse(native_unresolved(self.ledger.read()))

        Catalog(self.ledger).save_limits({**DEFAULT_BUDGET, "seconds": 1}, 0)
        short_approval = digest(self.runner.gui_verification_scope("fixture-a"))

        def stall_after_launch():
            if any(self.ledger.local_directory("temp").glob("gui-verification-*/windows.json")):
                sleep(1.05)
            return False

        timeout = self.runner.verify_gui_fixture("fixture-a", short_approval, stall_after_launch)
        self.assertEqual(("NOT_VERIFIED", "TIME_LIMIT", 0, "CLOSED"),
                         (timeout["result"], timeout["reason"], timeout["job_active"], timeout["job_handles"]))
        self.assertFalse(native_unresolved(self.ledger.read()))
        self.assertFalse(list(self.ledger.local_directory("temp").iterdir()))
