"""Bounded stdio App Server transport for a Controller-owned Windows Job."""
from collections import deque
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
from .ledger import canonical, check_path
from .instructions import selected_source, source_ref


MAX_FRAME = 256 * 1024
MAX_MESSAGES = 4096
MAX_NOTICES_PER_REQUEST = 16
SAFE_METHODS = frozenset((
    "initialize", "initialized", "config/read", "configRequirements/read", "command/exec",
    "thread/start", "thread/started", "turn/start", "turn/started", "turn/completed",
    "item/started", "item/completed", "item/agentMessage/delta", "thread/tokenUsage/updated",
    "warning", "configWarning", "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval", "item/permissions/requestApproval", "tool/requestUserInput",
))
ERROR_CODES = {-32700: "PARSE_ERROR", -32600: "INVALID_REQUEST", -32601: "METHOD_NOT_FOUND",
               -32602: "INVALID_PARAMS", -32603: "INTERNAL_ERROR", -32001: "OVERLOADED"}
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
        self.read_failure = None
        self.request_method = None
        self.request_id = None
        self.request_ids = set()
        self.completed_ids = set()
        self.received = 0
        self.config_warning_seen = False
        self.failure_code = None
        self.diagnostics = deque(maxlen=32)
        self.cleanup_receipt = {"job": "UNKNOWN", "pipes": "UNKNOWN"}
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
                    self.read_failure = ("FRAME_TOO_LARGE" if len(line) > MAX_FRAME
                                         else "TRUNCATED_FRAME", len(line))
                    self.broken.set()
                    break
                self.frames.put_nowait(line)
        except (OSError, Full, ValueError):
            self.read_failure = ("READER_OR_QUEUE_FAILURE", None)
            self.broken.set()
        finally:
            try:
                self.frames.put_nowait(None)
            except Full:
                if self.read_failure is None:
                    self.read_failure = ("QUEUE_LIMIT", None)
                self.broken.set()

    def _note(self, classification, *, method=None, matches=None, valid=None, size=None, error=None):
        self.diagnostics.append({
            "stage": "RPC" if self.request_method else "STREAM",
            "request_method": self.request_method,
            "classification": classification,
            "server_method": method if isinstance(method, str) and method in SAFE_METHODS
            else "UNRECOGNIZED" if method is not None else None,
            "id_matches": matches, "json_valid": valid, "frame_bytes": size, "error": error,
        })

    def _fail(self, classification, **fields):
        self.failure_code = classification
        self._note(classification, **fields)
        raise Blocked(f"App Server {classification}")

    def diagnostic_receipt(self):
        return {"protocol": list(self.diagnostics), "received_messages": self.received,
                "failure_code": self.failure_code, "cleanup": dict(self.cleanup_receipt)}

    def _check(self):
        if self.broken.is_set():
            reason, size = self.read_failure or ("READER_OR_QUEUE_FAILURE", None)
            self._fail(reason, size=size)
        if self.cancelled():
            self._fail("CANCELLED")
        if monotonic() >= self.deadline:
            self._fail("TIMEOUT")

    def send(self, message):
        self._check()
        frame = (json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if len(frame) > MAX_FRAME:
            self._fail("REQUEST_FRAME_LIMIT", size=len(frame))
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
            self._fail("WRITE_FAILED")

    @staticmethod
    def _unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    @staticmethod
    def _invalid_constant(_value):
        raise ValueError("invalid JSON constant")

    def receive(self):
        while True:
            self._check()
            try:
                line = self.frames.get(timeout=0.05)
                break
            except Empty:
                continue
        return self._decode(line)

    def _decode(self, line):
        if line is None:
            self._fail("EOF")
        self.received += 1
        if self.received > MAX_MESSAGES:
            self._fail("MESSAGE_LIMIT", size=len(line))
        try:
            message = json.loads(line.decode("utf-8"), object_pairs_hook=self._unique_pairs,
                                 parse_constant=self._invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            self.failure_code = "INVALID_JSON"
            self._note("INVALID_JSON", valid=False, size=len(line))
            raise Blocked("App Server INVALID_JSON") from None
        size = len(line)
        if not isinstance(message, dict) or message.get("jsonrpc", "2.0") != "2.0":
            self._fail("INVALID_ENVELOPE", valid=True, size=size)
        has_id = "id" in message
        identifier = message.get("id")
        matches = type(identifier) is int and identifier == self.request_id if has_id else None
        if "method" in message:
            method = message["method"]
            if (not isinstance(method, str) or not method or "result" in message or "error" in message
                    or ("params" in message and not isinstance(message["params"], (dict, list, type(None))))
                    or set(message) - {"id", "method", "params", "jsonrpc"}):
                self._fail("INVALID_ENVELOPE", valid=True, size=size)
            if has_id:
                self._fail("SERVER_REQUEST_BLOCKED" if type(identifier) in (int, str)
                           else "INVALID_ENVELOPE", method=method, matches=matches, valid=True, size=size)
            self._note("NOTIFICATION", method=method, valid=True, size=size)
        else:
            if (not has_id or type(identifier) not in (int, str)
                    or ("result" in message) == ("error" in message)
                    or set(message) - {"id", "result", "error", "jsonrpc"}):
                self._fail("INVALID_ENVELOPE", matches=matches, valid=True, size=size)
            if type(identifier) is int and identifier in self.completed_ids:
                self._fail("DUPLICATE_RESPONSE", matches=matches, valid=True, size=size)
            if not matches:
                self._fail("UNEXPECTED_RESPONSE_ID", matches=False, valid=True, size=size)
            if "error" in message:
                error = message["error"]
                if not isinstance(error, dict) or type(error.get("code")) is not int or not isinstance(error.get("message"), str):
                    self._fail("INVALID_ERROR_RESPONSE", matches=True, valid=True, size=size)
                self._fail("RPC_ERROR", matches=True, valid=True, size=size,
                           error=ERROR_CODES.get(error["code"], "OTHER_RPC_ERROR"))
            if not isinstance(message["result"], dict):
                self._fail("INVALID_RESULT", matches=True, valid=True, size=size)
            self._note("MATCHED_RESPONSE", matches=True, valid=True, size=size)
        return message

    def _informational(self, message):
        method, params = message["method"], message.get("params")
        if method not in ("warning", "configWarning"):
            return False
        field = "message" if method == "warning" else "summary"
        if not isinstance(params, dict) or not isinstance(params.get(field), str):
            self._fail("INVALID_NOTIFICATION", method=method, valid=True)
        if method == "configWarning":
            self.config_warning_seen = True
        return True

    def request(self, request_id, method, params, on_notification=None, *, allowed_notifications=()):
        if type(request_id) is not int or request_id in self.request_ids or self.request_method is not None:
            self._fail("INVALID_REQUEST_ID")
        self.request_ids.add(request_id)
        self.request_id = request_id
        self.request_method = method if method in SAFE_METHODS else "UNRECOGNIZED"
        try:
            self.send({"id": request_id, "method": method, "params": params})
            for _ in range(MAX_NOTICES_PER_REQUEST + 1):
                reply = self.receive()
                if "method" not in reply:
                    self.completed_ids.add(request_id)
                    return reply["result"]
                if self._informational(reply):
                    continue
                if on_notification is None or reply["method"] not in allowed_notifications:
                    self._fail("UNSUPPORTED_NOTIFICATION", method=reply["method"], valid=True)
                on_notification(reply)
            self._fail("NOTIFICATION_LIMIT")
        finally:
            self.request_id = self.request_method = None

    def check_trailing_messages(self):
        for _ in range(MAX_NOTICES_PER_REQUEST + 1):
            try:
                line = self.frames.get_nowait()
            except Empty:
                return
            if line is None:
                return
            message = self._decode(line)
            if not self._informational(message):
                self._fail("UNSUPPORTED_TRAILING_NOTIFICATION", method=message["method"], valid=True)
        self._fail("NOTIFICATION_LIMIT")

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
        if self.config_warning_seen:
            self._fail("CONFIG_WARNING_REQUIRES_REVIEW", method="configWarning")
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


def save_inspection_diagnostic(ledger, request_id, server, status):
    """Write only fixed classifications to a separate local file, even if SQLite failed."""
    directory = ledger.local_directory("logs", create=True)
    path = directory / f"app-server-{request_id}.json"
    check_path(path)
    data = server.diagnostic_receipt() if server is not None else {
        "protocol": [], "received_messages": 0, "cleanup": {"job": "UNKNOWN", "pipes": "UNKNOWN"},
        "failure_code": "LAUNCH_OR_SETUP_UNVERIFIED",
    }
    if status == "RESULT_COMMIT_UNVERIFIED":
        data["failure_code"] = status
    elif status != "RESULT_COMMITTED" and data["failure_code"] is None:
        data["failure_code"] = status
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical({"request_id": request_id, "status": status, **data}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


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
            try:
                _cleanup(api, job, info, drained)
            except Exception:
                if connection is not None:
                    connection._fail("JOB_DRAIN_UNVERIFIED")
                raise
            if connection is not None:
                connection.cleanup_receipt["job"] = "DRAINED_NORMALLY" if drained else "TERMINATED_AND_DRAINED"
                connection.reader.join(1)
                if connection.writer is not None:
                    connection.writer.join(1)
                if connection.reader.is_alive() or (connection.writer and connection.writer.is_alive()):
                    connection._fail("PIPE_DRAIN_UNVERIFIED")
                stdin.close()
                stdout.close()
                connection.cleanup_receipt["pipes"] = "CLOSED"
                if drained and connection.broken.is_set():
                    reason, size = connection.read_failure or ("READER_OR_QUEUE_FAILURE", None)
                    connection._fail(reason, size=size)
                if drained:
                    connection.check_trailing_messages()
                    if connection.config_warning_seen:
                        connection._fail("CONFIG_WARNING_REQUIRES_REVIEW", method="configWarning")


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
