"""Fixed model-free App Server file boundary probe for an approved synthetic lab."""
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path

from .app_server import NativeInspectionApproval
from .lease import Blocked
from .ledger import check_path, digest


ALLOWED = b"DEVORCHESTRA_P243_ALLOWED"
PROTECTED = b"DEVORCHESTRA_P243_PROTECTED"
COMMAND_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class NativeAccessApproval:
    inspection: NativeInspectionApproval
    powershell_sha256: str


def _targets(work):
    return work / "allowed.txt", work.parent / "protected" / "protected.txt"


def _matches(path, expected):
    check_path(path)
    with path.open("rb") as stream:
        return stream.read(len(expected) + 1) == expected


def prepare_probe(work, powershell_sha256):
    from .mxc_launcher import _known_folder, _pin, _signed_offline

    allowed, protected = _targets(work)
    check_path(protected.parent)
    if ({path.name for path in protected.parent.iterdir()} != {"protected.txt"}
            or not _matches(allowed, ALLOWED) or not _matches(protected, PROTECTED)):
        raise Blocked("Synthetic probe markers changed")
    # Positive host control: known marker bytes and a write-capable open without mutation.
    with allowed.open("r+b"):
        pass
    windows = _known_folder(0x24)
    shell = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    _pin(shell, powershell_sha256)
    if not _signed_offline(shell):
        raise Blocked("System PowerShell signature unverified")
    return build_command(shell, allowed, protected)


def build_command(shell: Path, allowed: Path, protected: Path):
    def literal(path):
        return "'" + str(path).replace("'", "''") + "'"

    script = r"""
$ErrorActionPreference = 'Stop'
function Invoke-MarkerRead($Path, $Expected) {
    try {
        if ([IO.File]::ReadAllText($Path) -ceq $Expected) { 'OK' } else { 'ERROR' }
    } catch {
        if ($_.Exception.GetBaseException().HResult -eq -2147024891) { 'DENIED_5' } else { 'ERROR' }
    }
}
function Invoke-MarkerWrite($Path) {
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Write)
        try { $stream.WriteByte(88) } finally { $stream.Dispose() }
        'OK'
    } catch {
        if ($_.Exception.GetBaseException().HResult -eq -2147024891) { 'DENIED_5' } else { 'ERROR' }
    }
}
"""
    script += f"Write-Output ('ALLOWED_READ=' + (Invoke-MarkerRead {literal(allowed)} '{ALLOWED.decode()}'))\n"
    script += f"Write-Output ('ALLOWED_WRITE=' + (Invoke-MarkerWrite {literal(allowed)}))\n"
    script += f"Write-Output ('PROTECTED_READ=' + (Invoke-MarkerRead {literal(protected)} '{PROTECTED.decode()}'))\n"
    script += "exit 0\n"
    encoded = b64encode(script.encode("utf-16le")).decode("ascii")
    return [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def command_request(work, command):
    return {
        "command": command, "cwd": str(work), "timeoutMs": COMMAND_TIMEOUT_MS,
        "sandboxPolicy": {"type": "readOnly", "access": {
            "type": "restricted", "includePlatformDefaults": False, "readableRoots": [str(work)],
        }},
    }


def probe_receipt(result, work, command):
    names = ("ALLOWED_READ", "ALLOWED_WRITE", "PROTECTED_READ")
    outcomes = dict.fromkeys(names, "NOT_VERIFIED")
    code = result.get("exitCode")
    stdout = result.get("stdout")
    if (type(code) is int and code == 0 and type(stdout) is str and len(stdout) <= 256
            and result.get("stderr") == ""):
        lines = stdout.splitlines()
        if len(lines) == len(names):
            for name, line in zip(names, lines):
                key, separator, status = line.partition("=")
                if key == name and separator and status in ("OK", "DENIED_5"):
                    outcomes[name] = status
    try:
        unchanged = all(_matches(path, marker) for path, marker in zip(_targets(work), (ALLOWED, PROTECTED)))
    except (Blocked, OSError):
        unchanged = None
    failed = (unchanged is False or outcomes["ALLOWED_READ"] == "DENIED_5"
              or any(outcomes[name] == "OK" for name in names[1:]))
    passed = unchanged is True and tuple(outcomes.values()) == ("OK", "DENIED_5", "DENIED_5")
    return {"status": "FAIL" if failed else "PASS" if passed else "NOT_VERIFIED",
            "outcomes": outcomes, "markers_unchanged": unchanged, "command_hash": digest(command),
            "exit_code": code if type(code) is int else None,
            "desktop_noninterference": "NOT_VERIFIED", "network": "NOT_VERIFIED"}
