"""Bounded stdio App Server transport for a Controller-owned Windows Job."""
from contextlib import ExitStack, contextmanager
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

from .job_probe import (
    ExtendedLimits, JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, ProcessInfo, _account, _api, _cleanup,
    _create_owned, _require, _wait_empty,
)
from .lease import Blocked
from .ledger import check_path
from .instructions import selected_source, source_ref


MAX_FRAME = 256 * 1024
INSPECTION_CONFIG = 'sandbox_mode = "read-only"\napproval_policy = "never"\n'


@dataclass(frozen=True)
class NativeInspectionApproval:
    request_id: str
    expires_epoch: float
    binary: Path
    lab_root: Path


def prepare_inspection(approval, lab_parent, *, access_probe=False):
    """Accept only an isolated, pre-staged lab with no credentials or plugins."""
    from .mxc_launcher import _known_folder

    root = approval.lab_root
    home, work, temporary = (root / part for part in ("home", "work", "tmp"))
    for path in (root, home, work, temporary):
        check_path(path)
    local = _known_folder(0x1C)
    root_entries = {"home", "work", "tmp"} | ({"protected"} if access_probe else set())
    work_entries = {"AGENTS.md"} | ({"allowed.txt"} if access_probe else set())
    if (root.parent != lab_parent
            or not root.name.startswith("app-server-")
            or (access_probe and root.name != f"app-server-{approval.request_id}")
            or {p.name for p in root.iterdir()} != root_entries
            or {p.name for p in home.iterdir()} != {"AGENTS.md", "config.toml"}
            or {p.name for p in work.iterdir()} != work_entries
            or any(temporary.iterdir())):
        raise Blocked("App Server lab inventory rejected")
    config = home / "config.toml"
    check_path(config)
    with config.open("rb") as stream:
        if stream.read(len(INSPECTION_CONFIG) + 1) != INSPECTION_CONFIG.encode("ascii"):
            raise Blocked("App Server lab configuration rejected")
    sources = instruction_sources(home, work)
    windows = _known_folder(0x24)
    env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
           "LOCALAPPDATA": str(local), "ProgramData": str(_known_folder(0x23)),
           "PATH": str(windows / "System32"), "CODEX_HOME": str(home),
           "TEMP": str(temporary), "TMP": str(temporary)}
    return work, env, sources


class AppServer:
    def __init__(self, source, sink, timeout, cancelled):
        self.sink = sink
        self.deadline = monotonic() + timeout
        self.cancelled = cancelled
        self.frames = Queue(maxsize=32)
        self.broken = Event()
        self.reader = Thread(target=self._read, args=(source,), daemon=True)
        self.reader.start()
        self.writer = None

    def _read(self, source):
        try:
            while True:
                line = source.readline(MAX_FRAME + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    self.broken.set()
                    break
                self.frames.put_nowait(line)
        except (OSError, Full, ValueError):
            self.broken.set()
        finally:
            try:
                self.frames.put_nowait(None)
            except Full:
                self.broken.set()

    def _check(self):
        if self.broken.is_set() or self.cancelled() or monotonic() >= self.deadline:
            raise Blocked("App Server transport stopped")

    def send(self, message):
        self._check()
        frame = (json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if len(frame) > MAX_FRAME:
            raise Blocked("App Server request exceeds frame limit")
        sent, failed = Event(), Event()

        def write():
            try:
                self.sink.write(frame)
                self.sink.flush()
            except (OSError, ValueError):
                failed.set()
            finally:
                sent.set()

        self.writer = Thread(target=write, daemon=True)
        self.writer.start()
        while not sent.wait(0.05):
            self._check()
        if failed.is_set():
            raise Blocked("App Server write failed")

    def receive(self):
        while True:
            self._check()
            try:
                line = self.frames.get(timeout=0.05)
                break
            except Empty:
                continue
        try:
            message = json.loads(line) if line is not None else None
        except (UnicodeError, ValueError, RecursionError):
            raise Blocked("Invalid App Server frame") from None
        if not isinstance(message, dict):
            raise Blocked("App Server frame or EOF was unexpected")
        return message

    def request(self, request_id, method, params, on_notification=None):
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            reply = self.receive()
            if "method" not in reply and type(reply.get("id")) is int and reply["id"] == request_id:
                if "error" in reply or not isinstance(reply.get("result"), dict):
                    raise Blocked("App Server rejected the request")
                return reply["result"]
            if "id" in reply or on_notification is None:
                # Approval requests and every unexpected response fail closed.
                raise Blocked("Unexpected App Server request or response")
            on_notification(reply)

    def initialize(self):
        result = self.request(0, "initialize", {"clientInfo": {
            "name": "devorchestra", "title": "DevOrchestra", "version": "0.1.0",
        }})
        if result.get("platformOs") != "windows":
            raise Blocked("Unexpected App Server platform")
        self.send({"method": "initialized"})
        return result

    def inspect_policy(self):
        """Read effective config and requirements without starting a thread."""
        self.initialize()
        config = self.request(1, "config/read", {"includeLayers": False})
        requirements = self.request(2, "configRequirements/read", {})
        values = config.get("config")
        if not isinstance(values, dict) or "requirements" not in requirements:
            raise Blocked("App Server policy response incomplete")
        sandbox = values.get("sandbox_mode")
        approval = values.get("approval_policy")
        return {
            "sandbox": sandbox if sandbox in ("read-only", "workspace-write") else "UNVERIFIED",
            "approval": approval if approval in ("never", "on-request") else "UNVERIFIED",
            "managed_requirements_present": requirements["requirements"] is not None,
        }


def instruction_sources(home, replica):
    """Fingerprint the expected root-level source in each scope, never its text."""
    result = {}
    for scope, root in (("global", home), ("project", replica)):
        try:
            path, content = selected_source(root)
        except Blocked as error:
            if str(error) == "Missing Codex instructions":
                raise Blocked(f"Missing {scope} Codex instructions") from None
            raise
        result[scope] = source_ref(path, content)
    return result


@contextmanager
def _owned_stdio(executable, argv, cwd, environment, timeout, cancelled):
    """Start atomically inside a non-breakaway Job; use only owned handles."""
    import msvcrt

    api = _api()
    with ExitStack() as resources:
        def pipe():
            read_fd, write_fd = os.pipe()
            reader = resources.enter_context(os.fdopen(read_fd, "rb"))
            writer = resources.enter_context(os.fdopen(write_fd, "wb"))
            return reader, writer

        child_in, stdin = pipe()
        stdout, child_out = pipe()
        stderr = resources.enter_context(open(os.devnull, "wb"))
        stdio = tuple(msvcrt.get_osfhandle(file.fileno()) for file in (child_in, child_out, stderr))
        for handle in stdio:
            os.set_handle_inheritable(handle, True)
        job = _require(api.CreateJobObjectW(None, None))
        info = ProcessInfo()
        connection = None
        drained = False
        try:
            limits = ExtendedLimits()
            limits.basic.flags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                  | JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY)
            limits.basic.active_limit = 8
            limits.job_memory = 512 * 1024 * 1024
            _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
            actual = ExtendedLimits()
            _require(api.QueryInformationJobObject(job, 9, c.byref(actual), c.sizeof(actual), None))
            if (actual.basic.flags != limits.basic.flags
                    or actual.basic.active_limit != limits.basic.active_limit
                    or actual.job_memory != limits.job_memory):
                raise Blocked("App Server Job limits unverified")
            if cancelled():
                raise Blocked("App Server launch cancelled")
            _create_owned(api, job, executable, argv, cwd, environment, info, stdio)
            child_in.close()
            child_out.close()
            stderr.close()
            if _account(api, job).active != 1 or api.ResumeThread(info.thread) != 1:
                raise Blocked("App Server ownership could not be confirmed")
            connection = AppServer(stdout, stdin, timeout, cancelled)
            yield connection
            stdin.close()
            while api.WaitForSingleObject(info.process, 50) == 258:
                connection._check()
            code = w.DWORD()
            if api.WaitForSingleObject(info.process, 0) != 0:
                raise Blocked("App Server root exit unverified")
            _require(api.GetExitCodeProcess(info.process, c.byref(code)))
            if code.value != 0:
                raise Blocked("App Server root exited unsuccessfully")
            if _wait_empty(api, job, connection.deadline).active:
                raise Blocked("App Server Job drain unverified")
            drained = True
        finally:
            _cleanup(api, job, info, drained)
            if connection is not None:
                connection.reader.join(1)
                if connection.writer is not None:
                    connection.writer.join(1)
                if connection.reader.is_alive() or (connection.writer and connection.writer.is_alive()):
                    raise Blocked("App Server pipe drain unverified")
                if drained and connection.broken.is_set():
                    raise Blocked("App Server output was incomplete")


@contextmanager
def open_app_server(binary: Path, cwd: Path, environment, timeout, cancelled, *, explicit_mxc=False):
    """Pinned App Server entry for a future approved Controller admission."""
    from .mxc_launcher import CLI_HASH, _pin, _signed_offline

    check_path(cwd)
    if (not cwd.is_dir() or binary.name.casefold() != "codex.exe"
            or not 0 < timeout <= 60):
        raise Blocked("App Server path or working directory rejected")
    _pin(binary, CLI_HASH)
    if not _signed_offline(binary):
        raise Blocked("App Server signature unverified")
    argv = ["-c", "windows.sandbox=mxc", "app-server"] if explicit_mxc else ["app-server"]
    with _owned_stdio(binary, argv, cwd, environment, timeout, cancelled) as connection:
        yield connection
