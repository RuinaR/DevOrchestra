"""Offline evidence for the existing Godot pilot's frozen bootstrap."""
from hashlib import sha256
import json
from pathlib import Path
import re
from shutil import copyfile
from struct import unpack
from time import monotonic, sleep
from uuid import uuid4

from .gui_verification import _small_file
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import PROFILE, OwnedMxcStopped, _args, _observe
from .mxc_launcher import _known_folder
from .mxc_owned_gui import _job_pids, capture_owned_window as _capture_window, inspect_owned_window as _inspect_export
from .native_command_probe import ARCHIVE_SHA256, _stage_release, _staging
from .native_developer_trial import _read_observation
from .project_onboarding import _relative
from .replica_evidence import fingerprint, snapshot
from .replica_readiness import baseline, original_matches


PROJECT = "native-game-pilot"
REGISTERED_GUI = "godot-owned-gui"
CRITERIA = ("C1-555e38d7", "C2-9dcbacb0", "C3-b935f137", "C4-c00426e2", "C5-41940c23")
FROZEN_CRITERIA_SHA256 = "0aefb917198ad528d5bd4edab0bf778cabb76699f14bac3a600810a8b95413b1"
DEVELOPMENT_FILES = ("docs/GAME-DESIGN.md", "project.godot", "scenes/game.tscn", "scenes/player.tscn",
                     "scenes/hud.tscn", "scripts/game.gd", "scripts/player.gd", "scripts/hud.gd")
ROLE_LIMITS = {"manager-planning": 45000, "developer": 75000, "reviewer": 55000, "manager-final": 45000}
SESSION_SECONDS = 14400
SESSION_TOKENS = 1500000
ROLE_SECONDS = {"manager-planning": 1200, "developer": 3600, "reviewer": 1200, "manager-final": 1200}
ROLE_TOKENS = {"manager-planning": 120000, "developer": 350000, "reviewer": 150000, "manager-final": 120000}


def graceful_policy(*, followup=False):
    return {"role_grace_seconds": 900, "idle_seconds": 600, "tool_idle_seconds": 1200,
            "signoff_seconds": 1800, "signoff_tokens": 300000,
            "signoff_within_session_tokens": followup}


def recommended_limits():
    return {"seconds": SESSION_SECONDS, "tokens": SESSION_TOKENS, "cycles": None,
            "role_seconds": dict(ROLE_SECONDS), "role_tokens": dict(ROLE_TOKENS)}


def validate_limits(limits, *, maximum_seconds=SESSION_SECONDS, maximum_tokens=SESSION_TOKENS,
                    role_token_caps=None):
    if type(limits) is not dict or limits.keys() != recommended_limits().keys():
        raise Blocked("세션 한도 항목을 확인하세요.")
    seconds, tokens, cycles = (limits[key] for key in ("seconds", "tokens", "cycles"))
    if type(seconds) is not int or not 60 <= seconds <= maximum_seconds:
        raise Blocked(f"전체 시간 기준은 60~{maximum_seconds:,}초로 입력하세요.")
    if type(tokens) is not int or not 1 <= tokens <= maximum_tokens:
        raise Blocked(f"전체 토큰 기준은 1~{maximum_tokens:,}토큰으로 입력하세요.")
    if cycles is not None and (type(cycles) is not int or not 1 <= cycles <= 50):
        raise Blocked("사이클 상한은 비워두거나 1~50으로 입력하세요.")
    for key, maxima, total, unit in (
            ("role_seconds", ROLE_SECONDS, seconds, "초"),
            ("role_tokens", role_token_caps or ROLE_TOKENS, tokens, "토큰")):
        values = limits[key]
        if type(values) is not dict or values.keys() != maxima.keys():
            raise Blocked(f"역할별 {unit} 기준 항목을 확인하세요.")
        for role, cap in maxima.items():
            if type(values[role]) is not int or not 1 <= values[role] <= min(cap, total):
                name = "Manager" if role.startswith("manager-") else role.title()
                raise Blocked(f"{name} 호출당 기준은 1~{min(cap, total):,}{unit}으로 입력하세요.")
MANUAL_CHECKS = {
    CRITERIA[1]: "In the exported game, observe movement, walls and single-count pickups under keyboard input.",
    CRITERIA[2]: "Play through a hazard failure and a reachable victory and observe the game states.",
    CRITERIA[3]: "Exercise start, HUD, results and restart, then inspect rendered text, feedback and clipping.",
    CRITERIA[4]: "Review asset provenance and the accepted design against a recorded 5–10 minute playthrough.",
}
PLAYER_FEEDBACK_FIELDS = ("controls", "difficulty", "visuals", "progression")


def player_candidate(ledger, events):
    _, replica, _, _ = baseline(ledger, PROJECT)
    source = digest(snapshot(replica))
    for row in reversed(events):
        if (row.get("project_id") != PROJECT or row.get("kind") != "native_inspection_result"
                or row.get("probe_kind") != EXPORT_KIND or row.get("outcome") != "PASS"
                or row.get("source_sha256") != source or row.get("owned_tree") != "DRAINED"
                or row.get("staging_cleanup", {}).get("status") != "REMOVED"):
            continue
        name = row["artifact_directory"]
        if not re.fullmatch(r"godot-windows-export-[a-f0-9]{32}", name):
            raise Blocked("Unrecognized game export directory")
        executable = ledger.local_directory("recovery") / name / "GamePilot.exe"
        if fingerprint(executable, 256 * 1024**2) != row["executable"]:
            raise Blocked("The verified game executable has changed")
        return {"run_id": row["run_id"], "source_sha256": source, "executable": str(executable),
                "identity": row["executable"]}
    raise Blocked("Export the current game candidate before user playtesting")


def save_player_feedback(runner, candidate, ratings, notes, finished):
    if (ratings.keys() != set(PLAYER_FEEDBACK_FIELDS)
            or any(value not in {"OK", "ISSUE", "NOT_TESTED"} for value in ratings.values())
            or type(finished) is not bool or len(notes) > 1000):
        raise Blocked("Invalid player feedback")
    ledger = runner.ledger
    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        if player_candidate(ledger, events) != candidate:
            raise Blocked("The playtest candidate changed")
        ledger.append(len(events), head, {"kind": "native_inspection_player_feedback", "project_id": PROJECT,
                      "source_sha256": candidate["source_sha256"], "export_run_id": candidate["run_id"],
                      "executable_sha256": candidate["identity"]["sha256"], "origin": "USER_REPORTED",
                      "ratings": dict(ratings), "notes": notes, "finished": finished})


def latest_player_feedback(events, source, export_run_id):
    return next((event for event in reversed(events)
                 if event["kind"] == "native_inspection_player_feedback" and event.get("project_id") == PROJECT
                 and event.get("source_sha256") == source and event.get("export_run_id") == export_run_id), None)


def run_player_candidate(runner, candidate, *, cancelled):
    from .runtime import require_admission

    ledger = runner.ledger
    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        if player_candidate(ledger, events) != candidate or cancelled():
            raise Blocked("The playtest candidate changed or the launch was cancelled")
        run_id = uuid4().hex
        fields = {"project_id": PROJECT, "probe_kind": "GODOT_USER_PLAY", "run_id": run_id,
                  "request_id": run_id, "state": "DIAGNOSTIC", "source_sha256": candidate["source_sha256"],
                  "export_run_id": candidate["run_id"], "model_calls": 0}
        with _staging(ledger) as (lab, cleanup):
            executable = lab / "GamePilot.exe"
            copyfile(candidate["executable"], executable)
            if fingerprint(executable, 256 * 1024**2) != candidate["identity"]:
                raise Blocked("The staged game executable has changed")
            windows = _known_folder(0x24)
            env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                   "PATH": str(windows / "System32"), "LOCALAPPDATA": str(lab), "APPDATA": str(lab),
                   "HOME": str(lab), "USERPROFILE": str(lab), "TEMP": str(lab), "TMP": str(lab)}
            count, fence = ledger.append(len(events), head, {
                **fields, "kind": "native_inspection_intent", "workspace": lab.name,
                "executable_sha256": candidate["identity"]["sha256"]}, admission=require_admission)
            cleanup["preserve"] = True

            def check():
                if cancelled():
                    raise Blocked("Player closed the playtest panel")
                runner._check_diagnostic_fence(count, fence)

            try:
                result = _observe(executable, ["--resolution", "800x540"], lab, lab, env,
                                  seconds=1200, memory=1024**3,
                                  check=check, encoding="utf-8", output_limit=65536)
                outcome = "EXITED" if result["exitCode"] == 0 else "FAILED"
            except OwnedMxcStopped:
                outcome = "CANCELLED" if cancelled() else "STOPPED"
            cleanup["preserve"] = False
        ledger.append(count, fence, {**fields, "kind": "native_inspection_result", "outcome": outcome,
                      "owned_tree": "DRAINED", "staging_cleanup": cleanup}, allow_shutdown=True)
        return outcome


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


def _engine(replica, tools_root=None):
    folder = (tools_root or replica.parent / "tools") / "godot-4.7.2"
    check_path(folder)
    if not folder.is_dir() or {path.name for path in folder.iterdir()} != ENGINE_HASHES.keys():
        raise Blocked("The pinned project-owned Godot installation is incomplete or has extra files")
    for name, expected in ENGINE_HASHES.items():
        if fingerprint(folder / name, 256 * 1024**2)["sha256"] != expected:
            raise Blocked("Godot executable identity changed")
    return folder / (ENGINE + "_console.exe")


def _template(replica, tools_root=None):
    folder = (tools_root or replica.parent / "tools") / "godot-4.7.2-export-windows-x64"
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


def development_tools(replica, tools_root=None):
    engine = _engine(replica, tools_root)
    template, identity = _template(replica, tools_root)
    return {"executable": str(engine), "root": str(engine.parent), "template": str(template),
            "template_identity": identity, "engine_sha256": ENGINE_HASHES, "mode": "godot-game",
            "manual_checks": MANUAL_CHECKS}


def _export_preset(template):
    return (PACK_PRESET + f'custom_template/release={json.dumps(template.as_posix())}\n'
            'binary_format/embed_pck=true\ndebug/export_console_wrapper=0\n'
            'application/modify_resources=false\ncodesign/enable=false\n')


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
        if not original_matches(original, frozen):
            raise Blocked("Game original no longer matches its onboarding snapshot")
        candidate = None
        if digest(image) != onboarding["replica_sha256"]:
            if not export_windows:
                raise Blocked("Game pilot no longer matches its onboarding snapshot")
            from .registered_cycle import settled_game_candidate

            candidate = settled_game_candidate(ledger, replica, image, frozen, onboarding, DEVELOPMENT_FILES)
        engine = _engine(replica)
        template, template_identity = _template(replica) if export_windows else (None, None)
        assets = sorted(name for name in image["files"] if name.startswith("assets/") and name.endswith(".png"))
        if not assets or cancelled():
            raise Blocked("Frozen PNG assets are unavailable or the check was cancelled")
        run_id = uuid4().hex
        common = {"project_id": PROJECT, "probe_kind": EXPORT_KIND if export_windows else KIND, "run_id": run_id,
                  "request_id": run_id, "state": "DIAGNOSTIC"}
        if candidate:
            common.update(parent_run_id=candidate["parent_run_id"], model_calls=0)
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
                preset = _export_preset(template)
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


def _export_release(ledger, run_id, replica, data, lab, observe, check, workers, result, *, generic=False):
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
    before_input = data / "before-input.png" if generic else None

    def interact(api, job, deadline):
        observed = _inspect_export(api, job, deadline, check, screenshot, workers, before_input=before_input)
        result["window_image"] = observed.pop("image")
        result["window_size"] = observed

    _, stderr = observe("windows-runtime", ["--resolution", "800x540"], interact,
                        directory=release, executable=exe)
    if stderr.strip() or fingerprint(exe, 256 * 1024**2) != identity:
        raise Blocked("Exported game produced unexpected diagnostics or changed")
    output = ledger.local_directory("recovery", create=True) / f"godot-windows-export-{run_id}"
    output.mkdir()
    license_path = replica / "assets" / "kenney" / "License.txt"
    files = [(exe, exe.name), (screenshot, screenshot.name)]
    if generic:
        files.append((before_input, before_input.name))
    else:
        files.append((license_path, "Kenney-LICENSE.txt"))
    for source, name in files:
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
                  input=("SPACE_AND_CLIENT_CLICK_SENT_RESPONSE_NOT_VALIDATED" if generic
                         else "SPACE_MESSAGES_SENT_RESPONSE_NOT_VALIDATED"))


def verify_development(session, cycle):
    """Build the reviewed candidate under the session fence and preserve independent EXE evidence."""
    scope = session.scope
    tool = scope["verifier"]
    generic = tool["mode"] == REGISTERED_GUI
    ledger = session.runner.ledger
    engine, template = Path(tool["executable"]), Path(tool["template"])
    session.check()
    session.check_image()
    source_hash = session.source()
    session.record("native_inspection_role_intent", cycle=cycle, phase="verifier", source_sha256=source_hash)
    lab = session.logs(f"{cycle}-godot-verifier")
    work, data, home = (lab / name for name in ("work", "data", "config"))
    _copy_source(session.work, work, session.image)
    data.mkdir()
    home.mkdir()
    (home / "config.toml").write_text(_profile(work, data, engine.parent, template), encoding="utf-8")
    preset = work / "export_presets.cfg"
    if generic and preset.exists():
        preset.unlink()
    with preset.open("x", encoding="utf-8") as stream:
        stream.write(_export_preset(template))
    env = {**session.verify_env, "APPDATA": str(data), "LOCALAPPDATA": str(data), "HOME": str(data),
           "USERPROFILE": str(data), "TEMP": str(data), "TMP": str(data), "CODEX_HOME": str(home)}
    result, workers = {"steps": {}}, []

    def observe(name, args, interact=None, directory=work, executable=None):
        session.check()
        logs = lab / name
        logs.mkdir()
        argv = _args(executable, *args) if executable else _args(engine, "--path", str(work), *args)
        response = session.owned(lambda: _observe(session.binary, argv, directory, logs, env,
                                                  seconds=min(45, session.verification_seconds()), memory=1024**3,
                                                  check=session.check, output_limit=65536, encoding="utf-8",
                                                  interact=interact))
        if response["job_active"]:
            session.owned_verified = False
            raise Blocked("Game Verifier Job is unresolved")
        stderr = _read_observation(logs / "probe.stderr", 65537)
        result["steps"][name] = {"exit_code": response["exitCode"], "job_active": 0,
                                 "stdout_sha256": sha256(response["stdout"].encode("utf-8")).hexdigest(),
                                 "stderr_sha256": sha256(stderr).hexdigest()}
        if response["exitCode"] or len(stderr) > 65536:
            raise Blocked(f"Godot {name} failed")
        return response["stdout"], stderr.decode("utf-8")

    try:
        _export_release(ledger, f"{session.fields['run_id']}-cycle-{cycle}", session.work,
                        data, lab, observe, session.check, workers, result, generic=generic)
    except Blocked as error:
        if not session.owned_verified or str(error) == "SIGNOFF_PENDING":
            raise
        if str(error) == "MXC probe timeout" and session.signoff_started is not None:
            session.verification_seconds()
        result.update(outcome="FAIL", reason=str(error))
    finally:
        for worker in workers:
            worker.join(1)
            if worker.is_alive():
                session.owned_verified = False
                raise Blocked("Game capture thread did not settle")
    session.check_image()
    if generic:
        receipt = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
                   "verdict": result["outcome"], "source_sha256": source_hash, "observations": [],
                   "user_test_required": list(scope["criteria"]) if result["outcome"] == "PASS" else [],
                   "game": result}
        return _record_development_verification(session, cycle, receipt)
    # A successful launch of the original tile sheet is useful evidence but is not C1.
    settings = _small_file(session.work / "project.godot", 24000).decode("utf-8")
    changed_game = (
        re.search(r'^run/main_scene\s*=\s*"res://scenes/game\.tscn"\s*$', settings, re.MULTILINE)
        and {"scenes/game.tscn", "scripts/game.gd", "docs/GAME-DESIGN.md"} <= session.image["files"].keys()
        and b'res://scripts/game.gd' in _small_file(session.work / "scenes" / "game.tscn", 24000)
    )
    observations = [{"title": CRITERIA[0], "expected": "CANDIDATE_WINDOWS_GAME_LAUNCHED",
                     "actual": "CANDIDATE_WINDOWS_GAME_LAUNCHED" if result["outcome"] == "PASS" and changed_game
                     else "BOOTSTRAP_OR_FAILED_EXPORT"}]
    receipt = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
               "verdict": "PASS" if observations[0]["actual"] == observations[0]["expected"] else "FAIL",
               "source_sha256": source_hash, "observations": observations,
               "user_test_required": list(MANUAL_CHECKS), "game": result}
    return _record_development_verification(session, cycle, receipt)


def _record_development_verification(session, cycle, receipt):
    artifact = receipt["game"].get("artifact_directory")
    if artifact:
        output = session.runner.ledger.local_directory("recovery") / artifact / "verification.json"
        with output.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, ensure_ascii=False)
    session.record("native_inspection_role_result", cycle=cycle, **receipt)
    session.phases.append({"cycle": cycle, "phase": "verifier", "outcome": receipt["verdict"],
                           "artifact_directory": artifact})
    return receipt
