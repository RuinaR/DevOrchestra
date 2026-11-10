"""Scoped Windows GUI checks for the Controller's trusted WinForms host."""
from base64 import b64encode
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
from hashlib import sha256
import json
import struct
from time import monotonic, sleep

from .gui_fixture import SCRIPT
from .job_probe import (
    ExtendedLimits, ProcessInfo, _account, _api, _cleanup, _create_owned, _require, _wait_empty,
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    KILL_EXIT,
)
from .lease import Blocked
from .ledger import check_path, digest
from .mxc_launcher import _known_folder
from .system_powershell import _api as signature, _open_locked, _system_path

SCENARIO = "winforms-details-v1"
PROJECT_SCENARIO = "registered-native-form-v1"
FIXTURE_SHA256 = sha256(SCRIPT.encode()).hexdigest()
ROLES = {
    "Manager": ("read-receipt",),
    "Developer": ("read-receipt", "registered-gui-scenario"),
    "Reviewer": ("read-receipt", "registered-gui-scenario"),
    "Verifier": ("read-receipt", "fixed-gui-scenario", "registered-gui-scenario"),
}
DEFAULT_FORM = {
    "controls": {name: name for name in ("status", "open", "apply", "entry", "list")},
    "scenes": [
        {"title": "Launch", "expected": "Ready", "steps": []},
        {"title": "Details", "expected": "Details opened", "steps": [{"op": "click", "target": "open"}]},
        {"title": "Completed", "expected": "Applied: Controller QA", "steps": [
            {"op": "text", "target": "entry", "value": "Controller QA"},
            {"op": "click", "target": "apply"}, {"op": "scroll", "target": "list", "value": 30},
            {"op": "resize", "width": 730, "height": 505},
        ]},
    ],
}


def scenario_tool(registered, role):
    tool = "registered-gui-scenario" if registered else "fixed-gui-scenario"
    if tool not in ROLES.get(role, ()):
        raise Blocked("GUI tool denied for this role")
    return tool


def scope(project_id, revision, timeout_seconds, role="Verifier", descriptor=None):
    tool = scenario_tool(descriptor is not None, role)
    executable = _system_path()
    check_path(executable.parent)
    with _open_locked(executable) as (stream, _handle):
        data = stream.read(8 * 1024 * 1024 + 1)
    if not data or len(data) > 8 * 1024 * 1024:
        raise Blocked("GUI fixture executable size invalid")
    exe_hash = sha256(data).hexdigest()
    return {"project_id": project_id, "project_revision": revision, "role": role,
            "tool": tool, "scenario": SCENARIO if descriptor is None else PROJECT_SCENARIO,
            "exe": str(executable), **(descriptor or {}),
            "exe_sha256": exe_hash, "fixture_sha256": FIXTURE_SHA256,
            "timeout_seconds": timeout_seconds,
            "data": "FIXTURE_ONLY" if descriptor is None else "SYNTHETIC_PROJECT_FORM",
            "capture": "OWNED_WINDOW_RENDER"}


def _small_file(path, limit):
    check_path(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise Blocked("GUI artifact size invalid")
    return data


def verified_image(path, expected=None):
    data = _small_file(path, 4 * 1024 * 1024)
    if len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise Blocked("GUI artifact is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not (100 <= width <= 2048 and 100 <= height <= 2048):
        raise Blocked("GUI artifact dimensions invalid")
    checksum = sha256(data).hexdigest()
    if expected is not None and expected != checksum:
        raise Blocked("GUI artifact hash changed")
    return data, {"file": path.name, "sha256": checksum, "width": width, "height": height}


class _Window:
    def __init__(self, handles, names, pid, check, alive):
        if (set(handles) != names or len(set(handles.values())) != len(names)
                or any(type(value) is not int or value <= 0 for value in handles.values())):
            raise Blocked("Owned control manifest invalid")
        user = c.WinDLL("user32", winmode=0x800, use_last_error=True)
        self.owner = signature(user, "GetWindowThreadProcessId", w.DWORD, w.HWND, c.POINTER(w.DWORD))
        self.ancestor = signature(user, "GetAncestor", w.HWND, w.HWND, w.UINT)
        self.send = signature(user, "SendMessageTimeoutW", c.c_ssize_t, w.HWND, w.UINT,
                              w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t))
        self.post = signature(user, "PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        self.size = signature(user, "GetWindowRect", w.BOOL, w.HWND, c.POINTER(w.RECT))
        self.place = signature(user, "SetWindowPos", w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int,
                               c.c_int, c.c_int, w.UINT)
        self.menu = signature(user, "GetMenu", w.HMENU, w.HWND)
        self.submenu = signature(user, "GetSubMenu", w.HMENU, w.HMENU, c.c_int)
        self.menu_count = signature(user, "GetMenuItemCount", c.c_int, w.HMENU)
        self.menu_text = signature(user, "GetMenuStringW", c.c_int, w.HMENU, w.UINT, w.LPWSTR, c.c_int, w.UINT)
        self.menu_id = signature(user, "GetMenuItemID", w.UINT, w.HMENU, c.c_int)
        self.pid, self.handles, self.check, self.alive = pid, handles, check, alive
        self.thread = self._owner_thread(handles["root"])
        for name in handles:
            self.owned(name)

    def _owner_thread(self, target):
        pid = w.DWORD()
        thread = self.owner(target, c.byref(pid))
        if not self.alive() or not thread or pid.value != self.pid:
            raise Blocked("GUI process ownership changed")
        return thread

    def owned(self, name):
        target = self.handles[name]
        if (self._owner_thread(target) != self.thread
                or self.ancestor(target, 2) != self.handles["root"]):
            raise Blocked("GUI window ownership changed")
        return target

    def message(self, name, message, first=0, second=0):
        self.check()
        result = c.c_size_t()
        _require(self.send(self.owned(name), message, first, second, 0x23, 1000, c.byref(result)))
        return result.value

    def click(self, name):
        self.message(name, 0x00F5)  # BM_CLICK, no desktop input

    def click_menu(self, captions):
        self.check()
        menu = self.menu(self.owned("root"))
        for level, caption in enumerate(captions):
            count = self.menu_count(menu) if menu else -1
            if not 1 <= count <= 32:
                raise Blocked("Owned native menu unavailable")
            matches = []
            for index in range(count):
                text = c.create_unicode_buffer(128)
                self.menu_text(menu, index, text, len(text), 0x400)
                if text.value == caption:
                    matches.append(index)
            if len(matches) != 1:
                raise Blocked("Owned native menu caption is missing or ambiguous")
            index = matches[0]
            if level < len(captions) - 1:
                menu = self.submenu(menu, index)
        command = self.menu_id(menu, index)
        if not 0 < command < 65536:
            raise Blocked("Owned menu item has no native command")
        self.message("root", 0x0111, command)

    def text(self, name):
        buffer = c.create_unicode_buffer(128)
        self.message(name, 0x000D, len(buffer), c.addressof(buffer))
        return buffer.value

    def close(self):
        _require(self.post(self.owned("root"), 0x0010, 0, 0))  # WM_CLOSE

    def dimensions(self):
        rect = w.RECT()
        _require(self.size(self.owned("root"), c.byref(rect)))
        return rect.right - rect.left, rect.bottom - rect.top

    def resize(self, width, height):
        self.check()
        # Preserve location and z-order. Do not activate the window.
        _require(self.place(self.owned("root"), None, 0, 0, width, height, 0x4016))


def _exercise(window, directory, wait_for, observed, scenes, form):
    for number, scene in enumerate(form["scenes"], 1):
        actions = [_perform(window, step, wait_for, observed) for step in scene["steps"]]
        title = scene["title"]
        status = window.text(form["controls"]["status"])
        if status != scene["expected"]:
            raise Blocked("Observed GUI status did not match the scenario")
        window.click("capture")
        original = directory / f"scene-{number}.png"
        annotated = directory / f"report-{number}.png"
        wait_for(annotated.exists)
        source = verified_image(original)[1]
        processed = verified_image(annotated)[1]
        scenes.append({"title": title, "input": ", ".join(actions) or "Start owned app", "observed": status,
                       "captured_at": datetime.now(timezone.utc).isoformat(),
                       "original": source, "processed": processed})
        observed[title] = status

    if len({scene["original"]["sha256"] for scene in scenes}) != len(scenes):
        raise Blocked("GUI representative scenes were duplicates")


def _perform(window, step, wait_for, observed):
    operation = step["op"]
    if operation == "menu":
        window.click_menu(step["path"])
        return "Menu " + " › ".join(step["path"])
    if operation == "resize":
        dimensions = (step["width"], step["height"])
        before = window.dimensions()
        if before == dimensions:
            raise Blocked("GUI did not resize")
        window.resize(*dimensions)
        wait_for(lambda: window.dimensions() == dimensions)
        observed.update(window_size_before=list(before), window_size_after=list(dimensions))
        return f"Resize {dimensions[0]}×{dimensions[1]}"
    target = step["target"]
    if operation == "click":
        window.click(target)
    elif operation == "text":
        content = c.create_unicode_buffer(step["value"])
        window.message(target, 0x000C, second=c.addressof(content))
        if window.text(target) != content.value:
            raise Blocked("GUI text input was not observed")
    elif operation == "scroll":
        window.message(target, 0x0197, step["value"])
        if window.message(target, 0x018E) != step["value"]:
            raise Blocked("GUI list did not scroll")
        observed["list_top_index"] = step["value"]
    else:
        raise Blocked("GUI operation not admitted")
    return f"{operation.capitalize()} {target}"


def run_fixture(approved, directory, cancelled, admission, locked_executable, form=None, exe_data=None):
    """Return observations only after the Job, handles and app have been accounted for."""
    expected = ("fixed-gui-scenario", SCENARIO) if form is None else ("registered-gui-scenario", PROJECT_SCENARIO)
    from .gui_exe import EXE_MEMORY, SOURCE_SHA256

    executable_fixture = exe_data is not None
    expected_source = SOURCE_SHA256 if executable_fixture else FIXTURE_SHA256
    if ((approved["tool"], approved["scenario"]) != expected
            or approved["tool"] not in ROLES.get(approved["role"], ())
            or approved["fixture_sha256"] != expected_source
            or (form is not None and approved.get("form_sha256") != digest(form))
            or not 1 <= approved["timeout_seconds"] <= 20):
        raise Blocked("GUI role or scenario outside the approved scope")
    if executable_fixture and (form is None or approved.get("trust") != "CONTROLLER_BUILT_TEST_EXE"
                               or approved.get("arguments") != [] or approved.get("cwd") != "PRIVATE_STAGING"
                               or approved.get("memory_bytes") != EXE_MEMORY
                               or sha256(exe_data).hexdigest() != approved["exe_sha256"]):
        raise Blocked("Trusted EXE launch does not match approval")
    api = _api()
    kernel = c.WinDLL("kernel32", winmode=0x800, use_last_error=True)
    times = signature(kernel, "GetProcessTimes", w.BOOL, w.HANDLE, *([c.POINTER(w.FILETIME)] * 4))
    membership = signature(kernel, "IsProcessInJob", w.BOOL, w.HANDLE, w.HANDLE, c.POINTER(w.BOOL))
    info = ProcessInfo()
    exit_code = w.DWORD()
    observed, scenes = {}, []
    comparison = None
    drained = False
    status, failure = "NOT_VERIFIED", None
    window = None
    active = total = None
    graceful = False
    deadline = monotonic() + approved["timeout_seconds"]
    created = None

    def check():
        if cancelled():
            raise Blocked("CANCELLED")
        if monotonic() >= deadline:
            raise Blocked("TIME_LIMIT")
        admission()

    def wait_for(ready):
        while True:
            check()
            if ready():
                return
            if api.WaitForSingleObject(info.process, 0) != 258:
                raise Blocked("Fixture exited before UI observation")
            sleep(0.03)

    if not executable_fixture:
        exe, recheck = locked_executable
        if str(exe) != approved["exe"]:
            raise Blocked("GUI executable path changed")

    def creation_time():
        fields = [w.FILETIME() for _ in range(4)]
        _require(times(info.process, *(c.byref(field) for field in fields)))
        return fields[0].dwHighDateTime, fields[0].dwLowDateTime

    def alive():
        if created is None or api.WaitForSingleObject(info.process, 0) != 258:
            return False
        member = w.BOOL()
        _require(membership(info.process, job, c.byref(member)))
        return bool(member.value and creation_time() == created)

    manifest = directory / "windows.json"
    names = {"root", "capture", *(form or DEFAULT_FORM)["controls"].values()}

    def resolve_window():
        return _Window(json.loads(_small_file(manifest, 4096)), names, info.pid, check, alive)

    job = _require(api.CreateJobObjectW(None, None))
    try:
        limits = ExtendedLimits()
        limits.basic.flags = (JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
                              | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.basic.active_limit = 2
        limits.job_memory = EXE_MEMORY
        _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        check()
        if form is not None:
            with (directory / "approved-form.json").open("x", encoding="utf-8") as stream:
                json.dump(form, stream, ensure_ascii=True)
        if executable_fixture:
            exe = directory / "approved-app.exe"
            with exe.open("xb") as stream:
                stream.write(exe_data)
            argv = []
        else:
            recheck()
            argv = ["-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand",
                    b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")]
        check()
        if executable_fixture:
            with _open_locked(exe) as (stream, _handle):
                if sha256(stream.read(len(exe_data) + 1)).hexdigest() != approved["exe_sha256"]:
                    raise Blocked("Staged GUI executable changed")
                _launch(api, job, exe, argv, directory, info)
        else:
            _launch(api, job, exe, argv, directory, info)
        created = creation_time()
        if _account(api, job).active != 1 or not alive() or api.ResumeThread(info.thread) != 1:
            raise Blocked("Owned GUI process admission unverified")
        wait_for(manifest.exists)
        window = resolve_window()
        _exercise(window, directory, wait_for, observed, scenes, form or DEFAULT_FORM)
        comparison_path = directory / "comparison.png"
        wait_for(comparison_path.exists)
        comparison = verified_image(comparison_path)[1]
        check()
        status = "PASS"
    except (Blocked, OSError, ValueError, TypeError) as error:
        status = "NOT_VERIFIED" if str(error) in {"CANCELLED", "TIME_LIMIT"} else "FAIL"
        failure = str(error)
    finally:
        try:
            if info.process:
                try:
                    if alive():
                        (window or resolve_window()).close()
                        graceful = True
                except (Blocked, OSError, ValueError, TypeError):
                    pass
                counts = _wait_empty(api, job, monotonic() + (3 if graceful else 0))
                if counts.active:
                    graceful = False
                    _require(api.TerminateJobObject(job, KILL_EXIT))
                    counts = _wait_empty(api, job, monotonic() + 3)
                if counts.active or api.WaitForSingleObject(info.process, 1000) != 0:
                    raise Blocked("Owned GUI Job drain unverified")
                _require(api.GetExitCodeProcess(info.process, c.byref(exit_code)))
                active, total = counts.active, counts.total
                drained = True
        finally:
            _cleanup(api, job, info, drained)
    if status == "PASS" and (not graceful or active != 0 or exit_code.value != 0):
        status, failure = "FAIL", "Fixture did not exit cleanly"
    return {"result": status, "reason": failure, "observed": observed, "scenes": scenes,
            "comparison": comparison,
            "exit_code": exit_code.value if drained else None, "job_active": active, "job_total": total,
            "job_handles": "CLOSED", "pipes": "NONE_CREATED", "graceful_close": graceful}


def _launch(api, job, exe, argv, directory, info):
    _create_owned(api, job, exe, argv, directory,
                  {"SystemRoot": str(_known_folder(0x24)), "TEMP": str(directory), "TMP": str(directory)}, info)
