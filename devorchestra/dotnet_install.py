"""Approved portable .NET SDK installation from Microsoft release metadata."""
from hashlib import sha512
from http.client import HTTPSConnection
import json
from pathlib import Path, PurePosixPath
import re
import stat
from time import monotonic
from zipfile import ZipFile

from .dotnet_environment import _json, _version, select
from .lease import Blocked
from .ledger import check_path, digest

METADATA = "dotnetcli.blob.core.windows.net"
DOWNLOAD = "builds.dotnet.microsoft.com"
ARCHIVE_LIMIT = 500 * 1024 * 1024


def _fetch(host, path, limit, write, check):
    connection = HTTPSConnection(host, timeout=15)
    try:
        check()
        connection.request("GET", path, headers={"Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Encoding") not in (None, "identity"):
            raise Blocked("Microsoft HTTPS download rejected, including redirects")
        total = 0
        while True:
            check()
            data = response.read(256 * 1024)
            if not data:
                break
            total += len(data)
            if total > limit:
                raise Blocked("Microsoft download exceeds approved limit")
            write(data)
    finally:
        connection.close()


def project_root(ledger, project):
    return ledger.local_directory("recovery") / "sdk" / digest(project)[:24]


def identity_at(replica, root):
    from .mxc_dotnet import _toolchain

    _, _, _, _, identity, selected = _toolchain(replica, root)
    return selected["sdk"], {Path(path).relative_to(root).as_posix(): value for path, value in identity.items()}


def registered(ledger, project, replica):
    for event in reversed(ledger.read()):
        if (event["kind"] == "sdk_install_result" and event["project_id"] == project and event["result"] == "PASS"):
            root = project_root(ledger, project) / event["scope"]["sdk"]
            check_path(root)
            try:
                version, identity = identity_at(replica, root)
            except Blocked:
                continue
            # Keys are relative so the same identity covers staging and final location.
            if identity != event["tool_sha256"] or version != event["scope"]["sdk"]:
                raise Blocked("Registered project SDK identity changed")
            return root
    return None


def verify(scope, replica, scratch, output, check):
    from .app_server import verify_app_server_binary
    from .build_execution import BuildExecution, limits
    from .mxc_dotnet import _toolchain
    from .mxc_host_probe import LAUNCHER_PIN, PROFILE, _profile
    from .mxc_launcher import _known_folder

    launcher = Path(scope["launcher"])
    verify_app_server_binary(launcher, approved_sha256=LAUNCHER_PIN)
    root, tool, _, _, identity, selected = _toolchain(replica, output)
    home, work, logs = [scratch / name for name in ("home", "work", "logs")]
    for path in (home, work, logs):
        path.mkdir()
    profile = _profile(work, home, logs).replace(
        f"[permissions.{PROFILE}.network]", f'{json.dumps(str(root))} = "read"\n[permissions.{PROFILE}.network]',
    )
    (home / "config.toml").write_text(profile, encoding="ascii")
    windows = _known_folder(0x24)
    env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
           "LOCALAPPDATA": str(_known_folder(0x1C)), "ProgramData": str(_known_folder(0x23)),
           "PATH": str(windows / "System32"), "CODEX_HOME": str(home), "TEMP": str(logs), "TMP": str(logs),
           "DOTNET_CLI_HOME": str(logs), "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
           "DOTNET_CLI_UI_LANGUAGE": "en-US", "DOTNET_EnableDiagnostics": "0"}
    step = BuildExecution(launcher, tool, work, logs, env, limits(20), check)
    report, receipt = step.step("dotnet-info", ["--info"])
    if report["exitCode"] or report["stderr"] or not re.search(
            rf"(?m)^\s*Version:\s*{re.escape(selected['sdk'])}\s*$", report["stdout"]):
        raise Blocked("Installed dotnet --info did not confirm the selected SDK")
    return {"info": receipt, "tool_sha256": {Path(path).relative_to(root).as_posix(): value
                                               for path, value in identity.items()}}


def review(replica, project):
    config, config_hash = _json(replica / "global.json")
    sdk = config.get("sdk")
    if type(sdk) is not dict or not (version := _version(sdk.get("version"))):
        raise Blocked("Portable installation requires a stable SDK version in global.json")
    channel = ".".join(map(str, version[:2]))
    requested = ".".join(map(str, version))
    if sdk.get("rollForward") != "disable" or set(config) != {"sdk"} or set(sdk) - {
            "version", "rollForward", "allowPrerelease"} or sdk.get("allowPrerelease", False) is not False:
        raise Blocked("Portable installation requires an exact stable SDK pin")
    return {"project_id": project, "sdk": requested, "rid": "win-x64", "global_json_sha256": config_hash,
            "metadata_host": METADATA, "metadata_path": f"/dotnet/release-metadata/{channel}/releases.json",
            "download_host": DOWNLOAD, "download_path": f"/dotnet/Sdk/{requested}/dotnet-sdk-{requested}-win-x64.zip",
            "archive_limit": ARCHIVE_LIMIT, "seconds": 600, "installer_scripts": False}


def _extract(archive, output, check):
    seen, total = set(), 0
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 50000:
            raise Blocked("SDK archive contains too many entries")
        for entry in entries:
            check()
            name = entry.filename.rstrip("/")
            path = PurePosixPath(name)
            if (not path.parts or path.as_posix() != name or path.is_absolute()
                    or any(not re.fullmatch(r"[A-Za-z0-9_.+@ -]+", part) or part in (".", "..")
                           or part.endswith((".", " ")) or re.fullmatch(
                               r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part, re.I)
                           for part in path.parts)
                    or name.casefold() in seen or stat.S_IFMT(entry.external_attr >> 16) not in
                    (0, stat.S_IFREG, stat.S_IFDIR)):
                raise Blocked("Unsafe path or entry type in SDK archive")
            seen.add(name.casefold())
            total += entry.file_size
            if total > 2 * 1024**3:
                raise Blocked("Expanded SDK archive exceeds limit")
            target = output.joinpath(*path.parts)
            # The archive can contain valid paths beyond legacy Win32 MAX_PATH.
            target = Path("\\\\?\\" + str(target))
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, target.open("xb") as destination:
                while chunk := source.read(1024 * 1024):
                    check()
                    destination.write(chunk)
    return {"entries": len(seen), "expanded_bytes": total}


def install(scope, replica, scratch, check):
    deadline = monotonic() + scope["seconds"]

    def active():
        check()
        if monotonic() >= deadline:
            raise Blocked("SDK download or extraction timed out")

    data = bytearray()
    _fetch(scope["metadata_host"], scope["metadata_path"], 16 * 1024**2, data.extend, active)
    metadata = json.loads(data)
    expected_url = f"https://{scope['download_host']}{scope['download_path']}"
    matches = {file["hash"].lower() for release in metadata["releases"] for sdk in release.get("sdks", [])
               if sdk["version"] == scope["sdk"] for file in sdk["files"]
               if file["rid"] == scope["rid"] and file["url"] == expected_url}
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{128}", checksum := next(iter(matches))):
        raise Blocked("SDK ZIP is absent or ambiguous in Microsoft release metadata")
    archive = scratch / "sdk.zip"
    observed = sha512()
    with archive.open("xb") as stream:
        def write(chunk):
            observed.update(chunk)
            stream.write(chunk)
        _fetch(scope["download_host"], scope["download_path"], scope["archive_limit"], write, active)
    if observed.hexdigest() != checksum:
        raise Blocked("Microsoft SDK archive SHA-512 mismatch")
    output = scratch / "sdk"
    output.mkdir()
    extracted = _extract(archive, output, active)
    if select(output, replica)["sdk"] != scope["sdk"]:
        raise Blocked("Extracted SDK does not match the approved project")
    check_path(output)
    return output, {**extracted, "source": expected_url, "sha512": checksum,
                    "archive_bytes": archive.stat().st_size}
