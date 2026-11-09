"""Fixed model-free App Server file boundary probe for an approved synthetic lab."""
from base64 import b64encode
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from .app_server import InspectionBlocked, NativeInspectionApproval, inspection_check
from .lease import Blocked
from .ledger import check_path, digest


ALLOWED = b"DEVORCHESTRA_P243_ALLOWED"
PROTECTED = b"DEVORCHESTRA_P243_PROTECTED"
COMMAND_TIMEOUT_MS = 5000
PROFILE_ID = "devorchestra-probe-read"
# P2.51 host-tested App Server file probe. A rebuild requires a new pin and review.
BOUNDARY_PROBE_SHA256 = "6f3121bb3bd50b250479f994f0a890f3a53e1325f542b5a9397605987d97399e"


@dataclass(frozen=True)
class NativeAccessApproval:
    inspection: NativeInspectionApproval
    powershell_sha256: str


@dataclass(frozen=True)
class NativeBoundaryApproval:
    inspection: NativeInspectionApproval
    probe_sha256: str
    config_sha256: str
    request_sha256: str


def native_boundary_contract(work):
    """Build the exact staged config and request without launching anything."""
    check_path(work)
    if (not work.is_absolute() or len(work.drive) != 2 or work.drive[1] != ":"
            or work.name != "work" or len(str(work)) > 240):
        raise InspectionBlocked("COMMAND_POLICY", "RESTRICTED_WORK_PATH_INVALID")
    quoted = json.dumps(str(work), ensure_ascii=False)
    config = (f'default_permissions = "{PROFILE_ID}"\n'
              'approval_policy = "never"\n\n'
              '[windows]\nsandbox = "mxc"\n\n'
              f'[permissions.{PROFILE_ID}.filesystem]\n{quoted} = "read"\n\n'
              f'[permissions.{PROFILE_ID}.network]\nenabled = false\n').encode("utf-8")
    return config, {
        "command": [str(work / "mxc-probe.exe"), "--app-server-file-test"],
        "cwd": str(work), "timeoutMs": COMMAND_TIMEOUT_MS, "permissionProfile": PROFILE_ID,
    }


def verify_native_boundary(work, approval, config, request):
    with inspection_check("COMMAND_POLICY", "NATIVE_BOUNDARY_CONTRACT_MISMATCH"):
        if sha256(config).hexdigest() != approval.config_sha256 or digest(request) != approval.request_sha256:
            raise Blocked("Candidate config or request differs from approved hash")
    with inspection_check("MARKERS", "MARKER_INVALID"):
        allowed, protected = _targets(work)
        check_path(protected.parent)
        if ({path.name for path in protected.parent.iterdir()} != {"protected.txt"}
                or not _matches(allowed, ALLOWED) or not _matches(protected, PROTECTED)):
            raise Blocked("Synthetic probe markers changed")
    from .mxc_launcher import _pin

    with inspection_check("PROBE_IDENTITY", "NATIVE_PROBE_IDENTITY_INVALID"):
        if approval.probe_sha256 != BOUNDARY_PROBE_SHA256:
            raise Blocked("Approval does not name the pinned native probe")
        _pin(work / "mxc-probe.exe", BOUNDARY_PROBE_SHA256)


def _targets(work):
    return work / "allowed.txt", work.parent / "protected" / "protected.txt"


def _matches(path, expected):
    check_path(path)
    with path.open("rb") as stream:
        return stream.read(len(expected) + 1) == expected


@contextmanager
def prepare_probe(work, powershell_sha256):
    from .system_powershell import hold_system_powershell

    allowed, protected = _targets(work)
    def verify_markers():
        check_path(protected.parent)
        if ({path.name for path in protected.parent.iterdir()} != {"protected.txt"}
                or not _matches(allowed, ALLOWED) or not _matches(protected, PROTECTED)):
            raise Blocked("Synthetic probe markers changed")

    with inspection_check("MARKERS", "MARKER_INVALID"):
        verify_markers()
        # Positive host control: known marker bytes and a write-capable open without mutation.
        with allowed.open("r+b"):
            pass
    with ExitStack() as resources:
        with inspection_check("POWERSHELL_IDENTITY", "POWERSHELL_IDENTITY_INVALID"):
            shell, recheck_identity = resources.enter_context(hold_system_powershell(powershell_sha256))
        def before_dispatch():
            verify_markers()
            recheck_identity()

        yield build_command(shell, allowed, protected), before_dispatch
        recheck_identity()


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
    request = {
        "command": command, "cwd": str(work), "timeoutMs": COMMAND_TIMEOUT_MS,
        "sandboxPolicy": {"type": "readOnly", "access": {
            "type": "restricted", "includePlatformDefaults": False, "readableRoots": [str(work)],
        }},
    }
    validate_restricted_command(request)
    return request


def validate_restricted_command(request):
    """Fail closed until an equivalent restricted-read Windows contract is established."""
    policy = request.get("sandboxPolicy")
    if isinstance(policy, dict) and policy.get("type") == "readOnly":
        access = policy.get("access")
        # The pinned SandboxPolicyDeserialize rejects LegacyReadOnlyAccess::Restricted.
        if isinstance(access, dict) and access.get("type") == "restricted":
            raise InspectionBlocked("COMMAND_POLICY", "LEGACY_RESTRICTED_READ_UNSUPPORTED")
    raise InspectionBlocked("COMMAND_POLICY", "RESTRICTED_READ_CONTRACT_UNVERIFIED")


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
    markers = {}
    for path, marker in zip(_targets(work), (ALLOWED, PROTECTED)):
        before = sha256(marker).hexdigest()
        try:
            check_path(path)
            with path.open("rb") as stream:
                data = stream.read(len(marker) + 1)
            after = sha256(data).hexdigest() if len(data) <= len(marker) else None
            same = data == marker
        except (Blocked, OSError):
            after = same = None
        markers[path.name] = {"before": before, "after": after, "unchanged": same}
    comparisons = [marker["unchanged"] for marker in markers.values()]
    unchanged = False if False in comparisons else None if None in comparisons else True
    failed = (unchanged is False or outcomes["ALLOWED_READ"] == "DENIED_5"
              or any(outcomes[name] == "OK" for name in names[1:]))
    passed = unchanged is True and tuple(outcomes.values()) == ("OK", "DENIED_5", "DENIED_5")
    return {"status": "FAIL" if failed else "PASS" if passed else "NOT_VERIFIED",
            "outcomes": outcomes, "markers_unchanged": unchanged, "command_hash": digest(command),
            "marker_hashes": markers,
            "exit_code": code if type(code) is int else None,
            "desktop_noninterference": "NOT_VERIFIED", "network": "NOT_VERIFIED"}
