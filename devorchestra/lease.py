"""OS-wide execution slot for the trusted Windows runner."""
import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from threading import local

MUTEX_NAME = r"Global\DevOrchestra.P1.Execution.v1"
_thread_slot = local()


@contextmanager
def held_execution_slot():
    """A sequential phase may borrow its enclosing Controller session's slot."""
    if not getattr(_thread_slot, "owned", False):
        raise Blocked("Development session does not own the execution slot")
    yield False


def current_development_session():
    return getattr(_thread_slot, "development_session", None)


@contextmanager
def development_session_scope(identifier):
    if not getattr(_thread_slot, "owned", False) or current_development_session() is not None:
        raise Blocked("Development session ownership is unavailable")
    _thread_slot.development_session = identifier
    try:
        yield
    finally:
        del _thread_slot.development_session


class Blocked(RuntimeError):
    pass


class SlotBlocked(Blocked):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


@contextmanager
def execution_slot():
    if os.name != "nt":
        raise Blocked("Windows execution slot unavailable")
    if getattr(_thread_slot, "owned", False):
        raise SlotBlocked("Execution slot busy (recursive entry)", "EXECUTION_SLOT_BUSY")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    for function in (kernel.ReleaseMutex, kernel.CloseHandle):
        function.argtypes = (wintypes.HANDLE,)
        function.restype = wintypes.BOOL

    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise SlotBlocked("Execution slot unavailable", "EXECUTION_SLOT_UNAVAILABLE")
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            code = "EXECUTION_SLOT_BUSY" if result == 258 else "EXECUTION_SLOT_UNAVAILABLE"
            raise SlotBlocked("Execution slot busy or unverifiable", code)
        _thread_slot.owned = True
        try:
            yield result == 0x80
        finally:
            _thread_slot.owned = False
            if not kernel.ReleaseMutex(handle):
                raise Blocked("Execution slot release failed")
    finally:
        kernel.CloseHandle(handle)
