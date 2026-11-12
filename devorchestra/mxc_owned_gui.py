"""Operate the WinForms process launched inside the same MXC build Job fence."""
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
import re
from struct import pack
from threading import Thread
from time import monotonic, sleep
from zlib import compress, crc32

from .gui_verification import _Window, _perform, _small_file, verified_image
from .job_probe import _require
from .lease import Blocked
from .role_contract import _unique_object
from .system_powershell import _api as signature


def validate(value):
    from .gui_project import _text, _validate_step

    if type(value) is not dict or set(value) != {"controls", "scenes"}:
        raise Blocked("SDK GUI scenario must declare controls and scenes")
    controls = value["controls"]
    if (type(controls) is not dict or set(controls) != {"status", "open", "apply", "entry", "list"}
            or any(type(name) is not str or name == "root" or not re.fullmatch("[a-z][a-z0-9-]{0,30}", name)
                   for name in controls.values()) or len(set(controls.values())) != len(controls)):
        raise Blocked("SDK GUI scenario control mapping is invalid")
    scenes = value["scenes"]
    if type(scenes) is not list or not 2 <= len(scenes) <= 3:
        raise Blocked("SDK GUI scenario requires two to three capture scenes")
    for scene in scenes:
        if type(scene) is not dict or set(scene) != {"title", "expected", "steps"}:
            raise Blocked("SDK GUI scene is invalid")
        _text(scene["title"], 45)
        _text(scene["expected"], 120)
        if type(scene["steps"]) is not list or len(scene["steps"]) > 8:
            raise Blocked("SDK GUI scene actions exceed the bound")
        for step in scene["steps"]:
            if type(step) is dict and step.get("op") in ("expect-text", "expect-selection"):
                if set(step) != {"op", "target", "value"}:
                    raise Blocked("SDK GUI checkpoint fields are invalid")
                if step["op"] == "expect-text":
                    if step["target"] not in ("root", controls["status"]):
                        raise Blocked("Text checkpoints require the owned window or status control")
                    _text(step["value"], 120)
                elif (step["target"] != controls["list"] or type(step["value"]) is not int
                      or not 0 <= step["value"] <= 100):
                    raise Blocked("Selection checkpoint must be a bounded owned list index")
            else:
                _validate_step(step, controls)
    if len({scene["title"] for scene in scenes}) != len(scenes) or not any(
            scene["steps"] for scene in scenes):
        raise Blocked("SDK GUI scene names or interaction evidence are incomplete")
    return value


def verify_images(ledger, evidence, run_id, scenario, *, require_pass=True):
    if (not evidence or evidence.get("artifact_dir") != f"gui-{run_id}"
            or not re.fullmatch("[0-9a-f]{32}", run_id)
            or (require_pass and not evidence.get("passed"))
            or len(evidence.get("scenes", ())) != len(scenario["scenes"])):
        raise Blocked("WinForms visual evidence does not match the approved run")
    folder = ledger.local_directory("logs") / evidence["artifact_dir"]
    for index, (scene, approved) in enumerate(zip(evidence["scenes"], scenario["scenes"]), 1):
        image = scene["original"]
        if (image["file"] != f"scene-{index}.png" or scene["title"] != approved["title"]
                or scene["expected"] != approved["expected"]
                or (require_pass and scene["observed"] != approved["expected"])):
            raise Blocked("WinForms capture does not match the approved scene")
        if verified_image(folder / image["file"], image["sha256"])[1] != image:
            raise Blocked("WinForms image dimensions changed")
    return evidence


class _JobPids(c.Structure):
    _fields_ = [("assigned", w.DWORD), ("count", w.DWORD), ("ids", c.c_size_t * 8)]


def _job_pids(api, job):
    result = _JobPids()
    _require(api.QueryInformationJobObject(job, 3, c.byref(result), c.sizeof(result), None))
    if result.count != result.assigned or result.count > len(result.ids):
        raise Blocked("MXC GUI Job process list is incomplete")
    return set(result.ids[:result.count])


class _Bitmap(c.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                ("image", w.DWORD), ("x", w.LONG), ("y", w.LONG),
                ("used", w.DWORD), ("important", w.DWORD)]


def _render(hwnd, width, height):
    if not 100 <= width <= 900 or not 100 <= height <= 700:
        raise Blocked("Owned capture dimensions exceed the bound")
    gdi = c.WinDLL("gdi32", winmode=0x800, use_last_error=True)
    dc = _require(signature(gdi, "CreateCompatibleDC", w.HDC, w.HDC)(None))
    select = signature(gdi, "SelectObject", w.HANDLE, w.HDC, w.HANDLE)
    delete = signature(gdi, "DeleteObject", w.BOOL, w.HANDLE)
    bitmap = previous = None
    try:
        info = _Bitmap(c.sizeof(_Bitmap), width, -height, 1, 32)
        pixels = c.c_void_p()
        create = signature(gdi, "CreateDIBSection", w.HANDLE, w.HDC, c.POINTER(_Bitmap), w.UINT,
                           c.POINTER(c.c_void_p), w.HANDLE, w.DWORD)
        bitmap = _require(create(dc, c.byref(info), 0, c.byref(pixels), None, 0))
        previous = _require(select(dc, bitmap))
        user = c.WinDLL("user32", winmode=0x800, use_last_error=True)
        _require(signature(user, "PrintWindow", w.BOOL, w.HWND, w.HDC, w.UINT)(hwnd, dc, 2))
        data = c.string_at(pixels, width * height * 4)
    finally:
        if previous:
            select(dc, previous)
        if bitmap:
            delete(bitmap)
        signature(gdi, "DeleteDC", w.BOOL, w.HDC)(dc)
    return data


def _capture(window, path, workers):
    from concurrent.futures import Future

    width, height = window.dimensions()
    future = Future()

    def render():
        try:
            future.set_result(_render(window.owned("root"), width, height))
        except (Blocked, OSError, ValueError) as error:
            future.set_exception(error)

    worker = Thread(target=render, daemon=True)
    workers.append(worker)
    worker.start()
    while not future.done():
        window.check()
        worker.join(0.03)
    data = future.result()
    if len(set(data)) < 8:
        raise Blocked("Owned window rendered no usable visual evidence")
    stride = width * 4
    rows = []
    for offset in range(0, len(data), stride):
        bgra = data[offset:offset + stride]
        row = bytearray(width * 3)
        row[0::3], row[1::3], row[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        rows.append(b"\0" + row)

    def chunk(tag, payload):
        return pack(">I", len(payload)) + tag + payload + pack(">I", crc32(tag + payload))

    with path.open("xb") as stream:
        stream.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", compress(b"".join(rows))) + chunk(b"IEND", b""))
    return verified_image(path)[1]


def capture_owned_window(api, job, pid, hwnd, check, image_path, workers, *, input_probe=False, before_input=None):
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
                if before_input is not None:
                    before = _capture(window, before_input, workers)
                    before["captured_at"] = datetime.now(timezone.utc).isoformat()
                window.message("root", 0x0100, 0x20, 0x00390001)  # WM_KEYDOWN, Space
                window.message("root", 0x0101, 0x20, 0xC0390001)  # WM_KEYUP
                if before_input is not None:
                    window.click_client(24, 24)
                    sleep(0.2)
                    check()
                after = _capture(window, image_path, workers)
                result = {"image": after, "initial_size": list(initial), "resized": list(requested)}
                if before_input is not None:
                    after["captured_at"] = datetime.now(timezone.utc).isoformat()
                    result.update(before_input=before, screen_changed=before["sha256"] != after["sha256"],
                                  input="SPACE_AND_CLIENT_CLICK_SENT")
                return result
            return _capture(window, image_path, workers)
        finally:
            if alive():
                window.close()
    finally:
        _require(api.CloseHandle(process))


def inspect_owned_window(api, job, deadline, check, image_path, workers, *, before_input=None):
    user = c.WinDLL("user32", winmode=0x800, use_last_error=True)
    callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    enum = signature(user, "EnumWindows", w.BOOL, callback, w.LPARAM)
    owner = signature(user, "GetWindowThreadProcessId", w.DWORD, w.HWND, c.POINTER(w.DWORD))
    visible = signature(user, "IsWindowVisible", w.BOOL, w.HWND)

    def current():
        check()
        if monotonic() >= deadline:
            raise Blocked("Owned verification window did not appear")

    while True:
        current()
        members = _job_pids(api, job)
        if not members:
            raise Blocked("Owned process exited before window observation")
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
            return capture_owned_window(api, job, pid, hwnd, current, image_path, workers, input_probe=True,
                                   before_input=before_input)
        sleep(0.05)


def _inside_client(window, controls):
    user = c.WinDLL("user32", winmode=0x800, use_last_error=True)
    visible = signature(user, "IsWindowVisible", w.BOOL, w.HWND)
    client = signature(user, "GetClientRect", w.BOOL, w.HWND, c.POINTER(w.RECT))
    origin = signature(user, "ClientToScreen", w.BOOL, w.HWND, c.POINTER(w.POINT))
    hwnd = window.owned("root")
    bounds, point = w.RECT(), w.POINT()
    _require(client(hwnd, c.byref(bounds)))
    _require(origin(hwnd, c.byref(point)))
    for name in controls:
        handle = window.owned(name)
        if not visible(handle):
            continue
        rect = w.RECT()
        _require(window.size(handle, c.byref(rect)))
        if not (point.x <= rect.left < rect.right <= point.x + bounds.right
                and point.y <= rect.top < rect.bottom <= point.y + bounds.bottom):
            return False
    return True


def execute(execution, dll, output, scenario, runtime):
    manifest = output / "windows.json"
    execution.environment["DEVORCHESTRA_GUI_MANIFEST"] = str(manifest)
    scenes, observations, workers, checkpoints = [], {}, [], []

    def interact(api, job, deadline):
        def check():
            execution.check()
            if monotonic() >= deadline:
                raise Blocked("SDK GUI time limit")

        def wait_for(ready):
            while True:
                check()
                if ready():
                    return
                if not _job_pids(api, job):
                    raise Blocked("SDK GUI process exited before observation")
                sleep(0.03)

        wait_for(manifest.is_file)
        try:
            value = json.loads(_small_file(manifest, 4096), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError) as error:
            raise Blocked("SDK GUI process manifest invalid") from error
        if (type(value) is not dict or set(value) != {"pid", "handles"}
                or type(value["pid"]) is not int or value["pid"] not in _job_pids(api, job)
                or type(value["handles"]) is not dict):
            raise Blocked("SDK GUI PID is not owned by the MXC Job")
        pid = value["pid"]
        proc = _require(signature(api, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(
            0x00101000, False, pid))  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        member = signature(api, "IsProcessInJob", w.BOOL, w.HANDLE, w.HANDLE, c.POINTER(w.BOOL))
        window = None
        try:
            def alive():
                owned = w.BOOL()
                return (api.WaitForSingleObject(proc, 0) == 258
                        and bool(member(proc, job, c.byref(owned)) and owned.value))

            window = _Window(value["handles"], {"root", *scenario["controls"].values()}, pid, check, alive)
            for index, scene in enumerate(scenario["scenes"], 1):
                actions = []
                for step in scene["steps"]:
                    if step["op"] in ("expect-text", "expect-selection"):
                        actual = (window.text(step["target"]) if step["op"] == "expect-text" else
                                  window.message(step["target"], 0x0188))  # LB_GETCURSEL
                        checkpoints.append({"title": f"{scene['title']} / {step['op']} {step['target']}",
                                            "expected": step["value"], "actual": actual})
                        actions.append(step["op"] + " " + step["target"])
                    else:
                        actions.append(_perform(window, step, wait_for, observations))
                status = window.text(scenario["controls"]["status"])
                image = _capture(window, output / f"scene-{index}.png", workers)
                layout = _inside_client(window, scenario["controls"].values())
                scenes.append({"title": scene["title"], "input": ", ".join(actions) or "Start owned app",
                               "observed": status, "expected": scene["expected"], "original": image,
                               "processed": image, "layout_inside_client": layout,
                               "captured_at": datetime.now(timezone.utc).isoformat()})
                observations[scene["title"]] = status
        finally:
            try:
                if window is not None and alive():
                    window.close()
            finally:
                _require(api.CloseHandle(proc))

    try:
        report, receipt = execution.step("test", ["exec", "--fx-version", runtime, str(dll)], interact=interact)
    finally:
        execution.environment.pop("DEVORCHESTRA_GUI_MANIFEST")
        for worker in workers:
            worker.join(1)
            if worker.is_alive():
                raise Blocked("Owned window capture thread did not settle")
    return {**report, "receipt": receipt, "gui": {"scenes": scenes, "observed": observations,
            "checkpoints": checkpoints,
            "passed": len(scenes) == len(scenario["scenes"])
            and len({scene["original"]["sha256"] for scene in scenes}) == len(scenes)
            and all(item["actual"] == item["expected"] for item in checkpoints)
            and all(scene["observed"] == scene["expected"] and scene["layout_inside_client"] for scene in scenes)}}
