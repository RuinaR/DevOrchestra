"""Narrow offline Roslyn adapter for a reviewed two-source console project."""
from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path
import re
import socket
from tempfile import TemporaryDirectory

from .app_server import verify_app_server_binary
from .build_execution import BuildExecution, limits
from .dotnet_environment import select
from .lease import Blocked
from .ledger import canonical, check_path, digest
from .mxc_host_probe import LAUNCHER_PIN, PROFILE, _profile
from .mxc_launcher import _known_folder, _signed_offline
from .replica_evidence import fingerprint, snapshot

KIND = "mxc-dotnet-console-v1"
PROGRAM = b"""using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;

static class Program {
    static bool Denied(Action action) {
        try { action(); return false; }
        catch (UnauthorizedAccessException) { return true; }
    }
    static int Main(string[] args) {
        var work = Directory.GetCurrentDirectory();
        var marker = Path.Combine(work, "..", "protected", "marker.cs");
        if (args.Length == 1 && args[0] == "child")
            return Denied(() => File.ReadAllBytes(marker)) ? 0 : 3;
        if (!Denied(() => { using var file = File.OpenWrite(args[2]); })
            || !Denied(() => File.ReadAllBytes(marker))
            || !Denied(() => { using var file = File.OpenWrite(marker); })) return 4;
        try {
            using var client = new TcpClient();
            client.Connect(IPAddress.Loopback, int.Parse(args[1], CultureInfo.InvariantCulture));
            return 5;
        } catch (SocketException e) {
            if (e.NativeErrorCode != 10013) return 6;
        }
        var start = new ProcessStartInfo(Environment.ProcessPath) { UseShellExecute = false };
        start.ArgumentList.Add("exec");
        start.ArgumentList.Add(typeof(Program).Assembly.Location);
        start.ArgumentList.Add("child");
        using var child = Process.Start(start);
        if (!child.WaitForExit(3000) || child.ExitCode != 0) return 7;
        Console.WriteLine("SOURCE_WRITE_DENIED; OUTSIDE_READ_WRITE_DENIED; LOOPBACK_10013; CHILD_READ_DENIED");
        Console.WriteLine(Calculation.Twice(int.Parse(args[0], CultureInfo.InvariantCulture))
            .ToString(CultureInfo.InvariantCulture));
        return 0;
    }
}
"""
CALCULATION = b"""static class Calculation {
    public static int Twice(int value) => checked(value * 2);
}
"""


def _toolchain(replica):
    root = _known_folder(0x26) / "dotnet"
    selected = select(root, replica)
    compiler = selected["compiler"]
    refs = selected["ref_pack"] / "ref" / selected["target"]
    names = ("System.Runtime", "System.Console", "System.Net.Primitives", "System.Net.Sockets",
             "System.Diagnostics.Process", "System.ComponentModel.Primitives", "Microsoft.Win32.Primitives")
    tool = root / "dotnet.exe"
    libraries = [refs / (name + ".dll") for name in names]
    runtime = selected["runtime"]
    files = [tool, compiler / "csc.dll", compiler / "csc.runtimeconfig.json", compiler / "csc.deps.json",
             compiler / "Microsoft.CodeAnalysis.dll", compiler / "Microsoft.CodeAnalysis.CSharp.dll",
             selected["host"] / "hostfxr.dll",
             runtime / "coreclr.dll", runtime / "hostpolicy.dll", runtime / "System.Private.CoreLib.dll",
             *libraries]
    try:
        identity = {str(path): fingerprint(path)["sha256"] for path in files}
    except OSError as error:
        raise Blocked("Required pinned .NET SDK or runtime files are missing") from error
    if not _signed_offline(tool) or not _signed_offline(compiler / "csc.dll"):
        raise Blocked(".NET host or compiler signature is unverified")
    return root, tool, compiler / "csc.dll", libraries, identity, selected


def review(replica, launcher, source_names, vectors, seconds):
    if (type(source_names) is not list or len(source_names) != 2
            or any(type(name) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}\.cs", name)
                   for name in source_names)
            or len({name.casefold() for name in source_names}) != 2):
        raise Blocked("Expected two explicit root C# source paths")
    if (type(vectors) is not list or not 1 <= len(vectors) <= 4
            or any(type(pair) is not list or len(pair) != 2
                   or any(type(value) is not int or abs(value) > 1000000 for value in pair) for pair in vectors)
            or type(seconds) is not int or not 1 <= seconds <= 30):
        raise Blocked("Invalid .NET test vectors or time budget")
    image = snapshot(replica)
    if any(path.casefold().endswith((".csproj", ".sln", ".slnx", ".props", ".targets"))
           for path in image["files"]):
        raise Blocked("Project build scripts and MSBuild metadata are outside the direct Roslyn adapter")
    sources = {name: image["files"].get(name, {}).get("sha256") for name in source_names}
    if sorted(filter(None, sources.values())) != sorted(sha256(data).hexdigest() for data in (PROGRAM, CALCULATION)):
        raise Blocked("Only the reviewed .NET console sources are admitted")
    if not image["git"]:
        raise Blocked(".NET verification requires a Git replica")
    launcher = Path(launcher)
    if not launcher.is_absolute() or launcher.name.casefold() != "codex.exe":
        raise Blocked("MXC requires the absolute path of the approved Codex launcher")
    verify_app_server_binary(launcher, approved_sha256=LAUNCHER_PIN)
    root, tool, compiler, libraries, identity, selected = _toolchain(replica)
    runtime = selected["runtime"].name
    config = {"runtimeOptions": {"tfm": selected["target"], "rollForward": "Disable",
                                 "framework": {"name": "Microsoft.NETCore.App", "version": runtime}}}
    compiler_command = ["exec", "--fx-version", runtime, str(compiler)]
    return {"kind": KIND, "launcher": str(launcher), "launcher_sha256": LAUNCHER_PIN,
            "replica": str(replica), "replica_sha256": digest(image), "git": image["git"],
            "sources": sources, "vectors": vectors, "tool": str(tool), "sdk": selected["sdk"], "runtime": runtime,
            "environment_selection": {"sdk_policy": selected["sdk_policy"],
                                      "global_json_sha256": selected["global_json_sha256"],
                                      "reference_pack": selected["ref_pack"].name, "host": selected["host"].name,
                                      "architecture": "win-x64", "source": "INSTALLED_SIGNED_TOOLCHAIN"},
            "tool_sha256": identity, "arguments": {
                "version": [*compiler_command, "/version"],
                "compile": [*compiler_command, "/nologo", "/noconfig", "/nostdlib+", "/target:exe",
                            "/preferreduilang:en-US", "/deterministic+", *[f"/reference:{path}" for path in libraries]],
                "execute": ["exec", "PRIVATE_APP_DLL", "TEST_VALUE", "OWNED_LOOPBACK_PORT", "SOURCE_NAME"],
            }, "workspace": "READ_ONLY_HASHED_COPY", "cwd": "STAGED_INPUT_ROOT",
            "writable": ["PRIVATE_TOOL_HOME", "PRIVATE_OUTPUT"],
            "outputs": ["app.dll", "app.runtimeconfig.json"],
            "tool_root_read_only": str(root), "runtime_config": config,
            "environment": ["SystemRoot", "WINDIR", "SystemDrive", "LOCALAPPDATA", "ProgramData", "PATH",
                            "CODEX_HOME", "TEMP", "TMP", "DOTNET_CLI_HOME", "DOTNET_CLI_TELEMETRY_OPTOUT",
                            "DOTNET_CLI_UI_LANGUAGE", "DOTNET_MULTILEVEL_LOOKUP", "DOTNET_EnableDiagnostics"],
            "limits": limits(seconds)}


def run(ledger, scope, check, workspace=None):
    replica, launcher = Path(scope["replica"]), Path(scope["launcher"])
    names = sorted(scope["sources"])
    current = review(replica, launcher, names, scope["vectors"], scope["limits"]["max_seconds"])
    if any(scope.get(key) != value for key, value in current.items()):
        raise Blocked(".NET execution scope changed")
    with (nullcontext(workspace) if workspace is not None else
          TemporaryDirectory(prefix="mxc-dotnet-", dir=ledger.local_directory("temp", create=True))) as temp:
        root = Path(temp)
        work, home, output, protected = [root / name for name in ("work", "home", "output", "protected")]
        for folder in (work, home, output, protected):
            folder.mkdir()
        for name in names:
            path = replica / name
            check_path(path)
            data = path.read_bytes()
            if sha256(data).hexdigest() != scope["sources"][name]:
                raise Blocked(".NET input changed")
            (work / name).write_bytes(data)
        marker = protected / "marker.cs"
        marker.write_bytes(b"static class Protected {}")
        profile = _profile(work, home, output).replace(
            f"[permissions.{PROFILE}.network]", f'{json.dumps(scope["tool_root_read_only"])} = "read"\n'
            f"[permissions.{PROFILE}.network]",
        )
        (home / "config.toml").write_text(profile, encoding="ascii")
        windows, appdata = (_known_folder(number) for number in (0x24, 0x1C))
        environment = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                       "LOCALAPPDATA": str(appdata), "ProgramData": str(_known_folder(0x23)),
                       "PATH": str(windows / "System32"), "CODEX_HOME": str(home),
                       "TEMP": str(output), "TMP": str(output), "DOTNET_CLI_HOME": str(output),
                       "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_CLI_UI_LANGUAGE": "en-US",
                       "DOTNET_MULTILEVEL_LOOKUP": "0", "DOTNET_EnableDiagnostics": "0"}
        execution = BuildExecution(launcher, Path(scope["tool"]), work, output, environment, scope["limits"], check)
        compiler = scope["arguments"]["compile"]
        steps = {}

        def step(name, arguments):
            report, steps[name] = execution.step(name, arguments)
            return report

        app = output / "app.dll"
        config = output / "app.runtimeconfig.json"
        result = {"result": "FAIL", "steps": steps, "job_active": 0, "observations": [],
                  "profile_sha256": sha256(profile.encode()).hexdigest()}
        version = step("compiler-version", scope["arguments"]["version"])
        compiler_version = version["stdout"].strip()
        if (version["exitCode"] or version["stderr"]
                or not re.fullmatch(r"[0-9.]+(?:-[\w.+-]+)?(?: \([0-9a-f]+\))?", compiler_version)):
            return {**result, "result": "BLOCKED", "reason": "COMPILER_VERSION_UNVERIFIED"}
        result["compiler_version"] = compiler_version
        report = step("compile", [*compiler, f"/out:{app}", *names])
        if report["exitCode"] != 0 or report["stderr"]:
            return {**result, "reason": "COMPILER_FAILED"}
        artifact = fingerprint(app)
        config.write_text(canonical(scope["runtime_config"]), encoding="ascii")
        result["artifacts"] = {app.name: artifact, config.name: fingerprint(config)}
        write = step("compiler-write", [*compiler, "/out:denied.dll", *names])
        read = step("compiler-read", [*compiler, f"/out:{output / 'denied.dll'}", *names, str(marker)])
        result["boundaries"] = {
            "compiler_read_denied": read["exitCode"] == 1 and "error CS1504" in read["stdout"]
            and "denied" in read["stdout"].lower() and not (output / "denied.dll").exists(),
            "compiler_write_denied": write["exitCode"] == 1 and "error CS2012" in write["stdout"]
            and "denied" in write["stdout"].lower() and not (work / "denied.dll").exists(),
        }
        if not all(result["boundaries"].values()):
            return {**result, "result": "BLOCKED", "reason": "COMPILER_BOUNDARY_UNVERIFIED"}
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.25)
            address = listener.getsockname()
            with socket.create_connection(address, timeout=1):
                peer, _ = listener.accept()
                peer.close()
            for index, (value, expected) in enumerate(scope["vectors"]):
                report = step(f"run-{index}", ["exec", str(app), str(value), str(address[1]), names[0]])
                lines = report["stdout"].splitlines()
                if (report["exitCode"] or report["stderr"] or len(lines) != 2
                        or lines[0] != "SOURCE_WRITE_DENIED; OUTSIDE_READ_WRITE_DENIED; LOOPBACK_10013; CHILD_READ_DENIED"
                        or not re.fullmatch(r"-?[0-9]{1,8}", lines[1])):
                    return {**result, "result": "BLOCKED", "reason": "RUNTIME_BOUNDARY_OR_EXIT_UNVERIFIED"}
                result["observations"].append({"input": value, "expected": expected, "actual": int(lines[1])})
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                pass
            else:
                peer.close()
                return {**result, "result": "BLOCKED", "reason": "LOOPBACK_REACHED"}
        result["boundaries"].update(runtime_source_write_denied=True, runtime_outside_read_write_denied=True,
                                    runtime_loopback_error=10013, child_read_denied=True)
        if (marker.read_bytes() != b"static class Protected {}" or fingerprint(app) != artifact
                or digest(snapshot(replica)) != scope["replica_sha256"]
                or any(fingerprint(work / name)["sha256"] != scope["sources"][name] for name in names)):
            return {**result, "result": "BLOCKED", "reason": "EXECUTION_INPUT_OR_ARTIFACT_CHANGED"}
        return {**result, "result": "PASS" if all(row["actual"] == row["expected"]
                                                for row in result["observations"]) else "FAIL"}
