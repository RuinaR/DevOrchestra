"""Offline MXC build for a restricted graph of SDK-style .NET projects."""
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
from xml.etree import ElementTree

from .app_server import verify_app_server_binary
from .build_execution import BuildExecution, limits
from .lease import Blocked
from .ledger import canonical, check_path, digest
from .mxc_dotnet import _toolchain
from .mxc_host_probe import LAUNCHER_PIN, PROFILE, OwnedMxcStopped, _profile
from .mxc_launcher import _known_folder
from .project_onboarding import _protected, _relative
from .replica_evidence import fingerprint, snapshot

KIND = "mxc-dotnet-sdk-project-v1"
PROPERTIES = {"OutputType": {"Exe", "Library"}, "ImplicitUsings": {"enable", "disable"},
              "Nullable": {"enable", "disable"}, "TreatWarningsAsErrors": {"true", "false"}}


def _project(data, name, target):
    if b"<!" in data or b"<?" in data:
        raise Blocked("DTD, XML directives and custom project constructs are not supported")
    try:
        root = ElementTree.fromstring(data.decode("utf-8-sig"))
    except (ElementTree.ParseError, UnicodeError) as error:
        raise Blocked("Invalid SDK project XML") from error
    if root.tag != "Project" or root.attrib != {"Sdk": "Microsoft.NET.Sdk"}:
        raise Blocked("Only the Microsoft.NET.Sdk project root is supported")
    properties, refs = {}, []
    for group in root:
        if group.attrib or group.tag not in {"PropertyGroup", "ItemGroup"} or (group.text or "").strip():
            raise Blocked("Custom imports, conditions, targets and tasks are blocked")
        for item in group:
            if len(item) or (item.tail or "").strip():
                raise Blocked("Nested project metadata is blocked")
            if group.tag == "PropertyGroup":
                value = (item.text or "").strip()
                allowed = {target} if item.tag == "TargetFramework" else PROPERTIES.get(item.tag, ())
                if item.attrib or item.tag in properties or value not in allowed:
                    raise Blocked("Unapproved SDK property or target framework")
                properties[item.tag] = value
            else:
                if item.tag in {"PackageReference", "PackageDownload"}:
                    raise Blocked("Package restore requires a separate verified offline package contract")
                include = item.attrib.get("Include", "")
                if (item.tag != "ProjectReference" or set(item.attrib) != {"Include"}
                        or (item.text or "").strip()
                        or not re.fullmatch(r"[A-Za-z0-9_./\\-]+\.csproj", include)):
                    raise Blocked("Only literal local ProjectReference entries are supported")
                reference = posixpath.normpath(posixpath.join(posixpath.dirname(name), include.replace("\\", "/")))
                _relative(reference)
                refs.append(reference)
    if "TargetFramework" not in properties or len(set(refs)) != len(refs):
        raise Blocked("Target framework or project references are incomplete")
    return {"output": properties.get("OutputType", "Library"), "references": sorted(refs)}


def review(replica, launcher, projects, tests, seconds, tool_root=None):
    if (type(projects) is not list or len(projects) != 1 or type(projects[0]) is not str
            or type(tests) is not list or not 1 <= len(tests) <= 16
            or any(type(name) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) for name in tests)
            or len(set(tests)) != len(tests) or type(seconds) is not int or not 1 <= seconds <= 120):
        raise Blocked("Select one executable test project and an explicit list of test case names")
    entry = projects[0]
    image = snapshot(replica)
    if not image["git"] or not image["git"]["index"]:
        raise Blocked("SDK build requires a registered Git replica")
    root, tool, _, _, identity, selected = _toolchain(replica, tool_root)
    launcher = Path(launcher)
    if not launcher.is_absolute() or launcher.name.casefold() != "codex.exe":
        raise Blocked("MXC requires the approved absolute launcher path")
    verify_app_server_binary(launcher, approved_sha256=LAUNCHER_PIN)
    sdk = root / "sdk" / selected["sdk"]
    # Recheck the complete selected SDK and framework trees, including implicit SDK targets.
    for tree in (sdk, selected["ref_pack"], selected["runtime"], selected["host"]):
        check_path(tree)
        for directory, folders, files in os.walk(tree):
            for name in [*folders, *files]:
                check_path(Path(directory) / name)
            for name in files:
                path = Path(directory) / name
                identity[str(path)] = fingerprint(path)["sha256"]
    inputs, graph = {}, {}
    for name, info in image["files"].items():
        path = _relative(name)
        folded = path.name.casefold()
        if (folded.endswith((".props", ".targets", ".rsp", ".fsproj", ".vbproj"))
                or folded in {"nuget.config", "packages.lock.json", "packages.config"}
                or any(part.casefold() in {"bin", "obj"} for part in path.parts)):
            raise Blocked("Custom build inputs, caches or package configuration are outside offline admission")
        if path.suffix.casefold() not in {".cs", ".csproj"}:
            continue
        if _protected(name) or not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+", name):
            raise Blocked("Build inputs must be safe files under explicit project directories")
        inputs[name] = info
        if path.suffix == ".csproj":
            if info["bytes"] > 16384:
                raise Blocked("Project XML exceeds the inspection limit")
            data = (replica / name).read_bytes()
            if sha256(data).hexdigest() != info["sha256"]:
                raise Blocked("Project XML changed during review")
            graph[name] = _project(data, name, selected["target"])
    if entry not in graph or graph[entry]["output"] != "Exe" or not 1 <= len(graph) <= 8:
        raise Blocked("A bounded graph with an executable test project is required")
    roots = {name: PurePosixPath(name).parent for name in graph}
    if any(left == right or left in right.parents for first, left in roots.items()
           for second, right in roots.items() if first != second):
        raise Blocked("Project directories must be separate to avoid implicit source glob overlap")
    visited, pending = set(), set()

    def visit(name):
        if name in pending or name not in graph:
            raise Blocked("Project references must be present, local and acyclic")
        if name in visited:
            return
        pending.add(name)
        for ref in graph[name]["references"]:
            visit(ref)
        pending.remove(name)
        visited.add(name)

    visit(entry)
    if (visited != set(graph) or any(name != entry and graph[name]["output"] != "Library" for name in graph)
            or any(not any(root_path in PurePosixPath(name).parents for root_path in roots.values()) for name in inputs)
            or len(inputs) > 128 or sum(item["bytes"] for item in inputs.values()) > 2 * 1024 * 1024):
        raise Blocked("Build inputs must belong to the reachable bounded project graph")
    return {"kind": KIND, "replica": str(replica), "replica_sha256": digest(image), "sources": inputs,
            "graph": graph, "entry": entry, "tests": tests, "sdk": selected["sdk"], "target": selected["target"],
            "runtime": selected["runtime"].name, "tool_root_read_only": str(root), "tool": str(tool),
            "toolchain_sha256": digest(identity), "launcher": str(launcher), "launcher_sha256": LAUNCHER_PIN,
            "configuration": "Release", "package_mode": "EMPTY_OFFLINE_FEED",
            "limits": {**limits(seconds), "output_limit": 131072, "encoding": "utf-8"}}


def run(ledger, scope, check, workspace):
    replica = Path(scope["replica"])
    current = review(replica, scope["launcher"], [scope["entry"]], scope["tests"],
                     scope["limits"]["max_seconds"], Path(scope["tool_root_read_only"]))
    if any(scope.get(key) != value for key, value in current.items()):
        raise Blocked("SDK project verification scope changed")
    home, output, work = (workspace / name for name in ("home", "output", "work"))
    profile_dir = output / "user"
    for folder in (home, output, work, profile_dir, profile_dir / "Roaming", profile_dir / "Local"):
        folder.mkdir()
    for name, info in scope["sources"].items():
        try:
            check()
        except Blocked as error:
            raise OwnedMxcStopped(str(error)) from error
        path = replica / name
        check_path(path)
        data = path.read_bytes()
        if sha256(data).hexdigest() != info["sha256"]:
            raise Blocked("SDK project input changed before staging")
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (work / "global.json").write_text(canonical({"sdk": {"version": scope["sdk"], "rollForward": "disable"}}),
                                    encoding="ascii")
    (work / "NuGet.Config").write_text(
        "<configuration><packageSources><clear /></packageSources></configuration>", encoding="ascii")
    windows = _known_folder(0x24)
    tool_root = Path(scope["tool_root_read_only"])
    env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
           "LOCALAPPDATA": str(profile_dir / "Local"), "APPDATA": str(profile_dir / "Roaming"),
           "USERPROFILE": str(profile_dir), "HOME": str(profile_dir), "ProgramData": str(profile_dir),
           "ProgramFiles": str(profile_dir), "ProgramFiles(x86)": str(profile_dir), "DOTNET_ROOT": str(tool_root),
           "PATH": str(windows / "System32"), "CODEX_HOME": str(home), "TEMP": str(output), "TMP": str(output),
           "DOTNET_CLI_HOME": str(output), "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
           "DOTNET_MULTILEVEL_LOOKUP": "0", "DOTNET_EnableDiagnostics": "0", "DOTNET_ADD_GLOBAL_TOOLS_TO_PATH": "0",
           "DOTNET_CLI_UI_LANGUAGE": "en-US", "DOTNET_CLI_FORCE_UTF8_ENCODING": "1",
           "MSBuildEnableWorkloadResolver": "false", "NUGET_PACKAGES": str(output / "packages")}

    def profile(writable):
        config = _profile(work, home, output).replace(
            f"[permissions.{PROFILE}.network]",
            f'{json.dumps(str(tool_root))} = "read"\n[permissions.{PROFILE}.network]',
        )
        if writable:
            config = config.replace(f'{json.dumps(str(work))} = "read"', f'{json.dumps(str(work))} = "write"')
        (home / "config.toml").write_text(config, encoding="ascii")

    profile(True)
    execution = BuildExecution(Path(scope["launcher"]), Path(scope["tool"]), work, output, env, scope["limits"], check)
    steps, logs = {}, {}

    def step(name, args):
        report, steps[name] = execution.step(name, args)
        logs[name] = {key: report[key][-8192:] for key in ("stdout", "stderr")}
        return report

    result = {"result": "FAIL", "job_active": 0, "steps": steps, "logs": logs, "tests": [], "artifacts": {}}
    common = ["-m:1", "-nr:false", "-p:UseSharedCompilation=false", "-p:NuGetAudit=false",
              "-p:ImportDirectoryBuildProps=false", "-p:ImportDirectoryBuildTargets=false",
              "-p:ImportDirectoryPackagesProps=false"]
    entry = str(work / scope["entry"])
    restore = step("restore", ["restore", entry, "--configfile", str(work / "NuGet.Config"), *common])
    if restore["exitCode"]:
        return {**result, "reason": "OFFLINE_RESTORE_FAILED"}
    build = step("build", ["build", entry, "--no-restore", "-c", scope["configuration"], *common])
    if build["exitCode"]:
        return {**result, "reason": "SDK_BUILD_FAILED"}
    app = Path(entry).parent / "bin" / scope["configuration"] / scope["target"]
    dll = app / (Path(entry).stem + ".dll")
    if not dll.is_file():
        return {**result, "reason": "TEST_ARTIFACT_MISSING"}
    artifacts = {path.relative_to(work).as_posix(): fingerprint(path) for path in app.iterdir()
                 if path.suffix.casefold() in {".dll", ".exe", ".json"}}
    if any(fingerprint(work / name) != info for name, info in scope["sources"].items()):
        raise Blocked("SDK build changed frozen source inputs")
    profile(False)
    report = step("test", ["exec", "--fx-version", scope["runtime"], str(dll)])
    expected = sorted("PASS " + name for name in scope["tests"])
    observed = sorted(report["stdout"].splitlines())
    if (any(fingerprint(work / name) != info for name, info in artifacts.items())
            or digest(snapshot(replica)) != scope["replica_sha256"]):
        raise Blocked("SDK build evidence or replica changed")
    passed = report["exitCode"] == 0 and not report["stderr"] and observed == expected
    artifact_dir = None
    if passed:
        try:
            check()
        except Blocked as error:
            raise OwnedMxcStopped(str(error)) from error
        if not re.fullmatch(r"build-verifier-[0-9a-f]{32}", workspace.name):
            raise Blocked("SDK artifact owner is not an admitted build")
        retained = ledger.local_directory("logs", create=True) / workspace.name
        check_path(retained)
        retained.mkdir()
        for name, info in artifacts.items():
            original = work / name
            data = original.read_bytes()
            if sha256(data).hexdigest() != info["sha256"]:
                raise Blocked("SDK artifact changed before private retention")
            destination = retained / original.name
            with destination.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if fingerprint(destination) != info:
                raise Blocked("Retained SDK artifact hash did not match")
        artifact_dir = retained.name
    return {**result, "result": "PASS" if passed else "FAIL", "reason": None if passed else "SDK_TEST_FAILED",
            "artifacts": artifacts, "artifact_dir": artifact_dir, "tests": observed, "source_files_sha256": {
                name: info["sha256"] for name, info in scope["sources"].items()}}


def run_session(ledger, manifest, replica, sources, run_id, check):
    from .owned_artifacts import cleanup, workspace

    frozen = manifest["build"]
    current = review(replica, frozen["launcher"], [frozen["entry"]], frozen["tests"],
                     frozen["limits"]["max_seconds"], Path(frozen["tool_root_read_only"]))
    if (any(current[key] != value for key, value in frozen.items() if key not in {"sources", "replica_sha256"})
            or {key: value for key, value in current["sources"].items() if key not in sources}
            != {key: value for key, value in frozen["sources"].items() if key not in sources}
            or any(current["sources"].get(name, {}).get("sha256") != sha256(data).hexdigest()
                   for name, data in sources.items())):
        raise Blocked("SDK build inputs outside the approved source edits changed")
    ledger.local_directory("temp", create=True)
    scratch = workspace(ledger, run_id)
    scratch.mkdir()
    identity = scratch.stat().st_ino
    try:
        evidence = run(ledger, current, check, scratch)
    except OwnedMxcStopped as error:
        evidence = {"result": "NOT_VERIFIED", "reason": str(error), "job_active": 0}
    removal = cleanup(ledger, run_id, scratch, identity)
    verdict = evidence["result"] if removal["status"] == "REMOVED" else "NOT_VERIFIED"
    steps = evidence.get("steps", {})
    build_ok = steps.get("build", {}).get("exitCode") == 0
    observations = [{"file": frozen["entry"], "input": "BUILD", "expected": True, "actual": build_ok}]
    observations.extend({"file": frozen["entry"], "input": name, "expected": True,
                         "actual": "PASS " + name in evidence.get("tests", [])} for name in frozen["tests"])
    return {"verdict": verdict, "exit_code": next(reversed(steps.values()), {}).get("exitCode"),
            "syntax": "PASS" if build_ok else "FAIL", "observations": observations, "runtime": KIND,
            "runtime_manifest_sha256": digest(manifest), "job_active": evidence["job_active"],
            "boundary_observations": {"profile": "MXC", "network_policy": "OFF"},
            "artifacts": evidence.get("artifacts", {}), "artifact_dir": evidence.get("artifact_dir"),
            "build_steps": steps, "build_logs": evidence.get("logs", {}),
            "cleanup": removal, "reason": evidence.get("reason")}
