"""A model-free Controller run for a reviewed Windows preview and input sequence."""
import json
from pathlib import Path
from shutil import copyfile
from time import monotonic, sleep, time
from uuid import uuid4

from . import adaptive_play, codex_setup, direct_git, replica_readiness
from .lease import Blocked, execution_slot
from .ledger import digest
from .mxc_host_probe import OwnedMxcStopped, _args, _observe
from .mxc_launcher import _known_folder
from .mxc_owned_gui import inspect_owned_window
from .native_command_probe import _stage_release, _staging
from .native_cycle import _FixtureSession
from .native_developer_trial import PROFILE, trial_flags
from .project_evidence import approved_preview, stage_preview
from .role_contract import _unique_object
from .windows_key_input import ReleaseUnconfirmed, SCAN_CODES


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
    binding = direct_git.binding(original, scratch, lambda: None)
    if direct_git._command(original, scratch, lambda: None, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise Blocked("The preview build record does not match the clean registered checkout")
    target = approved_preview(directory, original, identity={"commit": binding["head"]})
    target.pop("args")
    return {"project_id": project, "run_id": run_id, "expires": expires, "source": str(original),
            "git": binding, **target,
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
            executable = stage_preview(frozen, bundle)
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
            unconfirmed_release = None

            def check_hold():
                if cancelled() or time() >= scope["expires"]:
                    raise Blocked("Preview cancelled or approval expired")

            def check(*, force=False):
                nonlocal next_fence_check
                check_hold()
                if force or monotonic() >= next_fence_check:
                    runner._check_diagnostic_fence(count, fence)
                    # A large ledger can take longer than the minimum poll interval to verify.
                    next_fence_check = monotonic() + 0.15
                    check_hold()

            def interact(api, job, deadline):
                nonlocal unconfirmed_release
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
                            check(force=True)
                            row["held_ms"] = keyboard.hold(action["keys"], action["ms"])
                            row["release"] = keyboard.cleanup
                            check(force=True)
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

                try:
                    inspect_owned_window(api, job, deadline, check, None, workers, play=play,
                                         executable=executable, capture_fps=scope["fps"],
                                         desktop_hold_ms=scope["hold_ms"], activate_foreground=True,
                                         hold_check=check_hold)
                except ReleaseUnconfirmed as error:
                    unconfirmed_release = error
                    raise

            exit_code = None
            try:
                result = _observe(launcher, argv, bundle, logs, environment, seconds=scope["seconds"],
                                  memory=1024**3, check=check, interact=interact, output_limit=8192)
                if result["job_active"]:
                    raise Blocked("Preview Job did not drain")
                exit_code = result["exitCode"]
                outcome, reason = ("OBSERVED", None) if exit_code == 0 else ("NOT_VALIDATED", "Preview exit failed")
            except OwnedMxcStopped as error:
                # The launcher drains the Job before reporting Blocked. That does not verify key-up.
                if unconfirmed_release is not None:
                    raise unconfirmed_release from error
                outcome, reason = "NOT_VALIDATED", str(error)
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
