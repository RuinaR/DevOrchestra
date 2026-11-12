"""Action contract and an offline Windows play loop using an owned WinForms fixture."""
from base64 import b64encode
from contextlib import nullcontext
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from devorchestra import adaptive_play, gui_exe, registered_cycle
from devorchestra.lease import Blocked, execution_slot
from devorchestra.ledger import digest
from devorchestra.replica_evidence import fingerprint, snapshot


def proposal(action="keys", keys=("ENTER",), ms=45, x=0, y=0):
    return {"action": action, "keys": list(keys), "ms": ms, "x": x, "y": y, "observed": "Visible fixture"}


class PlayTests(unittest.TestCase):
    def test_action_shape_and_coordinates_are_enforced(self):
        client = (8, 30, 500, 250)
        self.assertEqual(proposal(), adaptive_play.parse(json.dumps(proposal()), client))
        for row in (proposal(keys=("ALT",)), proposal(keys=("A", "A")), proposal(ms=601),
                    proposal(action="click", keys=(), ms=0, x=500),
                    proposal(action="move", keys=(), ms=0, y=-1), proposal(ms=True)):
            with self.subTest(row=row), self.assertRaises(Blocked):
                adaptive_play.parse(json.dumps(row), client)

    def test_partial_keys_release_on_input_error_and_never_use_stale_geometry(self):
        window = Mock()
        client = (8, 30, 500, 250)
        window.is_foreground.return_value = False
        window.client_in_image.return_value = client
        window.key.side_effect = [None, Blocked("cancelled"), None, None]
        with self.assertRaisesRegex(Blocked, "cancelled"):
            adaptive_play.apply(window, proposal(keys=("W", "D")), client)
        self.assertEqual([(ord("W"), True), (ord("D"), True), (ord("D"), False), (ord("W"), False)],
                         [item.args for item in window.key.call_args_list])
        window.reset_mock()
        window.client_in_image.return_value = (8, 30, 400, 250)
        with self.assertRaisesRegex(Blocked, "geometry"):
            adaptive_play.apply(window, proposal(), client)
        window.key.assert_not_called()
        window.is_foreground.return_value = True
        with self.assertRaisesRegex(Blocked, "foreground"):
            adaptive_play.apply(window, proposal(), client)
        window.key.assert_not_called()

    def test_independent_checks_cannot_substitute_for_opted_in_play(self):
        state = {"criteria": {"C1": "VERIFIED"}, "regressed": []}
        scope = {"adaptive_play": True}
        self.assertEqual("USER_TEST_REQUIRED", registered_cycle._expected_final(scope, "C1", state, "ACCEPT"))
        self.assertEqual("REWORK", registered_cycle._expected_final(scope, "C1", state, "REWORK"))
        state["adaptive_play_actions"] = 3
        self.assertEqual("COMPLETED", registered_cycle._expected_final(scope, "C1", state, "ACCEPT"))

    @unittest.skipUnless(os.environ.get("DEVORCHESTRA_GUI_E2E") == "1", "Owned Windows GUI opt-in")
    def test_three_actual_owned_window_transitions_with_offline_reviewer(self):
        from devorchestra.mxc_host_probe import _observe
        from devorchestra.native_command_probe import _stage_release
        from devorchestra.system_powershell import _system_path, hold_system_powershell

        source = r'''
using System;
using System.Drawing;
using System.Windows.Forms;
class Fixture : Form {
    int stage;
    readonly Label text = new Label { AutoSize = true, Left = 22, Top = 48, Font = new Font("Arial", 20) };
    readonly Color[] colors = {Color.DarkBlue, Color.DarkRed, Color.DarkGreen, Color.DarkMagenta};
    readonly string[] labels = {"Enter to start", "Right to move", "Space to interact", "Fixture complete"};
    readonly int[] keys = {13, 39, 32};
    Fixture() {
        Text = "DevOrchestra owned play fixture"; ClientSize = new Size(430, 210);
        text.ForeColor = Color.White; Controls.Add(text); RefreshStage();
    }
    void RefreshStage() { BackColor = colors[stage]; text.Text = labels[stage]; }
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x100 && stage < keys.Length && m.WParam.ToInt32() == keys[stage]) {
            stage++; RefreshStage();
        }
        base.WndProc(ref m);
    }
    [STAThread] static void Main() { Application.Run(new Fixture()); }
}
'''
        script = gui_exe._BUILD_SCRIPT.replace("__SOURCE__", b64encode(source.encode()).decode("ascii"))
        with execution_slot(), TemporaryDirectory(prefix="owned-play-test-", dir=Path.cwd() / ".ai-local" / "temp") as folder:
            root = Path(folder)
            work, recovery, scratch = (root / name for name in ("work", "recovery", "scratch"))
            build = scratch / "1-gui-build"
            for path in (work, recovery, scratch, build):
                path.mkdir()
            tool = _system_path()
            with (hold_system_powershell(fingerprint(tool, installed_executable=True)["sha256"]) as compiler,
                  patch.object(gui_exe, "BUILD_SCRIPT", script)):
                data = gui_exe.compile_exe(root, compiler, lambda: None, 20)
            executable = build / "app.exe"
            executable.write_bytes(data)

            def logs(label):
                path = scratch / label
                path.mkdir()
                return path

            session = SimpleNamespace(
                runner=SimpleNamespace(ledger=SimpleNamespace(local_directory=lambda *_a, **_k: recovery)),
                fields={"run_id": "b" * 32}, scope={"role_tokens": {"reviewer": 100, "manager-final": 100},
                                                   "tokens": 1000, "model_calls_max": 5,
                                                   "adaptive_play": adaptive_play.approval()},
                owned_verified=True, stop_reason=None, calls=0, usage=0, scratch=scratch, work=work, logs=logs,
                source=lambda: "source", record=Mock(), check=Mock(), check_image=Mock(),
                binary=executable, verify_env=os.environ.copy(), soft_boundary=Mock())
            selected = []

            def choose(_cycle, role, _key, _prompt, **options):
                self.assertEqual("reviewer", role)
                self.assertFalse(options["shell_tools"])
                options["operation_check"]()
                frame = options["image_evidence"][0]
                self.assertEqual(fingerprint(options["images"][0])["sha256"], frame["sha256"])
                self.assertNotIn(frame["sha256"], selected)
                selected.append(frame["sha256"])
                session.calls += 1
                session.usage += 10
                return json.dumps(proposal(keys=[["ENTER", "RIGHT", "SPACE"][len(selected) - 1]])), False

            session.dispatch = choose
            archive = os.environ.get("DEVORCHESTRA_NATIVE_FIXTURE_ARCHIVE")
            if archive:
                session.binary = _stage_release(Path(archive), root / "package")
                home = root / "empty-codex-home"
                home.mkdir()
                session.verify_env = {**session.verify_env, "CODEX_HOME": str(home)}
                launch = nullcontext()
            else:
                launch = patch.object(adaptive_play, "_observe", side_effect=lambda _binary, _argv, *args, **kwargs:
                                      _observe(executable, [], *args, **kwargs))
            with launch:
                frames, actions, result = adaptive_play.run(session, 1, "C1", {
                    "visual_preview": {"status": "CAPTURED", "executable_sha256": fingerprint(executable)["sha256"],
                                       "bundle_sha256": digest(snapshot(build))}},
                    "Exercise the owned test fixture")
            self.assertEqual("STEP_LIMIT", result)
            self.assertEqual(4, len({frame["sha256"] for frame in frames}))
            self.assertEqual(["SCREEN_CHANGED"] * 3, [row["result"] for row in actions])
            self.assertTrue(session.owned_verified)
