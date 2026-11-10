"""Build provenance for a single reviewed standalone Windows GUI test app."""
from base64 import b64encode
import ctypes as c
from ctypes import wintypes as w
from hashlib import sha256
from pathlib import Path
import re
from time import monotonic

from .gui_test_exe import SOURCE
from .job_probe import (
    ExtendedLimits, ProcessInfo, _account, _api, _cleanup, _create_owned, _require, _wait_empty,
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
)
from .lease import Blocked
from .ledger import canonical, check_path
from .mxc_launcher import _known_folder
from .system_powershell import _open_locked

SOURCE_SHA256 = sha256(SOURCE.encode()).hexdigest()
EXE_MEMORY = 256 * 1024 * 1024
BUILD_MEMORY = 512 * 1024 * 1024
_BUILD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SOURCE__'))
$compiler = [Microsoft.CSharp.CSharpCodeProvider]::new()
try {
    $options = [CodeDom.Compiler.CompilerParameters]::new()
    $options.GenerateExecutable = $true
    $options.OutputAssembly = [IO.Path]::GetFullPath('gui-test.exe')
    $options.CompilerOptions = '/target:winexe /platform:x64 /optimize+'
    foreach ($reference in 'System.dll','System.Drawing.dll','System.Windows.Forms.dll','System.Web.Extensions.dll') {
        [void]$options.ReferencedAssemblies.Add($reference)
    }
    $result = $compiler.CompileAssemblyFromSource($options, $source)
    if ($result.Errors.HasErrors) {
        [IO.File]::WriteAllLines([IO.Path]::GetFullPath('build-errors.txt'), @($result.Errors | ForEach-Object { $_.ToString() }))
        exit 2
    }
} finally { $compiler.Dispose() }
"""
BUILD_SCRIPT = _BUILD_SCRIPT.replace("__SOURCE__", b64encode(SOURCE.encode()).decode("ascii"))
BUILD_SHA256 = sha256(BUILD_SCRIPT.encode()).hexdigest()


def embedded_build(form):
    """Insert only a base64 data literal into the fixed, reviewed host."""
    from .gui_project import parse

    data = canonical(form).encode("ascii")
    parse(data)
    encoded = b64encode(data).decode("ascii")
    source = SOURCE.replace('File.ReadAllText("approved-form.json")',
                            f'System.Text.Encoding.UTF8.GetString(Convert.FromBase64String("{encoded}"))')
    if source == SOURCE:
        raise Blocked("Trusted GUI source embedding point is unavailable")
    script = _BUILD_SCRIPT.replace("__SOURCE__", b64encode(source.encode()).decode("ascii"))
    return script, sha256(source.encode()).hexdigest(), sha256(script.encode()).hexdigest()


class BuildFailed(Blocked):
    """Known compiler failure after the owned Job is reclaimed."""


def tool_request(scope):
    return {**{key: scope[key] for key in ("project_id", "role", "generation", "session_id", "task_id",
                                          "cancel_token")},
            "tool": "gui_verify_registered_exe", "exe_name": Path(scope["exe"]).name}


def dynamic_gui_call(params, *, thread_id, turn_id, request, owner_scope_sha256, runner, cancelled):
    """Adapt the experimental wire payload without exposing approval or local artifacts to it."""
    fields = {"arguments", "callId", "threadId", "tool", "turnId"}
    if (type(params) is not dict or set(params) not in (fields, fields | {"namespace"})
            or params["threadId"] != thread_id or params["turnId"] != turn_id
            or type(params["callId"]) is not str or not 1 <= len(params["callId"]) <= 128
            or params["tool"] != request["tool"] or params["arguments"] != {}
            or params.get("namespace") is not None):
        raise Blocked("Dynamic GUI call is outside the bound turn and tool")
    receipt = runner.dispatch_gui_tool(request, owner_scope_sha256, cancelled)
    return {"success": receipt["result"] == "PASS", "contentItems": [
        {"type": "inputText", "text": f"{receipt['result']} · verification receipt {receipt['run_id']}"},
    ]}


def target(replica, filename):
    if not isinstance(filename, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,50}\.exe", filename):
        raise Blocked("Use one EXE filename at the registered work-copy root")
    path = Path(replica) / filename
    check_path(path)
    return path


def read_exe(path):
    check_path(path)
    with _open_locked(path) as (stream, _handle):
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024 or len(data) < 128 or data[:2] != b"MZ":
        raise Blocked("Trusted GUI build output is not a bounded PE")
    position = int.from_bytes(data[60:64], "little")
    if (data[position:position + 6] != b"PE\0\0\x64\x86"
            or data[position + 24:position + 26] != b"\x0b\x02"
            or data[position + 92:position + 94] != b"\x02\0"):
        raise Blocked("Trusted GUI build output is not an x64 windowed executable")
    return data


def compile_exe(directory, compiler, check, seconds, *, form=None):
    """Execute only the embedded compiler program, in the existing owned Job."""
    api = _api()
    job = _require(api.CreateJobObjectW(None, None))
    info, drained = ProcessInfo(), False
    try:
        limits = ExtendedLimits()
        limits.basic.flags = (JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
                              | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.basic.active_limit = 6
        limits.job_memory = BUILD_MEMORY
        _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        check()
        executable, recheck = compiler
        recheck()
        script = BUILD_SCRIPT if form is None else embedded_build(form)[0]
        command = ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                   b64encode(script.encode("utf-16-le")).decode("ascii")]
        _create_owned(api, job, executable, command, directory,
                      {"SystemRoot": str(_known_folder(0x24)), "TEMP": str(directory),
                       "TMP": str(directory)}, info)
        if _account(api, job).active != 1 or api.ResumeThread(info.thread) != 1:
            raise Blocked("Owned compiler assignment unverified")
        deadline = monotonic() + seconds
        while _account(api, job).active and monotonic() < deadline:
            check()
            _wait_empty(api, job, min(deadline, monotonic() + 0.1))
        if _account(api, job).active or api.WaitForSingleObject(info.process, 0) != 0:
            raise BuildFailed("Compiler timed out")
        drained = True
        code = w.DWORD()
        _require(api.GetExitCodeProcess(info.process, c.byref(code)))
        if code.value:
            raise BuildFailed(f"Trusted GUI compilation failed (exit {code.value})")
        try:
            return read_exe(directory / "gui-test.exe")
        except (Blocked, OSError) as error:
            raise BuildFailed("Compiler produced no verified x64 GUI executable") from error
    finally:
        _cleanup(api, job, info, drained)
