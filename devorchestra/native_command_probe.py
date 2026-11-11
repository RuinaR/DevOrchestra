"""Credential-free diagnostic for Codex's own MXC command executor."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import file_digest
from pathlib import Path
import socket
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4
from zipfile import ZipFile

from .app_server import open_app_server, verify_app_server_binary
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import (
    ALLOWED, FILE_PROBE, PROTECTED, _command_profiles, _loopback_challenge, _observe,
    _observe_server_commands, _powershell_command, _server_command,
)
from .mxc_launcher import _known_folder
from .native_manager import MANAGER_CONFIG, MANAGER_POLICY_METHODS, _verify_manager_config, _verify_manager_requirements
from .owned_artifacts import _remove_owned_tree
from .replica_evidence import snapshot
from .system_powershell import _open_locked, hold_system_powershell


RELEASE_SHA256 = "c12aca02de615a9fcb76f955bf005a33f4b10b577f8a1730161953f57a1155b3"
ARCHIVE_SHA256 = "191bd0dcd77a6aa17d4f8a4ab166fc9d05d24ca4b7015789e2aa9a53dcafb658"
ENTRYPOINT = "codex-x86_64-pc-windows-msvc.exe"
METHODS = MANAGER_POLICY_METHODS | {"command/exec"}
SOURCE_NAMES = ("subtotal.cmd", "total.cmd")
SUBTOTAL = '@echo off\nset /a subtotal=%1*{factor} >nul\n'
TOTAL = '@echo off\ncall subtotal.cmd %1\nset /a total=subtotal+{fee} >nul\necho %total%\n'
SOURCE_TEST = r"""
$ErrorActionPreference = 'Stop'
$result = & "$env:SystemRoot\System32\cmd.exe" /d /c 'call total.cmd 3'
if ($LASTEXITCODE -ne 0) { exit 2 }
[Console]::WriteLine($result)
if ($result -cne '11') { exit 1 }
"""


def _stage_release(archive, package):
    # Pin the complete distribution, including companion executables and resources.
    check_path(archive)
    with _open_locked(archive, source="Codex archive") as (stream, _):
        if file_digest(stream, "sha256").hexdigest() != ARCHIVE_SHA256:
            raise Blocked("The diagnostic requires the official Codex 0.167.1 Windows x64 archive")
        stream.seek(0)
        with ZipFile(stream) as release:
            release.extractall(package)
    binary = package / "codex.exe"
    (package / ENTRYPOINT).rename(binary)
    verify_app_server_binary(binary, approved_sha256=RELEASE_SHA256)
    return binary


@contextmanager
def _staging(ledger):
    lab = Path(mkdtemp(prefix="native-command-", dir=ledger.local_directory("temp")))
    identity = lab.stat().st_ino
    cleanup = {"preserve": False}
    try:
        yield lab, cleanup
    finally:
        if not cleanup.pop("preserve"):
            cleanup.update(_remove_owned_tree(lab, identity))


def _observe_fixture(server, powershell, work):
    before = snapshot(work)
    test = _powershell_command(powershell, SOURCE_TEST)
    baseline = _server_command(server, 6, work, test, "read")
    evidence = {"test_sha256": digest(test), "baseline_exit": baseline.get("exitCode")}
    if baseline.get("exitCode") != 1 or baseline.get("stdout", "").splitlines() != ["9"] or baseline.get("stderr"):
        return {**evidence, "status": "FAILED", "reason": "BASELINE_MISMATCH"}
    script = "$ErrorActionPreference = 'Stop'\n" + "\n".join(
        f"[IO.File]::WriteAllText([IO.Path]::Combine([Environment]::CurrentDirectory, '{name}'), '{source}')"
        for name, source in zip(SOURCE_NAMES, (SUBTOTAL.format(factor=3), TOTAL.format(fee=2))))
    edit = _powershell_command(powershell, script)
    mutation = _server_command(server, 7, work, edit, "write")
    changed = snapshot(work)
    files = {name for name in before["files"].keys() | changed["files"].keys()
             if before["files"].get(name) != changed["files"].get(name)}
    evidence.update(source_before_sha256=digest(before), source_after_sha256=digest(changed),
                    changed_file_count=len(files), edit_sha256=digest(edit), edit_exit=mutation.get("exitCode"))
    if mutation != {"exitCode": 0, "stdout": "", "stderr": ""}:
        return {**evidence, "status": "FAILED", "reason": "EDIT_FAILED"}
    if files != set(SOURCE_NAMES) or before["git"] != changed["git"]:
        return {**evidence, "status": "FAILED", "reason": "FILE_SCOPE_MISMATCH"}
    verified = _server_command(server, 8, work, test, "read")
    observed = verified.get("stdout", "").splitlines() == ["11"]
    passed = verified.get("exitCode") == 0 and observed and verified.get("stderr") == "" and snapshot(work) == changed
    return {**evidence, "status": "PASS" if passed else "FAILED", "changed_files": sorted(files),
            "verifier_exit": verified.get("exitCode"),
            "observed_expected_value": observed}


def _host_controls(powershell, work, scratch, environment, check):
    """Compare literal-IP TCP from a Controller-owned command with proxy variables set."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as proxy:
        proxy.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        proxy.bind(("127.0.0.1", 0))
        proxy.listen(1)
        proxy.setblocking(False)
        proxy_url = f"http://127.0.0.1:{proxy.getsockname()[1]}"
        environment = {**environment, "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url, "NO_PROXY": ""}
        controls = {}
        for family in ("ipv4", "ipv6"):
            output = scratch / family
            output.mkdir()
            check()
            controls[family] = _loopback_challenge(
                powershell, lambda command: _observe(powershell, command[1:], work, output, environment,
                                                       seconds=6, check=check), ipv6=family == "ipv6")
        try:
            connection, _ = proxy.accept()
        except BlockingIOError:
            proxy_contacted = False
        else:
            connection.close()
            proxy_contacted = True
    return {"literal_ip": controls, "proxy_contacted": proxy_contacted}


def run_native_command_probe(ledger, archive, powershell_sha256):
    """Use the signed release's local command sandbox without starting an agent thread."""
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        started = monotonic()
        runner = Runner(ledger)
        events, head = runner._idle_diagnostic(abandoned)
        fields = {"project_id": None, "run_id": None, "state": "DIAGNOSTIC", "request_id": uuid4().hex,
                  "source": "WINDOWS_NATIVE_CODEX", "probe_kind": "NATIVE_APP_SERVER_COMMAND",
                  "candidate_version": "0.167.1", "candidate_sha256": RELEASE_SHA256, "archive_sha256": ARCHIVE_SHA256,
                  "powershell_sha256": powershell_sha256, "file_probe_ref": digest(FILE_PROBE),
                  "timestamp": datetime.now(timezone.utc).isoformat(),
                  "host_egress": "NOT_VERIFIED", "model_turn": "NOT_STARTED", "usage": "UNKNOWN"}
        with _staging(ledger) as (lab, staging_cleanup):
            binary = _stage_release(Path(archive), lab / "package")
            work, home, scratch, protected = (lab / name for name in ("work", "home", "tmp", "protected"))
            for path in (work, home, scratch, protected):
                path.mkdir()
            (work / "allowed.txt").write_bytes(ALLOWED)
            (protected / "protected.txt").write_bytes(PROTECTED)
            for name, source in zip(SOURCE_NAMES, (SUBTOTAL.format(factor=2), TOTAL.format(fee=3))):
                (work / name).write_text(source, encoding="ascii")
            config = MANAGER_CONFIG + _command_profiles(work)
            (home / "config.toml").write_text(config, encoding="ascii")
            windows, appdata, program_data = (_known_folder(number) for number in (0x24, 0x1C, 0x23))
            environment = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                           "LOCALAPPDATA": str(appdata), "APPDATA": str(home), "USERPROFILE": str(home),
                           "ProgramData": str(program_data), "PATH": str(windows / "System32"),
                           "CODEX_HOME": str(home), "TEMP": str(scratch), "TMP": str(scratch)}
            fields["profile_ref"] = digest(config)
            with hold_system_powershell(powershell_sha256) as (powershell, recheck):
                count, fence = ledger.append(len(events), head, {**fields, "kind": "native_inspection_intent"},
                                             admission=require_admission)
                staging_cleanup["preserve"] = True

                def cancelled():
                    runner._check_diagnostic_fence(count, fence)
                    return False

                recheck()
                results = {"direct_host_controls": _host_controls(powershell, work, scratch, environment, cancelled)}
                with open_app_server(binary, work, environment, 25, cancelled, explicit_mxc=True,
                                     approved_sha256=RELEASE_SHA256) as server:
                    server.restrict_outbound(METHODS)
                    stage = "EFFECTIVE_POLICY"
                    try:
                        results["policy"] = server.inspect_policy(
                            cwd=work, verify_config=_verify_manager_config,
                            verify_requirements=_verify_manager_requirements, experimental_api=True)
                        if results["policy"]["approval"] != "never" or results["policy"]["sandbox"] != "read-only":
                            raise Blocked("Unexpected native probe policy")
                        stage = "FILE_COMMANDS"
                        recheck()
                        command = _powershell_command(powershell, FILE_PROBE)
                        results["server_commands"] = _observe_server_commands(server, work, command, first_id=3)
                        if all(results["server_commands"].get(mode, {}).get("status") == "PASS"
                               for mode in ("read", "write")):
                            stage = "COMMAND_LOOPBACK"
                            recheck()
                            results["loopback"] = _loopback_challenge(
                                powershell, lambda argv: _server_command(server, 5, work, argv, "write"))
                            results["loopback_ipv6"] = _loopback_challenge(
                                powershell, lambda argv: _server_command(server, 9, work, argv, "write"), ipv6=True)
                            if all(results[key]["status"] == "ACCESS_DENIED" for key in ("loopback", "loopback_ipv6")):
                                stage = "FIXED_SOURCE_EDIT_AND_VERIFICATION"
                                recheck()
                                results["fixed_fixture"] = _observe_fixture(server, powershell, work)
                    except Blocked:
                        cancelled()
                        results["failure"] = {"stage": stage, "code": server.failure_code or "POLICY_REJECTED"}
                staging_cleanup["preserve"] = False
            results["cleanup"] = server.cleanup_receipt
            results["protected_marker_unchanged"] = (protected / "protected.txt").read_bytes() == PROTECTED
        passed = (results.get("fixed_fixture", {}).get("status") == "PASS" and results["protected_marker_unchanged"]
                  and all(row["status"] == "REACHABLE" and row["job_active"] == 0
                          for row in results["direct_host_controls"]["literal_ip"].values())
                  and not results["direct_host_controls"]["proxy_contacted"]
                  and staging_cleanup.get("status") == "REMOVED")
        receipt = {**fields, **results, "kind": "native_inspection_result", "owned_tree": "DRAINED",
                   "status": "COMMAND_BOUNDARY_OBSERVED" if passed else "NOT_VERIFIED",
                   "staging_cleanup": staging_cleanup,
                   "elapsed_ms": round(1000 * (monotonic() - started)),
                   "timestamp": datetime.now(timezone.utc).isoformat()}
        ledger.append(count, fence, receipt, allow_shutdown=True)
        return receipt
