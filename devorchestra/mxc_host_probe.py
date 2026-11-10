"""Credential-free OS isolation test for an App Server launched inside MXC."""
from base64 import b64encode
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from .app_server import _owned_stdio, verify_app_server_binary
from .job_probe import (
    ExtendedLimits, ProcessInfo, _account, _api, _cleanup, _create_owned, _require, _wait_empty,
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
)
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_launcher import _known_folder
from .native_access_probe import ALLOWED, PROTECTED, probe_receipt
from .native_manager import MANAGER_POLICY_METHODS
from .system_powershell import hold_system_powershell

PROFILE = "manager-host-offline"
LAUNCHER_PIN = "4f91ae86895ff124760d70ed1630e2354ca874765c9bb61ae524b600327efc17"
SERVER_PIN = "d781e2c96caeae19f5c8280814d778b92d4958bcca86064d6bec09d789ae7e22"
FILE_PROBE = r"""
$ErrorActionPreference = 'Stop'
function Observe([string]$Name, [scriptblock]$Operation) {
    try { & $Operation; [Console]::WriteLine("$Name=OK") }
    catch {
        $failure = $_.Exception
        while ($null -ne $failure.InnerException) { $failure = $failure.InnerException }
        if (($failure.HResult -band 65535) -ne 5) { throw }
        [Console]::WriteLine("$Name=DENIED_5")
    }
}
$work = [Environment]::CurrentDirectory
$allowed = [IO.Path]::Combine($work, 'allowed.txt')
$protected = [IO.Path]::Combine([IO.Directory]::GetParent($work).FullName, 'protected', 'protected.txt')
Observe ALLOWED_READ { [void][IO.File]::ReadAllBytes($allowed) }
Observe ALLOWED_WRITE {
    $handle = [IO.File]::Open($allowed, [IO.FileMode]::Open, [IO.FileAccess]::Write)
    $handle.Dispose()
}
Observe PROTECTED_READ { [void][IO.File]::ReadAllBytes($protected) }
"""


def _profile(work, home, scratch):
    paths = "\n".join(f'{json.dumps(str(path))} = "{access}"'
                      for path, access in ((work, "read"), (home, "write"), (scratch, "write")))
    return (f'default_permissions = "{PROFILE}"\napproval_policy = "never"\n'
            f'[permissions.{PROFILE}.filesystem]\n":root" = "deny"\n":minimal" = "read"\n{paths}\n'
            f'[permissions.{PROFILE}.network]\nenabled = false\n')


def _args(child, *arguments):
    return ["-c", "windows.sandbox=mxc", "sandbox", "--include-managed-config",
            "--permission-profile", PROFILE, "--", str(child), *arguments]


def _observe_files(launcher, argv, work, scratch, environment):
    import msvcrt

    api = _api()
    job = _require(api.CreateJobObjectW(None, None))
    info, drained = ProcessInfo(), False
    stdout, stderr = scratch / "probe.stdout", scratch / "probe.stderr"
    try:
        limits = ExtendedLimits()
        limits.basic.flags = (JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
                              | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.basic.active_limit = 8
        limits.job_memory = 512 * 1024 * 1024
        _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        with open(os.devnull, "rb") as stdin, stdout.open("xb") as out, stderr.open("xb") as err:
            stdio = tuple(msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, out, err))
            for handle in stdio:
                os.set_handle_inheritable(handle, True)
            _create_owned(api, job, launcher, argv, work, environment, info, stdio)
        if _account(api, job).active != 1 or api.ResumeThread(info.thread) != 1:
            raise Blocked("MXC file probe ownership unverified")
        if _wait_empty(api, job, monotonic() + 15).active or api.WaitForSingleObject(info.process, 0) != 0:
            raise Blocked("MXC file probe timeout")
        drained = True
        code = w.DWORD()
        _require(api.GetExitCodeProcess(info.process, c.byref(code)))
    finally:
        _cleanup(api, job, info, drained)
    for path in (stdout, stderr):
        check_path(path)
    with stdout.open("rb") as stream:
        output = stream.read(257)
    return probe_receipt({"exitCode": code.value, "stdout": output.decode("ascii", errors="replace"),
                          "stderr": "" if stderr.stat().st_size == 0 else "REDACTED"}, work, argv)


def run_host_probe(ledger, launcher, launcher_sha256, candidate, candidate_sha256, powershell_sha256):
    """Test only pinned binaries and synthetic files; this cannot admit a model turn."""
    launcher, candidate = map(Path, (launcher, candidate))
    if (launcher.name.casefold() != "codex.exe" or launcher_sha256 != LAUNCHER_PIN
            or candidate.name.casefold() != "codex-app-server-x86_64-pc-windows-msvc.exe"
            or candidate_sha256 != SERVER_PIN):
        raise Blocked("The measured Codex CLI launcher and standalone App Server pins are required")
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        runner = Runner(ledger)
        events, tail = runner._idle_diagnostic(abandoned)
        verify_app_server_binary(launcher, approved_sha256=launcher_sha256)
        verify_app_server_binary(candidate, approved_sha256=candidate_sha256)
        temporary = ledger.local_directory("temp")
        request_id = uuid4().hex
        fields = {"project_id": None, "run_id": None, "state": "DIAGNOSTIC",
                  "source": "WINDOWS_NATIVE_CODEX", "request_id": request_id,
                  "probe_kind": "MXC_OFFLINE_MANAGER_HOST", "launcher_sha256": launcher_sha256,
                  "candidate_sha256": candidate_sha256, "powershell_sha256": powershell_sha256,
                  "file_probe_ref": digest(FILE_PROBE)}
        with TemporaryDirectory(prefix="mxc-host-", dir=temporary) as folder:
            lab = Path(folder) / f"app-server-{uuid4().hex}"
            work, home, scratch, protected = (lab / part for part in ("work", "home", "tmp", "protected"))
            for path in (work, home, scratch, protected):
                path.mkdir(parents=True)
            server_path = work / candidate.name
            copyfile(candidate, server_path)
            verify_app_server_binary(server_path, approved_sha256=candidate_sha256)
            profile = _profile(work, home, scratch)
            (home / "config.toml").write_text(profile, encoding="ascii")
            (work / "allowed.txt").write_bytes(ALLOWED)
            (protected / "protected.txt").write_bytes(PROTECTED)
            windows, appdata = (_known_folder(number) for number in (0x24, 0x1C))
            env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                   "LOCALAPPDATA": str(appdata), "ProgramData": str(_known_folder(0x23)),
                   "PATH": str(windows / "System32"), "CODEX_HOME": str(home),
                   "TEMP": str(scratch), "TMP": str(scratch)}
            with hold_system_powershell(powershell_sha256) as (powershell, recheck):
                count, fence = ledger.append(len(events), tail, {
                    **fields, "kind": "native_inspection_intent", "profile_ref": digest(profile),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, admission=require_admission)
                runner._check_diagnostic_fence(count, fence)
                recheck()
                command = _args(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                b64encode(FILE_PROBE.encode("utf-16-le")).decode("ascii"))
                files = _observe_files(launcher, command, work, scratch, env)
            def cancelled():
                runner._check_diagnostic_fence(count, fence)
                return False

            cancelled()
            with _owned_stdio(launcher, _args(server_path, "--listen", "stdio://"),
                              work, env, 15, cancelled) as server:
                server.restrict_outbound(MANAGER_POLICY_METHODS)
                server.initialize()
        result = {**fields, "kind": "native_inspection_result", "timestamp": datetime.now(timezone.utc).isoformat(),
                  "sandboxed_app_server_stdio": "INITIALIZED", "file_probe": files, "owned_tree": "DRAINED",
                  "cleanup": server.cleanup_receipt, "model_turn": "NOT_STARTED",
                  "provider_egress": "NOT_VERIFIED", "server_internal_tools": "NOT_VERIFIED"}
        ledger.append(count, fence, result, allow_shutdown=True)
        return result
