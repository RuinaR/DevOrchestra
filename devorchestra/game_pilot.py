"""Offline evidence for the existing Godot pilot's frozen bootstrap."""
import ctypes as c
from ctypes import wintypes as w
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile
from struct import unpack
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
EXPORT_KIND = "GODOT_PILOT_WINDOWS_EXPORT"
TEMPLATE_ARCHIVE_SHA256 = "f298490b8d44d934be425a5a65a51bf15f422428b229a06a6e11d9ffea248011"
WINDOWS_TEMPLATE_SHA256 = "d34d36f3be1a6c49c56525ae86469b92e4f417ddf0b43cf00dd80c385c4b0562"
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


def _profile(work, data, engine, template=None):
    grants = {":root": "deny", ":minimal": "read", str(work): "write",
              str(data): "write", str(engine): "read"}
    if template is not None:
        grants[str(template.parent)] = "read"
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


def _template(replica):
    folder = replica.parent / "tools" / "godot-4.7.2-export-windows-x64"
    path = folder / "windows_release_x86_64.exe"
    check_path(folder)
    if not folder.is_dir() or {item.name for item in folder.iterdir()} != {path.name, "receipt.json"}:
        raise Blocked("The project-owned Windows export template is not installed")
    receipt = json.loads(_small_file(folder / "receipt.json", 8192))
    info = fingerprint(path, 256 * 1024**2)
    if (receipt["archive_sha256"] != TEMPLATE_ARCHIVE_SHA256
            or receipt["template_sha256"] != WINDOWS_TEMPLATE_SHA256
            or info["sha256"] != WINDOWS_TEMPLATE_SHA256 or receipt["template_bytes"] != info["bytes"]):
        raise Blocked("Installed Godot export template does not match its receipt")
    return path, info


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
    return _capture_window(api, job, pid, hwnd, current, image_path, workers)


def _capture_window(api, job, pid, hwnd, check, image_path, workers, *, input_probe=False):
    process = _require(signature(api, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x101000, False, pid))
    member = signature(api, "IsProcessInJob", w.BOOL, w.HANDLE, w.HANDLE, c.POINTER(w.BOOL))
    try:
        def alive():
            value = w.BOOL()
            return (api.WaitForSingleObject(process, 0) == 258
                    and bool(member(process, job, c.byref(value)) and value.value))

        window = _Window({"root": hwnd}, {"root"}, pid, check, alive)
        try:
            if input_probe:
                initial = window.dimensions()
                requested = (initial[0] + 32, initial[1] + 24)
                window.resize(*requested)
                while window.dimensions() != requested:
                    check()
                    sleep(0.03)
                window.message("root", 0x0100, 0x20, 0x00390001)  # WM_KEYDOWN, Space
                window.message("root", 0x0101, 0x20, 0xC0390001)  # WM_KEYUP
                return {"image": _capture(window, image_path, workers),
                        "initial_size": list(initial), "resized": list(requested)}
            return _capture(window, image_path, workers)
        finally:
            if alive():
                window.close()
    finally:
        _require(api.CloseHandle(process))


def _inspect_export(api, job, deadline, check, image_path, workers):
    user = c.WinDLL("user32", winmode=0x800, use_last_error=True)
    callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    enum = signature(user, "EnumWindows", w.BOOL, callback, w.LPARAM)
    owner = signature(user, "GetWindowThreadProcessId", w.DWORD, w.HWND, c.POINTER(w.DWORD))
    visible = signature(user, "IsWindowVisible", w.BOOL, w.HWND)

    def current():
        check()
        if monotonic() >= deadline:
            raise Blocked("Exported Godot window did not appear")

    while True:
        current()
        members = _job_pids(api, job)
        if not members:
            raise Blocked("Exported Godot process exited before window observation")
        windows = []

        @callback
        def visit(hwnd, _):
            pid = w.DWORD()
            owner(hwnd, c.byref(pid))
            if pid.value in members and visible(hwnd):
                windows.append((pid.value, hwnd))
            return True

        _require(enum(visit, 0))
        if len(windows) == 1:
            pid, hwnd = windows[0]
            sleep(0.4)
            return _capture_window(api, job, pid, hwnd, current, image_path, workers, input_probe=True)
        sleep(0.05)


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


def run(runner, archive, *, cancelled=lambda: False, capture=False, export_windows=False):
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
        template, template_identity = _template(replica) if export_windows else (None, None)
        assets = sorted(name for name in image["files"] if name.startswith("assets/") and name.endswith(".png"))
        if not assets or cancelled():
            raise Blocked("Frozen PNG assets are unavailable or the check was cancelled")
        run_id = uuid4().hex
        common = {"project_id": PROJECT, "probe_kind": EXPORT_KIND if export_windows else KIND, "run_id": run_id,
                  "request_id": run_id, "state": "DIAGNOSTIC"}
        evidence = None
        workers = []
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(archive, lab / "package")
            work, data, home = (lab / name for name in ("work", "data", "config"))
            _copy_source(replica, work, image)
            data.mkdir()
            home.mkdir()
            (home / "config.toml").write_text(_profile(work, data, engine.parent, template), encoding="utf-8")
            manifest = data / "window.json"
            script = work / "devorchestra-bootstrap-probe.gd"
            preset = PACK_PRESET
            if export_windows:
                preset += (f'custom_template/release={json.dumps(template.as_posix())}\n'
                           'binary_format/embed_pck=true\ndebug/export_console_wrapper=0\n'
                           'application/modify_resources=false\ncodesign/enable=false\n')
            else:
                with script.open("x", encoding="utf-8") as stream:
                    stream.write(_probe_script(assets, manifest))
            with (work / "export_presets.cfg").open("x", encoding="utf-8") as stream:
                stream.write(preset)
            windows = _known_folder(0x24)
            env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                   "PATH": str(windows / "System32"), "LOCALAPPDATA": str(data), "APPDATA": str(data),
                   "HOME": str(data), "USERPROFILE": str(data), "TEMP": str(data), "TMP": str(data),
                   "CODEX_HOME": str(home)}
            count, fence = ledger.append(len(events), head, {
                **common, "kind": "native_inspection_intent", "workspace": lab.name,
                "source_sha256": digest(image), "engine_sha256": ENGINE_HASHES,
                "archive_sha256": ARCHIVE_SHA256, "network": "OFF", "capture": capture or export_windows,
                "template": template_identity}, admission=require_admission)
            cleanup["preserve"] = True

            def check():
                if cancelled():
                    raise Blocked("Game pilot check cancelled")
                runner._check_diagnostic_fence(count, fence)

            def observe(name, args, interact=None, directory=work, executable=None):
                logs = lab / name
                logs.mkdir()
                check()
                command = _args(executable, *args) if executable else _args(engine, "--path", str(directory), *args)
                result = _observe(binary, command, directory, logs, env,
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
                if export_windows:
                    _export_release(ledger, run_id, replica, data, lab, observe, check, workers, result)
                else:
                    evidence = _preflight(work, data, lab, observe, check, workers, manifest, script,
                                          assets, capture, result)
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
            if export_windows and _template(replica)[1] != template_identity:
                raise Blocked("Godot export template changed during verification")
            cleanup["preserve"] = False
        if evidence is not None:
            output = ledger.local_directory("recovery", create=True) / f"godot-bootstrap-{run_id}.png"
            with output.open("xb") as stream:
                stream.write(evidence)
            result["window_image_path"] = output.name
        result.update(staging_cleanup=cleanup, source_sha256=digest(image), template=template_identity)
        if cleanup["status"] != "REMOVED":
            result["outcome"] = "CLEANUP_UNVERIFIED"
        if result.get("artifact_directory"):
            output = ledger.local_directory("recovery") / result["artifact_directory"] / "verification.json"
            with output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
        ledger.append(count, fence, {**common, **result, "kind": "native_inspection_result"}, allow_shutdown=True)
        return result


def _preflight(work, data, lab, observe, check, workers, manifest, script, assets, capture, result):
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
        return evidence
    return None


def _export_release(ledger, run_id, replica, data, lab, observe, check, workers, result):
    release = data / "release"
    release.mkdir()
    exe = release / "GamePilot.exe"
    _, stderr = observe("windows-export", ["--headless", "--export-release", "Windows Desktop", str(exe)])
    result["export_volume_diagnostics"] = _volume_messages(stderr)
    identity = fingerprint(exe, 256 * 1024**2)
    with exe.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or not header.startswith(b"MZ"):
            raise Blocked("Windows export is missing its PE header")
        stream.seek(unpack("<I", header[60:64])[0])
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise Blocked("Windows export does not target x64")
    if set(path.name for path in release.iterdir()) != {exe.name}:
        raise Blocked("Windows export produced unexpected sidecar files")
    result["export"] = "WINDOWS_X64_EXPORTED"
    screenshot = data / "exported-window.png"

    def interact(api, job, deadline):
        observed = _inspect_export(api, job, deadline, check, screenshot, workers)
        result["window_image"] = observed.pop("image")
        result["window_size"] = observed

    _, stderr = observe("windows-runtime", [], interact, directory=release, executable=exe)
    if stderr.strip() or fingerprint(exe, 256 * 1024**2) != identity:
        raise Blocked("Exported game produced unexpected diagnostics or changed")
    output = ledger.local_directory("recovery", create=True) / f"godot-windows-export-{run_id}"
    output.mkdir()
    license_path = replica / "assets" / "kenney" / "License.txt"
    for source, name in ((exe, exe.name), (screenshot, screenshot.name), (license_path, "Kenney-LICENSE.txt")):
        check_path(source)
        copyfile(source, output / name)
    for stage in ("windows-export", "windows-runtime"):
        for channel in ("stdout", "stderr"):
            log = _read_observation(lab / stage / f"probe.{channel}", 65537)
            (output / f"{stage}.{channel}.log").write_bytes(log)
    if fingerprint(output / exe.name, 256 * 1024**2) != identity:
        raise Blocked("Preserved export differs from the executable that was tested")
    result.update(outcome="PASS", gui="EXPORTED_WINDOW_CAPTURED_AND_CLOSED", executable=identity,
                  artifact_directory=output.name, window_image_path=screenshot.name,
                  input="SPACE_MESSAGES_SENT_NO_GAME_RESPONSE_IMPLEMENTED")
