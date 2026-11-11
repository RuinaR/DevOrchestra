"""Credential-free OS isolation test for an App Server launched inside MXC."""
from base64 import b64encode
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from shutil import copyfile
import socket
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from .app_server import _owned_stdio, verify_app_server_binary
from .job_probe import (
    CODEX_DEVELOPER_PROCESS_LIMIT, ExtendedLimits, ProcessInfo, _account, _api, _cleanup, _create_owned,
    _require, _wait_empty,
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


class OwnedMxcStopped(Blocked):
    """Execution stopped and the owned Job was verified empty."""


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


def _profile(work, home, scratch, *, managed_loopback=False):
    paths = "\n".join(f'{json.dumps(str(path))} = "{access}"'
                      for path, access in ((work, "read"), (home, "write"), (scratch, "write")))
    network = ('enabled = true\nallow_local_binding = true\n'
               f'[permissions.{PROFILE}.network.domains]\n"boundary-probe.invalid" = "allow"\n'
               if managed_loopback else 'enabled = false\n')
    return (f'default_permissions = "{PROFILE}"\napproval_policy = "never"\n'
            f'[permissions.{PROFILE}.filesystem]\n":root" = "deny"\n":minimal" = "read"\n{paths}\n'
            f'[permissions.{PROFILE}.network]\n{network}')


def _args(child, *arguments, profile=PROFILE):
    return ["-c", "windows.sandbox=mxc", "sandbox", "--include-managed-config",
            "--permission-profile", profile, "--", str(child), *arguments]


def _observe(launcher, argv, work, scratch, environment, *, seconds=15, memory=512 * 1024 * 1024,
             check=None, output_limit=256, encoding="ascii", interact=None, process_limit=8):
    import msvcrt

    if type(process_limit) is not int or not 1 <= process_limit <= CODEX_DEVELOPER_PROCESS_LIMIT:
        raise Blocked("Owned command process limit is out of range")
    api = _api()
    job = _require(api.CreateJobObjectW(None, None))
    info, drained = ProcessInfo(), False
    stdout, stderr = scratch / "probe.stdout", scratch / "probe.stderr"
    stopped = None
    try:
        limits = ExtendedLimits()
        limits.basic.flags = (JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
                              | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.basic.active_limit = process_limit
        limits.job_memory = memory
        _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        with open(os.devnull, "rb") as stdin, stdout.open("xb") as out, stderr.open("xb") as err:
            stdio = tuple(msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, out, err))
            for handle in stdio:
                os.set_handle_inheritable(handle, True)
            _create_owned(api, job, launcher, argv, work, environment, info, stdio)
        if _account(api, job).active != 1 or api.ResumeThread(info.thread) != 1:
            raise Blocked("MXC probe ownership unverified")
        deadline = monotonic() + seconds
        if interact is not None:
            interact(api, job, deadline)
        if check is not None:
            while _account(api, job).active and monotonic() < deadline:
                check()
                _wait_empty(api, job, min(deadline, monotonic() + 0.1))
        counts = _wait_empty(api, job, deadline)
        if counts.active or api.WaitForSingleObject(info.process, 500) != 0:
            raise Blocked("MXC probe timeout")
        drained = True
        code = w.DWORD()
        _require(api.GetExitCodeProcess(info.process, c.byref(code)))
    except Blocked as error:
        if check is None:
            raise
        stopped = str(error)
    finally:
        _cleanup(api, job, info, drained)
    if stopped is not None:
        raise OwnedMxcStopped(stopped)
    for path in (stdout, stderr):
        check_path(path)
    with stdout.open("rb") as stream:
        output = stream.read(output_limit + 1)
    if check is not None and len(output) > output_limit:
        raise Blocked("MXC output exceeds the approved limit")
    return {"exitCode": code.value, "stdout": output.decode(encoding, errors="strict" if encoding == "utf-8" else "replace"),
            "job_active": counts.active, "job_total": counts.total,
            "stderr": "" if stderr.stat().st_size == 0 else "REDACTED"}


def _observe_loopback(launcher, powershell, work, scratch, env):
    def run(command):
        return _observe(launcher, _args(*command), work, scratch, env)

    return _loopback_challenge(powershell, run)


def _loopback_challenge(powershell, run, *, ipv6=False):
    family, host = (socket.AF_INET6, "::1") if ipv6 else (socket.AF_INET, "127.0.0.1")
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if ipv6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((host, 0))
        listener.listen(1)
        listener.settimeout(0.5)
        address = listener.getsockname()
        marker_bytes = uuid4().hex.encode("ascii")
        with socket.socket(family, socket.SOCK_STREAM) as control_client:
            control_client.settimeout(1)
            control_client.connect(address)
            control, _ = listener.accept()
            control.close()
        # Only this freshly bound numeric loopback endpoint can be contacted.
        script = f"""
$ErrorActionPreference = 'Stop'
$address = [Net.IPAddress]::Parse('{host}')
$client = [Net.Sockets.TcpClient]::new($address.AddressFamily)
try {{
    if ($client.ConnectAsync($address, {address[1]}).Wait(1500)) {{
        $payload = [Text.Encoding]::ASCII.GetBytes('{marker_bytes.decode("ascii")}')
        $client.GetStream().Write($payload, 0, $payload.Length)
        [Console]::WriteLine('CONNECTED')
    }} else {{ [Console]::WriteLine('TIMEOUT') }}
}} catch {{
    $failure = $_.Exception.GetBaseException()
    if ($failure -isnot [Net.Sockets.SocketException]) {{ throw }}
    [Console]::WriteLine('SOCKET_' + $failure.NativeErrorCode)
}} finally {{ $client.Dispose() }}
"""
        command = _powershell_command(powershell, script)
        observation = run(command)
        accepted = False
        matched = False
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            pass
        else:
            accepted = True
            with connection:
                connection.settimeout(0.5)
                try:
                    with connection.makefile("rb") as stream:
                        matched = stream.read(len(marker_bytes)) == marker_bytes
                except TimeoutError:
                    pass
    marker = observation["stdout"].strip()
    status = "INCONCLUSIVE"
    if observation["exitCode"] == 0 and not observation["stderr"]:
        if marker == "CONNECTED" and matched:
            status = "REACHABLE"
        elif marker == "SOCKET_10013" and not accepted:
            status = "ACCESS_DENIED"
    return {"status": status, "host_control": "CONNECTED", "listener_accepted": accepted,
            "challenge_matched": matched,
            "exit_code": observation["exitCode"], "job_active": observation.get("job_active"),
            "child_observation": marker if marker in {"CONNECTED", "TIMEOUT", "SOCKET_10013"} else "OTHER"}


def _command_profiles(work):
    path = json.dumps(str(work))
    return '\n[windows]\nsandbox = "mxc"\n' + ''.join(
        f'\n[permissions.probe-{access}.filesystem]\n":root" = "deny"\n'
        f'":minimal" = "read"\n{path} = "{access}"\n'
        f'[permissions.probe-{access}.network]\nenabled = false\n' for access in ("read", "write"))


def _powershell_command(powershell, script):
    return [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
            b64encode(script.encode("utf-16-le")).decode("ascii")]


def _server_command(server, request_id, work, command, access):
    return server.request(request_id, "command/exec", {
        "command": command, "cwd": str(work), "timeoutMs": 5000,
        "permissionProfile": "probe-" + access,
    })


def _observe_server_commands(server, work, command, *, first_id=1):
    observations = {}
    for request_id, access in enumerate(("read", "write"), first_id):
        try:
            response = _server_command(server, request_id, work, command, access)
        except Blocked:
            if server.failure_code != "RPC_ERROR":
                raise
            diagnostic = server.diagnostics[-1]
            observations[access] = {"status": "RPC_REJECTED", "error": diagnostic["error"],
                                    **diagnostic.get("shape", {})}
            break
        observations[access] = _command_observation(response, work, command, access)
    return observations


def _command_observation(response, work, command, access):
    receipt = probe_receipt(response, work, command)
    expected = {"ALLOWED_READ": "OK", "ALLOWED_WRITE": "OK" if access == "write" else "DENIED_5",
                "PROTECTED_READ": "DENIED_5"}
    return {**receipt, "status": "PASS" if receipt["markers_unchanged"] is True
            and receipt["outcomes"] == expected else "NOT_VERIFIED"}


def run_host_probe(ledger, launcher, launcher_sha256, candidate, candidate_sha256, powershell_sha256,
                   *, loopback_only=False, managed_loopback=False, command_boundary=False):
    """Test only pinned binaries and synthetic files; this cannot admit a model turn."""
    if managed_loopback and not loopback_only:
        raise Blocked("Managed network diagnostic is restricted to the owned loopback listener")
    if command_boundary and loopback_only:
        raise Blocked("Choose one MXC diagnostic")
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
                  "probe_kind": ("MXC_MANAGED_LOOPBACK" if managed_loopback else
                                 "MXC_OFFLINE_LOOPBACK" if loopback_only else
                                 "MXC_APP_SERVER_COMMAND" if command_boundary else "MXC_OFFLINE_MANAGER_HOST"),
                  "launcher_sha256": launcher_sha256, "candidate_sha256": candidate_sha256,
                  "powershell_sha256": powershell_sha256}
        if not loopback_only:
            fields["file_probe_ref"] = digest(FILE_PROBE)
        with TemporaryDirectory(prefix="mxc-host-", dir=temporary) as folder:
            lab = Path(folder) / f"app-server-{uuid4().hex}"
            work, home, scratch, protected = (lab / part for part in ("work", "home", "tmp", "protected"))
            for path in (work, home, scratch, protected):
                path.mkdir(parents=True)
            if not loopback_only:
                server_path = work / candidate.name
                copyfile(candidate, server_path)
                verify_app_server_binary(server_path, approved_sha256=candidate_sha256)
                (work / "allowed.txt").write_bytes(ALLOWED)
                (protected / "protected.txt").write_bytes(PROTECTED)
            profile = _profile(work, home, scratch, managed_loopback=managed_loopback)
            if command_boundary:
                inner_work = scratch / "command-work"
                inner_work.mkdir()
                inner_protected = scratch / "protected"
                inner_protected.mkdir()
                (inner_work / "allowed.txt").write_bytes(ALLOWED)
                (inner_protected / "protected.txt").write_bytes(PROTECTED)
                profile += _command_profiles(inner_work)
            (home / "config.toml").write_text(profile, encoding="ascii")
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
                if loopback_only:
                    loopback = _observe_loopback(launcher, powershell, work, scratch, env)
                else:
                    command = _powershell_command(powershell, FILE_PROBE)
                    outer_command = _args(*command)
                    files = probe_receipt(_observe(launcher, outer_command, work, scratch, env), work, outer_command)
                    if command_boundary:
                        controls = {}
                        for access in ("read", "write"):
                            runner._check_diagnostic_fence(count, fence)
                            recheck()
                            output = scratch / access
                            output.mkdir()
                            argv = _args(*command, profile="probe-" + access)
                            controls[access] = _command_observation(
                                _observe(launcher, argv, inner_work, output, env), inner_work, argv, access)

                def cancelled():
                    runner._check_diagnostic_fence(count, fence)
                    return False

                if not loopback_only:
                    cancelled()
                    with _owned_stdio(launcher, _args(server_path, "--listen", "stdio://"),
                                      work, env, 20, cancelled) as server:
                        methods = (frozenset(("initialize", "initialized", "command/exec"))
                                   if command_boundary else MANAGER_POLICY_METHODS)
                        server.restrict_outbound(methods)
                        server.initialize(experimental_api=command_boundary)
                        if command_boundary:
                            recheck()
                            server_commands = _observe_server_commands(server, inner_work, command)
        result = {**fields, "kind": "native_inspection_result", "timestamp": datetime.now(timezone.utc).isoformat(),
                  "profile_ref": digest(profile), "owned_tree": "DRAINED", "model_turn": "NOT_STARTED",
                  "provider_egress": "NOT_VERIFIED", "server_internal_tools": "NOT_VERIFIED"}
        if loopback_only:
            result["loopback"] = loopback
        else:
            result.update(sandboxed_app_server_stdio="INITIALIZED", file_probe=files, cleanup=server.cleanup_receipt)
            if command_boundary:
                result["server_commands"] = server_commands
                result["direct_controls"] = controls
        ledger.append(count, fence, result, allow_shutdown=True)
        return result
