"""A model-free Controller run for a reviewed Windows preview and input sequence."""
import json
from pathlib import Path
from shutil import copyfile
from time import monotonic, sleep, time
from uuid import uuid4

from . import adaptive_play, codex_setup, direct_git, registered_cycle, replica_readiness
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import OwnedMxcStopped, _args, _observe
from .mxc_launcher import _known_folder
from .mxc_owned_gui import inspect_owned_window
from .native_command_probe import _stage_release, _staging
from .native_cycle import _FixtureSession
from .native_developer_trial import PROFILE, trial_flags
from .project_evidence import preview_entry
from .replica_evidence import fingerprint, snapshot
from .role_contract import _unique_object
from .windows_key_input import ReleaseUnconfirmed, SCAN_CODES


MAX_BUNDLE_BYTES = registered_cycle.GUI_PREVIEW_MAX_BYTES
KIND = "REGISTERED_PREVIEW_PLAY"


def input_plan(raw, *, steps, seconds, hold, fps, evidence):
    if len(raw) > 16384:
        raise Blocked("Preview input plan is too large")
    adaptive_play.approval(limits={"input_steps": steps, "seconds": seconds}, foreground=True,
                           capture_fps=fps, evidence_mib=evidence, desktop_hold_ms=hold)
    actions = json.loads(raw, object_pairs_hook=_unique_object)
    if type(actions) is not list or not 1 <= len(actions) <= steps:
        raise Blocked("Specify one to the approved maximum number of preview actions")
    duration = 0
    for action in actions:
        if type(action) is not dict:
            raise Blocked("Invalid preview action")
        kind = action.get("action")
        if kind == "wait" and action.keys() == {"action", "ms"}:
            ms = action["ms"]
            if type(ms) is int and 100 <= ms <= 20000:
                duration += ms
                continue
        if kind == "keys" and action.keys() == {"action", "keys", "ms"}:
            keys, ms = action["keys"], action["ms"]
            if (type(keys) is list and 1 <= len(keys) <= 2
                    and all(type(key) is str and key in SCAN_CODES for key in keys)
                    and len(set(keys)) == len(keys) and type(ms) is int and 30 <= ms <= hold):
                duration += ms
                continue
        if kind == "click" and action.keys() == {"action", "x", "y"}:
            if all(type(action[key]) is int and 0 <= action[key] < 2048 for key in ("x", "y")):
                continue
        raise Blocked("Invalid preview action or action outside the approved input limits")
    if duration + (len(actions) + 1) * 4000 + 5000 >= seconds * 1000:
        raise Blocked("Preview duration leaves insufficient time for capture and cleanup")
    return actions


def _scope(runner, scratch, project, directory, raw, run_id, expires, *, steps, seconds, hold, fps, evidence):
    if not time() < expires <= time() + 900:
        raise Blocked("Preview approval expired")
    actions = input_plan(raw, steps=steps, seconds=seconds, hold=hold, fps=fps, evidence=evidence)
    original, _, _, _ = replica_readiness.baseline(runner.ledger, project)
    check_path(directory)
    if directory.resolve() != directory or directory.is_relative_to(original):
        raise Blocked("Select a separate preview folder outside the registered checkout")
    bundle = snapshot(directory, max_file_bytes=MAX_BUNDLE_BYTES, long_local=True)
    entry = preview_entry(directory, bundle["files"])
    if not entry or bundle["git"] or sum(item["bytes"] for item in bundle["files"].values()) > MAX_BUNDLE_BYTES:
        raise Blocked("Invalid or oversized Windows preview bundle")
    metadata = directory.parent / "build.json"
    fingerprint(metadata, 65536)
    build = json.loads(metadata.read_bytes(), object_pairs_hook=_unique_object)
    binding = direct_git.binding(original, scratch, lambda: None)
    if (not isinstance(build, dict) or build.get("commit") != binding["head"]
            or build.get("files") != bundle["files"]
            or direct_git._command(original, scratch, lambda: None, "status",
                                   "--porcelain=v1", "-z", "--untracked-files=all")):
        raise Blocked("The preview build record does not match the clean registered checkout")
    return {"project_id": project, "run_id": run_id, "expires": expires, "source": str(original),
            "git": binding, "bundle": str(directory), "inventory": bundle, "entry": entry,
            "window": "only foreground HWND of the exact staged executable in its owned Job",
            "actions": actions, "steps": steps, "seconds": seconds, "hold_ms": hold, "fps": fps,
            "evidence_mib": evidence, "model_calls": 0}


def review(runner, project, directory, raw, **limits):
    with execution_slot() as abandoned:
        runner._idle_diagnostic(abandoned)
        with _staging(runner.ledger) as (lab, _):
            return _scope(runner, lab, project, directory, raw, uuid4().hex, int(time()) + 600, **limits)


def run(runner, scope, approval, *, cancelled):
    from .runtime import require_admission

    ledger = runner.ledger
    if approval != digest(scope):
        raise Blocked("Preview input approval changed")
    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        run_id, project = scope["run_id"], scope["project_id"]
        if cancelled() or any(event.get("run_id") == run_id for event in events):
            raise Blocked("Preview approval was cancelled or already used")
        with _staging(ledger) as (lab, cleanup):
            frozen = _scope(runner, lab, project, Path(scope["bundle"]), json.dumps(scope["actions"]),
                            run_id, scope["expires"], steps=scope["steps"], seconds=scope["seconds"],
                            hold=scope["hold_ms"], fps=scope["fps"], evidence=scope["evidence_mib"])
            if frozen != scope:
                raise Blocked("Preview or registered project changed after approval")
            bundle, logs, data, home = (lab / name for name in ("bundle", "logs", "data", "config"))
            for folder in (bundle, logs, data, home):
                folder.mkdir()
            files = frozen["inventory"]["files"]
            for name in files:
                source, target = Path(scope["bundle"]) / name, bundle / name
                check_path(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source, target)
            if snapshot(bundle, max_file_bytes=MAX_BUNDLE_BYTES, long_local=True)["files"] != files:
                raise Blocked("Staged preview differs from the approved bundle")
            executable = bundle / scope["entry"]
            launcher = _stage_release(codex_setup.archive_path(ledger), lab / "codex")
            windows = _known_folder(0x24)
            environment = _FixtureSession.runtime_environment(
                {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                 "PATH": str(windows / "System32"), "CODEX_HOME": str(home)}, data)
            argv = [*trial_flags(bundle, writable=False, runtime_root=data, shell_tools=False),
                    *_args(executable, profile=PROFILE)]
            if cancelled() or time() >= scope["expires"]:
                raise Blocked("Preview approval expired or was cancelled before launch")
            fields = {"project_id": project, "run_id": run_id, "request_id": run_id, "probe_kind": KIND,
                      "state": "DIAGNOSTIC", "approval_sha256": approval, "model_calls": 0}
            count, fence = ledger.append(len(events), head, {
                **fields, "kind": "native_inspection_intent", "scope": scope, "workspace": lab.name,
            }, admission=require_admission)
            cleanup["preserve"] = True
            frames, receipts, workers = [], [], []
            evidence_dir = ledger.local_directory("recovery", create=True) / f"preview-play-{run_id}"
            evidence_dir.mkdir()
            used_bytes = 0
            next_fence_check = 0

            def check():
                nonlocal next_fence_check
                if cancelled() or time() >= scope["expires"]:
                    raise Blocked("Preview cancelled or approval expired")
                now = monotonic()
                if now >= next_fence_check:
                    runner._check_diagnostic_fence(count, fence)
                    next_fence_check = now + 0.15

            def interact(api, job, deadline):
                def play(window, capture, keyboard):
                    nonlocal used_bytes
                    client = window.client_in_image()

                    def observe():
                        nonlocal used_bytes
                        keyboard._check()
                        path = logs / "frame.png"
                        image = capture(path)
                        try:
                            keyboard._check()
                            if image["uniform_client"] or tuple(image["client"]) != client:
                                raise Blocked("Preview client is not visible or its geometry changed")
                            size = path.stat().st_size
                            if used_bytes + size > scope["evidence_mib"] * 1024**2:
                                raise Blocked("Preview evidence limit reached")
                            used_bytes += size
                            name = f"frame-{len(frames):03}.png"
                            copyfile(path, evidence_dir / name)
                            frames.append({**image, "file": name})
                        finally:
                            path.unlink(missing_ok=True)

                    observe()
                    for action in scope["actions"]:
                        keyboard._check()
                        if window.client_in_image() != client:
                            raise Blocked("Preview client geometry changed before input")
                        row = {"action": action, "before": frames[-1]["client_sha256"]}
                        if action["action"] == "keys":
                            row["held_ms"] = keyboard.hold(action["keys"], action["ms"])
                            row["release"] = keyboard.cleanup
                        elif action["action"] == "click":
                            keyboard.click(action["x"], action["y"])
                            row["release"] = keyboard.cleanup
                        else:
                            stop = monotonic() + action["ms"] / 1000
                            while monotonic() < stop:
                                keyboard._check()
                                sleep(0.025)
                        receipts.append(row)
                        observe()
                        row["after"] = frames[-1]["client_sha256"]

                inspect_owned_window(api, job, deadline, check, None, workers, play=play,
                                     executable=executable, capture_fps=scope["fps"],
                                     desktop_hold_ms=scope["hold_ms"], activate_foreground=True)

            exit_code = None
            try:
                result = _observe(launcher, argv, bundle, logs, environment, seconds=scope["seconds"],
                                  memory=1024**3, check=check, interact=interact, output_limit=8192)
                if result["job_active"]:
                    raise Blocked("Preview Job did not drain")
                exit_code = result["exitCode"]
                outcome, reason = ("OBSERVED", None) if exit_code == 0 else ("NOT_VALIDATED", "Preview exit failed")
            except OwnedMxcStopped as error:
                # The launcher reports this only after verifying that its owned Job drained.
                outcome, reason = "NOT_VALIDATED", str(error)
            except ReleaseUnconfirmed:
                # Keep the intent unresolved when the desktop input release could not be verified.
                raise
            finally:
                for worker in workers:
                    worker.join(1)
                    if worker.is_alive():
                        raise Blocked("Preview capture worker did not settle")
            cleanup["preserve"] = False
        receipt = {**fields, "kind": "native_inspection_result", "outcome": outcome, "reason": reason,
                   "owned_tree": "DRAINED", "exit_code": exit_code, "staging_cleanup": cleanup,
                   "evidence_directory": evidence_dir.name, "frames": frames, "actions": receipts,
                   "evidence_bytes": used_bytes}
        ledger.append(count, fence, receipt, allow_shutdown=True)
        return receipt
