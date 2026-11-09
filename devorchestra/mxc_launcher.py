"""Internal one-shot native self-test runner. No CLI or GUI entry point."""
from dataclasses import dataclass
import ctypes as c
from ctypes import wintypes as w
from hashlib import sha256
import os
from pathlib import Path
import re
from shutil import copyfileobj
import sqlite3
import subprocess
from threading import Thread
from time import monotonic, time
from uuid import UUID, uuid4

from .development import session_view
from .lease import Blocked, execution_slot
from .ledger import check_path
from .mxc_diagnostic import MAX_STREAM_BYTES, native_probe_receipt
from .runtime import TERMINAL, last_run, require_admission


CLI_HASH = "4b3286e35d7fe866c6b0eb3f07d7a4cdb5e96489576170dfd6b3a15aa1c67382"
PROBE_HASH = "5bcaf68f8f75cc58db781812ff84c85e541042b3b7e6504f7d6d09816e598357"
CLI_VERSION = "0.167.0-alpha.3.2"  # Previously measured for these exact signed bytes.
TIME_LIMIT = 15
CLEANUP_LIMIT = 2
PROFILE = '''default_permissions = "mxc-file-test"
approval_policy = "never"

[permissions.mxc-file-test]
extends = ":workspace"

[permissions.mxc-file-test.filesystem]
":root" = "deny"
":minimal" = "read"
":tmpdir" = "deny"
":slash_tmp" = "deny"

[permissions.mxc-file-test.network]
enabled = false
'''


@dataclass(frozen=True)
class _OwnerApproval:
    """Receipt supplied by the trusted owner workflow, never read from model input."""

    request_id: str
    expires_epoch: int


def _signed_offline(path):
    class TrustFile(c.Structure):
        _fields_ = [("size", w.DWORD), ("name", w.LPCWSTR), ("file", w.HANDLE), ("subject", c.c_void_p)]

    class TrustData(c.Structure):
        _fields_ = [
            ("size", w.DWORD), ("policy", c.c_void_p), ("client", c.c_void_p),
            ("ui", w.DWORD), ("revocation", w.DWORD), ("choice", w.DWORD),
            ("file", c.POINTER(TrustFile)), ("action", w.DWORD), ("state", w.HANDLE),
            ("url", w.LPCWSTR), ("flags", w.DWORD), ("context", w.DWORD), ("settings", c.c_void_p),
        ]

    file = TrustFile(c.sizeof(TrustFile), str(path), None, None)
    data = TrustData()
    data.size, data.ui, data.choice = c.sizeof(data), 2, 1  # No UI, verify file.
    data.flags = 0x1000  # WTD_CACHE_ONLY_URL_RETRIEVAL
    data.file = c.pointer(file)
    action = (c.c_ubyte * 16).from_buffer_copy(UUID("00aac56b-cd44-11d0-8cc2-00c04fc295ee").bytes_le)
    verify = c.WinDLL("wintrust", winmode=0x800).WinVerifyTrust
    verify.argtypes = (w.HWND, c.c_void_p, c.POINTER(TrustData))
    verify.restype = w.LONG
    return verify(None, action, c.byref(data)) == 0


def _known_folder(folder):
    lookup = c.WinDLL("shell32", winmode=0x800).SHGetFolderPathW
    lookup.argtypes = (w.HWND, c.c_int, w.HANDLE, w.DWORD, w.LPWSTR)
    lookup.restype = w.LONG
    value = c.create_unicode_buffer(260)
    if lookup(None, folder | 0x4000, None, 0, value) != 0 or not value.value:
        raise Blocked("Host folder verification failed")
    return Path(value.value)


def _pin(path, expected):
    check_path(path)
    if not path.is_file():
        raise Blocked("Pinned executable missing")
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise Blocked("Executable pin mismatch")


def _prepare(lab):
    if os.name != "nt":
        raise Blocked("Windows diagnostic only")
    local, windows, program_data = (_known_folder(folder) for folder in (0x1C, 0x24, 0x23))
    system = windows / "System32"
    get_system = c.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW
    get_system.argtypes = (w.LPWSTR, w.UINT)
    get_system.restype = w.UINT
    folder = c.create_unicode_buffer(260)
    if not 0 < get_system(folder, len(folder)) < len(folder) or Path(folder.value) != system:
        raise Blocked("System directory verification failed")
    for name, value in {"LOCALAPPDATA": str(local), "SystemRoot": str(windows), "WINDIR": str(windows),
                        "SystemDrive": windows.drive, "ProgramData": str(program_data)}.items():
        if os.environ.get(name, "").casefold() != value.casefold():
            raise Blocked("Host initialization environment mismatch")
    # Only the exact installed artifact measured in P2.12 is accepted. The
    # hash binds the measured publisher/version; trust is rechecked offline.
    cli = local / "OpenAI" / "Codex" / "bin" / "f71a721e0e6193a9" / "codex.exe"
    probe = (Path(__file__).absolute().parent.parent / "build"
             / "mxc-native-bea36dcb9c28450e827a0f2482c6f605" / "work" / "mxc-probe.exe")
    _pin(cli, CLI_HASH)
    _pin(probe, PROBE_HASH)
    if not _signed_offline(cli):
        raise Blocked("Offline CLI signature check failed")
    managed = program_data / "OpenAI" / "Codex"
    for name in ("requirements.toml", "managed_config.toml", "config.toml"):
        path = managed / name
        check_path(path)
        if path.exists():
            raise Blocked("Managed configuration requires review")
    check_path(lab)
    root = lab / f"mxc-native-{uuid4().hex}"
    root.mkdir()
    work, home, temporary = (root / name for name in ("work", "codex-home", "tmp"))
    for directory in (work, home, temporary):
        directory.mkdir()
    with (home / "config.toml").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(PROFILE)
    copy = work / "mxc-probe.exe"
    with probe.open("rb") as source, copy.open("xb") as destination:
        copyfileobj(source, destination)
    _pin(copy, PROBE_HASH)
    if list(work.iterdir()) != [copy] or (home / "config.toml").read_text(encoding="utf-8") != PROFILE:
        raise Blocked("Diagnostic profile or work inventory changed")
    return cli, copy, work, {
        "SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
        "LOCALAPPDATA": str(local), "ProgramData": str(program_data), "PATH": str(system),
        "TEMP": str(temporary), "TMP": str(temporary), "CODEX_HOME": str(home),
    }


class _Capture:
    def __init__(self):
        self.data = bytearray()
        self.complete = self.overflow = False

    def drain(self, pipe):
        try:
            with pipe:
                while chunk := pipe.read(4096):
                    remaining = MAX_STREAM_BYTES - len(self.data)
                    self.overflow |= len(chunk) > remaining
                    self.data.extend(chunk[:remaining])
            self.complete = True
        except Exception:
            # No exception string or partial payload leaves the capture.
            pass


def _launch(prepared):
    cli, probe, work, env = prepared
    args = [str(cli), "-c", "windows.sandbox=mxc", "sandbox", "--include-managed-config",
            "--permission-profile", "mxc-file-test", "--", str(probe), "--self-test"]
    try:
        process = subprocess.Popen(args, executable=str(cli), cwd=work, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        return native_probe_receipt(preflight=True, cli_started=False, cli_exit=None,
                                    streams_complete=False), "UNKNOWN"
    deadline = monotonic() + TIME_LIMIT
    captures = [_Capture(), _Capture()]
    readers = [Thread(target=capture.drain, args=(pipe,), daemon=True)
               for capture, pipe in zip(captures, (process.stdout, process.stderr))]
    exit_code = None
    timed_out = False
    try:
        for reader in readers:
            reader.start()
        exit_code = process.wait(timeout=max(0, deadline - monotonic()))
    except (Exception, KeyboardInterrupt):
        timed_out = True
        try:
            process.kill()  # Only the Popen handle created above.
            exit_code = process.wait(timeout=CLEANUP_LIMIT)
        except (subprocess.TimeoutExpired, OSError):
            pass
    for reader in readers:
        if reader.ident is not None:
            reader.join(max(0, deadline - monotonic()) if not timed_out else 0)
    overflow = any(capture.overflow for capture in captures)
    complete = (not timed_out and not overflow
                and all(not reader.is_alive() and capture.complete for reader, capture in zip(readers, captures)))
    receipt = native_probe_receipt(preflight=True, cli_started=True, cli_exit=exit_code,
                                   streams_complete=complete,
                                   stdout=bytes(captures[0].data) if complete else b"",
                                   stderr=bytes(captures[1].data) if complete else b"")
    receipt.update(capture_complete=complete, capture_overflow=overflow, timed_out=timed_out)
    return receipt, "UNKNOWN"


def _run_approved_self_test(ledger, approval):
    """Trusted owner diagnostic only. No command, binary or profile input."""
    try:
        return _run(ledger, approval)
    except (OSError, sqlite3.Error, ValueError):
        raise Blocked("Diagnostic storage or evidence verification failed") from None


def _run(ledger, approval):
    if (type(approval) is not _OwnerApproval or type(approval.request_id) is not str
            or not re.fullmatch("[0-9a-f]{32}", approval.request_id)
            or type(approval.expires_epoch) is not int or time() >= approval.expires_epoch):
        raise Blocked("Current owner diagnostic approval required")
    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("Diagnostic slot ownership unverified")
        events, tail = ledger.snapshot(recover_journal=True)
        require_admission(events)
        run, session = last_run(events), session_view(events)
        if (run and run[-1]["state"] not in TERMINAL or session and not session["end"]
                or any(event["kind"].startswith("mxc_diagnostic_") for event in events)):
            raise Blocked("Diagnostic requires idle admission and unused evidence ledger")
        fields = {"project_id": None, "run_id": None, "state": "DIAGNOSTIC",
                  "source": "OWNED_MXC_DIAGNOSTIC", "request_id": approval.request_id,
                  "generation": len(events) + 1}
        try:
            prepared = _prepare(ledger.local_directory("temp", create=True))
        except (Blocked, OSError, ValueError):
            receipt = native_probe_receipt(preflight=False, cli_started=None, cli_exit=None, streams_complete=False)
            ledger.append(len(events), tail, {**fields, "kind": "mxc_diagnostic_result",
                                              "receipt": receipt, "owned_tree": "NOT_CREATED"},
                          admission=require_admission)
            return receipt
        if time() >= approval.expires_epoch:
            raise Blocked("Diagnostic approval expired before admission")
        count, head = ledger.append(len(events), tail, {
            **fields, "kind": "mxc_diagnostic_intent", "cli_sha256": CLI_HASH, "cli_version": CLI_VERSION,
            "probe_sha256": PROBE_HASH, "profile": "mxc-file-test", "command": "native --self-test",
        }, admission=require_admission)
        try:
            receipt, owned_tree = _launch(prepared)
        except Exception:
            raise Blocked("Diagnostic evidence collection interrupted") from None
        ledger.append(count, head, {**fields, "kind": "mxc_diagnostic_result",
                                    "receipt": receipt, "owned_tree": owned_tree}, allow_shutdown=True)
        return receipt
