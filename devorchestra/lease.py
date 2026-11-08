"""OS-wide execution slot for the trusted Windows runner."""
import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from threading import local

MUTEX_NAME = r"Global\DevOrchestra.P1.Execution.v1"
_thread_slot = local()


class Blocked(RuntimeError):
    pass


@contextmanager
def execution_slot():
    if os.name != "nt":
        raise Blocked("Windows execution slot unavailable")
    if getattr(_thread_slot, "owned", False):
        raise Blocked("Execution slot busy (recursive entry)")
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
        raise Blocked("Execution slot unavailable")
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise Blocked("Execution slot busy or unverifiable")
        _thread_slot.owned = True
        try:
            yield result == 0x80
        finally:
            _thread_slot.owned = False
            if not kernel.ReleaseMutex(handle):
                raise Blocked("Execution slot release failed")
    finally:
        kernel.CloseHandle(handle)
