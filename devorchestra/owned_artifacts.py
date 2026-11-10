"""Remove only the disposable workspace created for a fenced Controller run."""
import ctypes as c
from ctypes import wintypes as w
import re
from shutil import rmtree

from .lease import Blocked
from .ledger import check_path


def _require_unopened(path):
    kernel = c.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
    create.restype = w.HANDLE
    handle = create(str(path), 0x80000000, 0, None, 3, 0x00200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.CloseHandle.restype = w.BOOL
    if not kernel.CloseHandle(handle):
        raise c.WinError(c.get_last_error())


def workspace(ledger, run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise Blocked("Invalid disposable workspace owner")
    parent = ledger.local_directory("temp")
    root = parent / ("build-verifier-" + run_id)
    check_path(root)
    return root


def cleanup(ledger, run_id, root, identity):
    """Called only after all stages have proved their owned Jobs empty."""
    if root != workspace(ledger, run_id):
        raise Blocked("Disposable workspace ownership changed")
    return _remove_owned_tree(root, identity)


def _remove_owned_tree(root, identity):
    result = {"classification": "AUTO_CLEAN", "workspace": root.name}
    try:
        check_path(root)
        if root.stat().st_ino != identity:
            raise Blocked("Disposable workspace ownership changed")
        count = size = 0
        pending = [root]
        while pending:
            folder = pending.pop()
            check_path(folder)
            for path in folder.iterdir():
                check_path(path)
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    _require_unopened(path)
                    count += 1
                    size += path.stat().st_size
                else:
                    raise Blocked("Unrecognized entry in disposable workspace")
        check_path(root)
        if root.stat().st_ino != identity:
            raise Blocked("Disposable workspace ownership changed")
        rmtree(root)
        if root.exists():
            raise Blocked("Disposable workspace still present")
    except (Blocked, OSError):
        return {**result, "classification": "REVIEW_CANDIDATE", "status": "UNCONFIRMED",
                "reason": "Disposable workspace ownership or removal could not be confirmed"}
    return {**result, "status": "REMOVED", "files": count, "logical_bytes": size}


def review(ledger, retain):
    """Report historical candidates without assuming that references are absent."""
    if type(retain) is not int or not 1 <= retain <= 20:
        raise Blocked("Retain count must be between 1 and 20")
    base = ledger.state_boundary
    events = ledger.read() if ledger.path.exists() else []
    completed = {event["run_id"] for event in events if event["kind"] == "build_verifier_result"}
    pending = {event["cleanup_plan"]["workspace"] for event in events
               if event["kind"] == "build_verifier_intent" and "cleanup_plan" in event
               and event["run_id"] not in completed}
    entries, releases = [], []
    for category in ("release", "temp"):
        directory = base / category
        check_path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            row = {"path": f"{category}/{path.name}", "classification": "REVIEW_CANDIDATE",
                   "reason": "Ownership and references have not been proven for removal"}
            entries.append(row)
            try:
                check_path(path)
                if category == "temp" and path.name in pending:
                    row.update(classification="PROTECTED", reason="Unsettled Controller run")
                elif category == "release" and path.is_dir() and (match := re.fullmatch(
                        r"(\d{1,5})\.(\d{1,5})\.(\d{1,5})-[0-9a-f]{12}-[0-9a-f]{8}", path.name)):
                    exe = path / "dist" / "DevOrchestra.exe"
                    check_path(exe)
                    if exe.is_file():
                        row["exe_bytes"] = exe.stat().st_size
                        releases.append((tuple(map(int, match.groups())), path.stat().st_mtime_ns, row))
            except (Blocked, OSError):
                row.update(classification="PROTECTED", reason="Path boundary could not be verified")
    for _, _, row in sorted(releases, key=lambda item: item[:2], reverse=True)[:retain]:
        row.update(classification="RETAIN", reason="Recent local build")
    return {"retain_recent_builds": retain, "entries": entries, "automatic_historical_removal": False}
