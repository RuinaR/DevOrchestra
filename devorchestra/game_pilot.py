"""Offline evidence for the existing Godot pilot's frozen bootstrap."""
import ctypes as c
from ctypes import wintypes as w
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from .gui_verification import _Window, _small_file
from .job_probe import _require
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import PROFILE, _args, _observe
from .mxc_launcher import _known_folder
from .mxc_owned_gui import _capture, _job_pids
from .native_command_probe import ARCHIVE_SHA256, _stage_release, _staging
from .native_developer_trial import _read_observation
from .project_onboarding import _relative
from .replica_evidence import fingerprint, snapshot
from .replica_readiness import baseline, original_matches
from .system_powershell import _api as signature


PROJECT = "native-game-pilot"
ENGINE = "Godot_v4.7.2-stable_win64"
ENGINE_HASHES = {
    ENGINE + ".exe": "ab1824f85bfd8e0e4128182c000c4003a3e042245b2967848d089b2a04b22424",
    ENGINE + "_console.exe": "c8f0a6bc45a19b33541501e57f6f7cd972ab18453743266339d495cbbe846643",
}
VOLUME_MESSAGE = ('ERROR: Method/function failed. Returning: ""',
                  'at: get_filesystem_type (drivers/windows/dir_access_windows.cpp:412)')
KIND = "GODOT_PILOT_PREFLIGHT"
PACK_PRESET = """[preset.0]
name="Windows Desktop"
platform="Windows Desktop"
runnable=true
export_filter="all_resources"
include_filter=""
exclude_filter=""

[preset.0.options]
binary_format/architecture="x86_64"
"""


def _volume_messages(stderr):
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if len(lines) % 2 or any(tuple(lines[i:i + 2]) != VOLUME_MESSAGE for i in range(0, len(lines), 2)):
        raise Blocked("Godot produced an unexpected import diagnostic")
    return len(lines) // 2


def _profile(work, data, engine):
    grants = {":root": "deny", ":minimal": "read", str(work): "write",
              str(data): "write", str(engine): "read"}
    entries = "\n".join(f"{json.dumps(path)} = {json.dumps(access)}" for path, access in grants.items())
    return (f'default_permissions = "{PROFILE}"\napproval_policy = "never"\n'
            f'[permissions.{PROFILE}.filesystem]\n{entries}\n'
            f'[permissions.{PROFILE}.network]\nenabled = false\n')


def _engine(replica):
    folder = replica.parent / "tools" / "godot-4.7.2"
    check_path(folder)
    if not folder.is_dir() or {path.name for path in folder.iterdir()} != ENGINE_HASHES.keys():
        raise Blocked("The pinned project-owned Godot installation is incomplete or has extra files")
    for name, expected in ENGINE_HASHES.items():
        if fingerprint(folder / name, 256 * 1024**2)["sha256"] != expected:
            raise Blocked("Godot executable identity changed")
    return folder / (ENGINE + "_console.exe")


def _copy_source(replica, work, image):
    work.mkdir()
    for name, identity in image["files"].items():
        relative = _relative(name)
        source = replica / relative
        target = work / relative
        check_path(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        if fingerprint(target) != identity:
            raise Blocked("Frozen game input changed during staging")


def _inspect_window(api, job, deadline, check, manifest, image_path, workers):
    def current():
        check()
        if monotonic() >= deadline:
            raise Blocked("Game window did not settle before its deadline")

    while not manifest.is_file() or not manifest.stat().st_size:
        current()
        if not _job_pids(api, job):
            raise Blocked("Godot exited before producing an owned window")
        sleep(0.04)
    pid, hwnd = json.loads(_small_file(manifest, 4096))
    if type(pid) is not int or type(hwnd) is not int or pid not in _job_pids(api, job):
        raise Blocked("Godot window is outside the owned Job")
    process = _require(signature(api, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x101000, False, pid))
    member = signature(api, "IsProcessInJob", w.BOOL, w.HANDLE, w.HANDLE, c.POINTER(w.BOOL))
    try:
        def alive():
            value = w.BOOL()
            return (api.WaitForSingleObject(process, 0) == 258
                    and bool(member(process, job, c.byref(value)) and value.value))

        window = _Window({"root": hwnd}, {"root"}, pid, current, alive)
        try:
            return _capture(window, image_path, workers)
        finally:
            if alive():
                window.close()
    finally:
        _require(api.CloseHandle(process))


def _probe_script(assets, manifest):
    return f'''extends SceneTree

func _initialize():
	call_deferred("_check_bootstrap")

func _check_bootstrap():
	for path in {json.dumps(["res://" + name for name in assets])}:
		var texture := load(path) as Texture2D
		if texture == null or texture.get_width() < 1 or texture.get_height() < 1:
			quit(3)
			return
	var scene := load("res://scenes/bootstrap.tscn") as PackedScene
	if scene == null:
		quit(4)
		return
	root.add_child(scene.instantiate())
	if DisplayServer.get_name() == "headless":
		print("PILOT_TEXTURES_AND_SCENE_OK")
		quit()
		return
	await RenderingServer.frame_post_draw
	var handle := DisplayServer.window_get_native_handle(DisplayServer.WINDOW_HANDLE)
	var file := FileAccess.open({json.dumps(manifest.as_posix())}, FileAccess.WRITE)
	if file == null or handle <= 0:
		quit(5)
		return
	file.store_string(JSON.stringify([OS.get_process_id(), handle]))
	file.close()
'''


def run(runner, archive, *, cancelled=lambda: False, capture=False):
    """Verify the original onboarding snapshot in owned, offline staging."""
    from .runtime import require_admission

    ledger = runner.ledger
    archive = Path(archive)
    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        original, replica, frozen, onboarding = baseline(ledger, PROJECT)
        image = snapshot(replica)
        if digest(image) != onboarding["replica_sha256"] or not original_matches(original, frozen):
            raise Blocked("Game pilot no longer matches its onboarding snapshot")
        engine = _engine(replica)
        assets = sorted(name for name in image["files"] if name.startswith("assets/") and name.endswith(".png"))
        if not assets or cancelled():
            raise Blocked("Frozen PNG assets are unavailable or the check was cancelled")
        run_id = uuid4().hex
        common = {"project_id": PROJECT, "probe_kind": KIND, "run_id": run_id,
                  "request_id": run_id, "state": "DIAGNOSTIC"}
        evidence = None
        workers = []
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(archive, lab / "package")
            work, data, home = (lab / name for name in ("work", "data", "config"))
            _copy_source(replica, work, image)
            data.mkdir()
            home.mkdir()
            (home / "config.toml").write_text(_profile(work, data, engine.parent), encoding="utf-8")
            manifest = data / "window.json"
            script = work / "devorchestra-bootstrap-probe.gd"
            with script.open("x", encoding="utf-8") as stream:
                stream.write(_probe_script(assets, manifest))
            with (work / "export_presets.cfg").open("x", encoding="utf-8") as stream:
                stream.write(PACK_PRESET)
            windows = _known_folder(0x24)
            env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                   "PATH": str(windows / "System32"), "LOCALAPPDATA": str(data), "APPDATA": str(data),
                   "HOME": str(data), "USERPROFILE": str(data), "TEMP": str(data), "TMP": str(data),
                   "CODEX_HOME": str(home)}
            count, fence = ledger.append(len(events), head, {
                **common, "kind": "native_inspection_intent", "workspace": lab.name,
                "source_sha256": digest(image), "engine_sha256": ENGINE_HASHES,
                "archive_sha256": ARCHIVE_SHA256, "network": "OFF", "capture": capture}, admission=require_admission)
            cleanup["preserve"] = True

            def check():
                if cancelled():
                    raise Blocked("Game pilot check cancelled")
                runner._check_diagnostic_fence(count, fence)

            def observe(name, args, interact=None, directory=work):
                logs = lab / name
                logs.mkdir()
                check()
                result = _observe(binary, _args(engine, "--path", str(directory), *args), directory, logs, env,
                                  seconds=45, memory=1024**3, check=check, output_limit=65536,
                                  encoding="utf-8", interact=interact)
                stderr = _read_observation(logs / "probe.stderr", 65537)
                if len(stderr) > 65536:
                    raise Blocked("Godot diagnostics exceed the observation limit")
                steps[name] = {"exit_code": result["exitCode"], "job_active": result["job_active"],
                               "stdout_sha256": sha256(result["stdout"].encode("utf-8")).hexdigest(),
                               "stderr_sha256": sha256(stderr).hexdigest()}
                if result["exitCode"] or result["job_active"]:
                    raise Blocked(f"Godot {name} did not complete successfully")
                return result["stdout"], stderr.decode("utf-8")

            steps = {}
            result = {"outcome": "BLOCKED", "owned_tree": "DRAINED", "gui": "NOT_VALIDATED",
                      "export": "NOT_VALIDATED", "criteria_verified": 0, "steps": steps}
            try:
                _, stderr = observe("import", ["--headless", "--import"])
                result["volume_diagnostics"] = _volume_messages(stderr)
                imported = work / ".godot" / "imported"
                check_path(imported)
                for name in assets:
                    matches = list(imported.glob(Path(name).name + "-*.ctex"))
                    if len(matches) != 1 or not fingerprint(matches[0])["bytes"]:
                        raise Blocked("Godot did not produce an imported texture for every PNG")
                stdout, stderr = observe("headless", ["--headless", "--script", str(script)])
                if stderr.strip() or stdout.splitlines().count("PILOT_TEXTURES_AND_SCENE_OK") != 1:
                    raise Blocked("Godot did not load every texture and the bootstrap scene")
                pack = data / "bootstrap.pck"
                _, stderr = observe("pack", ["--headless", "--export-pack", "Windows Desktop", str(pack)])
                result["pack_volume_diagnostics"] = _volume_messages(stderr)
                pack_identity = fingerprint(pack)
                if _read_observation(pack, 4) != b"GDPC":
                    raise Blocked("Godot did not produce a valid pack header")
                _, stderr = observe("pack-start", ["--headless", "--main-pack", str(pack), "--quit-after", "2"],
                                    directory=data)
                if stderr.strip() or fingerprint(pack) != pack_identity:
                    raise Blocked("Godot did not start the verified pack")
                result["resource_pack"] = pack_identity
                result.update(outcome="PASS", imported_textures=len(assets))
                if capture:
                    image_path = lab / "bootstrap.png"

                    def interact(api, job, deadline):
                        result["window_image"] = _inspect_window(api, job, deadline, check, manifest,
                                                                image_path, workers)

                    _, stderr = observe("gui", ["--script", str(script)], interact)
                    if stderr.strip():
                        raise Blocked("Godot window produced unexpected diagnostics")
                    evidence = image_path.read_bytes()
                    result.update(gui="BOOTSTRAP_CAPTURED")
            except Blocked as error:
                result.update(outcome="BLOCKED", reason=str(error))
            finally:
                for worker in workers:
                    worker.join(1)
                    if worker.is_alive():
                        raise Blocked("Game capture thread did not settle")
            if snapshot(replica) != image or not original_matches(original, frozen):
                raise Blocked("Game original or registered replica changed during verification")
            _engine(replica)
            cleanup["preserve"] = False
        if evidence is not None:
            output = ledger.local_directory("recovery", create=True) / f"godot-bootstrap-{run_id}.png"
            with output.open("xb") as stream:
                stream.write(evidence)
            result["window_image_path"] = output.name
        result.update(staging_cleanup=cleanup, source_sha256=digest(image))
        if cleanup["status"] != "REMOVED":
            result["outcome"] = "CLEANUP_UNVERIFIED"
        ledger.append(count, fence, {**common, **result, "kind": "native_inspection_result"}, allow_shutdown=True)
        return result
