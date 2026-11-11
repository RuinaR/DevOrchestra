"""Read-only Windows token facts for reviewing a native egress deployment."""
from contextlib import ExitStack
import ctypes as c
from ctypes import wintypes as w
import os

from .lease import Blocked
from .system_powershell import _api


class _SidAndAttributes(c.Structure):
    _fields_ = [("sid", c.c_void_p), ("attributes", w.DWORD)]


class _TokenGroups(c.Structure):
    _fields_ = [("count", w.DWORD), ("groups", _SidAndAttributes * 1)]


def _token_information(query, token, kind):
    size = w.DWORD()
    if query(token, kind, None, 0, c.byref(size)) or c.get_last_error() != 122:
        raise Blocked("Windows token information size unverified")
    if not 0 < size.value <= 1024 * 1024:
        raise Blocked("Windows token information length out of bounds")
    buffer = c.create_string_buffer(size.value)
    if not query(token, kind, buffer, size, c.byref(size)) or size.value > len(buffer):
        raise Blocked("Windows token information unavailable")
    return buffer, size.value


def _administrator_group(query, token, create_sid, equal_sid):
    # WinBuiltinAdministratorsSid. Never expose the token's other group SIDs.
    sid = c.create_string_buffer(68)  # SECURITY_MAX_SID_SIZE
    size = w.DWORD(len(sid))
    if not create_sid(26, None, sid, c.byref(size)):
        raise Blocked("Built-in Administrators SID unavailable")
    buffer, length = _token_information(query, token, 2)  # TokenGroups
    offset = _TokenGroups.groups.offset
    if length < offset:
        raise Blocked("Windows token group list incomplete")
    count = w.DWORD.from_buffer(buffer).value
    if count > (length - offset) // c.sizeof(_SidAndAttributes):
        raise Blocked("Windows token group list invalid")
    for group in (_SidAndAttributes * count).from_buffer(buffer, offset):
        if equal_sid(group.sid, sid):
            if group.attributes & 0x10:  # SE_GROUP_USE_FOR_DENY_ONLY
                return "DENY_ONLY"
            return "ENABLED" if group.attributes & 0x4 else "DISABLED"
    return "ABSENT"


def _token_dword(query, token, kind):
    value, length = w.DWORD(), w.DWORD()
    if not query(token, kind, c.byref(value), c.sizeof(value), c.byref(length)) or length.value != c.sizeof(value):
        raise Blocked("Windows elevation response invalid")
    return value.value


def preflight():
    """Inspect only this process token. Never infer policy enforcement from privilege."""
    if os.name != "nt":
        raise Blocked("Native egress preflight requires Windows")
    kernel = c.WinDLL("kernel32", winmode=0x800, use_last_error=True)
    advapi = c.WinDLL("advapi32", winmode=0x800, use_last_error=True)
    current = _api(kernel, "GetCurrentProcess", w.HANDLE)
    close = _api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
    open_token = _api(advapi, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, c.POINTER(w.HANDLE))
    query = _api(advapi, "GetTokenInformation", w.BOOL, w.HANDLE, w.DWORD, c.c_void_p, w.DWORD, c.POINTER(w.DWORD))
    create_sid = _api(advapi, "CreateWellKnownSid", w.BOOL, w.DWORD, c.c_void_p, c.c_void_p, c.POINTER(w.DWORD))
    equal_sid = _api(advapi, "EqualSid", w.BOOL, c.c_void_p, c.c_void_p)
    with ExitStack() as handles:
        token = w.HANDLE()
        if not open_token(current(), 0x8, c.byref(token)):  # TOKEN_QUERY
            raise Blocked("Windows process token query denied")
        handles.callback(close, token)
        group = _administrator_group(query, token, create_sid, equal_sid)
        token_type = {1: "DEFAULT", 2: "FULL", 3: "LIMITED"}.get(_token_dword(query, token, 18))
        elevation_flag = _token_dword(query, token, 20)
        if token_type is None or elevation_flag not in (0, 1):
            raise Blocked("Windows elevation state unknown")
    return {"administrator_group": group, "token_elevation_type": token_type, "elevated": bool(elevation_flag),
            "policy_install_privilege_observed": group == "ENABLED" and bool(elevation_flag),
            "policy_changed": False, "host_egress": "NOT_VERIFIED", "online_admission": "BLOCKED"}
