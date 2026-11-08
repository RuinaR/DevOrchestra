"""Bounded Windows Job Object probes for the trusted test runner only."""
import ctypes as c
from ctypes import wintypes as w
from enum import Enum
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

from .lease import Blocked

SIZE = c.c_size_t
PTR = c.c_void_p
MEMORY_LIMIT = 96 * 1024 * 1024
MAX_PROCESSES = 2
TIME_LIMIT = 2.0
CLEANUP_LIMIT = 3.0
KILL_EXIT = 119

# Windows SDK constants.
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000


class Probe(str, Enum):
    EXIT = "exit"
    ERROR = "error"
    TREE = "tree"


FIXTURES = {
    Probe.EXIT: "raise SystemExit(0)",
    Probe.ERROR: "import os; os._exit(17)",
    Probe.TREE: (
        "import os, subprocess, sys; "
        "subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', "
        "'import time; time.sleep(8)'], env={'SystemRoot': os.environ['SystemRoot']}, close_fds=True, "
        "creationflags=subprocess.CREATE_NO_WINDOW)"
    ),
}


class BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                ("flags", w.DWORD), ("min_ws", SIZE), ("max_ws", SIZE),
                ("active_limit", w.DWORD), ("affinity", SIZE),
                ("priority", w.DWORD), ("scheduling", w.DWORD)]


class ExtendedLimits(c.Structure):
    _fields_ = [("basic", BasicLimits), ("io", c.c_ulonglong * 6),
                ("process_memory", SIZE), ("job_memory", SIZE),
                ("peak_process", SIZE), ("peak_job", SIZE)]


class Accounting(c.Structure):
    _fields_ = [("times", c.c_longlong * 4), ("faults", w.DWORD),
                ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]


class StartupInfo(c.Structure):
    _fields_ = [("size", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                ("title", w.LPWSTR), ("geometry", w.DWORD * 8),
                ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_data", PTR),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class StartupInfoEx(c.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", PTR)]


class ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
                ("pid", w.DWORD), ("tid", w.DWORD)]


def _api():
    if os.name != "nt":
        raise Blocked("Windows Job Objects unavailable")
    api = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": (w.HANDLE, (PTR, w.LPCWSTR)),
        "SetInformationJobObject": (w.BOOL, (w.HANDLE, c.c_int, PTR, w.DWORD)),
        "QueryInformationJobObject": (w.BOOL, (w.HANDLE, c.c_int, PTR, w.DWORD, PTR)),
        "InitializeProcThreadAttributeList": (w.BOOL, (PTR, w.DWORD, w.DWORD, c.POINTER(SIZE))),
        "UpdateProcThreadAttribute": (w.BOOL, (PTR, w.DWORD, SIZE, PTR, SIZE, PTR, PTR)),
        "DeleteProcThreadAttributeList": (None, (PTR,)),
        "CreateProcessW": (w.BOOL, (w.LPCWSTR, w.LPWSTR, PTR, PTR, w.BOOL, w.DWORD,
                                     PTR, w.LPCWSTR, PTR, c.POINTER(ProcessInfo))),
        "ResumeThread": (w.DWORD, (w.HANDLE,)),
        "WaitForSingleObject": (w.DWORD, (w.HANDLE, w.DWORD)),
        "GetExitCodeProcess": (w.BOOL, (w.HANDLE, c.POINTER(w.DWORD))),
        "TerminateJobObject": (w.BOOL, (w.HANDLE, w.UINT)),
        "CloseHandle": (w.BOOL, (w.HANDLE,)),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(api, name)
        function.restype = result
        function.argtypes = arguments
    return api


def _require(result):
    if not result:
        raise c.WinError(c.get_last_error())
    return result


def _account(api, job):
    counts = Accounting()
    _require(api.QueryInformationJobObject(job, 1, c.byref(counts), c.sizeof(counts), None))
    return counts


def _create(api, job, fixture, cwd, info):
    size = SIZE()
    api.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
    if not size.value or c.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
        raise Blocked("Cannot size process attributes")
    attributes = c.create_string_buffer(size.value)
    _require(api.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)))
    try:
        handles = (w.HANDLE * 1)(job)
        _require(api.UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                                               handles, c.sizeof(handles), None, None))
        startup = StartupInfoEx()
        startup.startup.size = c.sizeof(startup)
        startup.attributes = c.cast(attributes, PTR)
        interpreter = sys.executable
        command = c.create_unicode_buffer(subprocess.list2cmdline(
            [interpreter, "-I", "-S", "-B", "-c", FIXTURES[fixture]]
        ))
        # Python's Windows process bootstrap needs SystemRoot. Inherit no other variables.
        environment = c.create_unicode_buffer(f"SystemRoot={os.environ['SystemRoot']}\0")
        flags = (CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
                 | EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW)
        # The kernel assigns the Job at creation. No create-then-assign window.
        _require(api.CreateProcessW(interpreter, command, None, None, False, flags,
                                    environment, str(cwd), c.byref(startup), c.byref(info)))
    finally:
        api.DeleteProcThreadAttributeList(attributes)


def _wait_empty(api, job, deadline):
    while True:
        counts = _account(api, job)
        if counts.active == 0 or monotonic() >= deadline:
            return counts
        sleep(0.01)


def _run_probe(fixture, cwd, record_created, cancel_requested):
    """Run a fixed synthetic fixture while its caller owns the durable fence."""
    if not isinstance(fixture, Probe) or not Path(cwd).is_absolute():
        raise Blocked("Only fixed Windows Job fixtures are supported")
    api = _api()
    job = _require(api.CreateJobObjectW(None, None))
    info = ProcessInfo()
    try:
        limits = ExtendedLimits()
        limits.basic.flags = (JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
                              | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.basic.active_limit = MAX_PROCESSES
        limits.job_memory = MEMORY_LIMIT
        _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        configured = ExtendedLimits()
        _require(api.QueryInformationJobObject(job, 9, c.byref(configured), c.sizeof(configured), None))
        if (configured.basic.flags != limits.basic.flags or configured.job_memory != MEMORY_LIMIT
                or configured.basic.active_limit != MAX_PROCESSES):
            raise Blocked("Owned Job limits could not be verified")
        _create(api, job, fixture, cwd, info)
        record_created(_account(api, job).active)
        if api.ResumeThread(info.thread) != 1:
            raise Blocked("Unexpected synthetic child suspend count")
        deadline = monotonic() + TIME_LIMIT
        stop = None
        while True:
            counts = _account(api, job)
            if not counts.active:
                break
            if cancel_requested():
                stop = "CANCELLED"
                break
            if monotonic() >= deadline:
                stop = "TIME_LIMIT"
                break
            sleep(0.01)
        active_before_stop = counts.active
        root_wait = api.WaitForSingleObject(info.process, 0)
        if root_wait not in (0, 258):
            raise Blocked("Owned root status could not be verified")
        if stop:
            _require(api.TerminateJobObject(job, KILL_EXIT))
            counts = _wait_empty(api, job, monotonic() + CLEANUP_LIMIT)
        if counts.active or api.WaitForSingleObject(info.process, 1000) != 0:
            raise Blocked("Owned Job drain could not be verified")
        exit_code = w.DWORD()
        _require(api.GetExitCodeProcess(info.process, c.byref(exit_code)))
        return {"result": stop or ("OK" if exit_code.value == 0 else "FAIL"),
                "exit_code": exit_code.value, "active_before_stop": active_before_stop,
                "root_exited_before_stop": root_wait == 0,
                "active_processes": counts.active, "total_processes": counts.total,
                "job_memory_limit": configured.job_memory, "job_process_limit": configured.basic.active_limit}
    finally:
        # A failed launch, receipt or query must never leave a resumable orphan.
        try:
            if info.process and _account(api, job).active:
                _require(api.TerminateJobObject(job, KILL_EXIT))
                if _wait_empty(api, job, monotonic() + CLEANUP_LIMIT).active:
                    raise Blocked("Owned Job cleanup is unverified")
        finally:
            try:
                if info.thread:
                    _require(api.CloseHandle(info.thread))
                if info.process:
                    _require(api.CloseHandle(info.process))
            finally:
                _require(api.CloseHandle(job))
