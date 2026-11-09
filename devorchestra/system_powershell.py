"""Pinned System32 Windows PowerShell identity, used only by the fixed probe."""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from hashlib import sha256
import os
from pathlib import Path
import stat
from uuid import UUID

from .lease import Blocked
from .ledger import check_path


def _api(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, args
    return function


def _system_path():
    from .mxc_launcher import _known_folder

    kernel = c.WinDLL("kernel32", winmode=0x800)
    get_system = _api(kernel, "GetSystemDirectoryW", w.UINT, w.LPWSTR, w.UINT)
    buffer = c.create_unicode_buffer(260)
    if (not 0 < get_system(buffer, len(buffer)) < len(buffer)
            or Path(buffer.value) != _known_folder(0x24) / "System32"):
        raise Blocked("Trusted System32 directory unverified")
    return Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _identity(info):
    if (not stat.S_ISREG(info.st_mode) or not info.st_ino
            or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise Blocked("System PowerShell file identity unverified")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def _open_locked(path):
    import msvcrt

    ptr = c.c_void_p
    kernel = c.WinDLL("kernel32", winmode=0x800, use_last_error=True)
    create = _api(kernel, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                  ptr, w.DWORD, w.DWORD, w.HANDLE)
    close = _api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
    # Read sharing only; OPEN_EXISTING and OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ptr(-1).value:
        raise Blocked("System PowerShell read lock unavailable")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        close(handle)
        raise
    with os.fdopen(fd, "rb") as stream:
        yield stream, handle


def _catalog_publisher(path, handle):
    """Verify the SHA-256 Windows catalog member and read its trusted signer."""
    ptr = c.c_void_p
    trust = c.WinDLL("wintrust", winmode=0x800)
    crypto = c.WinDLL("crypt32", winmode=0x800)
    acquire = _api(trust, "CryptCATAdminAcquireContext2", w.BOOL, c.POINTER(w.HANDLE),
                   ptr, w.LPCWSTR, ptr, w.DWORD)
    release = _api(trust, "CryptCATAdminReleaseContext", w.BOOL, w.HANDLE, w.DWORD)
    calculate = _api(trust, "CryptCATAdminCalcHashFromFileHandle2", w.BOOL, w.HANDLE,
                     w.HANDLE, c.POINTER(w.DWORD), ptr, w.DWORD)
    find = _api(trust, "CryptCATAdminEnumCatalogFromHash", w.HANDLE,
                w.HANDLE, ptr, w.DWORD, w.DWORD, ptr)
    info_from = _api(trust, "CryptCATCatalogInfoFromContext", w.BOOL, w.HANDLE, ptr, w.DWORD)
    release_catalog = _api(trust, "CryptCATAdminReleaseCatalogContext", w.BOOL,
                           w.HANDLE, w.HANDLE, w.DWORD)

    class CatalogPath(c.Structure):
        _fields_ = [("size", w.DWORD), ("path", w.WCHAR * 260)]

    class CatalogMember(c.Structure):
        _fields_ = [("size", w.DWORD), ("version", w.DWORD), ("catalog", w.LPCWSTR),
                    ("tag", w.LPCWSTR), ("path", w.LPCWSTR), ("file", w.HANDLE),
                    ("hash", ptr), ("hash_size", w.DWORD), ("context", ptr), ("admin", w.HANDLE)]

    admin = w.HANDLE()
    if not acquire(c.byref(admin), None, "SHA256", None, 0):
        return None
    try:
        size = w.DWORD(32)
        value = (c.c_ubyte * 32)()
        if not calculate(admin, handle, c.byref(size), value, 0) or size.value != 32:
            return None
        catalog = find(admin, value, size.value, 0, None)
        if not catalog:
            return None
        try:
            info = CatalogPath(c.sizeof(CatalogPath))
            if not info_from(catalog, c.byref(info), 0):
                return None
            member = CatalogMember(c.sizeof(CatalogMember), 0, info.path, bytes(value).hex().upper(),
                                   str(path), handle, c.cast(value, ptr), size.value, None, admin)
            return _trusted_catalog_signer(trust, crypto, member)
        finally:
            release_catalog(admin, catalog, 0)
    finally:
        release(admin, 0)


def _trusted_catalog_signer(trust, crypto, member):
    ptr = c.c_void_p

    class TrustData(c.Structure):
        _fields_ = [("size", w.DWORD), ("policy", ptr), ("client", ptr),
                    ("ui", w.DWORD), ("revocation", w.DWORD), ("choice", w.DWORD),
                    ("subject", ptr), ("action", w.DWORD), ("state", w.HANDLE),
                    ("url", w.LPCWSTR), ("flags", w.DWORD), ("context", w.DWORD), ("settings", ptr)]

    class ProviderCertificate(c.Structure):
        _fields_ = [("size", w.DWORD), ("certificate", ptr)]

    verify = _api(trust, "WinVerifyTrust", w.LONG, w.HWND, ptr, ptr)
    provider = _api(trust, "WTHelperProvDataFromStateData", ptr, w.HANDLE)
    signer = _api(trust, "WTHelperGetProvSignerFromChain", ptr, ptr, w.DWORD, w.BOOL, w.DWORD)
    certificate = _api(trust, "WTHelperGetProvCertFromChain", c.POINTER(ProviderCertificate), ptr, w.DWORD)
    name = _api(crypto, "CertGetNameStringW", w.DWORD, ptr, w.DWORD, w.DWORD,
                ptr, w.LPWSTR, w.DWORD)
    action = (c.c_ubyte * 16).from_buffer_copy(UUID("00aac56b-cd44-11d0-8cc2-00c04fc295ee").bytes_le)
    data = TrustData(c.sizeof(TrustData), None, None, 2, 0, 2,
                     c.cast(c.pointer(member), ptr), 1, None, None, 0x1000, 0, None)
    try:
        if verify(None, action, c.byref(data)) != 0:
            return None
        evidence = provider(data.state)
        signed_by = signer(evidence, 0, False, 0) if evidence else None
        cert = certificate(signed_by, 0) if signed_by else None
        if not cert or not cert.contents.certificate:
            return None
        values = []
        for oid in (b"2.5.4.3", b"2.5.4.10"):  # common name, organization
            buffer = c.create_unicode_buffer(256)
            count = name(cert.contents.certificate, 3, 0, c.cast(c.c_char_p(oid), ptr), buffer, len(buffer))
            if not 1 < count < len(buffer):
                return None
            values.append(buffer.value)
        return tuple(values)
    finally:
        if data.state:
            data.action = 2  # WTD_STATEACTION_CLOSE
            verify(None, action, c.byref(data))


@contextmanager
def hold_system_powershell(expected_sha256):
    path = _system_path()
    check_path(path.parent)
    expected_identity = _identity(path.lstat())
    with _open_locked(path) as (stream, handle):
        def recheck():
            check_path(path.parent)
            if (_identity(os.fstat(stream.fileno())) != expected_identity
                    or _identity(path.lstat()) != expected_identity):
                raise Blocked("System PowerShell file changed")

        recheck()
        checksum = sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
        if checksum.hexdigest() != expected_sha256:
            raise Blocked("System PowerShell hash mismatch")
        stream.seek(0)
        if _catalog_publisher(path, handle) != ("Microsoft Windows", "Microsoft Corporation"):
            raise Blocked("System PowerShell catalog signature or publisher unverified")
        recheck()
        yield path, recheck
