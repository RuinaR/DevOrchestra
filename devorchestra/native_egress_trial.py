"""Controller-owned, separately approved WFP recovery experiment with no worker or model."""
import ctypes as c
from ctypes import wintypes as w
from hashlib import file_digest, sha256
from pathlib import Path
import re
from time import time
from uuid import UUID, uuid4

from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_launcher import _known_folder
from .replica_evidence import fingerprint
from .system_powershell import _api, _open_locked


KEY_PREFIX = "54b49f93-eb64-4c60-874b-87dc274cc6"
ACCOUNT = "DevOrchP2116"
# Reviewed MSVC x64 build of the fixed pilot. Rebuilding with different inputs requires re-pinning.
PILOT_SHA256 = "9adb557c873388da3f48441ff7a9b8d9f87a857966709ed6cf633949f0d8ea6f"


def readback():
    """Independent reads of only the fixed pilot resources. Denied reads stay unknown."""
    ptr = c.c_void_p
    library = c.WinDLL("fwpuclnt", winmode=0x800)
    start = _api(library, "FwpmEngineOpen0", w.DWORD, w.LPCWSTR, w.UINT, ptr, ptr, c.POINTER(w.HANDLE))
    close = _api(library, "FwpmEngineClose0", w.DWORD, w.HANDLE)
    free = _api(library, "FwpmFreeMemory0", None, c.POINTER(ptr))
    engine = w.HANDLE()
    code = start(None, 10, None, None, c.byref(engine))
    if code:
        raise Blocked(f"WFP readback unavailable ({code})")
    result = {}
    try:
        for name, keys, absent in (("Provider", (1,), 0x80320005), ("SubLayer", (2,), 0x80320007),
                                   ("Filter", range(16, 32), 0x80320003)):
            lookup = _api(library, f"Fwpm{name}GetByKey0", w.DWORD, w.HANDLE, ptr, c.POINTER(ptr))
            observations = []
            for key in keys:
                guid = c.create_string_buffer(UUID(f"{KEY_PREFIX}{key:02x}").bytes_le)
                found = ptr()
                outcome = lookup(engine, guid, c.byref(found))
                if found.value:
                    free(c.byref(found))
                observations.append("ABSENT" if outcome == absent else "PRESENT" if outcome == 0 else "UNKNOWN")
            result[name.lower()] = observations
    finally:
        close(engine)
    net = c.WinDLL("netapi32", winmode=0x800)
    lookup = _api(net, "NetUserGetInfo", w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.POINTER(ptr))
    free_user = _api(net, "NetApiBufferFree", w.DWORD, ptr)
    found = ptr()
    code = lookup(None, ACCOUNT, 0, c.byref(found))
    if found.value:
        free_user(found)
    result["account"] = ["ABSENT" if code == 2221 else "PRESENT" if code == 0 else "UNKNOWN"]
    root = _known_folder(0x23) / "DevOrchestra"
    try:
        root.lstat()
    except FileNotFoundError:
        result["directory"] = ["ABSENT"]
    except OSError:
        result["directory"] = ["UNKNOWN"]
    else:
        result["directory"] = ["PRESENT"]
    return result


def _clear(observations):
    return observations == {"provider": ["ABSENT"], "sublayer": ["ABSENT"], "filter": ["ABSENT"] * 16,
                            "account": ["ABSENT"], "directory": ["ABSENT"]}


class _ShellExecuteInfo(c.Structure):
    _fields_ = [("size", w.DWORD), ("mask", w.ULONG), ("hwnd", w.HWND), ("verb", w.LPCWSTR),
                ("file", w.LPCWSTR), ("parameters", w.LPCWSTR), ("directory", w.LPCWSTR),
                ("show", c.c_int), ("instance", w.HINSTANCE), ("id_list", c.c_void_p),
                ("class_name", w.LPCWSTR), ("class_key", w.HKEY), ("hot_key", w.DWORD),
                ("icon_or_monitor", w.HANDLE), ("process", w.HANDLE)]


def _launch(binary, parameters, expected_hash):
    shell = c.WinDLL("shell32", winmode=0x800, use_last_error=True)
    kernel = c.WinDLL("kernel32", winmode=0x800, use_last_error=True)
    launch = _api(shell, "ShellExecuteExW", w.BOOL, c.POINTER(_ShellExecuteInfo))
    wait = _api(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
    exit_code = _api(kernel, "GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    close = _api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
    info = _ShellExecuteInfo()
    info.size, info.mask, info.verb = c.sizeof(info), 0x140, "runas"
    info.file, info.parameters, info.directory, info.show = str(binary), parameters, str(binary.parent), 1
    with _open_locked(binary, source="Pilot binary") as (stream, _):
        if file_digest(stream, "sha256").hexdigest() != expected_hash:
            raise Blocked("Pilot binary changed before UAC")
        if not launch(c.byref(info)) or not info.process:
            raise Blocked(f"Pilot UAC dispatch unconfirmed ({c.get_last_error()})")
        try:
            code = w.DWORD()
            if wait(info.process, 60_000) != 0 or not exit_code(info.process, c.byref(code)):
                raise Blocked("Pilot process completion unconfirmed")
            return code.value
        finally:
            close(info.process)


def _pending(events):
    pending = {}
    for event in events:
        if event["kind"] == "native_egress_trial_intent":
            pending[event["request_id"]] = event
        elif event["kind"] == "native_egress_trial_cleanup_verified":
            pending.pop(event["request_id"], None)
    return pending


def _review(ledger, events, head, binary, expires, recovery):
    if not int(time()) < expires <= time() + 300:
        raise Blocked("Use an approval expiry within the next five minutes")
    binary = Path(binary)
    check_path(binary)
    if (binary.name != "pilot.exe" or binary.parent.parent != ledger.local_directory("temp")
            or not binary.parent.name.startswith("wfp-trial-")):
        raise Blocked("Pilot must be in a dedicated wfp-trial-* staging directory")
    binary_sha256 = fingerprint(binary)["sha256"]
    if binary_sha256 != PILOT_SHA256:
        raise Blocked("Pilot binary differs from the reviewed build")
    pending = _pending(events)
    if recovery:
        old = pending.get(recovery)
        if not old or old["scope"]["binary"] != str(binary) or old["scope"]["binary_sha256"] != binary_sha256:
            raise Blocked("Recovery must use the same reviewed pilot for the pending trial")
        if old["scope"].get("mode") != "FAULT_BEFORE_ENABLE" or old["scope"].get("starts_worker") is not False:
            raise Blocked("CLEANUP_BLOCKED: disabled recovery cannot establish active-worker exit")
    else:
        if pending or not _clear(readback()):
            raise Blocked("CLEANUP_BLOCKED: pilot resources are present or unverified")
        fault_receipt = binary.parent / "pilot-fault-result.txt"
        check_path(fault_receipt)
        if fault_receipt.exists():
            raise Blocked("A new fault trial requires a fresh staging directory")
    return {"mode": "RECOVER_DISABLED" if recovery else "FAULT_BEFORE_ENABLE", "request_id": recovery,
            "binary": str(binary), "binary_sha256": binary_sha256, "expires": expires, "ledger_head": head,
            "account": ACCOUNT, "directory": "ProgramData/DevOrchestra/P2116", "wfp_key_prefix": KEY_PREFIX,
            "filters": 16, "external_network": False, "starts_worker": False}


def _receipt(binary, name, request_id, marker):
    receipt = binary.parent / name
    check_path(receipt)
    with receipt.open("rb") as stream:
        data = stream.read(4097)
    if len(data) > 4096 or f"TRIAL={request_id}".encode() not in data.splitlines() or marker not in data.splitlines():
        raise Blocked("Pilot terminal receipt unverified")
    return sha256(data).hexdigest(), b"POLICY_PRESENT_AFTER_FAULT=16" in data.splitlines()


def trial(ledger, binary, expires, approved=None, recovery=None):
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        if recovery:
            if not re.fullmatch(r"[0-9a-f]{32}", recovery):
                raise Blocked("Invalid recovery trial ID")
            events, head = ledger.snapshot(recover_journal=True)
        else:
            events, head = Runner(ledger)._idle_diagnostic(abandoned)
        scope = _review(ledger, events, head, binary, expires, recovery)
        approval = digest(scope)
        if approved is None:
            return {"status": "NEED_APPROVAL", "scope": scope, "approval_sha256": approval}
        if approved != approval:
            raise Blocked("WFP trial approval changed")
        request_id = recovery or uuid4().hex
        fields = {"request_id": request_id, "state": "DIAGNOSTIC", "scope": scope, "approval_sha256": approval}
        nonce = uuid4().hex
        kind = "native_egress_trial_recovery_intent" if recovery else "native_egress_trial_intent"
        count, fence = ledger.append(len(events), head, {**fields, "kind": kind, "attempt": nonce},
                                     admission=None if recovery else require_admission)
        binary = Path(scope["binary"])
        result = {**fields, "kind": "native_egress_trial_cleanup_blocked", "status": "CLEANUP_BLOCKED"}
        try:
            if recovery:
                code = _launch(binary, f"--recover-disabled {request_id} {nonce}", scope["binary_sha256"])
                if code != 0:
                    raise Blocked(f"Recovery helper exit {code}")
                result["receipt_sha256"], persisted = _receipt(
                    binary, f"pilot-recovery-{nonce}.txt", request_id, b"DISABLED_TRIAL_ABSENCE_VERIFIED=1")
                fault_seen = any(event.get("request_id") == request_id and event.get("expected_fault_observed") is True
                                 for event in events)
                result["policy_persistence"] = "OBSERVED" if persisted and fault_seen else "NOT_VERIFIED"
                result["readback"] = readback()
                if not _clear(result["readback"]):
                    raise Blocked("Independent cleanup readback is incomplete")
                result.update(kind="native_egress_trial_cleanup_verified", status="CLEANUP_VERIFIED")
            else:
                code = _launch(binary, f"--crash-before-enable {request_id}", scope["binary_sha256"])
                if code != 86:
                    raise Blocked(f"Fault helper exit {code}")
                result["receipt_sha256"], _ = _receipt(binary, "pilot-fault-result.txt", request_id,
                                                      b"FAULT_BEFORE_ACCOUNT_ENABLE=1")
                result["expected_fault_observed"] = True
                result["reason"] = "Expected fault before account enable. Explicit recovery required."
        except (Blocked, OSError) as error:
            result["reason"] = str(error) if isinstance(error, Blocked) else "Pilot I/O unconfirmed"
        except KeyboardInterrupt:
            result["reason"] = "Controller interrupted. Pilot cleanup is unconfirmed."
        ledger.append(count, fence, result, allow_shutdown=True)
        return result
